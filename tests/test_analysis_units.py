"""Unit tests for the deterministic analysis helpers."""

from types import SimpleNamespace

import pytest

from oracle_impact.analysis.annotate import annotate, classify, fingerprint
from oracle_impact.analysis.compilation import MAX_DEPENDENTS, collect_compilation, compilation_findings
from oracle_impact.analysis.coverage import build_coverage, edge_records
from oracle_impact.analysis.hashing import finding_id, source_hash
from oracle_impact.analysis.plsql_scanner import authid_clause, error_declarations, financial_text, scan_package
from oracle_impact.analysis.transactions import NOT_EVALUATED, _differences, build_profiles, facts, performance_assessment
from oracle_impact.infrastructure.oracle.gateway import (
    Catalog,
    ErrorFetch,
    FixtureGateway,
    OracleAccessError,
    OracleUnavailable,
    StatusFetch,
)
from oracle_impact.infrastructure.reports.diagram import dependency_diagram
from oracle_impact.models.assessment import RoutineFacts
from oracle_impact.models.domain import CapturedObject, Snapshot
from oracle_impact.models.enums import Confidence, EvidenceStatus, Severity
from oracle_impact.models.report import DependencyRow, Finding

BODY = """CREATE OR REPLACE PACKAGE BODY APP.PKG AS
  PROCEDURE post_invoice(p_id NUMBER) IS
    PRAGMA AUTONOMOUS_TRANSACTION;
  BEGIN
    -- UPDATE commented_table SET x = 1;
    INSERT INTO app.invoices (id) VALUES (p_id);
    UPDATE ledger SET posted = 'Y' WHERE id = p_id;
    SAVEPOINT s1;
    COMMIT;
    EXECUTE IMMEDIATE 'DELETE FROM audit_log';
  EXCEPTION
    WHEN OTHERS THEN NULL;
  END;
  FUNCTION rate(p_total NUMBER) RETURN NUMBER IS
    v NUMBER;
  BEGIN
    SELECT tax_rate INTO v FROM rates JOIN dual ON 1 = 1;
    IF p_total > 1000 THEN RAISE_APPLICATION_ERROR(-20001, 'too big'); END IF;
    RETURN v;
  END;
END;
"""


def _finding(**overrides) -> Finding:
    values = {
        "id": "F-1",
        "changed_object": "APP.PKG",
        "object_owner": "APP",
        "object_name": "PKG",
        "object_type": "PACKAGE BODY",
        "source_database": "test",
        "change_category": "business_logic",
        "evidence": "Line 10 changed.",
        "severity": Severity.MEDIUM,
        "confidence": Confidence.HIGH,
        "status": EvidenceStatus.CONFIRMED,
        "explanation": "x",
        "recommended_action": "y",
    }
    values.update(overrides)
    return Finding(**values)


# ----------------------------------------------------------------- hashing / scanner


def test_source_hash_ignores_line_ending_style_and_finding_ids_are_stable():
    assert source_hash("a\r\nb\rc") == source_hash("a\nb\nc")
    assert source_hash("a") != source_hash("b")
    first = finding_id("run", "data", "APP.PKG")
    assert first == finding_id("run", "data", "APP.PKG")
    assert first.startswith("F-") and len(first) == 10 and first[2:].isupper()
    assert first != finding_id("run", "data", "APP.OTHER")


