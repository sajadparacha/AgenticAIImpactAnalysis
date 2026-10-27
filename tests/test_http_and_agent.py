import json
import re

from pypdf import PdfReader
import io

from oracle_impact.infrastructure.evidence.validator import validate_findings
from oracle_impact.infrastructure.llm.mock import ScriptedProvider
from oracle_impact.infrastructure.llm.openai_compatible import EgressDisabled, OpenAICompatibleProvider
from oracle_impact.infrastructure.llm.provider import ModelResponse, ToolCall
from oracle_impact.infrastructure.llm.investigator import Investigator
from oracle_impact.models.domain import Snapshot
from oracle_impact.models.enums import EvidenceStatus, Severity
from oracle_impact.models.report import Finding
from tests.support import client_for, login


def test_low_and_high_fixture_reports_differ(tmp_path):
    client = client_for(tmp_path)
    login(client)
    low = _analyze(client, "DEMO.PKG_STATUS")
    high = _analyze(client, "DEMO.PKG_BILLING")
    assert "Eligible for review after testing" in low
    assert "Not recommended for deployment" in high
    assert "GET_WELL_STATUS" not in low
    assert "GENERATE_BILL" not in high
    assert "deterministic-only" in low.lower() or "AI review did not run" in low


def test_json_and_pdf_exports(tmp_path):
    client = client_for(tmp_path)
    login(client)
    page = _analyze(client, "DEMO.PKG_STATUS")
    match = re.search(r"/analyses/([0-9a-f-]+)/", page)
    assert match
    run_id = match.group(1)
    progress = client.get(f"/analyses/{run_id}/status").json()
    messages = " ".join(event["message"] for event in progress["events"])
    assert "Comparing specifications and bodies." in messages
    assert "Report ready." in messages
    assert any("PACKAGE BODY" in line for line in progress["steps"]["comparing"]["lines"])
    assert progress["steps"]["snapshotting"]["lines"]
    exported = client.get(f"/analyses/{run_id}/export?format=json")
    payload = exported.json()
    assert payload["recommendation_label"] == "Eligible for review after testing"
    finding_id = payload["findings"][0]["id"]
    pdf = client.get(f"/analyses/{run_id}/export?format=pdf")
    assert pdf.status_code == 200
    reader = PdfReader(io.BytesIO(pdf.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Eligible for review after testing" in text
    assert finding_id in text
    assert "Known limitations" in text


def test_invalid_package_and_missing_csrf(tmp_path):
    client = client_for(tmp_path)
    login(client)
    rejected = client.post("/analyses", data={"packages": "demo.pkg", "csrf_token_value": _csrf(client)})
    assert rejected.status_code == 400
    missing = client.post("/analyses", data={"packages": "DEMO.PKG_STATUS", "csrf_token_value": "nope"}, follow_redirects=False)
    assert missing.status_code == 303
    assert "error=" in missing.headers["location"]


class _ModelChoiceProvider:
    def __init__(self, served: list[str], used: list[str], model: str = "") -> None:
        self.served = served
        self.used = used
        self.model = model

    def list_models(self) -> list[str]:
        return list(self.served)

    def with_model(self, model: str) -> "_ModelChoiceProvider":
        return _ModelChoiceProvider(self.served, self.used, model)

    def complete(self, messages, tools) -> ModelResponse:
        self.used.append(self.model)
        return ModelResponse(content='{"reviews": []}')


def test_dashboard_lists_models_and_runs_the_chosen_one(tmp_path):
    from oracle_impact.main import create_app
    from fastapi.testclient import TestClient
    from tests.support import settings

    used: list[str] = []
    investigator = Investigator(_ModelChoiceProvider(["llama3.2:latest", "qwen3:8b"], used), 15, 40, 30, 8000, 2)
    configured = settings(tmp_path, llm_egress_enabled=True, llm_model="llama3.2:latest")
    client = TestClient(create_app(configured, investigator=investigator))
    login(client)

    dashboard = client.get("/").text
    assert '<select id="model" name="model">' in dashboard
    assert "llama3.2:latest (default)" in dashboard
    assert '<option value="qwen3:8b"' in dashboard

    started = client.post(
        "/analyses",
        data={"packages": "DEMO.PKG_STATUS", "model": "qwen3:8b", "csrf_token_value": _csrf(client)},
        follow_redirects=True,
    )
    run_id = re.search(r"/analyses/([0-9a-f-]+)", str(started.url)).group(1)
    report = client.get(f"/analyses/{run_id}/export?format=json").json()
    assert report["model_name"] == "qwen3:8b"
    assert used and set(used) == {"qwen3:8b"}

    rejected = client.post(
        "/analyses",
        data={"packages": "DEMO.PKG_STATUS", "model": "not-served", "csrf_token_value": _csrf(client)},
    )
    assert rejected.status_code == 400
    assert "not served" in rejected.text


def test_dashboard_hides_model_choice_when_egress_is_off(tmp_path):
    client = client_for(tmp_path)
    login(client)
    assert 'name="model"' not in client.get("/").text
    rejected = client.post(
        "/analyses",
        data={"packages": "DEMO.PKG_STATUS", "model": "qwen3:8b", "csrf_token_value": _csrf(client)},
    )
    assert rejected.status_code == 400


def test_finished_runs_open_the_report_and_unfinished_runs_show_progress(tmp_path):
    client = client_for(tmp_path)
    login(client)
    started = client.post(
        "/analyses",
        data={"packages": "DEMO.PKG_STATUS", "csrf_token_value": _csrf(client)},
        follow_redirects=True,
    )
    run_id = re.search(r"/analyses/([0-9a-f-]+)", str(started.url)).group(1)
    assert str(started.url).endswith(f"/analyses/{run_id}/report")
    finished = client.get(f"/analyses/{run_id}", follow_redirects=False)
    assert finished.status_code == 303
    assert finished.headers["location"] == f"/analyses/{run_id}/report"
    assert f'href="/analyses/{run_id}/report?tab=audit#phases"' in started.text
    assert f"Run {run_id[:8]}" in started.text
    assert 'id="phases"' in started.text

    store = client.app.state.store
    store.create("pending-run", ["DEMO.PKG_STATUS"], mode="agentic")
    store.write_status("pending-run", "comparing", detail="Comparing specifications and bodies.")
    pending = client.get("/analyses/pending-run", follow_redirects=False)
    assert pending.status_code == 200
    assert 'aria-current="page">Progress' in pending.text
    assert 'href="/agentic">Agent-guided analysis' in pending.text
    assert 'data-report-url="/analyses/pending-run/report"' in pending.text
    assert "The report opens here when the analysis finishes." in pending.text

    store.write_status("pending-run", "failed", error="Production is unreachable.")
    failed = client.get("/analyses/pending-run", follow_redirects=False)
    assert failed.status_code == 200
    assert "Production is unreachable." in failed.text
    assert "The report opens here" not in failed.text


def test_path_traversal_run_id(tmp_path):
    client = client_for(tmp_path)
    login(client)
    response = client.get("/analyses/..%2Fsecret", follow_redirects=False)
    assert response.status_code == 404


def test_egress_provider_does_not_send_when_disabled():
    provider = OpenAICompatibleProvider("http://127.0.0.1:9", "secret", "model", 1, egress_enabled=False)
    try:
        provider.complete([], [])
    except EgressDisabled:
        return
    raise AssertionError("egress call was not refused")


def test_investigator_downgrades_and_rejects_citations(tmp_path):
    from oracle_impact.fixtures.demo_catalog import build_demo_catalogs
    from oracle_impact.infrastructure.oracle.gateway import FixtureGateway
    from oracle_impact.services.analysis_service import AnalysisService
    from oracle_impact.infrastructure.storage.run_store import RunStore
    from tests.support import settings

    test_catalog, prod_catalog = build_demo_catalogs()
    good = json.dumps(
        {
            "findings": [
                {
                    "object_owner": "DEMO",
                    "object_name": "PKG_STATUS",
                    "object_type": "PACKAGE BODY",
                    "changed_lines_test": [1],
                    "change_category": "business_logic",
                    "severity": "low",
                    "confidence": "medium",
                    "status": "confirmed",
                    "evidence": "line 1",
                    "explanation": "The history branch is optional.",
                    "recommended_action": "Test both values.",
                }
            ]
        }
    )
    bad = json.dumps(
        {
            "findings": [
                {
                    "object_owner": "DEMO",
                    "object_name": "PKG_STATUS",
                    "object_type": "PACKAGE BODY",
                    "changed_lines_test": [9999],
                    "change_category": "business_logic",
                    "severity": "critical",
                    "confidence": "high",
                    "status": "confirmed",
                    "evidence": "missing line",
                    "explanation": "This claim is unsupported.",
                    "recommended_action": "Ignore.",
                }
            ]
        }
    )
    investigator = Investigator(
        provider=ScriptedProvider(
            [
                ModelResponse(
                    tool_calls=[ToolCall(name="get_package_source", arguments={"env": "test", "owner": "DEMO", "package": "PKG_STATUS"}, call_id="1")],
                    prompt_tokens=10,
                    completion_tokens=5,
                ),
                ModelResponse(content=good, prompt_tokens=10, completion_tokens=20),
            ]
        ),
        max_calls_per_package=15,
        max_calls_per_run=40,
        timeout_seconds=30,
        token_budget=8000,
        max_depth=2,
    )
    configured = settings(tmp_path, llm_egress_enabled=True, llm_model="mock")
    service = AnalysisService(
        configured,
        RunStore(tmp_path / "good"),
        FixtureGateway("test", "fixture-test", "TEST", test_catalog),
        FixtureGateway("production", "fixture-production", "PRODUCTION", prod_catalog),
        investigator=investigator,
    )
    run_id = service.create_run([_ref("DEMO.PKG_STATUS")])
    service.execute(run_id)
    report = service.store.read_json(run_id, "report.json")
    ai = [item for item in report["findings"] if item["origin"] == "investigator"]
    assert ai
    assert ai[0]["status"] == "potential"
    assert "cannot confirm" in ai[0]["explanation"]
    log = service.store.read_json(run_id, "tool_log.json")
    assert log and log[0]["tool"] == "get_package_source"
    assert "PACKAGE BODY" not in json.dumps(log)

    rejected = validate_findings(
        [
            Finding(
                id="AI-X",
                changed_object="DEMO.PKG_STATUS",
                object_owner="DEMO",
                object_name="PKG_STATUS",
                object_type="PACKAGE BODY",
                source_database="test",
                changed_lines_test=[9999],
                change_category="business_logic",
                evidence="missing",
                severity=Severity.CRITICAL,
                confidence="high",
                status=EvidenceStatus.CONFIRMED,
                explanation="unsupported",
                recommended_action="no",
            )
        ],
        Snapshot.model_validate(service.store.read_json(run_id, "snapshot.json")),
    )
    assert rejected[0].status == EvidenceStatus.POTENTIAL
    assert rejected[0].severity == Severity.LOW
    assert rejected[0].attributes["citation_unconfirmed"] is True
    assert "Hypothesis not accepted" not in rejected[0].explanation


def test_investigator_reads_evidence_before_answering(tmp_path):
    from oracle_impact.models.domain import CapturedObject, Snapshot
    from oracle_impact.infrastructure.llm.tools import ToolContext

    snapshot = Snapshot(
        packages=["DEMO.PKG_STATUS"],
        captured_at="2026-10-21T00:00:00+00:00",
        objects=[
            CapturedObject(
                environment="test",
                database_id="fixture-test",
                owner="DEMO",
                name="PKG_STATUS",
                object_type="PACKAGE BODY",
                found=True,
                source="BEGIN\n  NULL;\nEND;\n",
                retrieved_at="2026-10-21T00:00:00+00:00",
            )
        ],
    )
    review = json.dumps(
        {
            "reviews": [
                {
                    "finding_id": "F-1",
                    "object_owner": "DEMO",
                    "object_name": "PKG_STATUS",
                    "object_type": "PACKAGE BODY",
                    "changed_lines_test": [1],
                    "business_rule": "The optional flag only changes the returned text.",
                    "test_cases": ["Call get_employee_status with the flag omitted and with it true."],
                    "unknowns": ["Callers outside the database were not visible."],
                    "evidence": "Line 1 of the package body.",
                }
            ]
        }
    )
    provider = ScriptedProvider(
        [
            ModelResponse(content="The change is safe.", prompt_tokens=5, completion_tokens=5),
            ModelResponse(
                tool_calls=[ToolCall("get_package_source", {"env": "test", "owner": "DEMO", "package": "PKG_STATUS"}, "1")],
                prompt_tokens=5,
                completion_tokens=5,
            ),
            ModelResponse(content=review, prompt_tokens=5, completion_tokens=5),
        ]
    )
    investigator = Investigator(provider, 15, 40, 30, 8000, 2)
    findings, log, _ = investigator.investigate(
        snapshot,
        ToolContext(snapshot, [], {}, {}, 2),
        [],
        case_file="- F-1 [critical interface] mandatory parameter. Path: DEMO.PKG_API.",
        requires_tools=True,
    )
    assert provider.calls == 3
    assert log and log[0]["tool"] == "get_package_source"
    assert findings
    assert findings[0].proposed_tests
    assert "What changes:" in findings[0].explanation
    assert "What to do:" in findings[0].explanation
    assert "Not checked:" in findings[0].explanation
    assert findings[0].attributes["unknowns"]
    assert findings[0].attributes["blocks_eligibility"] is False


def test_investigator_budget_and_provider_failure(tmp_path):
    from oracle_impact.fixtures.demo_catalog import build_demo_catalogs
    from oracle_impact.infrastructure.llm.investigator import Investigator
    from oracle_impact.infrastructure.llm.tools import ToolContext

    test_catalog, prod_catalog = build_demo_catalogs()
    del prod_catalog
    snapshot_source = test_catalog.sources[("DEMO", "PKG_STATUS", "PACKAGE BODY")]
    from oracle_impact.models.domain import CapturedObject

    snapshot = Snapshot(
        packages=["DEMO.PKG_STATUS"],
        captured_at="2026-10-21T00:00:00+00:00",
        objects=[
            CapturedObject(
                environment="test",
                database_id="fixture-test",
                owner="DEMO",
                name="PKG_STATUS",
                object_type="PACKAGE BODY",
                found=True,
                source=snapshot_source,
                sha256="abc",
                retrieved_at="2026-10-21T00:00:00+00:00",
            )
        ],
    )
    context = ToolContext(snapshot, [], {}, {}, max_depth=2)
    looping = Investigator(
        provider=ScriptedProvider(
            [ModelResponse(tool_calls=[ToolCall("get_package_source", {"env": "test", "owner": "DEMO", "package": "PKG_STATUS"}, "1")])] * 5
        ),
        max_calls_per_package=1,
        max_calls_per_run=2,
        timeout_seconds=30,
        token_budget=8000,
        max_depth=2,
    )
    findings, log, limitations = looping.investigate(snapshot, context, ["note"])
    assert findings == []
    assert limitations
    assert any("limit" in item.lower() for item in limitations)

    class Boom:
        def complete(self, messages, tools):
            raise RuntimeError("down")

    failed = Investigator(Boom(), 15, 40, 30, 8000, 2)
    findings, _, limitations = failed.investigate(snapshot, context, ["note"])
    assert findings == []
    assert any("unavailable" in item.lower() for item in limitations)


def test_ai_notes_are_split_into_short_lines():
    from oracle_impact.infrastructure.llm.plain_text import review_lines

    def note(text: str, **extra) -> Finding:
        return Finding(
            id="AI-1",
            changed_object="IMPACT_LAB.PAY_PKG",
            object_owner="IMPACT_LAB",
            object_name="PAY_PKG",
            object_type="PACKAGE BODY",
            source_database="test",
            change_category="business_logic",
            dependency_path=["IMPACT_LAB.PAYMENT_API -> IMPACT_LAB.PAY_PKG.RELEASE_PAYMENT"],
            evidence="snapshot",
            severity=Severity.LOW,
            confidence="low",
            status=EvidenceStatus.POTENTIAL,
            explanation=text,
            recommended_action="Read the deterministic finding.",
            origin="investigator",
            **extra,
        )

    signature = review_lines(
        note(
            "What changes: RELEASE_PAYMENT gained parameter(s) P_APPROVE without a compatible defaulted suffix. "
            "Unupdated callers: IMPACT_LAB.PAYMENT_API. "
            "What to do: Run IMPACT_LAB.PAYMENT_API -> IMPACT_LAB.PAY_PKG.RELEASE_PAYMENT the way Production does today "
            "and confirm it no longer matches. "
            "Not checked: Programs outside the database were not read."
        )
    )
    assert signature["change"] == "RELEASE_PAYMENT now requires P_APPROVE. IMPACT_LAB.PAYMENT_API still uses the old arguments."
    assert "->" not in signature["todo"]
    assert signature["todo"] == "Call IMPACT_LAB.PAYMENT_API with the arguments Production uses today."
    assert signature["unchecked"] == "Programs outside the database."

    update = review_lines(
        note(
            "What changes: UPDATE against IMPACT_LAB.PAYMENT_ACCOUNT changed. Visible triggers: none visible. "
            "Static inspection cannot prove which rows the statement changes at runtime. "
            "What to do: Run the procedure and check IMPACT_LAB.PAYMENT_ACCOUNT.",
            affected_object="IMPACT_LAB.PAYMENT_ACCOUNT",
        )
    )
    assert update["change"] == (
        "RELEASE_PAYMENT changes rows in IMPACT_LAB.PAYMENT_ACCOUNT. The report cannot tell which rows."
    )
    assert "Static inspection" not in update["change"]
    assert update["todo"] == "Run RELEASE_PAYMENT and look at IMPACT_LAB.PAYMENT_ACCOUNT."

    kept = review_lines(
        note(
            "What changes: The optional flag only changes the returned text.\n"
            "What to do: Call get_employee_status with the flag omitted.\n"
            "Not checked: The screen that submits this call was not visible."
        )
    )
    assert kept["change"] == "The optional flag only changes the returned text."
    assert "get_employee_status" in kept["todo"]
    assert kept["unchecked"] == "The screen that submits this call was not visible."


def test_deterministic_cards_drop_the_source_dump():
    from oracle_impact.infrastructure.llm.plain_text import review_lines

    finding = Finding(
        id="F-16CE600E",
        changed_object="IMPACT_LAB.PAY_PKG",
        object_owner="IMPACT_LAB",
        object_name="PAY_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="business_logic",
        dependency_path=["IMPACT_LAB.PAY_PKG.RELEASE_PAYMENT"],
        evidence="source",
        severity=Severity.HIGH,
        confidence="medium",
        status=EvidenceStatus.POTENTIAL,
        explanation=(
            "RELEASE_PAYMENT executable text changed. Example: PROCEDURE release_payment( | "
            "p_account_id IN NUMBER, | p_approve IN VARCHAR2 | ) IS | v_amount NUMBER := 100; | "
            "v_tax NUMBER := 0; This describes the source change, not a proven runtime outcome."
        ),
        recommended_action="Read the changed branch with a developer who knows the business rule and test it.",
        proposed_tests=["Add a functional test that exercises RELEASE_PAYMENT with the new branch."],
        origin="deterministic",
    )
    lines = review_lines(finding)
    assert lines["change"] == "The text of RELEASE_PAYMENT changed. The exact lines are in the source comparison."
    assert "PROCEDURE" not in lines["change"]
    assert lines["todo"] == "Add a functional test that exercises RELEASE_PAYMENT with the new branch."
    assert lines["unchecked"] == ""


def test_ai_review_hides_a_repeated_finding():
    from types import SimpleNamespace

    from oracle_impact.infrastructure.llm.plain_text import investigator_notes, review_lines

    deterministic = Finding(
        id="F-3B37A1EA",
        changed_object="IMPACT_LAB.PAY_PKG",
        object_owner="IMPACT_LAB",
        object_name="PAY_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="transaction",
        evidence="RELEASE_PAYMENT transaction sequence changed.",
        severity=Severity.HIGH,
        confidence="medium",
        status=EvidenceStatus.POTENTIAL,
        explanation=(
            "RELEASE_PAYMENT transaction sequence changed from ('UPDATE', 'COMMIT') to "
            "('UPDATE', 'COMMIT', 'INSERT'). A moved commit can leave earlier statements durable "
            "if a later statement fails."
        ),
        recommended_action="Review the transaction boundary.",
        proposed_tests=["Force an error after each DML statement and inspect which changes remain."],
        origin="deterministic",
    )
    echoed = Finding(
        id="AI-4",
        changed_object="IMPACT_LAB.PAY_PKG",
        object_owner="IMPACT_LAB",
        object_name="PAY_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="transaction",
        evidence="same",
        severity=Severity.LOW,
        confidence="low",
        status=EvidenceStatus.POTENTIAL,
        explanation=(
            "What changes: RELEASE_PAYMENT saves its update and then inserts. "
            "If the insert fails, the update is already saved.\n"
            "What to do: Run RELEASE_PAYMENT and make the statement after the save fail. "
            "See which changes remain.\n"
            "Not checked: Programs outside the database."
        ),
        recommended_action="Run the test below.",
        proposed_tests=["Run RELEASE_PAYMENT and make the statement after the save fail. See which changes remain."],
        attributes={"review_of": "F-3B37A1EA"},
        origin="investigator",
    )
    report = SimpleNamespace(findings=[deterministic, echoed])
    lines = review_lines(echoed, report)
    assert lines["change"] == ""
    assert lines["todo"] == ""
    assert lines["show_beside"] is False
    assert lines["unchecked"] == "Programs outside the database."
    notes = investigator_notes(report)
    assert notes["unique"] == []
    assert notes["repeated"] == [{"id": "AI-4", "reviews": "F-3B37A1EA"}]


def test_model_thoughts_hide_source_and_json(tmp_path):
    from oracle_impact.infrastructure.llm.investigator import _visible_thought
    from oracle_impact.infrastructure.storage.run_store import RunStore

    assert _visible_thought("I'll read the caller, then the changed procedure.") == (
        "I'll read the caller, then the changed procedure."
    )
    assert _visible_thought('{"reviews":[]}') == ""
    assert _visible_thought("CREATE OR REPLACE PACKAGE BODY pay_pkg AS") == ""

    store = RunStore(tmp_path)
    store.create("run-1", ["IMPACT_LAB.CLAIM_PKG"])
    store.write_status(
        "run-1",
        "investigating",
        detail="Reading the package source for IMPACT_LAB.CLAIM_PKG in Test.",
        activity={"kind": "action", "text": "Reading the package source for IMPACT_LAB.CLAIM_PKG in Test."},
    )
    status = store.read_json("run-1", "status.json")
    assert status["activity"][-1]["kind"] == "action"
    assert "PACKAGE BODY" not in status["activity"][-1]["text"]


def test_ai_finding_page_renders():
    from oracle_impact.controllers.web import TEMPLATES

    finding = Finding(
        id="AI-1",
        changed_object="IMPACT_LAB.PAY_PKG",
        object_owner="IMPACT_LAB",
        object_name="PAY_PKG",
        object_type="PACKAGE",
        source_database="test",
        change_category="business_logic",
        evidence="RELEASE_PAYMENT gained parameter(s) P_APPROVE.",
        severity=Severity.LOW,
        confidence="low",
        status=EvidenceStatus.POTENTIAL,
        explanation=(
            "What changes: RELEASE_PAYMENT gained parameter(s) P_APPROVE without a compatible defaulted suffix. "
            "Unupdated callers: IMPACT_LAB.PAYMENT_API. "
            "What to do: Call IMPACT_LAB.PAYMENT_API with the arguments Production uses today. "
            "Not checked: Programs outside the database were not read."
        ),
        recommended_action="Read the deterministic finding.",
        origin="investigator",
    )
    page = TEMPLATES.get_template("finding.html").render({"report": type("R", (), {"run_id": "run-1"})(), "finding": finding})
    assert "What changes" in page
    assert "now requires P_APPROVE" in page
    assert "Internal Server Error" not in page


def test_agentic_page_is_off_and_rejects_runs_while_egress_is_off(tmp_path):
    client = client_for(tmp_path)
    login(client)
    page = client.get("/agentic")
    assert page.status_code == 200
    assert "investigator is off" in page.text
    assert 'action="/agentic/analyses"' not in page.text
    home = client.get("/").text
    mappings = client.get("/mappings").text
    assert 'href="/agentic"' in home
    assert 'href="/" aria-current="page"' in home
    assert 'href="/agentic" aria-current="page"' not in home
    assert 'href="/mappings" aria-current="page"' not in home
    assert 'href="/agentic" aria-current="page"' in page.text
    assert 'href="/" aria-current="page"' not in page.text
    assert 'href="/mappings" aria-current="page"' in mappings

    rejected = client.post("/agentic/analyses", data={"packages": "DEMO.PKG_STATUS", "csrf_token_value": _csrf(client)})
    assert rejected.status_code == 400
    assert "LLM egress is disabled" in rejected.text
    assert client.app.state.store.list_runs() == []

    expired = client.post("/agentic/analyses", data={"packages": "DEMO.PKG_STATUS", "csrf_token_value": "nope"}, follow_redirects=False)
    assert expired.status_code == 303
    assert expired.headers["location"].startswith("/agentic?error=")


def test_agentic_page_runs_the_investigator_and_shows_its_trace(tmp_path):
    from fastapi.testclient import TestClient

    from oracle_impact.main import create_app
    from tests.support import ChecklistProvider, agentic_investigator, settings

    provider = ChecklistProvider(final="I read both packages and their callers.")
    configured = settings(tmp_path, llm_egress_enabled=True, llm_model="mock")
    client = TestClient(create_app(configured, investigator=agentic_investigator(provider)))
    login(client)

    page = client.get("/agentic")
    assert 'action="/agentic/analyses"' in page.text
    started = client.post(
        "/agentic/analyses",
        data={"packages": "DEMO.PKG_STATUS", "csrf_token_value": _csrf(client)},
        follow_redirects=True,
    )
    assert started.status_code == 200
    run_id = re.search(r"/analyses/([0-9a-f-]+)", str(started.url)).group(1)
    assert str(started.url).endswith(f"/analyses/{run_id}/report")
    phases = started.text[started.text.index('id="phases"'):]
    assert phases.index("Agentic review") < phases.index("Source snapshot")
    assert {"get_package_source", "get_dependencies", "get_compile_errors"} <= set(provider.tools_seen)

    status = client.get(f"/analyses/{run_id}/status").json()
    assert status["status"] == "completed"
    assert any(item["kind"] == "action" for item in status["activity"])
    assert status["steps"]["investigating"]["started_at"] <= status["steps"]["snapshotting"]["started_at"]

    exported = client.get(f"/analyses/{run_id}/export?format=json").json()
    assert exported["run_mode"] == "agentic"
    assert exported["recommendation_label"] == "Eligible for review after testing"
    assert exported["agent_summary"] == "I read both packages and their callers."

    report = client.get(f"/analyses/{run_id}/report").text
    assert "Agentic read order, deterministic recommendation" in report
    assert "I read both packages and their callers." in report
    assert "Object status" in report
    assert "Agent-guided" in client.get("/agentic").text
    assert 'href="/agentic">Agent-guided analysis</a></li>' in report
    assert 'href="/agentic" aria-current="page"' in report


def test_live_tools_refuse_reads_outside_the_allowlist():
    from oracle_impact.fixtures.demo_catalog import build_demo_catalogs
    from oracle_impact.infrastructure.llm.live_tools import LiveToolContext, dispatch_live_tool, outstanding_reads
    from oracle_impact.infrastructure.oracle.gateway import FixtureGateway

    test_catalog, prod_catalog = build_demo_catalogs()
    context = LiveToolContext(
        {
            "test": FixtureGateway("test", "fixture-test", "TEST", test_catalog),
            "production": FixtureGateway("production", "fixture-production", "PRODUCTION", prod_catalog),
        },
        [_ref("DEMO.PKG_STATUS")],
        max_depth=2,
    )
    caller = {"env": "test", "owner": "DEMO", "name": "PKG_DASHBOARD", "object_type": "PACKAGE BODY"}
    assert "allowlist" in dispatch_live_tool("get_object_source", caller, context)["error"]
    assert "allowlist" in dispatch_live_tool("get_grants", {"env": "test", "owner": "SYS", "name": "DBMS_SQL"}, context)["error"]
    assert dispatch_live_tool("get_grants", {"env": "test", "owner": "demo", "name": "pkg_status"}, context)["error"] == "invalid tool arguments"
    assert "environment" in dispatch_live_tool("get_grants", {"env": "uat", "owner": "DEMO", "name": "PKG_STATUS"}, context)["error"]
    assert "not allowlisted" in dispatch_live_tool("run_sql", {"env": "test", "sql": "DROP TABLE X"}, context)["error"]
    bad_type = {"env": "test", "owner": "DEMO", "name": "PKG_STATUS", "object_type": "JAVA SOURCE"}
    assert "object type" in dispatch_live_tool("get_object_source", bad_type, context)["error"]
    assert context.reads.sources == {}

    source = dispatch_live_tool("get_package_source", {"env": "all", "owner": "DEMO", "package": "PKG_STATUS"}, context)
    assert set(source["results"]) == {"test", "production"}
    assert source["results"]["test"]["body"]["found"] is True
    assert "FUNCTION" not in json.dumps(source).upper()

    callers = dispatch_live_tool(
        "get_dependencies",
        {"env": "production", "owner": "DEMO", "object_name": "PKG_STATUS", "object_type": "PACKAGE", "direction": "referenced_by"},
        context,
    )
    assert {"owner": "DEMO", "name": "PKG_DASHBOARD", "type": "PACKAGE BODY"} in callers["results"]["production"]["objects"]
    assert dispatch_live_tool("get_object_source", caller, context)["results"]["test"]["found"] is True
    assert any(item.tool == "get_object_status" for item in outstanding_reads(context))


def _run_id(page: str) -> str:
    return re.search(r"/analyses/([0-9a-f-]+)/", page).group(1)


def test_report_offers_developer_and_support_email_drafts(tmp_path):
    from email import message_from_bytes

    client = client_for(
        tmp_path,
        email_developers="dev-team@example.com, not-an-address, evil@example.com\r\nBcc: spy@example.com",
        email_support="support@example.com",
        public_base_url="https://impact.example.com/",
    )
    login(client)
    page = _analyze(client, "DEMO.PKG_BILLING")
    run_id = _run_id(page)
    assert f"/analyses/{run_id}/email?audience=developer" in page
    assert f"/analyses/{run_id}/email?audience=support" in page
    assert "Prepare email" not in client.get(f"/analyses/{run_id}/export?format=html").text

    report = client.get(f"/analyses/{run_id}/export?format=json").json()
    blockers = [item["finding_id"] for item in report["policy"]["blockers"]]
    validations = [item["finding_id"] for item in report["policy"]["validations"]]
    assert blockers

    developer = client.get(f"/analyses/{run_id}/email?audience=developer")
    assert developer.status_code == 200
    assert "Must fix before deployment" in developer.text
    assert all(finding_id in developer.text for finding_id in blockers + validations)
    assert f"{len(blockers)} blocking item" in developer.text
    assert "dev-team@example.com" in developer.text and "spy@example.com" not in developer.text
    assert f"https://impact.example.com/analyses/{run_id}/report" in developer.text
    assert "v_total := calculate_amount" not in developer.text

    support = client.get(f"/analyses/{run_id}/email?audience=support")
    assert "What we need from you" in support.text and "What developers are fixing first" in support.text
    assert "support@example.com" in support.text
    assert "Suggested owner" not in support.text
    assert client.get(f"/analyses/{run_id}/email?audience=../../etc").text.count("Must fix before deployment") == 1

    draft = client.get(f"/analyses/{run_id}/email.eml?audience=support")
    assert draft.headers["content-type"].startswith("message/rfc822")
    assert f'impact-{run_id}-support.eml' in draft.headers["content-disposition"]
    message = message_from_bytes(draft.content)
    assert message["X-Unsent"] == "1" and message["To"] == "support@example.com"
    assert message["Subject"].startswith("Impact analysis for DEMO.PKG_BILLING")
    assert "What we need from you" in message.get_payload(decode=True).decode()

    assert client.get("/analyses/not-a-run/email").status_code == 404
    assert client.get("/analyses/../etc/email.eml").status_code == 404


def test_email_for_a_clean_report_follows_the_policy(tmp_path):
    client = client_for(tmp_path, email_developers="", email_support="", public_base_url="")
    login(client)
    run_id = _run_id(_analyze(client, "DEMO.PKG_STATUS"))
    report = client.get(f"/analyses/{run_id}/export?format=json").json()
    assert report["policy"]["blockers"] == [] and report["policy"]["validations"] == []
    developer = client.get(f"/analyses/{run_id}/email?audience=developer").text
    assert "ready for review" in developer and "good news" in developer
    assert "Must fix before deployment" not in developer and "Please test or justify" not in developer
    assert "No recipients are configured" in developer
    assert "OIA_PUBLIC_BASE_URL" in developer


def _analyze(client, package: str) -> str:
    page = client.get("/")
    token = re.search(r'name="csrf_token_value" value="([^"]+)"', page.text).group(1)
    started = client.post("/analyses", data={"packages": package, "csrf_token_value": token}, follow_redirects=True)
    assert started.status_code == 200
    match = re.search(r"/analyses/([0-9a-f-]+)", str(started.url))
    assert match, started.text
    report = client.get(f"/analyses/{match.group(1)}/report")
    assert report.status_code == 200
    return report.text


def _csrf(client) -> str:
    page = client.get("/")
    return re.search(r'name="csrf_token_value" value="([^"]+)"', page.text).group(1)


def _ref(qualified: str):
    from oracle_impact.models.domain import PackageRef

    owner, name = qualified.split(".", 1)
    return PackageRef(owner=owner, name=name)
