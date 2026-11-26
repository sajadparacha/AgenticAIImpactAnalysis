"""Presentation model and report page behaviour."""

import json
import re

from oracle_impact.analysis.diff_engine import build_object_diff
from oracle_impact.controllers.web import TEMPLATES, _sections
from oracle_impact.models.enums import EvidenceStatus, Recommendation
from oracle_impact.models.report import DependencyRow
from oracle_impact.services.report_view import build_report_view, format_timestamp, initial_state
from tests.support import client_for, login, run_catalogs
from tests.test_regression import _MOVED_COMMIT_PROD, _MOVED_COMMIT_TEST, _investigator, _pair

_REVIEW = json.dumps(
    {"reviews": [{"business_rule": "The update is saved before the second update runs.", "test_cases": ["Fail the second update."]}]}
)


def _reviewed_report(tmp_path):
    test_catalog, prod_catalog = _pair(_MOVED_COMMIT_PROD, _MOVED_COMMIT_TEST)
    return run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        test_catalog,
        prod_catalog,
        llm_egress_enabled=True,
        llm_model="mock",
        investigator=_investigator(_REVIEW, _REVIEW),
    )


def _render(report, state=None, print_mode=False):
    view = build_report_view(report)
    return TEMPLATES.get_template("report_body.html").render(
        {"report": report, "sections": _sections(report), "view": view, "state": state or initial_state(view, {}), "print_mode": print_mode}
    )


def test_metrics_count_deterministic_findings_and_reconcile_with_saved_counts(tmp_path):
    report = _reviewed_report(tmp_path)
    view = build_report_view(report)
    deterministic = [finding for finding in report.findings if finding.origin != "investigator"]
    metrics = view["metrics"]
    assert metrics["findings"] == len(deterministic)
    assert metrics["reconciled"] and metrics["saved_total"] == len(deterministic)
    assert sum(item["count"] for item in metrics["severity"]) == len(deterministic)
    assert sum(item["count"] for item in metrics["evidence"]) == len(deterministic)
    assert metrics["ai_notes_attached"] == 1 and metrics["ai_only"] == 0
    assert metrics["verified"] is None
    combined = [row for row in view["rows"] if row["source"] == "combined"]
    assert len(combined) == 1 and combined[0]["note_id"]


def test_recommendation_comes_from_policy_and_human_decision_is_not_recorded(tmp_path):
    report = _reviewed_report(tmp_path)
    view = build_report_view(report)
    assert report.recommendation == Recommendation.NOT_RECOMMENDED
    assert view["recommendation"]["label"] == report.recommendation_label
    assert view["recommendation"]["tone"] == "danger"
    driver_ids = [row["id"] for row in view["rows"] if row["driver"]]
    cited = [reason.partition(": ")[0] for reason in report.recommendation_reasons]
    assert driver_ids and set(driver_ids) <= set(cited)
    assert view["human_decision"] == {**view["human_decision"], "state": "not_recorded", "label": "Not recorded"}
    page = _render(report)
    assert "Human deployment decision" in page
    # "Deployment on hold" is the analyzer's policy readiness; no human decision is claimed.
    assert view["decision"]["readiness"]["decision"] == "on_hold"
    assert view["decision"]["readiness"]["open_blockers"] >= 1
    assert view["approval"]["latest"] is None
    assert 'name="decision"' not in page
    assert "Approval recording is off because no approver access token is configured." in page
    assert not re.search(r"(deployment|change) (approved|rejected)|approved by", page, re.IGNORECASE)


def test_next_steps_do_not_duplicate_hold_clearance(tmp_path):
    report = _reviewed_report(tmp_path)
    view = build_report_view(report)
    hold = view["decision"]["hold_rows"]
    assert hold
    hold_actions = {row["action"] for row in hold if row.get("action")}
    next_texts = {step["text"] for step in view["next_steps"]}
    assert hold_actions and not hold_actions & next_texts


