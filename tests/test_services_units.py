"""Unit tests for services, security helpers, configuration, snapshot capture, and the Oracle gateway."""

import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import oracledb
import pytest

from oracle_impact.fixtures.demo_catalog import build_demo_catalogs
from oracle_impact.infrastructure.oracle.gateway import Catalog, FixtureGateway, OracleUnavailable
from oracle_impact.infrastructure.oracle.oracledb_gateway import OracledbGateway
from oracle_impact.infrastructure.oracle.queries import QUERIES, SESSION_SQL, apply_dictionary_views
from oracle_impact.infrastructure.storage.registry import Registry
from oracle_impact.infrastructure.storage.review_store import ReviewStore
from oracle_impact.infrastructure.storage.run_store import RunStore
from oracle_impact.models.assessment import PolicyItem
from oracle_impact.models.domain import PackageRef
from oracle_impact.models.enums import EvidenceStatus
from oracle_impact.security import (
    _sign,
    csrf_token,
    csrf_valid,
    issue_session,
    session_role,
    session_valid,
    token_matches,
)
from oracle_impact.services import readiness
from oracle_impact.services.business_impact import business_impact
from oracle_impact.services.decision_service import ActionError, DecisionService, actor, offline_context
from oracle_impact.services.history import age_text, previous_run, related_runs, stale_reason, superseded_by
from oracle_impact.services.report_view import initial_state
from oracle_impact.services.snapshot_service import SnapshotService
from tests.support import run_catalogs, settings

APPROVER = actor("approver", "Alex")
ANALYST = actor("analyst", "Sam")


def _demo(tmp_path, package: str, **overrides):
    test_catalog, prod_catalog = build_demo_catalogs()
    return run_catalogs(tmp_path, [package], test_catalog, prod_catalog, **overrides)


def _service(tmp_path, **overrides) -> DecisionService:
    overrides.setdefault("approver_access_token", "approver-token")
    configured = settings(tmp_path, **overrides)
    store = RunStore(tmp_path)
    return DecisionService(configured, store, Registry(configured.registry_dir), ReviewStore(store))


def _code(call) -> str:
    with pytest.raises(ActionError) as caught:
        call()
    return caught.value.code


# ----------------------------------------------------------------- decision service


def test_actor_names_are_trimmed_and_never_blank():
    assert actor("analyst", "  Dana   Lee ") == {"role": "analyst", "name": "Dana Lee", "declared": True}
    assert actor("analyst", "")["name"] == "Not given"
    assert len(actor("analyst", "x" * 500)["name"]) == 80


