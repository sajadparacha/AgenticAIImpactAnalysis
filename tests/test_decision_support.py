"""Review, validation, approval, baseline, and manifest workflows over HTTP."""

import io
import re
import secrets

from fastapi.testclient import TestClient
from pypdf import PdfReader

from oracle_impact.fixtures.demo_catalog import build_demo_catalogs
from oracle_impact.infrastructure.oracle.gateway import FixtureGateway
from oracle_impact.main import create_app
from oracle_impact.security import _sign, csrf_token, session_role
from tests.support import client_for, settings

APPROVER_TOKEN = "approver-token"
COOKIE = "oia_session"


def _client(tmp_path, **overrides) -> TestClient:
    return client_for(tmp_path, approver_access_token=APPROVER_TOKEN, **overrides)


def _sign_in(client: TestClient, token: str) -> None:
    response = client.post("/login", data={"access_token": token}, follow_redirects=False)
    assert response.status_code == 303


def _csrf(client: TestClient) -> str:
    return csrf_token("test-secret", client.cookies.get(COOKIE))


def _start(client: TestClient, package: str) -> str:
    started = client.post("/analyses", data={"packages": package, "csrf_token_value": _csrf(client)}, follow_redirects=False)
    assert started.status_code == 303
    return started.headers["location"].rsplit("/", 1)[1]


def _post(client: TestClient, path: str, **fields) -> str:
    fields.setdefault("csrf_token_value", _csrf(client))
    response = client.post(path, data=fields, follow_redirects=False)
    assert response.status_code == 303, response.text
    return re.search(r"msg=([a-z_]+)", response.headers["location"]).group(1)


def _review(client: TestClient, run_id: str) -> dict:
    response = client.get(f"/analyses/{run_id}/review")
    assert response.status_code == 200
    return response.json()


def test_only_an_approver_can_record_approval_and_only_when_recommended(tmp_path):
    analyst = _client(tmp_path)
    _sign_in(analyst, "test-token")
    status_run = _start(analyst, "DEMO.PKG_STATUS")
    billing_run = _start(analyst, "DEMO.PKG_BILLING")
    assert _review(analyst, status_run)["readiness"]["decision"] == "recommended"
    assert _review(analyst, billing_run)["readiness"]["decision"] == "on_hold"

    assert _post(analyst, f"/analyses/{status_run}/approval", decision="approved", notes="ok") == "approver_required"
    assert _post(analyst, f"/analyses/{status_run}/approval", decision="approved", notes="ok", csrf_token_value="forged") == "form_expired"
    assert _review(analyst, status_run)["approval"]["latest"] is None

    approver = _client(tmp_path)
    _sign_in(approver, APPROVER_TOKEN)
    assert session_role("test-secret", approver.cookies.get(COOKIE)) == "approver"
    assert _post(approver, f"/analyses/{billing_run}/approval", decision="approved", notes="ship it") == "not_recommended"
    assert _post(approver, f"/analyses/{billing_run}/approval", decision="on_hold", notes="Blockers remain.") == "approval_saved"
    assert _post(approver, f"/analyses/{status_run}/approval", decision="approved", notes="", actor_name="Dana") == "reason_required"
    assert _post(approver, f"/analyses/{status_run}/approval", decision="approved", notes="Validated in TEST.", actor_name="Dana") == "approval_saved"

    latest = _review(approver, status_run)["approval"]["latest"]
    assert latest["decision"] == "approved" and latest["readiness"] == "recommended"
    assert latest["actor"]["role"] == "approver" and latest["actor"]["name"] == "Dana"
    page = approver.get(f"/analyses/{status_run}/report?tab=details").text
    assert "Dana" in page and "self-declared" in page.lower()


def test_approval_is_disabled_without_a_separate_approver_token(tmp_path):
    client = client_for(tmp_path)
    rejected = client.post("/login", data={"access_token": APPROVER_TOKEN}, follow_redirects=False)
    assert rejected.status_code == 401
    _sign_in(client, "test-token")
    run_id = _start(client, "DEMO.PKG_STATUS")
    assert _post(client, f"/analyses/{run_id}/approval", decision="on_hold", notes="x") == "approvals_disabled"
    same = client_for(tmp_path, approver_access_token="test-token")
    assert not same.app.state.settings.approvals_enabled


