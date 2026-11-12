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


def test_interface_snippet_callers_are_only_compiled_when_their_type_is_captured():
    """The caller was not captured, so the script must not guess its object type."""
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
    assert "Run in Test only" in combined
    assert "Do not run it in Production" in combined
    assert "P_ACCOUNT_ID => 1," in combined
    assert "P_APPROVE => 'X'" in combined
    assert "ALTER PACKAGE IMPACT_LAB.LEDGER_API" not in combined
    assert "was not captured" in combined
    assert "EXECUTE IMMEDIATE" not in combined
    old_call = next(item for item in scripts if "Production argument list" in item["title"])
    assert "P_APPROVE =>" not in old_call["sql"]
    assert "P_ACCOUNT_ID => 1" in old_call["sql"]
    assert "PLS-00306" in old_call["sql"]
    assert "It does not stop" not in old_call["sql"]


def test_caller_is_compiled_when_the_snapshot_proves_it_is_a_package():
    snapshot = Snapshot(
        packages=["IMPACT_LAB.LEDGER_PKG", "IMPACT_LAB.LEDGER_API"],
        captured_at="2026-10-22T00:00:00+00:00",
        objects=[
            _object(
                "test",
                "PACKAGE ledger_pkg AS PROCEDURE close_ledger(p_account_id IN NUMBER, p_approve IN VARCHAR2); END;",
            ),
            CapturedObject(
                environment="test",
                database_id="fixture",
                owner="IMPACT_LAB",
                name="LEDGER_API",
                object_type="PACKAGE",
                found=True,
                source="PACKAGE ledger_api AS PROCEDURE post(p_account_id IN NUMBER, p_approve IN VARCHAR2); END;",
                retrieved_at="2026-10-22T00:00:00+00:00",
            ),
        ],
    )
    finding = Finding(
        id="F-3",
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
        explanation="CLOSE_LEDGER now requires P_APPROVE.",
        recommended_action="Update every caller.",
        proposed_tests=["Call the caller."],
    )
    combined = "\n".join(item["sql"] for item in snippets_for(finding, snapshot))
    assert "ALTER PACKAGE IMPACT_LAB.LEDGER_API COMPILE BODY;" in combined
    assert "IMPACT_LAB.LEDGER_API.POST(" in combined


def test_function_is_called_as_a_value_and_its_result_is_printed():
    """A function used as a statement raises PLS-00221, so it must be assigned."""
    snapshot = Snapshot(
        packages=["IMPACT_LAB.STATUS_PKG"],
        captured_at="2026-10-22T00:00:00+00:00",
        objects=[
            CapturedObject(
                environment="test",
                database_id="fixture",
                owner="IMPACT_LAB",
                name="STATUS_PKG",
                object_type="PACKAGE",
                found=True,
                source=(
                    "PACKAGE status_pkg AS "
                    "FUNCTION status_of(p_id IN NUMBER) RETURN VARCHAR2; END;"
                ),
                retrieved_at="2026-10-22T00:00:00+00:00",
            )
        ],
    )
    finding = Finding(
        id="F-4",
        changed_object="IMPACT_LAB.STATUS_PKG",
        object_owner="IMPACT_LAB",
        object_name="STATUS_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="business_logic",
        affected_object="IMPACT_LAB.STATUS_PKG",
        evidence="STATUS_OF changed.",
        severity=Severity.MEDIUM,
        confidence="high",
        status=EvidenceStatus.POTENTIAL,
        explanation="STATUS_OF changed.",
        recommended_action="Test the function.",
        proposed_tests=["Run STATUS_OF with a real id."],
    )
    [script] = snippets_for(finding, snapshot)
    sql = script["sql"]
    assert "v_result VARCHAR2(4000);" in sql
    assert "v_result := IMPACT_LAB.STATUS_PKG.STATUS_OF(" in sql
    assert "DBMS_OUTPUT.PUT_LINE('Returned: ' || v_result);" in sql
    assert "EXCEPTION" in sql and "The call raised: " in sql