def test_scanner_finds_routines_statements_and_transaction_control():
    scan = scan_package(BODY)
    names = [(routine.kind, routine.name) for routine in scan.routines]
    assert names == [("PROCEDURE", "POST_INVOICE"), ("FUNCTION", "RATE")]
    post, rate = scan.routines
    writes = [(ref.operation, ref.qualified, ref.line) for ref in post.sql_refs]
    assert ("INSERT", "APP.INVOICES", 6) in writes and ("UPDATE", "LEDGER", 7) in writes
    assert not any("COMMENTED_TABLE" in ref.qualified or "AUDIT_LOG" in ref.qualified for ref in post.sql_refs)
    assert post.autonomous_lines == [3] and post.commit_lines == [9] and post.savepoint_lines == [8]
    assert post.dynamic_lines == [10]
    assert post.handler_lines == [12] and post.swallowed_lines == [12]
    assert post.transaction_sequence == ("INSERT", "UPDATE", "SAVEPOINT", "COMMIT")
    assert [ref.qualified for ref in rate.sql_refs] == ["RATES"]
    assert (18, "P_TOTAL", ">", "1000") in rate.thresholds
    assert rate.error_codes == [(18, "-20001")]
    assert scan.routine_at(7) is post and scan.routine_at(16) is rate and scan.routine_at(1) is None


def test_scanner_treats_a_package_without_routines_as_one_block():
    scan = scan_package("CREATE OR REPLACE PACKAGE APP.PKG AS\n  c_limit CONSTANT NUMBER := 5;\nEND;\n")
    assert [(routine.kind, routine.start_line, routine.end_line) for routine in scan.routines] == [("PACKAGE", 1, 3)]


def test_authid_and_error_declarations_ignore_comments():
    assert authid_clause("CREATE OR REPLACE PACKAGE APP.PKG\n  AUTHID CURRENT_USER AS\nEND;") == ("CURRENT_USER", 2)
    assert authid_clause("CREATE OR REPLACE PACKAGE APP.PKG AS -- AUTHID CURRENT_USER\nEND;") == ("DEFINER", 0)
    declared = error_declarations(
        "PACKAGE APP.PKG AS\n  e_bad EXCEPTION;\n  PRAGMA EXCEPTION_INIT(e_bad, - 20001);\n  -- e_old EXCEPTION;\nEND;"
    )
    assert declared.exceptions == {"E_BAD": 2}
    assert declared.exception_inits == {"E_BAD": ("-20001", 3)}
    assert financial_text("v_tax_amount") and not financial_text("v_counter")


# ----------------------------------------------------------------- annotate


@pytest.mark.parametrize(
    "category, attributes, expected",
    [
        ("interface", {"signature_kind": "removed", "breaking": True}, "SIG-REMOVED"),
        ("interface", {"signature_kind": "metadata_gap"}, "SIG-METADATA-GAP"),
        ("interface", {"signature_kind": "added_overload"}, "SIG-ADDED-OVERLOAD"),
        ("compilation", {"compile_scope": "dependent"}, "CMP-DEPENDENT"),
        ("coverage", {"snapshot_drift": True}, "COV-SNAPSHOT-DRIFT"),
        ("coverage", {}, "COV-METADATA-GAP"),
        ("data", {"missing_object": True}, "DATA-MISSING-OBJECT"),
        ("data", {"new_table_unverified": True}, "DATA-NEW-TABLE"),
        ("data", {}, "DATA-MODIFIED-DML"),
        ("transaction", {"partial_commit_hazard": True}, "TXN-PARTIAL-COMMIT"),
        ("transaction", {}, "TXN-SEQUENCE"),
        ("exception", {}, "EXC-HANDLER"),
        ("dynamic_sql", {}, "DYN-SQL"),
        ("business_logic", {"threshold": True}, "BIZ-THRESHOLD"),
        ("business_logic", {}, "BIZ-LOGIC"),
        ("error_contract", {"error_contract": "added"}, "ERR-ADDED"),
        ("error_contract", {"error_contract": "code_changed"}, "ERR-CODE-CHANGED"),
        ("security", {"authid_changed": True}, "SEC-AUTHID"),
        ("security", {"grant_difference": "missing_in_production"}, "SEC-GRANT-MISSING"),
        ("security", {"grant_difference": "extra_in_production"}, "SEC-GRANT-EXTRA"),
        ("format", {}, "FMT-ONLY"),
        ("dependency", {}, "OTHER-DEPENDENCY"),
    ],
)
def test_classify_assigns_rule_ids(category, attributes, expected):
    rule_id, rationale = classify(_finding(change_category=category, attributes=attributes))
    assert rule_id == expected
    if not expected.startswith("OTHER-"):
        assert rationale