def test_report_does_not_record_validation_or_review(tmp_path):
    client = _client(tmp_path)
    _sign_in(client, "test-token")
    run_id = _start(client, "DEMO.PKG_BILLING")
    review = _review(client, run_id)
    assert review["readiness"]["validations"][0]["state"] == "unresolved"
    page = client.get(f"/analyses/{run_id}/report?tab=findings").text
    assert "Validation and review status" not in page
    assert "Record a validation result" not in page
    assert "Record a reviewer decision" not in page
    for path in (f"/analyses/{run_id}/validations", f"/analyses/{run_id}/reviews"):
        response = client.post(path, data={"csrf_token_value": _csrf(client), "finding_id": "F-1"}, follow_redirects=False)
        assert response.status_code == 404
    after = _review(client, run_id)
    assert after["review"]["validations"] == [] and after["review"]["reviews"] == []
    assert all(entry["state"] == "unresolved" for entry in after["readiness"]["validations"])


def test_trust_record_endpoints_and_page_are_gone(tmp_path):
    client = _client(tmp_path)
    _sign_in(client, APPROVER_TOKEN)
    run_id = _start(client, "DEMO.PKG_STATUS")
    body = "DEMO.PKG_STATUS:PACKAGE BODY"
    csrf = _csrf(client)
    for path in (
        f"/analyses/{run_id}/baseline",
        f"/analyses/{run_id}/manifest",
        "/registry/baselines/B-1/clear",
        "/registry/manifest/M-1/withdraw",
    ):
        response = client.post(path, data={"csrf_token_value": csrf, "object_key": body, "note": "x"}, follow_redirects=False)
        assert response.status_code == 404
    assert client.get("/registry", follow_redirects=False).status_code == 404
    page = client.get("/").text
    assert "Trust records" not in page
    assert "approver-tools" not in client.get(f"/analyses/{run_id}/report?tab=objects").text


def test_report_names_test_and_production_when_both_labels_are_the_same(tmp_path):
    """Both environments can be labelled LOCAL; the page must still distinguish them."""
    client = _client(tmp_path, test_oracle_label="LOCAL", prod_oracle_label="LOCAL")
    _sign_in(client, "test-token")
    run_id = _start(client, "DEMO.PKG_BILLING")
    findings = client.get(f"/analyses/{run_id}/report?tab=findings").text
    assert "is INVALID in Test but VALID in Production" in findings
    for tab in ("overview", "findings", "objects", "details"):
        page = client.get(f"/analyses/{run_id}/report?tab={tab}").text
        assert "LOCAL" not in page, tab
    assert "Test" in client.get(f"/analyses/{run_id}/report?tab=objects").text


def test_newer_run_supersedes_an_older_run(tmp_path):
    test_catalog, prod_catalog = build_demo_catalogs()
    application = create_app(
        settings(tmp_path, approver_access_token=APPROVER_TOKEN),
        test_gateway=FixtureGateway("test", "fixture-test", "TEST", test_catalog),
        production_gateway=FixtureGateway("production", "fixture-production", "PRODUCTION", prod_catalog),
    )
    client = TestClient(application)
    _sign_in(client, APPROVER_TOKEN)
    first = _start(client, "DEMO.PKG_STATUS")
    body = prod_catalog.sources[("DEMO", "PKG_STATUS", "PACKAGE BODY")]
    prod_catalog.add_source("DEMO", "PKG_STATUS", "PACKAGE BODY", body + "\n-- hotfix applied directly\n")
    second = _start(client, "DEMO.PKG_STATUS")

    latest = _review(client, second)
    assert latest["readiness"]["decision"] != "indeterminate"
    assert "open_drift" not in latest["readiness"]
    older = _review(client, first)
    assert older["readiness"]["decision"] == "indeterminate"
    page = client.get(f"/analyses/{second}/report").text
    assert "Unexpected drift" not in page
    audit = client.get(f"/analyses/{second}/report?tab=details").text
    assert first in audit


def test_legacy_session_cookie_is_still_accepted_as_analyst(tmp_path):
    client = _client(tmp_path)
    nonce = secrets.token_hex(16)
    legacy = f"{nonce}.{_sign('test-secret', f'ok:{nonce}')}"
    assert session_role("test-secret", legacy) == "analyst"
    assert session_role("test-secret", f"approver.{nonce}.{_sign('test-secret', f'analyst:{nonce}')}") is None
    client.cookies.set(COOKIE, legacy)
    assert client.get("/", follow_redirects=False).status_code == 200
    run_id = _start(client, "DEMO.PKG_STATUS")
    assert _post(client, f"/analyses/{run_id}/approval", decision="approved", notes="x") == "approver_required"