def test_approval_rules_and_staleness(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    disabled = _service(tmp_path, approver_access_token="")
    assert _code(lambda: disabled.record_approval(report, {"decision": "approved", "notes": "x"}, APPROVER)) == "approvals_disabled"

    service = _service(tmp_path)
    assert _code(lambda: service.record_approval(report, {"decision": "approved", "notes": "x"}, ANALYST)) == "approver_required"
    assert _code(lambda: service.record_approval(report, {"decision": "ship", "notes": "x"}, APPROVER)) == "invalid_decision"
    assert _code(lambda: service.record_approval(report, {"decision": "approved", "notes": " "}, APPROVER)) == "reason_required"
    record = service.record_approval(report, {"decision": "approved", "notes": "Validated."}, APPROVER)
    assert record["readiness"] == "recommended" and record["policy_version"] == readiness.POLICY_VERSION
    assert service.context(report)["approval"]["stale"] is False

    _demo(tmp_path, "DEMO.PKG_STATUS")
    context = service.context(report)
    assert context["readiness"]["decision"] == "indeterminate" and context["history"]["superseded_by"]
    assert context["approval"]["stale"] is True


def test_approving_a_held_report_is_refused(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    service = _service(tmp_path)
    assert _code(lambda: service.record_approval(report, {"decision": "approved", "notes": "x"}, APPROVER)) == "not_recommended"
    assert service.record_approval(report, {"decision": "rejected", "notes": "Blockers."}, APPROVER)["readiness"] == "on_hold"


def test_baseline_and_manifest_input_rules(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    service = _service(tmp_path)
    key = "DEMO.PKG_BILLING:PACKAGE BODY"
    assert _code(lambda: service.set_baseline(report, {"environment": "production", "object_key": key, "note": "x"}, ANALYST)) == "approver_required"
    assert _code(lambda: service.set_baseline(report, {"environment": "test", "object_key": key, "note": "x"}, APPROVER)) == "invalid_environment"
    assert _code(lambda: service.set_baseline(report, {"environment": "production", "object_key": key, "note": ""}, APPROVER)) == "reason_required"
    assert _code(lambda: service.set_baseline(report, {"environment": "production", "object_key": "X.Y:PACKAGE", "note": "x"}, APPROVER)) == "unknown_object"
    assert _code(lambda: service.set_baseline(report, {"environment": "autumn", "object_key": "all", "note": "x"}, APPROVER)) == "invalid_environment"
    recorded = service.set_baseline(report, {"environment": "production", "object_key": "all", "note": "Release 12"}, APPROVER)
    assert {item["object_type"] for item in recorded} == {"PACKAGE", "PACKAGE BODY"}
    assert all(item["source_run"] == report.run_id and item["sha256"] for item in recorded)

    assert _code(lambda: service.approve_manifest(report, {"object_key": key, "change_ref": ""}, APPROVER)) == "change_ref_required"
    assert _code(lambda: service.approve_manifest(report, {"object_key": "X.Y:PACKAGE", "change_ref": "CHG-1"}, APPROVER)) == "unknown_object"
    entry = service.approve_manifest(report, {"object_key": key, "change_ref": "CHG-1"}, APPROVER)
    assert entry["change_kind"] == "create_or_modify" and entry["status"] == "approved"

    assert _code(lambda: service.withdraw_manifest(entry["id"], {"reason": ""}, APPROVER)) == "reason_required"
    assert _code(lambda: service.withdraw_manifest("M-NOPE", {"reason": "x"}, APPROVER)) == "unknown_record"
    assert _code(lambda: service.clear_baseline("B-NOPE", {"reason": "x"}, APPROVER)) == "unknown_record"
    service.clear_baseline(recorded[0]["id"], {"reason": "Wrong"}, APPROVER)

    context = service.context(report)
    assert sorted(item["status"] for item in context["baselines"]) == ["active", "cleared"]
    assert len(context["manifest"]) == 1
    assert {event["action"] for event in context["registry_events"]} == {"baseline.set", "manifest.approve"}


def test_mapping_input_rules(tmp_path):
    service = _service(tmp_path)
    form = {"object": " DEMO.PKG_BILLING ", "application": "Billing", "business_process": "Invoicing", "criticality": "high"}
    assert _code(lambda: service.add_mapping(form, ANALYST)) == "approver_required"
    assert _code(lambda: service.add_mapping({**form, "object": "demo.pkg_billing"}, APPROVER)) == "invalid_object"
    assert _code(lambda: service.add_mapping({**form, "object": "A.B C.D"}, APPROVER)) == "invalid_object"
    assert _code(lambda: service.add_mapping({**form, "object": "not valid!"}, APPROVER)) == "invalid_object"
    assert _code(lambda: service.add_mapping({**form, "criticality": "extreme"}, APPROVER)) == "invalid_criticality"
    assert _code(lambda: service.add_mapping({**form, "business_process": ""}, APPROVER)) == "mapping_fields_required"
    saved = service.add_mapping(form, APPROVER)
    assert (saved["owner"], saved["name"], saved["criticality"]) == ("DEMO", "PKG_BILLING", "high")
    assert _code(lambda: service.retire_mapping("MAP-NOPE", APPROVER)) == "unknown_record"
    service.retire_mapping(saved["id"], APPROVER)


def test_load_returns_none_for_unknown_or_unsafe_runs(tmp_path):
    service = _service(tmp_path)
    assert service.load("missing") is None
    assert service.load("../etc") is None


def test_offline_context_has_no_records_and_still_decides(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    context = offline_context(report)
    assert context["readiness"]["decision"] == "recommended"
    assert context["review"]["validations"] == [] and context["approval"]["latest"] is None


# ----------------------------------------------------------------- business impact


def _mapping(owner, name, criticality="medium", status="active"):
    return {"id": f"MAP-{name}", "owner": owner, "name": name, "criticality": criticality, "status": status, "application": "App"}


def test_business_impact_levels_follow_evidence_only(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    mappings = [
        _mapping("DEMO", "PKG_BILLING", "low"),
        _mapping("DEMO", "PKG_LEGACY", "critical"),
        _mapping("DEMO", "CUSTOMER_BILLS"),
        _mapping("OTHER", "THING"),
        _mapping("DEMO", "RETIRED", status="retired"),
    ]
    result = business_impact(report, mappings)
    levels = {row["qualified"]: row["level"] for row in result["rows"] + result["not_analyzed"]}
    assert levels == {
        "DEMO.PKG_BILLING": "confirmed",
        "DEMO.PKG_LEGACY": "confirmed",
        "DEMO.CUSTOMER_BILLS": "none_identified",
        "OTHER.THING": "not_analyzed",
    }
    assert [row["qualified"] for row in result["rows"]][:2] == ["DEMO.PKG_LEGACY", "DEMO.PKG_BILLING"]
    assert result["counts"] == {"confirmed": 2, "potential": 0, "none_identified": 1, "not_analyzed": 1}
    assert result["established"] is True
    assert result["unmapped"] == []
    assert business_impact(report, mappings[2:])["unmapped"] == ["DEMO.PKG_BILLING", "DEMO.PKG_LEGACY"]


def test_business_impact_without_findings_is_potential_for_changed_objects(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING").model_copy(update={"findings": []})
    result = business_impact(report, [_mapping("DEMO", "PKG_BILLING")])
    assert result["rows"][0]["level"] == "potential" and "changed in this change set" in result["rows"][0]["basis"]
    nothing = business_impact(report, [])
    assert nothing["established"] is False and nothing["unmapped"] == ["DEMO.PKG_BILLING"]


def test_low_or_unconfirmed_findings_are_only_potential_impact(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    row = business_impact(report, [_mapping("DEMO", "PKG_STATUS")])["rows"][0]
    assert row["level"] == "potential" and row["tone"] == "warning"


def test_investigator_findings_never_create_confirmed_business_impact(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    claim = report.findings[0].model_copy(
        update={"id": "F-AI", "origin": "investigator", "status": EvidenceStatus.CONFIRMED, "changed_object": "DEMO.PKG_DASHBOARD"}
    )
    report = report.model_copy(update={"findings": [*report.findings, claim]})
    row = business_impact(report, [_mapping("DEMO", "PKG_DASHBOARD")])["rows"][0]
    assert row["level"] == "none_identified"


# ----------------------------------------------------------------- history


def _run(run_id, created, ns=0, packages=("APP.PKG",), status="completed"):
    return {"run_id": run_id, "created_at": created, "requested_ns": ns, "packages": list(packages), "status": status}


def test_related_runs_match_the_exact_package_set():
    runs = [
        _run("a", "2026-10-01T00:00:00+00:00", packages=("APP.B", "APP.A")),
        _run("b", "2026-10-01T00:00:00+00:00", packages=("APP.A",)),
        _run("c", "2026-10-01T00:00:00+00:00", packages=("APP.A", "APP.B"), status="failed"),
        _run("d", "", packages=("APP.A", "APP.B")),
    ]
    assert [run["run_id"] for run in related_runs(runs, ["APP.A", "APP.B"])] == ["a"]


def test_superseded_and_previous_runs_use_request_time_for_ties(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    same = report.created_at
    runs = [
        _run("newer", same, ns=30, packages=report.packages),
        _run(report.run_id, same, ns=20, packages=report.packages),
        _run("older", same, ns=10, packages=report.packages),
        _run("other", "2030-01-01T00:00:00+00:00", ns=99, packages=("X.Y",)),
    ]
    assert superseded_by(runs, report) == "newer"
    assert previous_run(runs, report) == "older"
    assert superseded_by(runs, report.model_copy(update={"created_at": "not a date"})) is None


def test_stale_reason_and_age_text(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    created = datetime.fromisoformat(report.created_at)
    assert stale_reason(report, 24, created + timedelta(hours=23)) is None
    assert stale_reason(report, 0, created + timedelta(days=9)) is None
    message = stale_reason(report, 24, created + timedelta(days=3))
    assert message.startswith("The snapshot is 3 days old") and "24 hour" in message
    assert age_text(report, created + timedelta(minutes=1)) == "1 minute"
    assert age_text(report, created + timedelta(hours=5)) == "5 hours"
    assert age_text(report, created - timedelta(minutes=5)) == "0 minutes"
    assert age_text(report.model_copy(update={"created_at": ""})) == "unknown"


# ----------------------------------------------------------------- policy


def _item(resolution, finding_id="F-1"):
    return PolicyItem(rule_id="R", title="t", finding_id=finding_id, resolution=resolution, explanation="", required_action="")


def _validation(status, record_id="V1", finding_id="F-1"):
    return {"id": record_id, "finding_id": finding_id, "status": status}


@pytest.mark.parametrize(
    "resolution, validations, reviews",
    [
        (readiness.FIX_AND_REANALYZE, [_validation("passed")], [{"finding_id": "F-1", "decision": "accepted"}]),
        (readiness.VALIDATION_AND_REVIEW, [_validation("passed")], [{"finding_id": "F-1", "decision": "accepted"}]),
        (readiness.VALIDATION_AND_REVIEW, [], [{"finding_id": "F-1", "decision": "not_an_issue"}]),
        (readiness.VALIDATION, [_validation("passed")], []),
        (readiness.VALIDATION, [_validation("passed"), _validation("failed", "V2")], [{"finding_id": "F-1", "decision": "confirmed_issue"}]),
        (readiness.VALIDATION, [], []),
    ],
)
def test_item_status_ignores_stored_validation_and_review(resolution, validations, reviews):
    status = readiness.item_status(_item(resolution), validations, reviews)
    assert status["state"] == "unresolved" and status["recorded"] == 0 and status["decision"] == ""


def test_policy_file_overrides(tmp_path):
    report = _demo(tmp_path / "run", "DEMO.PKG_BILLING")
    assert readiness.load_rules(None) is readiness.DEFAULT_RULES
    assert readiness.load_rules(tmp_path / "missing.json") is readiness.DEFAULT_RULES
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert readiness.load_rules(broken) is readiness.DEFAULT_RULES
    wrong_shape = tmp_path / "list.json"
    wrong_shape.write_text(json.dumps({"rules": ["BLK-PARTIAL-COMMIT"]}))
    assert readiness.load_rules(wrong_shape) is readiness.DEFAULT_RULES

    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "rules": {
                    "BLK-PARTIAL-COMMIT": {"enabled": False},
                    "BLK-MISSING-OBJECT": {"resolution": "make_it_go_away"},
                    "VAL-BUSINESS-RULE": {"resolution": readiness.VALIDATION_AND_REVIEW},
                }
            }
        )
    )
    rules = {rule.id: rule for rule in readiness.load_rules(policy)}
    assert rules["BLK-PARTIAL-COMMIT"].enabled is False
    assert rules["BLK-MISSING-OBJECT"].resolution == readiness.FIX_AND_REANALYZE
    assert rules["VAL-BUSINESS-RULE"].resolution == readiness.VALIDATION_AND_REVIEW

    evaluation = readiness.evaluate_policy(report.findings, tuple(rules.values()))
    blocker_rules = {item.rule_id for item in evaluation.blockers}
    assert "BLK-PARTIAL-COMMIT" not in blocker_rules and "BLK-MISSING-OBJECT" in blocker_rules
    partial = next(finding.id for finding in report.findings if finding.rule_id == "TXN-PARTIAL-COMMIT")
    assert partial in {item.finding_id for item in evaluation.validations}
    assert any(rule["id"] == "BLK-PARTIAL-COMMIT" and rule["enabled"] is False for rule in evaluation.rules)


def test_policy_ignores_investigator_findings(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    claims = [finding.model_copy(update={"origin": "investigator"}) for finding in report.findings]
    evaluation = readiness.evaluate_policy(claims)
    assert evaluation.blockers == [] and evaluation.validations == []


def test_policy_for_recomputes_only_for_reports_without_a_saved_policy(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    saved, computed = readiness.policy_for(report)
    assert computed is False and saved is report.policy
    legacy, computed = readiness.policy_for(report.model_copy(update={"policy": None}))
    assert computed is True and [item.rule_id for item in legacy.blockers] == [item.rule_id for item in saved.blockers]


# ----------------------------------------------------------------- report view state


def test_initial_state_keeps_known_values_only():
    view = {
        "rows": [{"id": "F-1"}],
        "diffs": [{"key": "a", "state": "unchanged"}, {"key": "b", "state": "modified"}],
    }
    state = initial_state(view, {"cmp": "autumn:production", "line": "12", "side": "p", "mode": "support"})
    assert state["object"] == "b" and "cmp" not in state
    assert (state["line"], state["side"]) == (12, "p") and "mode" not in state
    unknown = initial_state(view, {"line": "x", "side": "q"})
    assert unknown["line"] is None and unknown["side"] == "t"


# ----------------------------------------------------------------- security and configuration


def test_sessions_carry_a_signed_role():
    analyst = issue_session("secret")
    approver = issue_session("secret", "approver")
    assert session_role("secret", analyst) == "analyst" and session_role("secret", approver) == "approver"
    with pytest.raises(ValueError):
        issue_session("secret", "admin")
    _, nonce, signature = analyst.split(".")
    assert session_role("secret", f"approver.{nonce}.{signature}") is None
    assert session_role("other-secret", approver) is None
    assert session_role("secret", f"admin.{nonce}.{signature}") is None
    assert session_role("secret", "a.b.c.d") is None
    assert session_role("secret", None) is None and session_role("secret", "nodot") is None
    legacy = f"{nonce}.{_sign('secret', f'ok:{nonce}')}"
    assert session_role("secret", legacy) == "analyst"
    assert session_valid("secret", analyst) and not session_valid("secret", analyst + "0")


def test_csrf_tokens_are_bound_to_the_session():
    first, second = issue_session("secret"), issue_session("secret")
    token = csrf_token("secret", first)
    assert csrf_valid("secret", first, token)
    assert not csrf_valid("secret", second, token)
    assert not csrf_valid("secret", first, "")
    assert not csrf_valid("secret", "forged.session", csrf_token("secret", "forged.session"))
    assert token_matches("abc", "abc") and not token_matches("abc", "abd")
    assert not token_matches("", "") and not token_matches("abc", "")


def test_approvals_need_a_distinct_approver_token(tmp_path):
    assert settings(tmp_path).approvals_enabled is False
    assert settings(tmp_path, approver_access_token="test-token").approvals_enabled is False
    assert settings(tmp_path, approver_access_token="other").approvals_enabled is True
    assert settings(tmp_path).registry_dir == tmp_path / "_registry"
    assert settings(tmp_path, state_dir=tmp_path / "state").registry_dir == tmp_path / "state"


# ----------------------------------------------------------------- snapshot capture


def _catalog(source="PACKAGE BODY x AS END;"):
    catalog = Catalog()
    catalog.add_source("APP", "PKG", "PACKAGE", "PACKAGE x AS END;")
    catalog.add_source("APP", "PKG", "PACKAGE BODY", source)
    return catalog


def _gateway(environment, catalog):
    return FixtureGateway(environment, f"db-{environment}", environment.upper(), catalog)


def test_snapshot_records_every_environment_and_capture_drift():
    test_catalog = _catalog()
    test_catalog.drift.add(("APP", "PKG", "PACKAGE BODY"))
    service = SnapshotService(_gateway("test", test_catalog), _gateway("production", _catalog()))
    assert service.environments == ["test", "production"]
    snapshot = service.capture([PackageRef(owner="APP", name="PKG")])
    assert len(snapshot.objects) == 4
    body = snapshot.find("test", "APP", "PKG", "PACKAGE BODY")
    assert body.drift is True and body.verification_sha256 != body.sha256
    assert any("changed during capture" in item for item in snapshot.limitations)


def test_snapshot_distinguishes_missing_and_unreadable_packages():
    with pytest.raises(LookupError, match="APP.GONE"):
        SnapshotService(_gateway("test", _catalog()), _gateway("production", _catalog())).capture([PackageRef(owner="APP", name="GONE")])
    hidden = _catalog()
    hidden.inaccessible_sources.update({("APP", "PKG", "PACKAGE"), ("APP", "PKG", "PACKAGE BODY")})
    snapshot = SnapshotService(_gateway("test", hidden), _gateway("production", _catalog())).capture([PackageRef(owner="APP", name="PKG")])
    assert any("could not be read in Test" in item for item in snapshot.limitations)


def test_production_outage_is_not_swallowed():
    class Down:
        database_id = "prod"

        def fetch_source(self, owner, name, object_type):
            raise OracleUnavailable("down")

    with pytest.raises(OracleUnavailable):
        SnapshotService(_gateway("test", _catalog()), Down()).capture([PackageRef(owner="APP", name="PKG")])


# ----------------------------------------------------------------- Oracle gateway


class _Recorder:
    def __init__(self, rows=None, failure=None):
        self.rows = rows or []
        self.failure = failure
        self.executed: list[tuple[str, dict | None]] = []

    def connect(self):
        return _Connection(self)


class _Connection:
    def __init__(self, recorder):
        self.recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, binds=None):
        self.recorder.executed.append((sql, binds))
        if self.recorder.failure is not None and "ROWNUM" not in sql and sql != SESSION_SQL:
            raise self.recorder.failure

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return list(self.recorder.rows)


def _oracle_gateway(recorder):
    gateway = OracledbGateway("test", "TEST", "dsn", "reader", "secret")
    gateway._connect = recorder.connect
    return gateway


def test_gateway_reads_object_status_with_bind_variables_in_a_read_only_transaction():
    recorder = _Recorder(rows=[("PACKAGE BODY", "INVALID", "2026-10-01T10:00:00"), (None, None, None)])
    fetched = _oracle_gateway(recorder).fetch_object_status("APP", "PKG")
    assert fetched.objects == [("PACKAGE BODY", "INVALID", "2026-10-01T10:00:00"), ("", "", None)]
    sql, binds = recorder.executed[-1]
    assert binds == {"owner": "APP", "name": "PKG"} and "dba_objects" in sql and "APP" not in sql
    assert recorder.executed[-2] == (SESSION_SQL, None)


def test_gateway_compile_errors_normalize_rows():
    recorder = _Recorder(rows=[("PACKAGE BODY", 12, 5, "PLS-00201: identifier  \n", None), ("PACKAGE BODY", None, None, None, "WARNING")])
    fetched = _oracle_gateway(recorder).fetch_compile_errors("APP", "PKG")
    assert fetched.errors == [("PACKAGE BODY", 12, 5, "PLS-00201: identifier", "ERROR"), ("PACKAGE BODY", 0, 0, "", "WARNING")]


def test_gateway_reports_denied_dictionary_reads_as_inaccessible():
    denied = oracledb.DatabaseError(SimpleNamespace(code=1031))
    assert _oracle_gateway(_Recorder(failure=denied)).fetch_object_status("APP", "PKG").inaccessible is True
    assert _oracle_gateway(_Recorder(failure=denied)).fetch_compile_errors("APP", "PKG").inaccessible is True


def test_every_catalog_query_is_a_read_with_bind_variables():
    assert SESSION_SQL == "SET TRANSACTION READ ONLY"
    forbidden = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|GRANT|EXECUTE|BEGIN|DECLARE|CALL)\b", re.IGNORECASE)
    for name, sql in QUERIES.items():
        text = sql.strip()
        assert re.match(r"(SELECT|WITH)\b", text, re.IGNORECASE), name
        assert not forbidden.search(text), name
        assert "{" not in text and "%s" not in text, name
        if re.search(r"\bWHERE\b", text, re.IGNORECASE):
            assert ":" in text, name
        assert apply_dictionary_views(text, "all") == text
    with pytest.raises(ValueError):
        apply_dictionary_views("SELECT 1 FROM dual", "user")