def test_next_steps_link_to_exact_destinations(tmp_path):
    # A missing object produces a fix-and-reanalyze blocker, which links to the fix plan.
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN INSERT INTO app.ledger_history (id) VALUES (p_id); END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    view = build_report_view(report)
    by_kind = {step["kind"]: step for step in view["next_steps"]}
    assert "blocker" in by_kind
    assert by_kind["blocker"]["href"].endswith("/fix-plan")
    assert by_kind["blocker"]["href_label"] == "Step-by-step fix plan"
    if "validation" in by_kind:
        assert "tab=findings&blk=validation" in by_kind["validation"]["href"]


def test_overview_explains_overlapping_counts_and_shortens_run_id(tmp_path):
    report = _reviewed_report(tmp_path)
    view = build_report_view(report)
    page = _render(report)
    assert "is a work view of the same items" in page
    assert "different dimensions" in page
    # The visible run ID is shortened; the full value survives only as copy text/title.
    assert f'<code title="{report.run_id}">{report.run_id[:8]}</code>' in page
    assert f'data-copy="{report.run_id}"' in page


def test_show_top_findings_false_when_drivers_are_all_hold_rows(tmp_path):
    report = _reviewed_report(tmp_path)
    view = build_report_view(report)
    hold_ids = {row["finding_id"] for row in view["decision"]["hold_rows"] if row.get("finding_id")}
    top_ids = {row["id"] for row in view["top_findings"]}
    assert top_ids and top_ids <= hold_ids
    assert view["show_top_findings"] is False


def test_ai_only_note_is_unrated_and_never_drives_the_decision(tmp_path):
    report = _reviewed_report(tmp_path)
    note = next(finding for finding in report.findings if finding.origin == "investigator")
    note.attributes = {key: value for key, value in note.attributes.items() if key != "review_of"}
    view = build_report_view(report)
    row = next(row for row in view["rows"] if row["id"] == note.id)
    assert row["source"] == "ai" and row["source_label"] == "AI assessment only"
    assert row["level"] == "unrated" and not row["driver"]
    assert row["evidence_status"] != EvidenceStatus.CONFIRMED.value
    assert row["recommended_action"] == "" and row["proposed_tests"] == []
    assert view["metrics"]["ai_only"] == 1
    assert view["recommendation"]["value"] == report.recommendation.value
    assert view["rows"][-1]["id"] == note.id
    detail = _render(report).split(f'id="detail-{note.id}"', 1)[1].split("</article>", 1)[0]
    assert "No deterministic evidence" in detail


def test_initial_state_rejects_unknown_deep_link_values(tmp_path):
    report = _reviewed_report(tmp_path)
    view = build_report_view(report)
    first_changed = next(diff["key"] for diff in view["diffs"] if diff["state"] != "unchanged")
    bad = initial_state(view, {"tab": "admin", "finding": "F-NOPE", "object": "../etc", "line": "1;drop", "side": "x", "mode": "root"})
    assert bad == {"tab": "overview", "finding": "", "object": first_changed, "line": None, "side": "t"}
    finding_id = view["rows"][0]["id"]
    assert initial_state(view, {"finding": finding_id})["tab"] == "findings"
    key = view["diffs"][-1]["key"]
    deep = initial_state(view, {"object": key, "line": "7", "side": "p", "mode": "support"})
    assert deep == {"tab": "objects", "finding": "", "object": key, "line": 7, "side": "p"}
    # Legacy tab keys are mapped before validation.
    for legacy, resolved in (("source", "objects"), ("validation", "details"), ("audit", "details")):
        assert initial_state(view, {"tab": legacy})["tab"] == resolved