def test_report_tabs_and_filters_render(tmp_path):
    client = _client(tmp_path)
    _sign_in(client, "test-token")
    run_id = _start(client, "DEMO.PKG_BILLING")
    page = client.get(f"/analyses/{run_id}/report").text
    tabs = re.findall(r'data-tab-link="([a-z]+)"', page)
    assert tabs == ["overview", "findings", "objects", "dependencies", "details"]
    assert "Deployment on hold" in page and "What's holding deployment" in page
    assert "On hold" in page and "Policy result: Deployment on hold" in page
    assert "No decision recorded" in page
    assert "step-by-step fix plan" in page
    for tile in ("Findings", "Changed objects", "Affected dependencies", "Coverage"):
        assert tile in page
    for key in ("ev", "blk", "env", "chg"):
        assert f'data-filter="{key}"' in page
    assert 'data-multi-filter="sev"' in page and 'data-multi-option="sev"' in page
    assert 'data-filter="val"' not in page and 'data-filter="ec"' not in page
    assert "Validation and review status" not in page
    assert "CMP-DEPENDENT" in page or "PKG_LEGACY" in page

    detail = client.get(f"/analyses/{run_id}/report?tab=findings").text
    for heading in ("What changed", "Does it block deployment", "What to test", "Evidence", "AI interpretation, not evidence", "Evidence details"):
        assert heading in detail
    assert "Consequences are predicted from static evidence" in detail

    for fmt in ("html", "json"):
        assert client.get(f"/analyses/{run_id}/export?format={fmt}").status_code == 200
    exported = client.get(f"/analyses/{run_id}/export?format=html").text
    assert "Deployment on hold" in exported and 'name="decision"' not in exported and "csrf_token_value" not in exported
    pdf = client.get(f"/analyses/{run_id}/export?format=pdf")
    assert pdf.status_code == 200
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Deployment on hold" in text and "BLK-COMPILE-FAILURE" in text and "Compilation readiness" in text


def test_reports_compare_test_and_production_only(tmp_path):
    client = _client(tmp_path)
    _sign_in(client, "test-token")
    run_id = _start(client, "DEMO.PKG_STATUS")
    exported = client.get(f"/analyses/{run_id}/export?format=json").json()
    assert "comparisons" not in exported
    assert [item["role"] for item in exported["environments"]] == ["source", "baseline"]
    assert {env["environment"] for state in exported["object_states"] for env in state["environments"]} == {"test", "production"}
    assert _review(client, run_id)["readiness"]["decision"] == "recommended"
    for tab in ("overview", "objects", "dependencies", "details"):
        page = client.get(f"/analyses/{run_id}/report?tab={tab}")
        assert page.status_code == 200
        assert "AUTUMN" not in page.text and "data-cmp" not in page.text
    # Legacy tab keys still resolve to the right panel.
    def _panel_not_hidden(markup: str, key: str) -> bool:
        attrs = re.search(rf'<section[^>]*data-panel="{key}"[^>]*>', markup).group(0)
        return " hidden" not in attrs

    assert _panel_not_hidden(client.get(f"/analyses/{run_id}/report?tab=source").text, "objects")
    assert _panel_not_hidden(client.get(f"/analyses/{run_id}/report?tab=validation").text, "details")
    assert _panel_not_hidden(client.get(f"/analyses/{run_id}/report?tab=audit").text, "details")
    assert "AUTUMN" not in client.get("/").text


def test_blank_optional_paths_fall_back_to_the_runs_directory(tmp_path):
    configured = settings(tmp_path, state_dir="", policy_file=" ")
    assert configured.state_dir is None and configured.policy_file is None
    assert configured.registry_dir == tmp_path / "_registry"


def test_actions_reject_unsafe_run_identifiers(tmp_path):
    client = _client(tmp_path)
    _sign_in(client, APPROVER_TOKEN)
    for path in ("/analyses/..%2F..%2Fetc/validations", "/analyses/not-a-run/approval"):
        response = client.post(path, data={"csrf_token_value": _csrf(client)}, follow_redirects=False)
        assert response.status_code == 404
    response = client.post("/registry/baselines/x/clear", data={"run_id": "../../etc", "csrf_token_value": _csrf(client)}, follow_redirects=False)
    assert response.status_code == 404
    assert client.get("/analyses/..%2Fx/review").status_code == 404
