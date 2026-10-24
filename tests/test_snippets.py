from oracle_impact.analysis.test_snippets import snippets_for
from oracle_impact.models.domain import CapturedObject, Snapshot
from oracle_impact.models.enums import EvidenceStatus, Severity
from oracle_impact.models.report import Finding


def _object(environment: str, source: str) -> CapturedObject:
    return CapturedObject(
        environment=environment,
        database_id="fixture",
        owner="IMPACT_LAB",
        name="LEDGER_PKG",
        object_type="PACKAGE",
        found=True,
        source=source,
        retrieved_at="2026-10-22T00:00:00+00:00",
    )


def test_interface_snippet_includes_the_new_argument_and_the_old_call():
    snapshot = Snapshot(
        packages=["IMPACT_LAB.LEDGER_PKG"],
        captured_at="2026-10-22T00:00:00+00:00",
        objects=[
            _object(
                "production",
                "PACKAGE ledger_pkg AS PROCEDURE close_ledger(p_account_id IN NUMBER); END;",
            ),
            _object(
                "test",
                "PACKAGE ledger_pkg AS PROCEDURE close_ledger(p_account_id IN NUMBER, p_approve IN VARCHAR2); END;",
            ),
        ],
    )
    finding = Finding(
        id="F-1",
        changed_object="IMPACT_LAB.LEDGER_PKG",
        object_owner="IMPACT_LAB",
        object_name="LEDGER_PKG",
        object_type="PACKAGE",
        source_database="test",
        change_category="interface",
        affected_object="IMPACT_LAB.LEDGER_API",
        dependency_path=["IMPACT_LAB.LEDGER_API -> IMPACT_LAB.LEDGER_PKG.CLOSE_LEDGER"],
        evidence="CLOSE_LEDGER gained parameter P_APPROVE.",
        severity=Severity.CRITICAL,
        confidence="high",
        status=EvidenceStatus.CONFIRMED,
        explanation="CLOSE_LEDGER now requires P_APPROVE. IMPACT_LAB.LEDGER_API still uses the old arguments.",
        recommended_action="Update every caller.",
        proposed_tests=["Call IMPACT_LAB.LEDGER_API with the arguments Production uses today."],
    )
    scripts = snippets_for(finding, snapshot)
    combined = "\n".join(item["sql"] for item in scripts)
    assert "Run in Test" in combined
    assert "does not run this script" in combined
    assert "P_APPROVE => 'X'" in combined
    assert "P_ACCOUNT_ID => 1" in combined
    assert "ALTER PACKAGE IMPACT_LAB.LEDGER_API COMPILE BODY;" in combined
    assert "EXECUTE IMMEDIATE" not in combined


def test_data_snippet_selects_the_affected_table():
    snapshot = Snapshot(
        packages=["IMPACT_LAB.LEDGER_PKG"],
        captured_at="2026-10-22T00:00:00+00:00",
        objects=[
            _object(
                "test",
                "PACKAGE ledger_pkg AS PROCEDURE close_ledger(p_account_id IN NUMBER, p_approve IN VARCHAR2); END;",
            )
        ],
    )
    finding = Finding(
        id="F-2",
        changed_object="IMPACT_LAB.LEDGER_PKG",
        object_owner="IMPACT_LAB",
        object_name="LEDGER_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="data",
        affected_object="IMPACT_LAB.LEDGER_AUDIT",
        evidence="INSERT references IMPACT_LAB.LEDGER_AUDIT.",
        severity=Severity.CRITICAL,
        confidence="high",
        status=EvidenceStatus.CONFIRMED,
        explanation="CLOSE_LEDGER inserts into IMPACT_LAB.LEDGER_AUDIT, but Production does not have that object.",
        recommended_action="Review the table.",
        proposed_tests=["Run CLOSE_LEDGER and look at IMPACT_LAB.LEDGER_AUDIT."],
    )
    scripts = snippets_for(finding, snapshot)
    select = next(item for item in scripts if "LEDGER_AUDIT" in item["title"])
    assert "FROM IMPACT_LAB.LEDGER_AUDIT" in select["sql"]
    assert "ROWNUM <= 20" in select["sql"]
    comments = " ".join(line[3:] for line in select["sql"].splitlines() if line.startswith("-- "))
    assert "expected to fail in Test" in comments
    assert "Do not run this in Production" in comments
    assert all(len(line) <= 80 for line in select["sql"].splitlines())