def test_coordinated_signature_change_is_rated_medium_not_breaking():
    finding = _finding(change_category="interface", attributes={"signature_kind": "removed", "breaking": True, "coordinated": True})
    assert classify(finding)[1].startswith("Medium")


def test_fingerprint_ignores_run_specific_fields_and_whitespace():
    one = _finding(id="F-AAAA", evidence="Line 10   changed.")
    two = _finding(id="F-BBBB", source_database="production", evidence="Line 10 changed.", severity=Severity.HIGH)
    assert fingerprint(one) == fingerprint(two)
    assert fingerprint(one).startswith("FP-") and len(fingerprint(one)) == 19
    assert fingerprint(one) != fingerprint(_finding(evidence="Line 11 changed."))
    assert fingerprint(one) != fingerprint(_finding(object_name="OTHER", evidence="Line 10 changed."))


def test_annotate_fills_missing_fields_keeps_existing_and_skips_investigator():
    plain = _finding(change_category="dynamic_sql")
    kept = _finding(id="F-2", change_category="dynamic_sql", rule_id="CUSTOM", severity_rationale="Set by rule")
    investigator = _finding(id="F-3", origin="investigator", status=EvidenceStatus.POTENTIAL)
    annotate([plain, kept, investigator])
    assert plain.rule_id == "DYN-SQL" and plain.fingerprint.startswith("FP-") and plain.severity_rationale
    assert kept.rule_id == "CUSTOM" and kept.severity_rationale == "Set by rule"
    assert investigator.rule_id == "" and investigator.fingerprint == ""


# ----------------------------------------------------------------- transactions


def test_transaction_differences_describe_observed_changes():
    before = RoutineFacts(writes={"UPDATE": ["LEDGER"]}, reads=["RATES"], commits=[], sequence=["UPDATE"])
    after = RoutineFacts(
        writes={"INSERT": ["AUDIT"], "UPDATE": ["LEDGER"]},
        reads=["REGIONS"],
        commits=[12],
        autonomous=True,
        swallowed=[20],
        sequence=["INSERT", "UPDATE", "COMMIT"],
    )
    lines = _differences(after, before)
    assert "Now runs INSERT on AUDIT." in lines
    assert "Now reads REGIONS." in lines and "No longer reads RATES." in lines
    assert "COMMIT count changed from 0 to 1." in lines
    assert "handler that discards the error (THEN NULL) count changed from 0 to 1." in lines
    assert "PRAGMA AUTONOMOUS_TRANSACTION was added." in lines
    assert "Write and transaction-control order changed from UPDATE to INSERT → UPDATE → COMMIT." in lines
    assert _differences(before, before) == []
    assert _differences(None, before) == ["The routine exists in Production and is absent from Test."]
    assert _differences(after, None)[0] == "The routine is new in Test."
    assert "No longer runs INSERT on AUDIT." in _differences(before, after)


def test_routine_facts_deduplicate_tables():
    post = scan_package(BODY).routines[0]
    result = facts(post)
    assert result.writes == {"INSERT": ["APP.INVOICES"], "UPDATE": ["LEDGER"]}
    assert result.autonomous is True and result.sequence == ["INSERT", "UPDATE", "SAVEPOINT", "COMMIT"]


