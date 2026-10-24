"""End-to-end HTTP flows through the FastAPI app with fixture gateways."""

import json

import pytest
from fastapi.testclient import TestClient

from oracle_impact.main import create_app
from oracle_impact.security import csrf_token
from tests.support import client_for, settings

APPROVER_TOKEN = "approver-token"
COOKIE = "oia_session"


def _client(tmp_path, **overrides) -> TestClient:
    overrides.setdefault("approver_access_token", APPROVER_TOKEN)
    return client_for(tmp_path, **overrides)


def _sign_in(client: TestClient, token: str = "test-token") -> TestClient:
    response = client.post("/login", data={"access_token": token}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    return client


def _csrf(client: TestClient) -> str:
    return csrf_token("test-secret", client.cookies.get(COOKIE))


def _start(client: TestClient, package: str) -> str:
    response = client.post("/analyses", data={"packages": package, "csrf_token_value": _csrf(client)}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[1]


def _post(client: TestClient, path: str, **fields):
    fields.setdefault("csrf_token_value", _csrf(client))
    return client.post(path, data=fields, follow_redirects=False)


def _msg(response) -> str:
    assert response.status_code == 303, response.text
    return response.headers["location"].split("msg=", 1)[1].split("&", 1)[0]


def _review(client: TestClient, run_id: str) -> dict:
    response = client.get(f"/analyses/{run_id}/review")
    assert response.status_code == 200
    return response.json()


def test_public_and_protected_routes(tmp_path):
    client = _client(tmp_path)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/login").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    for path in ("/", "/mappings", "/analyses/x/report", "/analyses/x/review", "/analyses/x/export"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login", path


def test_unauthenticated_writes_are_redirected_and_record_nothing(tmp_path):
    owner = _sign_in(_client(tmp_path))
    run_id = _start(owner, "DEMO.PKG_STATUS")
    before = _review(owner, run_id)["readiness"]
    anonymous = _client(tmp_path)
    for path, fields in (
        (f"/analyses/{run_id}/validations", {"finding_id": "F-1", "status": "passed"}),
        (f"/analyses/{run_id}/reviews", {"decision": "accepted"}),
        (f"/analyses/{run_id}/approval", {"decision": "approved", "notes": "x"}),
        (f"/analyses/{run_id}/baseline", {"environment": "production", "object_key": "all", "note": "x"}),
        (f"/analyses/{run_id}/manifest", {"object_key": "all", "change_ref": "x"}),
        ("/mappings", {"object": "DEMO.PKG_STATUS"}),
        ("/registry/baselines/B-1/clear", {"run_id": run_id}),
        ("/analyses", {"packages": "DEMO.PKG_STATUS"}),
    ):
        response = anonymous.post(path, data=fields, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login", path
    after = _review(owner, run_id)
    assert all(after["review"][key] == [] for key in ("validations", "reviews", "approvals", "events"))
    assert after["readiness"]["decision"] == before["decision"]
    assert not (tmp_path / "_registry" / "mappings.json").exists()
    assert len([path for path in tmp_path.iterdir() if path.is_dir() and not path.name.startswith("_")]) == 1


def test_login_roles_and_logout(tmp_path):
    client = _client(tmp_path)
    rejected = client.post("/login", data={"access_token": "wrong"}, follow_redirects=False)
    assert rejected.status_code == 401 and "not accepted" in rejected.text
    assert COOKIE not in client.cookies

    _sign_in(client, APPROVER_TOKEN)
    assert client.cookies.get(COOKIE).startswith("approver.")
    assert 'name="application"' in client.get("/mappings").text

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303

    analyst = _sign_in(_client(tmp_path))
    mappings = analyst.get("/mappings").text
    assert 'name="application"' not in mappings and "Sign in with the approver access token" in mappings

    disabled = client_for(tmp_path)
    denied = disabled.post("/login", data={"access_token": APPROVER_TOKEN}, follow_redirects=False)
    assert denied.status_code == 401


def test_security_headers_on_decision_pages(tmp_path):
    client = _sign_in(_client(tmp_path))
    run_id = _start(client, "DEMO.PKG_STATUS")
    for path in ("/", "/mappings", f"/analyses/{run_id}/report", f"/analyses/{run_id}/review", f"/analyses/{run_id}/export?format=html"):
        headers = client.get(path).headers
        assert headers["x-frame-options"] == "DENY", path
        assert headers["cache-control"] == "no-store", path
        assert "script-src 'self'" in headers["content-security-policy"], path
        assert "unsafe-inline" not in headers["content-security-policy"], path


def test_missing_runs_and_unknown_formats(tmp_path):
    client = _sign_in(_client(tmp_path))
    run_id = _start(client, "DEMO.PKG_STATUS")
    assert client.get("/analyses/nope").status_code == 404
    assert client.get("/analyses/nope/status").status_code == 404
    assert client.get("/analyses/nope/report").status_code == 404
    assert client.get("/analyses/nope/review").status_code == 404
    assert client.get("/analyses/nope/export").status_code == 404
    assert client.get(f"/analyses/{run_id}/findings/F-NOPE").status_code == 404
    assert client.get(f"/analyses/{run_id}/export?format=docx").status_code == 400
    assert _post(client, "/analyses/nope/validations", finding_id="F-1").status_code == 404
    assert _post(client, "/registry/manifest/M-1/withdraw", run_id="nope", reason="x").status_code == 404


def test_action_messages_are_fixed_texts(tmp_path):
    client = _sign_in(_client(tmp_path))
    run_id = _start(client, "DEMO.PKG_STATUS")
    saved = client.get(f"/analyses/{run_id}/report?msg=validation_saved").text
    assert "Validation result saved." in saved
    error = client.get(f"/analyses/{run_id}/report?msg=too_long").text
    assert "One of the fields is too long." in error
    injected = client.get(f"/analyses/{run_id}/report?msg=<script>alert(1)</script>").text
    assert "<script>alert(1)</script>" not in injected
    assert "Business mapping saved." in client.get("/mappings?msg=mapping_saved").text


def test_status_page_and_dashboard_after_registry_writes(tmp_path):
    client = _sign_in(_client(tmp_path), APPROVER_TOKEN)
    run_id = _start(client, "DEMO.PKG_STATUS")
    status = client.get(f"/analyses/{run_id}/status").json()
    assert status["status"] == "completed" and status["run_id"] == run_id
    assert client.get(f"/analyses/{run_id}").status_code == 200
    assert _msg(_post(client, "/mappings", object="DEMO.PKG_STATUS", application="HR", business_process="Status", criticality="low")) == "mapping_saved"
    dashboard = client.get("/").text
    assert run_id in dashboard and "_registry" not in dashboard


def test_records_survive_an_application_restart(tmp_path):
    client = _sign_in(_client(tmp_path), APPROVER_TOKEN)
    run_id = _start(client, "DEMO.PKG_BILLING")
    report = client.get(f"/analyses/{run_id}/export?format=json").json()
    finding = report["findings"][0]["id"]
    assert _msg(_post(client, f"/analyses/{run_id}/validations", finding_id=finding, status="failed", environment="test",
                      test_case="Invoice replay", evidence="LOG-7", actor_name="Dana")) == "validation_saved"
    assert _msg(_post(client, f"/analyses/{run_id}/reviews", finding_id=finding, decision="confirmed_issue", reason="Reproduced")) == "review_saved"
    assert _msg(_post(client, f"/analyses/{run_id}/approval", decision="on_hold", notes="Blockers remain.")) == "approval_saved"
    assert _msg(_post(client, f"/analyses/{run_id}/manifest", object_key="DEMO.PKG_BILLING:PACKAGE BODY", change_ref="CHG-9")) == "manifest_saved"
    assert _msg(_post(client, "/mappings", object="DEMO.PKG_BILLING", application="Billing", business_process="Invoicing", criticality="critical")) == "mapping_saved"

    restarted = _sign_in(TestClient(create_app(settings(tmp_path, approver_access_token=APPROVER_TOKEN))))
    review = _review(restarted, run_id)
    assert review["review"]["validations"][0]["created_by"]["name"] == "Dana"
    assert review["review"]["reviews"][0]["decision"] == "confirmed_issue"
    assert review["approval"]["latest"]["decision"] == "on_hold"
    page = restarted.get(f"/analyses/{run_id}/report?tab=audit").text
    assert "CHG-9" in restarted.get(f"/analyses/{run_id}/report?tab=objects").text
    assert "Dana" in page
    assert "Invoicing" in restarted.get("/mappings").text


def test_policy_file_and_recorded_evidence_drive_a_positive_recommendation(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "rules": {
                    "BLK-INTERFACE-BREAK": {"resolution": "validation"},
                    "BLK-MISSING-OBJECT": {"resolution": "validation"},
                    "BLK-COMPILE-FAILURE": {"resolution": "validation"},
                    "BLK-PARTIAL-COMMIT": {"resolution": "validation"},
                }
            }
        )
    )
    runs = tmp_path / "runs"
    client = _sign_in(_client(runs, policy_file=policy), APPROVER_TOKEN)
    run_id = _start(client, "DEMO.PKG_BILLING")
    readiness = _review(client, run_id)["readiness"]
    assert readiness["decision"] == "on_hold"
    items = readiness["blockers"] + readiness["validations"]
    assert {entry["item"]["resolution"] for entry in items} == {"validation"}

    for entry in items:
        response = _post(client, f"/analyses/{run_id}/validations", finding_id=entry["item"]["finding_id"], status="passed",
                         environment="test", test_case=f"Check {entry['item']['rule_id']}", evidence="RUN-42")
        assert _msg(response) == "validation_saved"
    readiness = _review(client, run_id)["readiness"]
    assert readiness["open_blockers"] == 0 and readiness["open_validations"] == 0
    assert readiness["decision"] == "recommended"
    assert _msg(_post(client, f"/analyses/{run_id}/approval", decision="approved", notes="All checks passed in TEST.")) == "approval_saved"

    held = _sign_in(_client(tmp_path / "default"), APPROVER_TOKEN)
    default_run = _start(held, "DEMO.PKG_BILLING")
    assert _review(held, default_run)["readiness"]["decision"] == "on_hold"


@pytest.mark.parametrize("fmt, kind", [("json", "application/json"), ("html", "text/html")])
def test_exports_include_readiness_without_the_secrets(tmp_path, fmt, kind):
    client = _sign_in(_client(tmp_path))
    run_id = _start(client, "DEMO.PKG_BILLING")
    response = client.get(f"/analyses/{run_id}/export?format={fmt}")
    assert response.status_code == 200 and response.headers["content-type"].startswith(kind)
    for secret in ("should-not-leak-test", "should-not-leak-prod", "should-not-leak-key", "test-token", APPROVER_TOKEN):
        assert secret not in response.text