def test_changes_object_list_orders_by_severity_then_package_name():
    from oracle_impact.models.enums import Confidence, Severity
    from oracle_impact.models.report import EnvironmentInfo, Finding, Report, ReportStatistics

    def _finding(fid, owner, name, object_type, severity):
        return Finding(
            id=fid,
            changed_object=f"{owner}.{name}",
            object_owner=owner,
            object_name=name,
            object_type=object_type,
            source_database="test",
            change_category="business_logic",
            evidence="Line 1 changed.",
            severity=severity,
            confidence=Confidence.HIGH,
            status=EvidenceStatus.CONFIRMED,
            explanation="changed",
            recommended_action="review",
            changed_lines_test=[1],
        )

    # Intentionally unordered: low package first, then critical packages out of name order.
    diffs = [
        build_object_diff("DEMO", "PKG_STATUS", "PACKAGE", "a", "b", "t1", "p1"),
        build_object_diff("DEMO", "PKG_STATUS", "PACKAGE BODY", "a", "b", "t2", "p2"),
        build_object_diff("DEMO", "PKG_ORDER", "PACKAGE", "a", "b", "t3", "p3"),
        build_object_diff("DEMO", "PKG_ORDER", "PACKAGE BODY", "a", "b", "t4", "p4"),
        build_object_diff("DEMO", "PKG_BILLING", "PACKAGE", "a", "b", "t5", "p5"),
        build_object_diff("DEMO", "PKG_BILLING", "PACKAGE BODY", "a", "b", "t6", "p6"),
    ]
    findings = [
        _finding("F-LOW-SPEC", "DEMO", "PKG_STATUS", "PACKAGE", Severity.LOW),
        _finding("F-LOW-BODY", "DEMO", "PKG_STATUS", "PACKAGE BODY", Severity.LOW),
        _finding("F-CRIT-ORDER-SPEC", "DEMO", "PKG_ORDER", "PACKAGE", Severity.CRITICAL),
        _finding("F-CRIT-ORDER-BODY", "DEMO", "PKG_ORDER", "PACKAGE BODY", Severity.CRITICAL),
        _finding("F-CRIT-BILL-SPEC", "DEMO", "PKG_BILLING", "PACKAGE", Severity.CRITICAL),
        _finding("F-CRIT-BILL-BODY", "DEMO", "PKG_BILLING", "PACKAGE BODY", Severity.CRITICAL),
        _finding("F-HIGH-BILL-BODY", "DEMO", "PKG_BILLING", "PACKAGE BODY", Severity.HIGH),
    ]
    report = Report(
        run_id="run-order",
        created_at="2026-11-25T00:00:00+00:00",
        packages=["DEMO.PKG_STATUS", "DEMO.PKG_ORDER", "DEMO.PKG_BILLING"],
        test_environment=EnvironmentInfo(role="proposed", label="TEST", database_id="fixture-test", fixture=True),
        production_environment=EnvironmentInfo(role="baseline", label="PRODUCTION", database_id="fixture-prod", fixture=True),
        analyzer="fixture",
        status="completed",
        recommendation=Recommendation.NOT_RECOMMENDED,
        recommendation_label="Not recommended",
        statistics=ReportStatistics(),
        findings=findings,
        diffs=diffs,
    )
    view = build_report_view(report)
    ordered = [(diff["label"], diff["object_type"], diff["level"]) for diff in view["diffs"]]
    assert ordered == [
        ("DEMO.PKG_BILLING", "PACKAGE", "critical"),
        ("DEMO.PKG_BILLING", "PACKAGE BODY", "critical"),
        ("DEMO.PKG_ORDER", "PACKAGE", "critical"),
        ("DEMO.PKG_ORDER", "PACKAGE BODY", "critical"),
        ("DEMO.PKG_STATUS", "PACKAGE", "low"),
        ("DEMO.PKG_STATUS", "PACKAGE BODY", "low"),
    ]


def test_source_diff_collapses_distant_context_and_links_findings(tmp_path):

    report = _reviewed_report(tmp_path)
    prod = "\n".join(f"  v_{i} := {i};" for i in range(1, 41))
    test = prod.replace("v_20 := 20;", "v_20 := 21;")
    report.diffs.append(build_object_diff("APP", "PKG_LONG", "PACKAGE BODY", test, prod, "t" * 64, "p" * 64))
    finding = next(item for item in report.findings if item.origin != "investigator").model_copy(deep=True)
    finding.id, finding.object_owner, finding.object_name = "F-LONG", "APP", "PKG_LONG"
    finding.changed_object, finding.object_type, finding.changed_lines_test = "APP.PKG_LONG", "PACKAGE BODY", [20]
    report.findings.append(finding)
    view = build_report_view(report)
    diff = next(item for item in view["diffs"] if item["key"] == "APP.PKG_LONG:PACKAGE BODY")
    assert diff["state"] == "changed" and diff["hunks"] == 1
    lines = [row for row in diff["rows"] if not row["gap"]]
    assert len(lines) == diff["row_count"]
    near = [row for row in lines if not row["far"]]
    assert [row["test_no"] for row in near if row["test_no"]] == list(range(17, 24))
    assert sum(row["gap"] for row in diff["rows"]) == len(lines) - len(near)
    assert any(row["id"] == f"{diff['dom_id']}-t20" and row["change"] for row in lines)
    row = next(row for row in view["rows"] if row["id"] == "F-LONG")
    assert row["diff_link"]["key"] == diff["key"] and row["diff_link"]["line"] == 20 and row["diff_link"]["side"] == "t"
    assert [item["id"] for item in diff["findings"]] == ["F-LONG"]