def test_build_profiles_reports_added_and_removed_routines_and_links_findings():
    test_scan = scan_package(BODY)
    prod_scan = scan_package(
        "PACKAGE BODY APP.PKG AS\n  PROCEDURE post_invoice(p_id NUMBER) IS\n  BEGIN\n    NULL;\n  END;\n"
        "  PROCEDURE legacy IS\n  BEGIN\n    DELETE FROM old_rows;\n  END;\nEND;\n"
    )
    scans = {("test", "APP", "PKG"): test_scan, ("production", "APP", "PKG"): prod_scan}
    linked = _finding(id="F-RATE", evidence="RATE now reads RATES.")
    other = _finding(id="F-OTHER", changed_object="APP.ELSE", evidence="RATE")
    profiles = build_profiles([("APP", "PKG")], scans, [], [linked, other])
    by_routine = {profile.routine: profile for profile in profiles}
    assert by_routine["RATE"].change == "added" and by_routine["RATE"].finding_ids == ["F-RATE"]
    assert by_routine["LEGACY"].change == "removed" and by_routine["LEGACY"].test is None
    assert "POST_INVOICE" not in by_routine


def test_performance_is_never_marked_evaluated():
    assessment = performance_assessment([], "")
    assert assessment.evaluated is False and assessment.plan_source == "none"
    assert "not evaluated" in NOT_EVALUATED


# ----------------------------------------------------------------- coverage


def _captured(environment, object_type, found=True, inaccessible=False, drift=False):
    return CapturedObject(
        environment=environment,
        database_id=environment,
        owner="APP",
        name="PKG",
        object_type=object_type,
        found=found,
        inaccessible=inaccessible,
        source="x" if found else "",
        sha256="a" if found else None,
        verification_sha256="a" if found else None,
        drift=drift,
        retrieved_at="t",
    )


def _coverage(objects, environments=("test", "production"), **overrides):
    snapshot = Snapshot(packages=["APP.PKG"], captured_at="t", objects=objects)
    values = {
        "packages": [("APP", "PKG")],
        "snapshot": snapshot,
        "environments": list(environments),
        "failures": {},
        "argument_gaps": set(),
        "dependency_gaps": [],
        "edges": [],
        "scans": {},
        "changed_test_lines": {},
        "skipped_system_objects": 0,
        "max_depth": 3,
        "completed_at": "t",
    }
    values.update(overrides)
    return build_coverage(**values)


def test_coverage_states_and_reasons():
    objects = [
        _captured("test", "PACKAGE", drift=True),
        _captured("production", "PACKAGE"),
        _captured("test", "PACKAGE BODY", inaccessible=True, found=False),
        _captured("production", "PACKAGE BODY"),
    ]
    summary = _coverage(objects, argument_gaps={("APP", "PKG")})
    states = {item.object_type: item for item in summary.items}
    assert states["PACKAGE"].state == "partial"
    assert "The test source changed while it was captured." in states["PACKAGE"].reasons
    assert "Argument metadata could not be read, so signatures were not compared." in states["PACKAGE"].reasons
    assert states["PACKAGE BODY"].state == "inaccessible"
    assert (summary.in_scope, summary.partial, summary.inaccessible, summary.analyzed) == (2, 1, 1, 0)


def test_coverage_marks_failures_and_missing_objects():
    objects = [_captured(env, kind, found=kind == "PACKAGE") for env in ("test", "production") for kind in ("PACKAGE", "PACKAGE BODY")]
    summary = _coverage(objects, failures={("APP", "PKG", "PACKAGE"): "Comparison failed."})
    states = {item.object_type: (item.state, item.reasons) for item in summary.items}
    assert states["PACKAGE"] == ("failed", ["Comparison failed."])
    assert states["PACKAGE BODY"][0] == "skipped"


