from oracle_impact.analysis.action_scripts import action_scripts_for, apply_action_scripts, known_types
from oracle_impact.models.domain import CapturedObject, Snapshot
from oracle_impact.models.enums import EvidenceStatus, Severity
from oracle_impact.models.report import Finding


def _finding(**overrides) -> Finding:
    values = {
        "id": "F-1",
        "changed_object": "APP.PKG_DEMO",
        "object_owner": "APP",
        "object_name": "PKG_DEMO",
        "object_type": "PACKAGE BODY",
        "source_database": "test",
        "change_category": "dependency",
        "evidence": "evidence",
        "severity": Severity.CRITICAL,
        "confidence": "high",
        "status": EvidenceStatus.CONFIRMED,
        "explanation": "explanation",
        "recommended_action": "action",
    }
    values.update(overrides)
    return Finding(**values)


def _code(script: dict) -> list[str]:
    return [line for line in script["sql"].splitlines() if line and not line.startswith("--")]


def test_missing_grant_becomes_grant_statements_for_production():
    finding = _finding(
        change_category="security",
        object_type="PACKAGE",
        attributes={
            "grant_difference": "missing_in_production",
            "grants": [{"grantee": "APP_USER", "privilege": "EXECUTE"}, {"grantee": "x; DROP TABLE t", "privilege": "EXECUTE"}],
        },
    )
    [script] = action_scripts_for(finding)
    assert script["environment"] == "production"
    assert "GRANT EXECUTE ON APP.PKG_DEMO TO APP_USER;" in _code(script)
    assert "DROP" not in script["sql"]
    assert "table_schema = 'APP'" in script["sql"]
    assert script["sql"].startswith("-- Run in Production (F-1). This application does not run this script.")


def test_grant_without_a_valid_entry_gets_no_script():
    finding = _finding(attributes={"grant_difference": "missing_in_test", "grants": [{"grantee": "APP_USER", "privilege": "EXECUTE; --"}]})
    assert action_scripts_for(finding) == []


def test_missing_sequence_extracts_ddl_from_test_then_checks_production():
    finding = _finding(affected_object="APP.ID_SEQ", attributes={"missing_object": True, "missing_type": "SEQUENCE"})
    extract, verify = action_scripts_for(finding)
    assert extract["environment"] == "test" and verify["environment"] == "production"
    assert "DBMS_METADATA.GET_DDL(REPLACE(object_type, ' ', '_'), object_name, owner)" in extract["sql"]
    assert "AND object_type IN ('SEQUENCE');" in extract["sql"]
    assert "START WITH" in extract["sql"]
    assert "AND object_type IN ('SEQUENCE');" in verify["sql"]
    assert not any(line.upper().startswith(("CREATE", "DROP", "ALTER")) for line in _code(extract) + _code(verify))
    assert all(len(line) <= 100 for line in extract["sql"].splitlines())


def test_missing_table_without_a_recorded_type_looks_for_a_table_or_view():
    finding = _finding(change_category="data", affected_object="APP.LEDGER_HISTORY", attributes={"missing_object": True})
    extract, _verify = action_scripts_for(finding)
    assert "AND object_type IN ('TABLE', 'VIEW');" in extract["sql"]
    assert "empty table" in extract["sql"]


def test_unsafe_affected_object_gets_no_script():
    finding = _finding(affected_object="APP.X'||'Y", attributes={"missing_object": True, "missing_type": "TABLE"})
    assert action_scripts_for(finding) == []


def test_unverified_dependency_gets_a_read_only_existence_check():
    finding = _finding(
        change_category="coverage",
        affected_object="APP.FEE_FOR",
        status=EvidenceStatus.UNKNOWN,
        attributes={"dependency_unverified": True, "dependency_type": "FUNCTION"},
    )
    [script] = action_scripts_for(finding)
    assert _code(script)[0].startswith("SELECT object_type, status")
    assert "AND object_type IN ('FUNCTION');" in script["sql"]


def test_invalid_body_in_test_is_recompiled_and_errors_are_read():
    finding = _finding(change_category="compilation", object_name="PKG_LEGACY", attributes={"compile_scope": "dependent", "compile_invalid": True})
    [script] = action_scripts_for(finding)
    assert script["environment"] == "test"
    assert "ALTER PACKAGE APP.PKG_LEGACY COMPILE BODY;" in _code(script)
    assert "FROM all_errors" in script["sql"] and "AND type = 'PACKAGE BODY'" in script["sql"]
    assert "Fix the source first" in script["sql"]


def test_invalid_object_in_production_is_only_inspected():
    finding = _finding(change_category="compilation", source_database="production", attributes={"compile_scope": "production"})
    [script] = action_scripts_for(finding)
    assert script["environment"] == "production"
    assert not any(line.startswith("ALTER") for line in _code(script))


def test_interface_callers_compile_only_when_their_type_was_captured():
    snapshot = Snapshot(
        packages=["APP.PKG_DEMO"],
        captured_at="2026-10-27T00:00:00+00:00",
        objects=[
            CapturedObject(
                environment="test", database_id="fixture", owner="APP", name="PKG_CALLER", object_type="PACKAGE BODY",
                found=True, source="PACKAGE BODY pkg_caller AS END;", retrieved_at="2026-10-27T00:00:00+00:00",
            )
        ],
    )
    finding = _finding(change_category="interface", object_type="PACKAGE", attributes={"breaking": True, "callers": ["APP.PKG_CALLER", "APP.RUN_JOB"]})
    [script] = action_scripts_for(finding, known_types([], snapshot))
    assert _code(script)[0] == "ALTER PACKAGE APP.PKG_CALLER COMPILE BODY;"
    assert not any("RUN_JOB COMPILE" in line for line in _code(script))
    assert "APP.RUN_JOB was not captured" in " ".join(line[3:] for line in script["sql"].splitlines() if line.startswith("-- "))
    assert "('APP', 'PKG_CALLER'), ('APP', 'RUN_JOB')" in script["sql"]


def test_review_actions_and_investigator_notes_get_no_script():
    review = _finding(change_category="error_contract", attributes={"error_contract": "removed"})
    note = _finding(id="AI-1", origin="investigator", affected_object="APP.T", attributes={"missing_object": True, "missing_type": "TABLE"})
    apply_action_scripts([review, note])
    assert review.attributes["action_scripts"] == []
    assert "action_scripts" not in note.attributes