def test_diff_table_orders_production_baseline_left_of_test_proposed(tmp_path):
    report = _reviewed_report(tmp_path)
    page = _render(report)
    tables = re.findall(r'<table class="diff-table[^"]*"[^>]*>.*?</table>', page, re.S)
    assert tables, "diff tables not rendered"
    for table in tables:
        thead = table[: table.find("</thead>")]
        left = thead.find("baseline")
        right = thead.find("proposed")
        assert left != -1 and right != -1, f"missing baseline/proposed labels in {thead[:200]}"
        assert left < right, "Production baseline must be the left column, Test proposed the right column"


def test_dependencies_link_findings_by_whole_object_name(tmp_path):
    report = _reviewed_report(tmp_path)
    anchor = next(item for item in report.findings if item.origin != "investigator")
    anchor.affected_object = "APP.ACCOUNT"
    report.dependencies = [
        DependencyRow(direction="uses", object_type="TABLE", object_name="ACCOUNT", owner="APP", usage="update", impact="Affected", depth=1, database=""),
        DependencyRow(direction="caller", object_type="PACKAGE", object_name="ACCOUNT_API", owner="APP", usage="call", impact="Unknown", depth=1, database="TEST"),
    ]
    view = build_report_view(report)
    table, api = view["dependencies"]
    assert anchor.id in table["finding_ids"] and table["impact_tone"] == "danger"
    assert table["database"] == "Not recorded"
    assert anchor.id not in api["finding_ids"] and api["direction_label"] == "Calls the selected package"
    row = next(row for row in view["rows"] if row["id"] == anchor.id)
    assert [dep["dom_id"] for dep in row["dependencies"]] == [table["dom_id"]]


def test_audit_phase_lines_split_out_finding_severity_and_environment(tmp_path):
    report = _reviewed_report(tmp_path)
    known = report.findings[0].id
    status = {
        "status": "completed",
        "steps": {
            "snapshotting": {"lines": ["production DEMO.PKG_STATUS PACKAGE: captured, checksum ab12."]},
            "dependencies": {"lines": [f"{known} high error_contract: Public exceptions changed.", "F-0000ABCD low coverage: production DEMO.SYN SYNONYM: not resolved."]},
            "validating": {"lines": [f"{known}: Kept after evidence check."]},
        },
    }
    phases = {phase["key"]: phase for phase in build_report_view(report, status)["audit"]["phases"]}
    snapshot = phases["snapshotting"]["lines"][0]
    assert snapshot["environment"] == "Production" and snapshot["text"] == "DEMO.PKG_STATUS PACKAGE: captured, checksum ab12."
    linked, unknown = phases["dependencies"]["lines"]
    assert (linked["finding_id"], linked["linked"], linked["severity"], linked["category"]) == (known, True, "high", "Error contract")
    assert linked["text"] == "Public exceptions changed."
    assert unknown["linked"] is False and unknown["environment"] == "Production" and unknown["text"] == "DEMO.SYN SYNONYM: not resolved."
    validated = phases["validating"]["lines"][0]
    assert validated["finding_id"] == known and validated["severity"] == "" and validated["text"] == "Kept after evidence check."