def test_coverage_ignores_synonym_gaps_and_counts_dependency_provenance():
    objects = [_captured(env, kind) for env in ("test", "production") for kind in ("PACKAGE", "PACKAGE BODY")]
    gaps = [
        SimpleNamespace(owner="APP", name="PKG", object_type="PACKAGE BODY", environment="production"),
        SimpleNamespace(owner="APP", name="PKG", object_type="SYNONYM", environment="test"),
    ]
    edges = [SimpleNamespace(provenance=kind) for kind in ("dictionary", "dictionary", "source_scan", "unresolved")]
    scans = {("test", "APP", "PKG"): scan_package(BODY)}
    summary = _coverage(objects, dependency_gaps=gaps, edges=edges, scans=scans, changed_test_lines={("APP", "PKG"): {10}}, skipped_system_objects=4)
    body = next(item for item in summary.items if item.object_type == "PACKAGE BODY")
    assert body.reasons == ["Dependency metadata could not be read in production."]
    dependency = summary.dependency
    assert (dependency.edges, dependency.verified_edges, dependency.inferred_edges, dependency.unresolved, dependency.gaps) == (4, 2, 1, 1, 1)
    assert dependency.skipped_system_objects == 4 and dependency.max_depth == 3
    assert [(ref.routine, ref.line, ref.changed) for ref in summary.dynamic_sql] == [("POST_INVOICE", 10, True)]


def test_edge_records_label_dictionary_scan_and_unresolved_edges():
    graph = [
        SimpleNamespace(direction="referenced", owner="APP", name="PKG", object_type="PACKAGE BODY", referenced_owner="APP",
                        referenced_name="LEDGER", referenced_type="TABLE", depth=1, database="test"),
        SimpleNamespace(direction="referenced", owner="APP", name="PKG", object_type="PACKAGE BODY", referenced_owner="APP",
                        referenced_name="LEDGER", referenced_type="TABLE", depth=1, database="test"),
        SimpleNamespace(direction="referenced", owner="APP", name="PKG", object_type="PACKAGE BODY", referenced_owner="PUBLIC",
                        referenced_name="REMOTE", referenced_type="SYNONYM", depth=1, database="test"),
    ]
    records = edge_records(graph, [("APP", "PKG")], {("test", "APP", "PKG"): scan_package(BODY)})
    by_name = {(record.referenced_name, record.provenance) for record in records}
    assert ("LEDGER", "dictionary") in by_name and ("REMOTE", "unresolved") in by_name
    assert ("INVOICES", "source_scan") in by_name and ("RATES", "source_scan") in by_name
    assert ("LEDGER", "source_scan") not in by_name
    assert ("Dynamic SQL in POST_INVOICE", "unresolved") in by_name
    assert sum(1 for record in records if record.referenced_name == "LEDGER") == 1


# ----------------------------------------------------------------- compilation


def _gateway(statuses=None, errors=None, inaccessible=()):
    catalog = Catalog()
    for owner, name, kind in (statuses or {}):
        catalog.sources[(owner, name, kind)] = "x"
    catalog.object_status.update(statuses or {})
    catalog.compile_errors.update(errors or {})
    catalog.inaccessible_status.update(inaccessible)
    return FixtureGateway("test", "db-test", "TEST", catalog)


def test_compilation_reports_gateways_without_readers_as_not_evaluated():
    summary = collect_compilation({"test": object()}, [("APP", "PKG")], {})
    assert summary.evaluated is False
    assert "does not expose object status" in summary.reason
    assert compilation_findings("run", summary, {}) == []


def test_compilation_access_error_limits_scope_and_outage_propagates():
    class Denied:
        def fetch_object_status(self, owner, name):
            raise OracleAccessError("denied")

        def fetch_compile_errors(self, owner, name):
            return ErrorFetch()

    class Down(Denied):
        def fetch_object_status(self, owner, name):
            raise OracleUnavailable("down")

    healthy = _gateway({("APP", "PKG", "PACKAGE"): "VALID"})
    summary = collect_compilation({"test": healthy, "production": Denied()}, [("APP", "PKG")], {})
    assert summary.evaluated is True and summary.environments == ["test"]
    assert summary.reason.startswith("Object validity was evaluated for test only.")
    with pytest.raises(OracleUnavailable):
        collect_compilation({"test": Down()}, [("APP", "PKG")], {})