def test_out_parameters_are_declared_and_passed():
    snapshot = Snapshot(
        packages=["IMPACT_LAB.STATUS_PKG"],
        captured_at="2026-10-22T00:00:00+00:00",
        objects=[
            CapturedObject(
                environment="test",
                database_id="fixture",
                owner="IMPACT_LAB",
                name="STATUS_PKG",
                object_type="PACKAGE",
                found=True,
                source="PACKAGE status_pkg AS PROCEDURE read_status(p_id IN NUMBER, p_status OUT VARCHAR2); END;",
                retrieved_at="2026-10-22T00:00:00+00:00",
            )
        ],
    )
    finding = Finding(
        id="F-5",
        changed_object="IMPACT_LAB.STATUS_PKG",
        object_owner="IMPACT_LAB",
        object_name="STATUS_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="business_logic",
        affected_object="IMPACT_LAB.STATUS_PKG",
        evidence="READ_STATUS changed.",
        severity=Severity.MEDIUM,
        confidence="high",
        status=EvidenceStatus.POTENTIAL,
        explanation="READ_STATUS changed.",
        recommended_action="Test the procedure.",
        proposed_tests=["Run READ_STATUS with a real id."],
    )
    [script] = snippets_for(finding, snapshot)
    assert "v_p_status VARCHAR2;" in script["sql"]
    assert "P_STATUS => v_p_status" in script["sql"]
    assert "DBMS_OUTPUT.PUT_LINE('P_STATUS = ' || v_p_status);" in script["sql"]


def test_in_out_parameters_are_passed_once():
    snapshot = Snapshot(
        packages=["IMPACT_LAB.STATUS_PKG"],
        captured_at="2026-10-22T00:00:00+00:00",
        objects=[
            CapturedObject(
                environment="test",
                database_id="fixture",
                owner="IMPACT_LAB",
                name="STATUS_PKG",
                object_type="PACKAGE",
                found=True,
                source="PACKAGE status_pkg AS PROCEDURE bump(p_id IN OUT NUMBER); END;",
                retrieved_at="2026-10-22T00:00:00+00:00",
            )
        ],
    )
    finding = Finding(
        id="F-6",
        changed_object="IMPACT_LAB.STATUS_PKG",
        object_owner="IMPACT_LAB",
        object_name="STATUS_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="business_logic",
        affected_object="IMPACT_LAB.STATUS_PKG",
        evidence="BUMP changed.",
        severity=Severity.MEDIUM,
        confidence="high",
        status=EvidenceStatus.POTENTIAL,
        explanation="BUMP changed.",
        recommended_action="Test the procedure.",
        proposed_tests=["Run BUMP."],
    )
    [script] = snippets_for(finding, snapshot)
    assert script["sql"].count("P_ID => v_p_id") == 1


def test_a_value_the_body_reads_is_selected_instead_of_guessed():
    """`P_CODE => 'X'` matched no row and stopped with ORA-01403."""
    snapshot = Snapshot(
        packages=["IMPACT_LAB.DISPATCH_PKG"],
        captured_at="2026-10-22T00:00:00+00:00",
        objects=[
            CapturedObject(
                environment="test",
                database_id="fixture",
                owner="IMPACT_LAB",
                name="DISPATCH_PKG",
                object_type="PACKAGE",
                found=True,
                source="PACKAGE dispatch_pkg AS PROCEDURE send(p_code IN VARCHAR2); END;",
                retrieved_at="2026-10-22T00:00:00+00:00",
            ),
            CapturedObject(
                environment="test",
                database_id="fixture",
                owner="IMPACT_LAB",
                name="DISPATCH_PKG",
                object_type="PACKAGE BODY",
                found=True,
                source=(
                    "PACKAGE BODY dispatch_pkg AS\n"
                    "  PROCEDURE send(p_code IN VARCHAR2) IS\n"
                    "    v_quote NUMBER;\n"
                    "  BEGIN\n"
                    "    SELECT quote INTO v_quote FROM quote_book WHERE code = p_code;\n"
                    "  END;\n"
                    "END;"
                ),
                retrieved_at="2026-10-22T00:00:00+00:00",
            ),
        ],
    )
    finding = Finding(
        id="F-6",
        changed_object="IMPACT_LAB.DISPATCH_PKG",
        object_owner="IMPACT_LAB",
        object_name="DISPATCH_PKG",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="business_logic",
        affected_object="IMPACT_LAB.DISPATCH_PKG",
        evidence="SEND changed.",
        severity=Severity.MEDIUM,
        confidence="high",
        status=EvidenceStatus.POTENTIAL,
        explanation="SEND changed.",
        recommended_action="Test SEND.",
        proposed_tests=["Run SEND with a code that exists."],
    )
    [script] = snippets_for(finding, snapshot)
    sql = script["sql"]
    assert "v_p_code VARCHAR2;" in sql
    assert "SELECT CODE INTO v_p_code" in sql
    assert "FROM IMPACT_LAB.QUOTE_BOOK" in sql
    assert "P_CODE => v_p_code" in sql
    assert "P_CODE => 'X'" not in sql
    assert "WHERE CODE IS NOT NULL" in sql


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
    assert "expected to fail in Test" not in comments
    assert "was not found in Production" in comments
    assert "runs in Test" in comments
    assert all(len(line) <= 80 for line in select["sql"].splitlines())