def test_audit_omits_investigator_thoughts_and_marks_missing_phases(tmp_path):
    report = _reviewed_report(tmp_path)
    thought = "Private reasoning about the package."
    status = {
        "status": "completed",
        "steps": {"comparing": {"summary": "Compared 2 objects.", "lines": ["PACKAGE BODY changed", thought]}},
        "events": [{"message": thought}, {"message": "Report ready."}],
        "activity": [{"kind": "thought", "text": thought}],
    }
    audit = build_report_view(report, status)["audit"]
    rendered = json.dumps(audit, default=str)
    assert thought not in rendered
    assert [event["message"] for event in audit["events"]] == ["Report ready."]
    phases = {phase["key"]: phase for phase in audit["phases"]}
    assert phases["comparing"]["recorded"] and [line["text"] for line in phases["comparing"]["lines"]] == ["PACKAGE BODY changed"]
    assert not phases["queued"]["recorded"]
    missing = build_report_view(report)
    assert missing["audit"]["status_available"] is False
    assert missing["unverified"]["tests_recorded"] is False


def test_format_timestamp_reports_missing_values():
    assert format_timestamp(None) == "Not recorded"
    assert format_timestamp("2026-10-24T09:05:03+00:00") == "2026-10-24 09:05:03 UTC"
    assert format_timestamp("not a date") == "not a date"


def test_print_export_shows_every_panel_and_finding_detail(tmp_path):
    report = _reviewed_report(tmp_path)
    page = _render(report, print_mode=True)
    for panel in ("overview", "findings", "objects", "dependencies", "details"):
        attrs = re.search(rf'<section[^>]*data-panel="{panel}"[^>]*>', page).group(0)
        assert " hidden" not in attrs
    assert 'role="tablist"' not in page and "data-drawer" not in page
    for finding in report.findings:
        if finding.origin != "investigator":
            assert f'id="detail-{finding.id}"' in page
    assert "Known limitations" in page


def test_report_page_deep_links_open_the_requested_panel_and_finding(tmp_path):
    client = client_for(tmp_path)
    login(client)
    run_id = _start(client, "DEMO.PKG_BILLING")
    exported = client.get(f"/analyses/{run_id}/export?format=json").json()
    finding_id = exported["findings"][0]["id"]

    page = client.get(f"/analyses/{run_id}/report?tab=findings&finding={finding_id}").text
    assert _panel_hidden(page, "overview") and not _panel_hidden(page, "findings")
    drawer = re.search(r"<aside[^>]*data-drawer[^>]*>", page).group(0)
    assert " hidden" not in drawer
    detail = re.search(rf'<article[^>]*id="detail-{finding_id}"[^>]*>', page).group(0)
    assert " hidden" not in detail

    key = exported["diffs"][0]["owner"] + "." + exported["diffs"][0]["name"] + ":" + exported["diffs"][0]["object_type"]
    source = client.get(f"/analyses/{run_id}/report", params={"tab": "source", "object": key, "line": "2", "mode": "support"}).text
    assert not _panel_hidden(source, "objects") and _panel_hidden(source, "findings")
    assert f'data-object="{key}"' in source and 'data-line="2"' in source and "data-mode=" not in source

    direct = client.get(f"/analyses/{run_id}/report", params={"tab": "objects", "object": key}).text
    assert not _panel_hidden(direct, "objects") and _panel_hidden(direct, "findings")

    fallback = client.get(f"/analyses/{run_id}/report?tab=<script>&finding=x").text
    assert not _panel_hidden(fallback, "overview") and "<script>" not in fallback.split('id="risk"', 1)[0]


def test_report_page_keeps_exports_and_legacy_anchors_without_a_presentation_switch(tmp_path):
    client = client_for(tmp_path)
    login(client)
    run_id = _start(client, "DEMO.PKG_STATUS")
    page = client.get(f"/analyses/{run_id}/report").text
    assert "Eligible for review after testing" in page and "Not recorded" in page
    for fmt in ("html", "pdf", "json"):
        assert f"/analyses/{run_id}/export?format={fmt}" in page
    assert "data-mode-set" not in page and "mode-switch" not in page
    assert 'id="risk"' in page and "Nothing is holding this deployment" in page
    assert "Recommended, pending approval" in page and "Human approval is still required." in page
    assert "Ready to deploy" not in page and "Not yet approved" not in page
    for anchor in ("changes", "comparison", "dependencies", "ai", "risk", "limitations"):
        assert f'id="{anchor}"' in page
    html = client.get(f"/analyses/{run_id}/export?format=html")
    assert html.status_code == 200
    assert "Known limitations" in html.text and 'role="tablist"' not in html.text