def test_inaccessible_in_scope_status_marks_environment_unavailable():
    summary = collect_compilation({"test": _gateway(inaccessible={("APP", "PKG")})}, [("APP", "PKG")], {})
    assert summary.evaluated is False and summary.unavailable == ["test: ALL_OBJECTS or ALL_ERRORS could not be read."]


def test_dependent_findings_distinguish_new_and_preexisting_invalid_objects():
    test = _gateway(
        {
            ("APP", "PKG", "PACKAGE BODY"): "VALID",
            ("APP", "NEW_BREAK", "PROCEDURE"): "INVALID",
            ("APP", "OLD_BREAK", "PROCEDURE"): "INVALID",
        },
        errors={("APP", "NEW_BREAK"): [("PROCEDURE", 4, 7, "PLS-00306: wrong number", "ERROR"), ("PROCEDURE", 9, 1, "unused", "WARNING")]},
    )
    production = _gateway(
        {
            ("APP", "PKG", "PACKAGE BODY"): "VALID",
            ("APP", "NEW_BREAK", "PROCEDURE"): "VALID",
            ("APP", "OLD_BREAK", "PROCEDURE"): "INVALID",
        }
    )
    dependents = [("APP", "NEW_BREAK"), ("APP", "OLD_BREAK"), ("APP", "PKG")]
    summary = collect_compilation({"test": test, "production": production}, [("APP", "PKG")], {"test": dependents, "production": dependents})
    findings = {finding.object_name: finding for finding in compilation_findings("run", summary, {"test": "TEST", "production": "PROD"})}
    assert set(findings) == {"NEW_BREAK", "OLD_BREAK"}
    new, old = findings["NEW_BREAK"], findings["OLD_BREAK"]
    assert new.severity == Severity.HIGH and new.attributes["compile_scope"] == "dependent" and new.attributes["compile_invalid"] is True
    assert new.changed_lines_test == [4] and "PLS-00306" in new.evidence and "unused" not in new.evidence
    assert old.severity == Severity.MEDIUM and old.attributes == {"compile_scope": "dependent_preexisting", "compile_invalid": False, "compile_status": "INVALID"}
    assert sum(1 for item in summary.objects if item.role == "dependent" and item.name == "PKG") == 0


def test_production_only_invalid_package_is_a_medium_finding():
    test = _gateway({("APP", "PKG", "PACKAGE BODY"): "VALID"})
    production = _gateway({("APP", "PKG", "PACKAGE BODY"): "INVALID"})
    summary = collect_compilation({"test": test, "production": production}, [("APP", "PKG")], {})
    [finding] = compilation_findings("run", summary, {"production": "PROD"})
    assert finding.source_database == "production" and finding.severity == Severity.MEDIUM
    assert finding.attributes["compile_scope"] == "production" and "PROD" in finding.evidence


def test_dependent_status_reads_are_capped():
    calls = []

    class Counting:
        def fetch_object_status(self, owner, name):
            calls.append(name)
            return StatusFetch(objects=[("PROCEDURE", "VALID", None)])

        def fetch_compile_errors(self, owner, name):
            return ErrorFetch()

    dependents = [("APP", f"DEP_{index}") for index in range(MAX_DEPENDENTS + 10)]
    collect_compilation({"test": Counting()}, [("APP", "PKG")], {"test": dependents})
    assert len(calls) == 1 + MAX_DEPENDENTS


# ----------------------------------------------------------------- diagram


def test_dependency_diagram_escapes_names_and_limits_columns():
    rows = [
        DependencyRow(direction="caller", object_type="PROCEDURE", object_name=f"<C{index}>", owner="APP", usage="", impact="", depth=1, database="test")
        for index in range(9)
    ]
    svg = dependency_diagram(["APP.<script>"], rows)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    assert "&lt;C5&gt;" in svg and "&lt;C6&gt;" not in svg