def test_finding_page_parity_shows_blocking_status(tmp_path):
    # A missing object produces a fix-and-reanalyze blocker; the full finding page
    # must show the same deployment effect the drawer does (A11/A12 parity).
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN INSERT INTO app.ledger_history (id) VALUES (p_id); END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    view = build_report_view(report)
    blocker = next(row for row in view["rows"] if row["policy"] and row["blocking"] == "blocker")
    page = TEMPLATES.get_template("finding.html").render(
        {"report": report, "finding": blocker["finding"], "view": view, "row": blocker}
    )
    assert "Does it block deployment" in page
    assert "Blocks deployment" in page
    assert "How it clears" in page
    assert "What changed" in page and "What to test" in page
    assert "Internal Server Error" not in page


def test_ai_lead_page_renders_hypothesis_and_inherited_severity():
    from oracle_impact.models.enums import Confidence, Severity
    from oracle_impact.models.report import EnvironmentInfo, Finding, Report, ReportStatistics

    anchor = Finding(
        id="F-1", changed_object="APP.PKG_DEMO", object_owner="APP", object_name="PKG_DEMO",
        object_type="PACKAGE BODY", source_database="test", change_category="dynamic_sql",
        evidence="CLOSE_ORDER uses dynamic SQL.", severity=Severity.HIGH, confidence=Confidence.LOW,
        status=EvidenceStatus.POTENTIAL, explanation="CLOSE_ORDER builds SQL while it runs.",
        recommended_action="Capture the runtime SQL.", rule_id="DYN-SQL",
    )
    lead = Finding(
        id="AI-BS-1", changed_object="APP.PKG_DEMO", object_owner="APP", object_name="PKG_DEMO",
        object_type="PACKAGE BODY", source_database="test", change_category="dynamic_sql",
        affected_object="DEMO.OUTBOX", evidence="The AI gave no explanation.",
        severity=Severity.LOW, confidence=Confidence.LOW, status=EvidenceStatus.POTENTIAL,
        explanation="Line 20 puts an UPDATE against OUTBOX into a variable and runs it.",
        recommended_action="Confirm this lead with the suggested test.",
        proposed_tests=["Read the OUTBOX row for a known id."],
        attributes={
            "blind_spot": "dynamic_sql", "subject": "CLOSE_ORDER", "review_of": "F-1",
            "likely_targets": ["DEMO.OUTBOX"],
            "checked": [{"environment": "test", "object": "DEMO.OUTBOX", "result": "exists (table)"}],
            "unknowns": ["Could not see any other OUTBOX name."],
            "citation_unconfirmed": True,
        },
        origin="investigator",
    )
    report = Report(
        run_id="run-1", created_at="2026-11-25T00:00:00+00:00", packages=["APP.PKG_DEMO"],
        test_environment=EnvironmentInfo(role="proposed", label="TEST", database_id="fixture-test", fixture=True),
        production_environment=EnvironmentInfo(role="baseline", label="PRODUCTION", database_id="fixture-prod", fixture=True),
        analyzer="fixture", status="completed",
        recommendation=Recommendation.REQUIRES_INVESTIGATION, recommendation_label="Requires further investigation",
        statistics=ReportStatistics(), findings=[anchor, lead],
    )
    page = TEMPLATES.get_template("finding.html").render({"report": report, "finding": lead})
    assert "Unverified AI lead" in page
    assert "Related finding: High" in page
    assert "Hypothesis" in page
    assert "What the AI looked up" in page and "DEMO.OUTBOX" in page
    assert "Suggested validation" in page and "AI suggestion, review before use" in page
    assert "Citation" in page and "line references could not be matched" in page
    assert "Internal Server Error" not in page


def _start(client, package: str) -> str:
    token = re.search(r'name="csrf_token_value" value="([^"]+)"', client.get("/").text).group(1)
    started = client.post("/analyses", data={"packages": package, "csrf_token_value": token}, follow_redirects=True)
    return re.search(r"/analyses/([0-9a-f-]+)", str(started.url)).group(1)


def _panel_hidden(page: str, key: str) -> bool:
    attrs = re.search(rf'<section[^>]*data-panel="{key}"[^>]*>', page).group(0)
    return " hidden" in attrs
