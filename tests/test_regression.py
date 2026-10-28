"""Regression scenarios for the deterministic engine."""

import json

from oracle_impact.infrastructure.llm.investigator import Investigator
from oracle_impact.infrastructure.llm.mock import ScriptedProvider
from oracle_impact.infrastructure.llm.provider import ModelResponse
from oracle_impact.infrastructure.oracle.gateway import Catalog
from oracle_impact.models.enums import Recommendation
from tests.support import argument, package, run_catalogs, table


def _pair(prod_body: str, test_body: str, prod_args=None, test_args=None, prod_spec: str | None = None, test_spec: str | None = None):
    prod_args = prod_args if prod_args is not None else [argument("RUN", "P_ID", 1, "NUMBER")]
    test_args = test_args if test_args is not None else prod_args
    prod_spec = prod_spec or "PACKAGE pkg_demo AS PROCEDURE run(p_id IN NUMBER); END;\n"
    test_spec = test_spec or prod_spec
    test_catalog, prod_catalog = Catalog(), Catalog()
    package(test_catalog, "APP", "PKG_DEMO", test_spec, test_body, test_args)
    package(prod_catalog, "APP", "PKG_DEMO", prod_spec, prod_body, prod_args)
    table(test_catalog, "APP", "ACCOUNT", trigger="TRG_ACCOUNT")
    table(prod_catalog, "APP", "ACCOUNT", trigger="TRG_ACCOUNT")
    return test_catalog, prod_catalog


def _expect(tmp_path, test_catalog, prod_catalog, recommendation, categories=(), unknown=None, evidence=None, no_breaking=False, packages=None):
    report = run_catalogs(tmp_path, packages or ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    assert report.recommendation == recommendation
    found = {finding.change_category for finding in report.findings}
    for category in categories:
        assert category in found
    if unknown:
        assert any(finding.status.value == "unknown" and finding.change_category == unknown for finding in report.findings)
    if evidence:
        assert evidence in "\n".join(finding.evidence for finding in report.findings)
    if no_breaking:
        assert not any(finding.attributes.get("breaking") for finding in report.findings)
    return report


def test_mandatory_parameter_breaks_external_caller(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER, p_mode IN VARCHAR2) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(
        prod,
        test,
        [argument("RUN", "P_ID", 1, "NUMBER")],
        [argument("RUN", "P_ID", 1, "NUMBER"), argument("RUN", "P_MODE", 2, "VARCHAR2")],
        "PACKAGE pkg_demo AS PROCEDURE run(p_id IN NUMBER); END;\n",
        "PACKAGE pkg_demo AS PROCEDURE run(p_id IN NUMBER, p_mode IN VARCHAR2); END;\n",
    )
    package(prod_catalog, "APP", "PKG_OTHER", "PACKAGE pkg_other AS PROCEDURE go; END;\n", "PACKAGE BODY pkg_other AS PROCEDURE go IS BEGIN pkg_demo.run(1); END; END;\n", [])
    prod_catalog.add_dependency(("APP", "PKG_OTHER", "PACKAGE BODY"), ("APP", "PKG_DEMO", "PACKAGE"))
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("interface",))


def test_optional_parameter_stays_eligible(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER, p_flag IN BOOLEAN DEFAULT FALSE) IS BEGIN IF p_flag THEN NULL; END IF; END; END;\n"
    test_catalog, prod_catalog = _pair(
        prod,
        test,
        [argument("RUN", "P_ID", 1, "NUMBER")],
        [argument("RUN", "P_ID", 1, "NUMBER"), argument("RUN", "P_FLAG", 2, "BOOLEAN", defaulted=True)],
    )
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.ELIGIBLE_FOR_REVIEW, ("interface",))


def test_validation_threshold_requires_investigation(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN IF p_limit > 100 THEN NULL; END IF; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN IF p_limit > 500 THEN NULL; END IF; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("business_logic",))


def test_financial_calculation_requires_investigation(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN v_tax := v_amount * 0.05; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN v_tax := v_amount * 0.08; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    report = _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("business_logic",))
    assert any(finding.attributes.get("financial") for finding in report.findings)


def test_modified_update_names_triggers(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN UPDATE account SET status = 'OPEN' WHERE id = p_id; COMMIT; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN UPDATE account SET status = 'HOLD' WHERE id = p_id; COMMIT; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("data",), evidence="TRG_ACCOUNT")


def test_new_missing_table_is_not_recommended(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN INSERT INTO app.ledger_history (id) VALUES (p_id); END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    report = _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("data",))
    [missing] = [finding for finding in report.findings if finding.attributes.get("missing_object")]
    assert missing.recommended_action.startswith("Deploy APP.LEDGER_HISTORY to Production before the package")
    [blocker] = [item for item in report.policy.blockers if item.rule_id == "BLK-MISSING-OBJECT"]
    assert blocker.required_action == missing.recommended_action


def test_new_table_that_exists_requires_investigation(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN INSERT INTO app.ledger_history (id) VALUES (p_id); COMMIT; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    table(test_catalog, "APP", "LEDGER_HISTORY")
    table(prod_catalog, "APP", "LEDGER_HISTORY")
    report = _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("data",))
    assert not any(finding.attributes.get("missing_object") for finding in report.findings)


def test_moved_commit_is_not_recommended(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN UPDATE account SET status = 'A' WHERE id = p_id; UPDATE account SET amount = 1 WHERE id = p_id; COMMIT; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN UPDATE account SET status = 'A' WHERE id = p_id; COMMIT; UPDATE account SET amount = 1 WHERE id = p_id; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    report = _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("transaction",))
    assert any(finding.attributes.get("partial_commit_hazard") for finding in report.findings)


def test_swallowed_exception_requires_investigation(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN RAISE; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("exception",))


def test_autonomous_transaction_is_not_recommended(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN UPDATE account SET status = 'A' WHERE id = p_id; COMMIT; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS PRAGMA AUTONOMOUS_TRANSACTION; BEGIN UPDATE account SET status = 'A' WHERE id = p_id; COMMIT; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("transaction",))


def test_dynamic_sql_is_unknown(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN EXECUTE IMMEDIATE v_sql; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("dynamic_sql",), unknown="dynamic_sql")


def test_coordinated_change_is_not_a_false_break(tmp_path):
    prod_body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER, p_force IN VARCHAR2) IS BEGIN NULL; END; END;\n"
    caller_prod = "PACKAGE BODY pkg_caller AS PROCEDURE go IS BEGIN pkg_demo.run(1); END; END;\n"
    caller_test = "PACKAGE BODY pkg_caller AS PROCEDURE go IS BEGIN pkg_demo.run(1, p_force => 'Y'); END; END;\n"
    test_catalog, prod_catalog = _pair(
        prod_body,
        test_body,
        [argument("RUN", "P_ID", 1, "NUMBER")],
        [argument("RUN", "P_ID", 1, "NUMBER"), argument("RUN", "P_FORCE", 2, "VARCHAR2")],
    )
    package(test_catalog, "APP", "PKG_CALLER", "PACKAGE pkg_caller AS PROCEDURE go; END;\n", caller_test, [])
    package(prod_catalog, "APP", "PKG_CALLER", "PACKAGE pkg_caller AS PROCEDURE go; END;\n", caller_prod, [])
    prod_catalog.add_dependency(("APP", "PKG_CALLER", "PACKAGE BODY"), ("APP", "PKG_DEMO", "PACKAGE"))
    test_catalog.add_dependency(("APP", "PKG_CALLER", "PACKAGE BODY"), ("APP", "PKG_DEMO", "PACKAGE"))
    report = _expect(
        tmp_path,
        test_catalog,
        prod_catalog,
        Recommendation.REQUIRES_INVESTIGATION,
        packages=["APP.PKG_DEMO", "APP.PKG_CALLER"],
        no_breaking=True,
    )
    assert report.recommendation != Recommendation.NOT_RECOMMENDED


def test_inaccessible_dependency_is_unknown(tmp_path):
    body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(body, body)
    prod_catalog.inaccessible_dependencies.add(("APP", "PKG_DEMO", "PACKAGE BODY"))
    prod_catalog.inaccessible_dependencies.add(("APP", "PKG_DEMO", "PACKAGE"))
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("coverage",), unknown="coverage")


def test_added_overload_requires_investigation(tmp_path):
    body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    prod_args = [argument("RUN", "P_ID", 1, "NUMBER")]
    test_args = [
        argument("RUN", "P_ID", 1, "NUMBER", overload="1"),
        argument("RUN", "P_ID", 1, "NUMBER", overload="2"),
        argument("RUN", "P_FLAG", 2, "BOOLEAN", defaulted=True, overload="2"),
    ]
    test_catalog, prod_catalog = _pair(body, body, prod_args, test_args)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("interface",), no_breaking=True)


def test_new_overload_body_is_not_compared_with_the_original_overload(tmp_path):
    prod = (
        "PACKAGE BODY pkg_demo AS\n"
        "  PROCEDURE run(p_id IN NUMBER) IS\n"
        "  BEGIN\n"
        "    UPDATE account SET status = 'A' WHERE id = p_id;\n"
        "  END run;\n"
        "END;\n"
    )
    test = prod.replace(
        "END;\n",
        "  PROCEDURE run(p_id IN VARCHAR2) IS\n  BEGIN\n    run(TO_NUMBER(p_id));\n  END run;\nEND;\n",
    )
    prod_args = [argument("RUN", "P_ID", 1, "NUMBER")]
    test_args = [argument("RUN", "P_ID", 1, "NUMBER", overload="1"), argument("RUN", "P_ID", 1, "VARCHAR2", overload="2")]
    test_catalog, prod_catalog = _pair(prod, test, prod_args, test_args)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    assert report.recommendation == Recommendation.REQUIRES_INVESTIGATION
    assert not [finding for finding in report.findings if finding.change_category in {"transaction", "data"}]


def test_removed_routine_is_not_recommended(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; PROCEDURE close IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    prod_args = [argument("RUN", "P_ID", 1, "NUMBER"), argument("CLOSE", "P_UNUSED", 1, "NUMBER")]
    test_args = [argument("RUN", "P_ID", 1, "NUMBER")]
    test_catalog, prod_catalog = _pair(prod, test, prod_args, test_args)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("interface",))


def test_unresolved_synonym_is_unknown(tmp_path):
    body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(body, body)
    prod_catalog.add_dependency(("APP", "PKG_DEMO", "PACKAGE BODY"), ("APP", "CUSTOMER_V", "SYNONYM"))
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("coverage",), unknown="coverage")


def test_snapshot_drift_blocks_eligibility(tmp_path):
    body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(body, body)
    test_catalog.drift.add(("APP", "PKG_DEMO", "PACKAGE BODY"))
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("coverage",), unknown="coverage")


def test_formatting_only_change_is_eligible(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS\n  PROCEDURE run(p_id IN NUMBER) IS\n  BEGIN\n    NULL;\n  END;\nEND;\n"
    test = "PACKAGE BODY pkg_demo AS -- formatted\n\nPROCEDURE run(p_id IN NUMBER) IS\nBEGIN\nNULL;\nEND;\nEND;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    report = _expect(tmp_path, test_catalog, prod_catalog, Recommendation.ELIGIBLE_FOR_REVIEW, ("format",))
    assert all(finding.severity.value in {"low", "informational"} for finding in report.findings)


def test_removed_parameter_is_not_recommended(tmp_path):
    prod_args = [argument("RUN", "P_ID", 1, "NUMBER"), argument("RUN", "P_MODE", 2, "VARCHAR2")]
    test_args = [argument("RUN", "P_ID", 1, "NUMBER")]
    body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(body, body, prod_args, test_args)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("interface",))


def test_parameter_type_change_is_not_recommended(tmp_path):
    prod_args = [argument("RUN", "P_ID", 1, "NUMBER")]
    test_args = [argument("RUN", "P_ID", 1, "VARCHAR2")]
    body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(body, body, prod_args, test_args)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("interface",))


def test_parameter_mode_change_is_not_recommended(tmp_path):
    prod_args = [argument("RUN", "P_ID", 1, "NUMBER", in_out="IN")]
    test_args = [argument("RUN", "P_ID", 1, "NUMBER", in_out="OUT")]
    body = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(body, body, prod_args, test_args)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("interface",))


def test_removed_exception_handler_requires_investigation(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.REQUIRES_INVESTIGATION, ("exception",))


_MOVED_COMMIT_PROD = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN UPDATE account SET status = 'A' WHERE id = p_id; UPDATE account SET amount = 1 WHERE id = p_id; COMMIT; END; END;\n"
_MOVED_COMMIT_TEST = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN UPDATE account SET status = 'A' WHERE id = p_id; COMMIT; UPDATE account SET amount = 1 WHERE id = p_id; END; END;\n"


class _FakeCursor:
    def __init__(self, failure, rows):
        self.failure = failure
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, binds=None):
        if "dba_objects WHERE ROWNUM" in sql and self.failure is not None:
            raise self.failure

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return list(self.rows)


class _FakeConnection(_FakeCursor):
    def cursor(self):
        return _FakeCursor(self.failure, self.rows)


def _oracle_error(code: int):
    import oracledb
    from types import SimpleNamespace

    return oracledb.DatabaseError(SimpleNamespace(code=code))


def test_database_outage_does_not_report_packages_as_missing():
    from types import SimpleNamespace

    import oracledb
    import pytest

    from oracle_impact.infrastructure.oracle.gateway import OracleUnavailable
    from oracle_impact.infrastructure.oracle.oracledb_gateway import OracledbGateway

    gateway = OracledbGateway("test", "TEST", "dsn", "reader", "secret")
    state = {"down": True}

    def connect():
        if state["down"]:
            raise oracledb.OperationalError(SimpleNamespace(code=12541))
        return _FakeConnection(None, [(1, "BEGIN NULL; END;")])

    gateway._connect = connect
    with pytest.raises(OracleUnavailable):
        gateway.fetch_source("IMPACT_LAB", "CLAIM_PKG", "PACKAGE BODY")
    assert gateway._dictionary_ready is False

    state["down"] = False
    record = gateway.fetch_source("IMPACT_LAB", "CLAIM_PKG", "PACKAGE BODY")
    assert gateway._dictionary_prefix == "dba"
    assert record.found is True


def test_missing_dba_privilege_falls_back_to_all_views():
    from oracle_impact.infrastructure.oracle.oracledb_gateway import OracledbGateway

    gateway = OracledbGateway("test", "TEST", "dsn", "reader", "secret")
    gateway._connect = lambda: _FakeConnection(_oracle_error(942), [])
    record = gateway.fetch_source("APP", "PKG_DEMO", "PACKAGE BODY")
    assert gateway._dictionary_prefix == "all"
    assert record.found is False


def _investigator(*contents: str):
    return Investigator(
        ScriptedProvider([ModelResponse(content=content) for content in contents]),
        max_calls_per_package=15,
        max_calls_per_run=40,
        timeout_seconds=30,
        token_budget=8000,
        max_depth=2,
    )


def test_investigator_without_reviews_adds_no_copied_findings(tmp_path):
    test_catalog, prod_catalog = _pair(_MOVED_COMMIT_PROD, _MOVED_COMMIT_TEST)
    report = run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        test_catalog,
        prod_catalog,
        llm_egress_enabled=True,
        llm_model="mock",
        investigator=_investigator("{}", '{"reviews": []}'),
    )
    assert report.ai_used
    assert report.recommendation == Recommendation.NOT_RECOMMENDED
    assert not [finding for finding in report.findings if finding.origin == "investigator"]
    assert any("did not return a review" in item for item in report.limitations)


def test_issue_list_does_not_repeat_investigator_notes(tmp_path):
    from oracle_impact.controllers.web import TEMPLATES, _sections

    test_catalog, prod_catalog = _pair(_MOVED_COMMIT_PROD, _MOVED_COMMIT_TEST)
    echo = json.dumps({"reviews": [{"business_rule": "This can impact functionality.", "test_cases": []}]})
    report = run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        test_catalog,
        prod_catalog,
        llm_egress_enabled=True,
        llm_model="mock",
        investigator=_investigator(echo, echo),
    )
    notes = [finding for finding in report.findings if finding.origin == "investigator"]
    assert notes
    anchor = next(finding for finding in report.findings if finding.id == notes[0].attributes["review_of"])
    assert anchor.origin == "deterministic"
    page = TEMPLATES.get_template("report_body.html").render({"report": report, "sections": _sections(report)})
    table = page.split('id="changes"', 1)[1].split("</section>", 1)[0]
    assert f">{anchor.id}</a>" in table
    assert table.count(f'id="issue-{anchor.id}"') == 1
    assert f">{notes[0].id}</a>" not in page
    assert 'id="business"' not in page and 'id="related"' not in page


def test_investigator_review_accepts_strings_where_lists_are_expected(tmp_path):
    test_catalog, prod_catalog = _pair(_MOVED_COMMIT_PROD, _MOVED_COMMIT_TEST)
    review = json.dumps(
        {
            "reviews": [
                {
                    "business_rule": "RUN saves the status update before the amount update runs.",
                    "test_cases": "Call RUN and make the amount update fail.",
                    "unknowns": "The nightly batch screen was not visible.",
                }
            ]
        }
    )
    report = run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        test_catalog,
        prod_catalog,
        llm_egress_enabled=True,
        llm_model="mock",
        investigator=_investigator(review, review),
    )
    note = next(finding for finding in report.findings if finding.origin == "investigator")
    assert all(len(test.split()) > 2 for test in note.proposed_tests)
    assert note.attributes["unknowns"] == ["The nightly batch screen was not visible."]


def test_investigator_review_shows_reviewed_severity_without_driving_the_decision(tmp_path):
    from oracle_impact.controllers.web import TEMPLATES, _sections
    from oracle_impact.infrastructure.llm.plain_text import severity_label

    test_catalog, prod_catalog = _pair(_MOVED_COMMIT_PROD, _MOVED_COMMIT_TEST)
    review = json.dumps({"reviews": [{"business_rule": "The update is saved before the second update runs.", "test_cases": ["Fail the second update."]}]})
    report = run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        test_catalog,
        prod_catalog,
        llm_egress_enabled=True,
        llm_model="mock",
        investigator=_investigator(review, review),
    )
    note = next(finding for finding in report.findings if finding.origin == "investigator")
    anchor = next(finding for finding in report.findings if finding.id == note.attributes["review_of"])
    assert anchor.severity.value in {"critical", "high", "medium"}
    assert note.severity.value == "low"
    assert report.recommendation == Recommendation.NOT_RECOMMENDED
    deterministic = [finding for finding in report.findings if finding.origin != "investigator"]
    assert sum(report.statistics.finding_counts.values()) == len(deterministic)

    label = severity_label(note, report)
    assert label == {"level": anchor.severity.value, "text": f"reviews {anchor.id} · {anchor.severity.value}"}
    assert severity_label(anchor, report) == {"level": anchor.severity.value, "text": anchor.severity.value}
    page = TEMPLATES.get_template("report_body.html").render({"report": report, "sections": _sections(report)})
    issues = page.split('id="changes"', 1)[1].split("</section>", 1)[0]
    row = issues.split(f'id="issue-{anchor.id}"', 1)[1].split("</tr>", 1)[0]
    assert "AI explanation" in row
    assert f">{note.id}</a>" in row
    assert f'id="issue-{note.id}"' not in page
    assert issues.count("AI explanation ·") == 1


def test_ai_review_groups_reads_and_rewrites_blind_spots(tmp_path):
    from types import SimpleNamespace

    from oracle_impact.infrastructure.llm.investigator import _unreviewed_notes
    from oracle_impact.infrastructure.llm.plain_text import ai_sources, issue_list

    log = [
        {"tool": "get_object_source", "target": "test IMPACT_LAB.LEDGER_API", "ok": True},
        {"tool": "get_object_source", "target": "test IMPACT_LAB.LEDGER_API", "ok": True},
        {"tool": "get_package_source", "target": "production IMPACT_LAB.CLAIM_PKG", "ok": True},
        {"tool": "get_package_source", "target": "test IMPACT_LAB.CLAIM_PKG", "ok": True},
        {"tool": "get_object_source", "target": "test IMPACT_LAB.CLAIM_GUARD", "ok": False},
    ]
    sources = ai_sources(SimpleNamespace(tool_log=log))
    assert sources["reads"] == 5 and sources["objects"] == 3 and sources["refused"] == 1
    claim = next(row for row in sources["rows"] if row["object"] == "IMPACT_LAB.CLAIM_PKG")
    assert claim["what"] == "Package source" and claim["test"] and claim["production"]
    assert sources["rows"][-1]["object"] == "IMPACT_LAB.CLAIM_GUARD" and sources["rows"][-1]["refused"]

    test_catalog, prod_catalog = _pair(_MOVED_COMMIT_PROD, _MOVED_COMMIT_TEST)
    review = json.dumps(
        {
            "reviews": [
                {
                    "business_rule": "RUN saves the status update before the amount update runs.",
                    "test_cases": ["Call RUN and make the amount update fail."],
                    "unknowns": ["I could not see the nightly schedule. I did not read the ACCOUNT trigger."],
                }
            ]
        }
    )
    report = run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        test_catalog,
        prod_catalog,
        llm_egress_enabled=True,
        llm_model="mock",
        investigator=_investigator(review, review),
    )
    spots = [spot["text"] for spot in issue_list(report)["blind_spots"]]
    assert "Could not see the nightly schedule." in spots
    assert "Did not read the ACCOUNT trigger." in spots

    brief = "\n".join(f"- finding_id F-{index}; object APP.PKG" for index in range(12))
    notes = _unreviewed_notes(["F-3", "F-99"], brief)
    assert notes[0].startswith("The AI was not asked about F-99.") and "at most 12 issues" in notes[0]
    assert "did not return a review for F-3." in notes[1]


_RAISING_BODY = (
    "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN\n"
    "  IF p_id IS NULL THEN\n"
    "    RAISE_APPLICATION_ERROR(\n"
    "      {code}, 'missing id');\n"
    "  END IF;\n"
    "  UPDATE account SET status = 'A' WHERE id = p_id;\n"
    "END; END;\n"
)
_QUIET_BODY = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN\n  NULL;\n  UPDATE account SET status = 'A' WHERE id = p_id;\nEND; END;\n"
_PLAIN_BODY = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"


def _only(report, category):
    found = [finding for finding in report.findings if finding.change_category == category]
    assert len(found) == 1, [(finding.change_category, finding.evidence) for finding in report.findings]
    return found[0]


def test_unchanged_dml_after_a_changed_line_is_not_reported_as_modified(tmp_path):
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN\n  UPDATE account SET status = 'A' WHERE id = p_id;\nEND; END;\n"
    test = (
        "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN\n  IF p_id IS NOT NULL THEN\n"
        "  UPDATE account SET status = 'A' WHERE id = p_id;\n  END IF;\nEND; END;\n"
    )
    test_catalog, prod_catalog = _pair(prod, test)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    assert not [finding for finding in report.findings if finding.change_category == "data"]
    assert any(finding.change_category == "business_logic" for finding in report.findings)


def test_removed_error_code_holds_the_recommendation(tmp_path):
    test_catalog, prod_catalog = _pair(_RAISING_BODY.format(code="-20001"), _QUIET_BODY)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    finding = _only(report, "error_contract")
    assert finding.severity.value == "high" and finding.status.value == "confirmed"
    assert "no longer raises error code -20001" in finding.evidence
    assert finding.corresponding_lines_prod == [3]
    assert report.recommendation == Recommendation.REQUIRES_INVESTIGATION
    assert any(reason.startswith(finding.id) for reason in report.recommendation_reasons)


def test_renumbered_error_code_reports_both_codes(tmp_path):
    test_catalog, prod_catalog = _pair(_RAISING_BODY.format(code="-20001"), _RAISING_BODY.format(code="- 20002"))
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    finding = _only(report, "error_contract")
    assert "no longer raises error code -20001 and now raises error code -20002" in finding.evidence
    assert finding.severity.value == "high"
    assert finding.changed_lines_test == [3]


def test_added_error_code_is_low_and_does_not_hold_the_recommendation(tmp_path):
    test_catalog, prod_catalog = _pair(_QUIET_BODY, _RAISING_BODY.format(code="-20001"))
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    finding = _only(report, "error_contract")
    assert finding.severity.value == "low" and "now raises error code -20001" in finding.evidence
    assert report.recommendation == Recommendation.ELIGIBLE_FOR_REVIEW


def test_exception_init_change_and_removed_public_exception(tmp_path):
    prod_spec = (
        "PACKAGE pkg_demo AS\n  e_busy EXCEPTION;\n  PRAGMA EXCEPTION_INIT(e_busy, -54);\n"
        "  e_missing EXCEPTION;\n  PROCEDURE run(p_id IN NUMBER);\nEND;\n"
    )
    test_spec = "PACKAGE pkg_demo AS\n  e_busy EXCEPTION;\n  PRAGMA EXCEPTION_INIT(e_busy, -60);\n  PROCEDURE run(p_id IN NUMBER);\nEND;\n"
    test_catalog, prod_catalog = _pair(_PLAIN_BODY, _PLAIN_BODY, prod_spec=prod_spec, test_spec=test_spec)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    contract = {finding.attributes["error_contract"]: finding for finding in report.findings if finding.change_category == "error_contract"}
    assert set(contract) == {"exception_init", "public_exception"}
    assert "E_BUSY now maps to -60 instead of -54" in contract["exception_init"].evidence
    assert "removed E_MISSING" in contract["public_exception"].evidence
    assert contract["public_exception"].corresponding_lines_prod == [4]
    assert all(finding.severity.value == "high" and finding.object_type == "PACKAGE" for finding in contract.values())
    assert report.recommendation == Recommendation.REQUIRES_INVESTIGATION


def test_authid_change_is_a_confirmed_security_finding(tmp_path):
    prod_spec = "PACKAGE pkg_demo AS PROCEDURE run(p_id IN NUMBER); END;\n"
    test_spec = "PACKAGE pkg_demo\n  AUTHID CURRENT_USER\nAS PROCEDURE run(p_id IN NUMBER); END;\n"
    test_catalog, prod_catalog = _pair(_PLAIN_BODY, _PLAIN_BODY, prod_spec=prod_spec, test_spec=test_spec)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    finding = _only(report, "security")
    assert "AUTHID changed from DEFINER to CURRENT_USER" in finding.evidence
    assert finding.severity.value == "high" and finding.status.value == "confirmed"
    assert finding.changed_lines_test == [2] and finding.corresponding_lines_prod == []
    assert report.recommendation == Recommendation.REQUIRES_INVESTIGATION

    explicit = "PACKAGE pkg_demo AUTHID DEFINER AS PROCEDURE run(p_id IN NUMBER); END;\n"
    test_catalog, prod_catalog = _pair(_PLAIN_BODY, _PLAIN_BODY, prod_spec=prod_spec, test_spec=explicit)
    report = run_catalogs(tmp_path / "explicit", ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    assert not [finding for finding in report.findings if finding.change_category == "security"]


def test_grant_missing_in_production_holds_the_recommendation(tmp_path):
    test_catalog, prod_catalog = _pair(_PLAIN_BODY, _PLAIN_BODY)
    test_catalog.add_grant("APP", "PKG_DEMO", "BILLING_APP", "EXECUTE")
    test_catalog.add_grant("APP", "PKG_DEMO", "REPORTING", "EXECUTE")
    prod_catalog.add_grant("APP", "PKG_DEMO", "REPORTING", "EXECUTE")
    prod_catalog.add_grant("APP", "PKG_DEMO", "LEGACY_BATCH", "EXECUTE")
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    grants = {finding.attributes["grant_difference"]: finding for finding in report.findings if finding.change_category == "security"}
    missing = grants["missing_in_production"]
    assert missing.severity.value == "medium" and missing.status.value == "confirmed"
    assert "EXECUTE to BILLING_APP" in missing.evidence and "REPORTING" not in missing.evidence
    assert grants["missing_in_test"].severity.value == "low" and "LEGACY_BATCH" in grants["missing_in_test"].evidence
    assert report.recommendation == Recommendation.REQUIRES_INVESTIGATION


def test_partial_or_unreadable_grants_are_reported_as_limitations(tmp_path):
    test_catalog, prod_catalog = _pair(_PLAIN_BODY, _PLAIN_BODY)
    test_catalog.add_grant("APP", "PKG_DEMO", "BILLING_APP", "EXECUTE")
    prod_catalog.grants_complete = False
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    finding = _only(report, "security")
    assert finding.status.value == "potential" and "Only grants visible" in finding.explanation
    assert any("ALL_TAB_PRIVS" in item for item in report.limitations)

    test_catalog, prod_catalog = _pair(_PLAIN_BODY, _PLAIN_BODY)
    test_catalog.add_grant("APP", "PKG_DEMO", "BILLING_APP", "EXECUTE")
    prod_catalog.inaccessible_grants.add(("APP", "PKG_DEMO"))
    report = run_catalogs(tmp_path / "hidden", ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    assert not [finding for finding in report.findings if finding.change_category == "security"]
    assert any("could not be read in Production" in item for item in report.limitations)
    assert report.recommendation == Recommendation.ELIGIBLE_FOR_REVIEW


def test_grant_read_falls_back_to_all_tab_privs_when_dba_view_is_denied():
    from types import SimpleNamespace

    import oracledb

    from oracle_impact.infrastructure.oracle.oracledb_gateway import OracledbGateway

    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, binds=None):
            executed.append(sql)
            if "dba_tab_privs" in sql:
                raise oracledb.DatabaseError(SimpleNamespace(code=942))

        def fetchall(self):
            return [("BILLING_APP", "EXECUTE")]

    class Connection(Cursor):
        def cursor(self):
            return Cursor()

    gateway = OracledbGateway("test", "TEST", "dsn", "reader", "secret")
    gateway._dictionary_ready, gateway._dictionary_prefix = True, "dba"
    gateway._connect = lambda: Connection()
    fetch = gateway.fetch_grants("APP", "PKG_DEMO")
    assert fetch.complete is False and fetch.grants == [("BILLING_APP", "EXECUTE")]
    assert any("all_tab_privs" in sql for sql in executed)


# Decision support: compilation, drift classification, coverage, readiness, history.

_THRESHOLD_PROD = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN IF p_limit > 100 THEN NULL; END IF; END; END;\n"
_THRESHOLD_TEST = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN IF p_limit > 500 THEN NULL; END IF; END; END;\n"


def _demo(tmp_path, package_name: str):
    from oracle_impact.fixtures.demo_catalog import build_demo_catalogs

    test_catalog, prod_catalog = build_demo_catalogs()
    return run_catalogs(tmp_path, [package_name], test_catalog, prod_catalog)


def _readiness(report, validations=(), reviews=(), superseded=None, stale=None):
    from oracle_impact.services.readiness import evaluate_readiness, policy_for

    policy, _ = policy_for(report)
    return evaluate_readiness(report, policy, list(validations), list(reviews), superseded, stale)


def test_dependent_that_does_not_compile_in_test_blocks_deployment(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    finding = next(item for item in report.findings if item.rule_id == "CMP-DEPENDENT")
    assert finding.status.value == "confirmed" and finding.object_name == "PKG_LEGACY"
    assert finding.fingerprint.startswith("FP-") and finding.severity_rationale
    assert "PLS-00306" in finding.evidence
    assert any(item.rule_id == "BLK-COMPILE-FAILURE" and item.finding_id == finding.id for item in report.policy.blockers)
    assert report.compilation.evaluated and report.compilation.errors[0].line == 4
    assert report.recommendation == Recommendation.NOT_RECOMMENDED
    assert _readiness(report)["decision"] == "on_hold"


def test_compile_failure_cannot_be_cleared_by_a_passed_validation(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    blocker = next(item for item in report.policy.blockers if item.rule_id == "BLK-COMPILE-FAILURE")
    passed = [{"id": "V1", "finding_id": blocker.finding_id, "status": "passed"}]
    accepted = [{"finding_id": blocker.finding_id, "decision": "accepted"}]
    entry = next(item for item in _readiness(report, passed, accepted)["blockers"] if item["item"].finding_id == blocker.finding_id)
    assert entry["state"] == "unresolved"


def test_dependency_edges_keep_dictionary_and_source_scan_provenance(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_BILLING")
    provenance = {edge.provenance for edge in report.dependency_edges}
    assert {"dictionary", "source_scan"} <= provenance
    assert all(edge.provenance in {"dictionary", "source_scan", "unresolved"} for edge in report.dependency_edges)


def test_required_tests_stay_open_and_stored_results_do_not_clear_them(tmp_path):
    test_catalog, prod_catalog = _pair(_THRESHOLD_PROD, _THRESHOLD_TEST)
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    assert not report.policy.blockers and report.policy.validations
    before = _readiness(report)
    assert before["decision"] == "on_hold" and before["open_validations"] == len(report.policy.validations)
    passed = [
        {"id": f"V{index}", "finding_id": item.finding_id, "status": "passed", "evidence": "TEST run 42"}
        for index, item in enumerate(report.policy.validations)
    ]
    after = _readiness(report, passed, [{"finding_id": item.finding_id, "decision": "accepted"} for item in report.policy.validations])
    assert after["decision"] == "on_hold" and after["open_validations"] == len(report.policy.validations)
    assert all(entry["state"] == "unresolved" for entry in after["validations"])


def test_partial_coverage_makes_readiness_indeterminate(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    assert report.coverage.analyzed == report.coverage.in_scope == 2
    assert _readiness(report)["decision"] == "recommended"
    report.coverage.inaccessible = 1
    result = _readiness(report)
    assert result["decision"] == "indeterminate"
    assert any("could not be read" in reason for reason in result["indeterminate_reasons"])


def test_report_without_coverage_or_newer_run_is_indeterminate(tmp_path):
    from oracle_impact.models.report import Report

    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    legacy = report.model_dump(mode="json")
    for key in ("coverage", "policy", "object_states", "compilation", "report_version"):
        legacy.pop(key, None)
    legacy["comparisons"] = [{"key": "test:autumn", "source_environment": "test", "target_environment": "autumn", "source_label": "TEST", "target_label": "AUTUMN", "diffs": []}]
    old = Report.model_validate(legacy)
    assert old.coverage is None and _readiness(old)["decision"] == "indeterminate"
    assert _readiness(report, superseded="newer-run")["decision"] == "indeterminate"
    assert _readiness(report, stale="The snapshot is older than 24 hours.")["decision"] == "indeterminate"


def test_object_summary_compares_captured_checksums(tmp_path):
    from oracle_impact.services.change_classification import classify_changes

    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    result = classify_changes(report)
    body = next(item for item in result["objects"] if item["object_type"] == "PACKAGE BODY")
    assert result["recorded"] is True and result["changed"] >= 1
    assert "classification" not in body and "reasons" not in body
    assert body["hashes"]["test"] != body["hashes"]["production"]


def test_readiness_does_not_depend_on_trust_records(tmp_path):
    report = _demo(tmp_path, "DEMO.PKG_STATUS")
    result = _readiness(report)
    assert result["decision"] == "recommended"
    assert "open_drift" not in result and "drift" not in result


def test_fingerprints_are_stable_and_run_comparison_tracks_resolution(tmp_path):
    from oracle_impact.services.history import compare_runs

    first = _demo(tmp_path / "a", "DEMO.PKG_BILLING")
    second = _demo(tmp_path / "b", "DEMO.PKG_BILLING")
    assert first.run_id != second.run_id
    assert sorted(item.fingerprint for item in first.findings) == sorted(item.fingerprint for item in second.findings)
    same = compare_runs(second, first)
    assert not same["new"] and not same["resolved"] and len(same["unchanged"]) == len(first.findings)

    status = _demo(tmp_path / "c", "DEMO.PKG_STATUS")
    changed = compare_runs(status, first)
    assert changed["resolved"] and changed["blockers_resolved"] and not changed["blockers_new"]


def test_investigator_failure_keeps_deterministic_findings(tmp_path):
    class Broken:
        def with_model(self, model):
            return self

        def available_models(self):
            return ["mock"]

        def investigate(self, *args, **kwargs):
            raise RuntimeError("model endpoint unavailable")

    from oracle_impact.fixtures.demo_catalog import build_demo_catalogs

    test_catalog, prod_catalog = build_demo_catalogs()
    report = run_catalogs(tmp_path, ["DEMO.PKG_BILLING"], test_catalog, prod_catalog, llm_egress_enabled=True, llm_model="mock", investigator=Broken())
    assert report.status == "completed"
    assert report.recommendation == Recommendation.NOT_RECOMMENDED
    assert {item.rule_id for item in report.policy.blockers} >= {"BLK-INTERFACE-BREAK", "BLK-COMPILE-FAILURE"}
    assert any("investigator failed" in item.lower() for item in report.limitations)
    assert not [item for item in report.findings if item.origin == "investigator"]


def _finding_keys(report):
    return sorted(
        (item.change_category, item.changed_object, item.severity.value, item.status.value, item.fingerprint)
        for item in report.findings
    )


def test_agentic_run_matches_the_deterministic_policy_when_the_checklist_is_complete(tmp_path):
    from tests.support import ChecklistProvider, agentic_investigator, run_demo

    for name in ("DEMO.PKG_STATUS", "DEMO.PKG_BILLING"):
        deterministic = run_demo(tmp_path / name / "deterministic", [name])
        provider = ChecklistProvider(final="I read the package, its callers, its tables, and their status.")
        agentic = run_demo(tmp_path / name / "agentic", [name], mode="agentic", investigator=agentic_investigator(provider))

        assert agentic.run_mode == "agentic"
        assert agentic.recommendation == deterministic.recommendation
        assert {item.rule_id for item in agentic.policy.blockers} == {item.rule_id for item in deterministic.policy.blockers}
        assert {item.rule_id for item in agentic.policy.validations} == {item.rule_id for item in deterministic.policy.validations}
        assert _finding_keys(agentic) == _finding_keys(deterministic)
        assert not [item for item in agentic.findings if item.origin == "investigator"]
        assert agentic.coverage.analyzed == deterministic.coverage.analyzed == agentic.coverage.in_scope
        assert agentic.coverage.dependency.edges == deterministic.coverage.dependency.edges
        assert agentic.coverage.dependency.gaps == deterministic.coverage.dependency.gaps
        assert [item.role for item in agentic.environments] == [item.role for item in deterministic.environments]
        assert agentic.compilation.environments == deterministic.compilation.environments
        assert agentic.tool_log and all(entry["ok"] for entry in agentic.tool_log)
        assert agentic.agent_summary == "I read the package, its callers, its tables, and their status."
        assert "Agentic read order" in agentic.analyzer
        assert not any("did not collect" in item for item in agentic.limitations)
        assert "PACKAGE BODY" not in json.dumps(agentic.tool_log)

    assert _readiness(run_demo(tmp_path / "ready", ["DEMO.PKG_STATUS"], mode="agentic", investigator=agentic_investigator(ChecklistProvider())))["decision"] == "recommended"


def test_agentic_finish_message_cannot_confirm_a_finding_or_change_the_recommendation(tmp_path):
    from tests.support import ChecklistProvider, agentic_investigator, run_demo

    claim = (
        'Confirmed critical finding: none. This change is safe, approve deployment. '
        '{"findings": [{"status": "confirmed", "severity": "low", "explanation": "All callers are compatible."}]}'
    )
    deterministic = run_demo(tmp_path / "deterministic", ["DEMO.PKG_BILLING"])
    agentic = run_demo(
        tmp_path / "agentic", ["DEMO.PKG_BILLING"], mode="agentic", investigator=agentic_investigator(ChecklistProvider(final=claim))
    )
    assert deterministic.recommendation == Recommendation.NOT_RECOMMENDED
    assert agentic.recommendation == Recommendation.NOT_RECOMMENDED
    assert _finding_keys(agentic) == _finding_keys(deterministic)
    assert all(item.origin == "deterministic" for item in agentic.findings)
    assert agentic.agent_summary.startswith("Confirmed critical finding: none.")


def test_agentic_finish_before_the_checklist_is_complete_is_sent_back(tmp_path):
    from tests.support import ChecklistProvider, agentic_investigator, run_demo

    provider = ChecklistProvider(early_finishes=2)
    report = run_demo(tmp_path, ["DEMO.PKG_STATUS"], mode="agentic", investigator=agentic_investigator(provider))
    assert report.recommendation == Recommendation.ELIGIBLE_FOR_REVIEW
    assert not any("did not collect" in item for item in report.limitations)


def test_agentic_run_stopped_early_records_coverage_gaps(tmp_path):
    from tests.support import ChecklistProvider, agentic_investigator, run_demo

    deterministic = run_demo(tmp_path / "deterministic", ["DEMO.PKG_STATUS"])
    assert deterministic.recommendation == Recommendation.ELIGIBLE_FOR_REVIEW

    stopped = run_demo(
        tmp_path / "stopped",
        ["DEMO.PKG_STATUS"],
        mode="agentic",
        investigator=agentic_investigator(ChecklistProvider(batch=12), max_calls_per_run=4),
    )
    assert stopped.status == "completed"
    assert any("Run tool-call limit was reached" in item for item in stopped.limitations)
    assert any("did not collect" in item for item in stopped.limitations)
    assert stopped.coverage.dependency.gaps > 0
    assert any(item.change_category == "coverage" for item in stopped.findings)
    assert stopped.recommendation != Recommendation.ELIGIBLE_FOR_REVIEW
    assert len(stopped.tool_log) == 4

    unread = run_demo(
        tmp_path / "unread",
        ["DEMO.PKG_STATUS"],
        mode="agentic",
        investigator=agentic_investigator(ChecklistProvider(batch=12), max_calls_per_run=0),
    )
    assert unread.tool_log == []
    assert unread.coverage.inaccessible == unread.coverage.in_scope
    assert "DEMO.PKG_STATUS was not read in Test by the investigator." in unread.limitations
    assert _readiness(unread)["decision"] == "indeterminate"


def test_agentic_unread_compile_errors_are_reported_as_not_read(tmp_path):
    from oracle_impact.services.report_view import build_report_view
    from tests.support import ChecklistProvider, agentic_investigator, run_demo

    def legacy_errors(item):
        return item["tool"] == "get_compile_errors" and item["arguments"].get("name") == "PKG_LEGACY"

    report = run_demo(
        tmp_path, ["DEMO.PKG_BILLING"], mode="agentic", investigator=agentic_investigator(ChecklistProvider(skip=legacy_errors))
    )
    finding = next(item for item in report.findings if item.rule_id == "CMP-DEPENDENT" and item.object_name == "PKG_LEGACY")
    assert "ALL_ERRORS was not read in this run" in finding.evidence
    assert "no ALL_ERRORS rows were visible" not in finding.evidence
    assert "PLS-00306" not in finding.evidence
    assert any(item.rule_id == "BLK-COMPILE-FAILURE" and item.finding_id == finding.id for item in report.policy.blockers)
    assert any("did not collect" in item and "PKG_LEGACY" in item for item in report.limitations)
    [legacy] = [item for item in build_report_view(report)["compilation"]["invalid"] if item["name"] == "PKG_LEGACY" and item["environment"] == "test"]
    assert legacy["errors"] == [] and legacy["errors_note"] == "ALL_ERRORS was not read in this run"


def test_agentic_reads_use_their_own_budget(tmp_path):
    from oracle_impact.infrastructure.llm.investigator import Investigator
    from tests.support import ChecklistProvider, run_demo

    investigator = Investigator(
        ChecklistProvider(),
        max_calls_per_package=500,
        max_calls_per_run=2,
        timeout_seconds=30,
        token_budget=1,
        max_depth=2,
        gather_token_budget=1_000_000,
        gather_max_calls_per_run=500,
    ).with_model("ignored")
    report = run_demo(tmp_path, ["DEMO.PKG_STATUS"], mode="agentic", investigator=investigator)
    assert not any("limit was reached" in item or "budget was exhausted" in item for item in report.limitations)
    assert len(report.tool_log) > 2
    assert report.recommendation == Recommendation.ELIGIBLE_FOR_REVIEW


def _new_dependencies():
    prod = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS BEGIN NULL; END; END;\n"
    test = "PACKAGE BODY pkg_demo AS PROCEDURE run(p_id IN NUMBER) IS v NUMBER; BEGIN v := id_seq.NEXTVAL + fee_for(p_id); END; END;\n"
    test_catalog, prod_catalog = _pair(prod, test)
    test_catalog.add_dependency(("APP", "PKG_DEMO", "PACKAGE BODY"), ("APP", "ID_SEQ", "SEQUENCE"))
    test_catalog.add_dependency(("APP", "PKG_DEMO", "PACKAGE BODY"), ("APP", "FEE_FOR", "FUNCTION"))
    test_catalog.add_object("APP", "ID_SEQ", "SEQUENCE")
    test_catalog.add_source("APP", "FEE_FOR", "FUNCTION", "FUNCTION fee_for(p NUMBER) RETURN NUMBER IS BEGIN RETURN 1; END;\n")
    return test_catalog, prod_catalog


def _missing_dependencies(report):
    return {item.affected_object: item for item in report.findings if item.rule_id == "DEP-MISSING-OBJECT"}


def test_new_dependency_missing_in_production_blocks_deployment(tmp_path):
    test_catalog, prod_catalog = _new_dependencies()
    prod_catalog.add_object("APP", "FEE_FOR", "PROCEDURE")
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)

    missing = _missing_dependencies(report)
    assert set(missing) == {"APP.ID_SEQ", "APP.FEE_FOR"}
    assert all(item.status.value == "confirmed" and item.severity.value == "critical" for item in missing.values())
    assert "now depends on SEQUENCE APP.ID_SEQ, which was not found in" in missing["APP.ID_SEQ"].evidence
    assert "has APP.FEE_FOR only as PROCEDURE." in missing["APP.FEE_FOR"].evidence
    blocked = {item.finding_id for item in report.policy.blockers if item.rule_id == "BLK-MISSING-OBJECT"}
    assert blocked == {item.id for item in missing.values()}
    assert report.recommendation == Recommendation.NOT_RECOMMENDED


def test_new_dependency_present_in_production_is_not_reported(tmp_path):
    test_catalog, prod_catalog = _new_dependencies()
    prod_catalog.add_object("APP", "ID_SEQ", "SEQUENCE")
    prod_catalog.add_source("APP", "FEE_FOR", "FUNCTION", "FUNCTION fee_for(p NUMBER) RETURN NUMBER IS BEGIN RETURN 1; END;\n")
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)
    assert _missing_dependencies(report) == {}
    assert not any(item.rule_id == "BLK-MISSING-OBJECT" for item in report.policy.blockers)
    assert not any(item.change_category == "coverage" for item in report.findings)


def test_unreadable_production_existence_is_a_coverage_gap_not_a_pass(tmp_path):
    test_catalog, prod_catalog = _new_dependencies()
    prod_catalog.add_object("APP", "ID_SEQ", "SEQUENCE")
    prod_catalog.inaccessible_status.add(("APP", "FEE_FOR"))
    report = run_catalogs(tmp_path, ["APP.PKG_DEMO"], test_catalog, prod_catalog)

    assert _missing_dependencies(report) == {}
    [gap] = [item for item in report.findings if item.change_category == "coverage" and item.affected_object == "APP.FEE_FOR"]
    assert gap.status.value == "unknown" and "could not be checked" in gap.evidence
    assert any(item.rule_id == "VAL-METADATA-GAP" and item.finding_id == gap.id for item in report.policy.validations)
    assert report.recommendation != Recommendation.ELIGIBLE_FOR_REVIEW


def test_agentic_run_collects_the_production_existence_reads(tmp_path):
    from tests.support import ChecklistProvider, agentic_investigator

    deterministic = run_catalogs(tmp_path / "deterministic", ["APP.PKG_DEMO"], *_new_dependencies())
    agentic = run_catalogs(
        tmp_path / "agentic", ["APP.PKG_DEMO"], *_new_dependencies(), mode="agentic", investigator=agentic_investigator(ChecklistProvider())
    )
    assert set(_missing_dependencies(agentic)) == set(_missing_dependencies(deterministic)) == {"APP.ID_SEQ", "APP.FEE_FOR"}
    assert _finding_keys(agentic) == _finding_keys(deterministic)
    assert not any("did not collect" in item for item in agentic.limitations)
    assert any(entry["tool"] == "get_object_status" and "production APP.ID_SEQ" in entry["target"] for entry in agentic.tool_log)


_BLIND_PROD = "PACKAGE BODY pkg_demo AS\n  PROCEDURE run(p_id IN NUMBER) IS\n  BEGIN\n    NULL;\n  END run;\nEND;\n"
_BLIND_TEST = (
    "PACKAGE BODY pkg_demo AS\n"
    "  PROCEDURE run(p_id IN NUMBER) IS\n"
    "    v_sql VARCHAR2(200);\n"
    "  BEGIN\n"
    "    v_sql := 'DELETE FROM app.audit_log WHERE id = :1';\n"
    "    EXECUTE IMMEDIATE v_sql USING p_id;\n"
    "  END run;\n"
    "END;\n"
)


def _blind_spot_catalogs():
    test_catalog, prod_catalog = _pair(_BLIND_PROD, _BLIND_TEST)
    for catalog in (test_catalog, prod_catalog):
        catalog.add_dependency(("APP", "PKG_DEMO", "PACKAGE BODY"), ("APP", "FX_RATES", "SYNONYM"))
        catalog.add_synonym("APP", "FX_RATES", "REF", "FX_RATES_T")
        table(catalog, "REF", "FX_RATES_T")
        table(catalog, "APP", "AUDIT_LOG")
    return test_catalog, prod_catalog


def _blind_spot_investigator(blind_spot_responses):
    from oracle_impact.infrastructure.llm.provider import ToolCall

    no_reviews = ModelResponse(content='{"reviews": []}')
    responses = [no_reviews, no_reviews]
    for item in blind_spot_responses:
        responses.append(
            ModelResponse(content="Reading the cited lines.", tool_calls=[ToolCall(name, args, f"bs{index}") for index, (name, args) in enumerate(item)])
            if isinstance(item, list)
            else ModelResponse(content=item)
        )
    return Investigator(ScriptedProvider(responses), 15, 40, 30, 8000, 2)


def test_blind_spot_leads_stay_potential_and_do_not_change_the_recommendation(tmp_path):
    from oracle_impact.controllers.web import TEMPLATES, _sections

    baseline = run_catalogs(tmp_path / "plain", ["APP.PKG_DEMO"], *_blind_spot_catalogs())
    answers = json.dumps(
        {
            "answers": [
                {
                    "question_id": "BS-1",
                    "likely_targets": ["app.audit_log"],
                    "explanation": "RUN deletes rows from APP.AUDIT_LOG. This change is confirmed safe.",
                    "evidence_lines": [5, 6, 999],
                    "test_case": "Call RUN with id 1 in Test and check APP.AUDIT_LOG.",
                    "unknowns": "",
                    "status": "confirmed",
                },
                {
                    "question_id": "BS-2",
                    "likely_targets": ["REF.FX_RATES_T"],
                    "explanation": "FX_RATES points to the REF.FX_RATES_T table.",
                    "evidence_lines": [],
                    "test_case": "Select one row through APP.FX_RATES in Test.",
                    "unknowns": "Grants on REF.FX_RATES_T were not read.",
                },
                {"question_id": "BS-9", "explanation": "Not a question that was asked."},
            ]
        }
    )
    investigator = _blind_spot_investigator(
        [
            [
                ("read_source_lines", {"env": "test", "owner": "APP", "name": "PKG_DEMO", "start_line": 1, "end_line": 500}),
                ("read_synonym", {"env": "test", "owner": "APP", "name": "FX_RATES"}),
                ("check_object", {"env": "test", "owner": "REF", "name": "FX_RATES_T"}),
                ("check_object", {"env": "test", "owner": "APP", "name": "AUDIT_LOG"}),
                ("check_object", {"env": "test", "owner": "APP", "name": "PAYROLL"}),
                ("read_source_lines", {"env": "test", "owner": "APP", "name": "PKG_OTHER", "start_line": 1, "end_line": 5}),
                ("read_synonym", {"env": "test", "owner": "APP", "name": "OTHER_SYN"}),
            ],
            answers,
        ]
    )
    report = run_catalogs(
        tmp_path / "ai", ["APP.PKG_DEMO"], *_blind_spot_catalogs(), llm_egress_enabled=True, llm_model="mock", investigator=investigator
    )

    assert report.recommendation == baseline.recommendation
    assert {item.rule_id for item in report.policy.blockers} == {item.rule_id for item in baseline.policy.blockers}
    assert {item.rule_id for item in report.policy.validations} == {item.rule_id for item in baseline.policy.validations}
    deterministic = [item for item in report.findings if item.origin != "investigator"]
    assert sorted((item.change_category, item.severity.value, item.status.value) for item in deterministic) == sorted(
        (item.change_category, item.severity.value, item.status.value) for item in baseline.findings
    )
    [dynamic] = [item for item in deterministic if item.change_category == "dynamic_sql"]
    assert dynamic.status.value == "unknown"

    leads = {item.attributes["question_id"]: item for item in report.findings if item.attributes.get("blind_spot")}
    assert set(leads) == {"BS-1", "BS-2"}
    for lead in leads.values():
        assert lead.origin == "investigator"
        assert lead.status.value == "potential" and lead.severity.value == "low"
        assert lead.attributes["blocks_eligibility"] is False
    sql = leads["BS-1"]
    assert sql.attributes["review_of"] == dynamic.id
    assert sql.changed_lines_test == [5, 6]
    assert sql.attributes["likely_targets"] == ["APP.AUDIT_LOG"]
    assert {"environment": "test", "object": "APP.AUDIT_LOG", "result": "exists (table)"} in sql.attributes["checked"]
    synonym = leads["BS-2"]
    assert synonym.change_category == "dependency"
    assert {"environment": "test", "object": "APP.FX_RATES", "result": "points to REF.FX_RATES_T"} in synonym.attributes["checked"]
    assert {"environment": "test", "object": "REF.FX_RATES_T", "result": "exists (table)"} in synonym.attributes["checked"]

    refused = [entry for entry in report.tool_log if not entry["ok"]]
    assert {entry["tool"] for entry in refused} == {"check_object", "read_source_lines", "read_synonym"}
    assert any("APP.PAYROLL" in entry["target"] for entry in refused)

    page = TEMPLATES.get_template("report_body.html").render({"report": report, "sections": _sections(report)})
    table_html = page.split('id="changes"', 1)[1].split("</section>", 1)[0]
    assert "AI-BS-1" not in table_html
    card = page.split('id="ai-leads"', 1)[1].split('id="limitations"', 1)[0]
    assert "AI-BS-1" in card and "AI-BS-2" in card
    assert "AI lead · not verified" in card
    assert "points to REF.FX_RATES_T" in card


def test_blind_spot_budget_breach_leaves_the_items_unresolved(tmp_path):
    investigator = _blind_spot_investigator(
        [
            [
                ("read_synonym", {"env": "test", "owner": "APP", "name": "FX_RATES"}),
                ("read_synonym", {"env": "production", "owner": "APP", "name": "FX_RATES"}),
            ]
        ]
    )
    report = run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        *_blind_spot_catalogs(),
        llm_egress_enabled=True,
        llm_model="mock",
        blind_spot_max_tool_calls=1,
        investigator=investigator,
    )
    assert not [item for item in report.findings if item.attributes.get("blind_spot")]
    assert any("Blind-spot tool-call limit was reached" in item for item in report.limitations)
    assert any(item.change_category == "dynamic_sql" and item.status.value == "unknown" for item in report.findings)


def test_blind_spot_question_limit_is_reported(tmp_path):
    investigator = _blind_spot_investigator([json.dumps({"answers": []})])
    report = run_catalogs(
        tmp_path,
        ["APP.PKG_DEMO"],
        *_blind_spot_catalogs(),
        llm_egress_enabled=True,
        llm_model="mock",
        blind_spot_max_questions=1,
        investigator=investigator,
    )
    assert any("1 more item the checks could not resolve was not sent to the AI" in item for item in report.limitations)
    assert any("did not answer blind-spot question BS-1" in item for item in report.limitations)


def test_agentic_run_asks_blind_spot_questions_after_scoring(tmp_path):
    from oracle_impact.infrastructure.llm.provider import ToolCall
    from tests.support import ChecklistProvider, agentic_investigator

    class ThenBlindSpots:
        def __init__(self, responses):
            self.gather = ChecklistProvider()
            self.responses = list(responses)

        def complete(self, messages, tools):
            if any(tool["function"]["name"] == "read_source_lines" for tool in tools):
                return self.responses.pop(0)
            return self.gather.complete(messages, tools)

    answers = json.dumps(
        {
            "answers": [
                {"question_id": "BS-1", "likely_targets": ["APP.AUDIT_LOG"], "explanation": "RUN deletes rows from APP.AUDIT_LOG.", "evidence_lines": [5, 6]},
                {"question_id": "BS-2", "likely_targets": ["REF.FX_RATES_T"], "explanation": "FX_RATES points to REF.FX_RATES_T."},
            ]
        }
    )
    provider = ThenBlindSpots(
        [
            ModelResponse(
                content="Reading the lines that build the statement.",
                tool_calls=[
                    ToolCall("read_source_lines", {"env": "test", "owner": "APP", "name": "PKG_DEMO", "start_line": 1, "end_line": 8}, "b1"),
                    ToolCall("read_synonym", {"env": "production", "owner": "APP", "name": "FX_RATES"}, "b2"),
                ],
            ),
            ModelResponse(content=answers),
        ]
    )
    baseline = run_catalogs(tmp_path / "plain", ["APP.PKG_DEMO"], *_blind_spot_catalogs())
    agentic = run_catalogs(
        tmp_path / "agentic", ["APP.PKG_DEMO"], *_blind_spot_catalogs(), mode="agentic", investigator=agentic_investigator(provider)
    )

    assert agentic.run_mode == "agentic"
    assert not any("did not collect" in item for item in agentic.limitations)
    assert agentic.recommendation == baseline.recommendation
    assert {item.rule_id for item in agentic.policy.blockers} == {item.rule_id for item in baseline.policy.blockers}
    assert {item.rule_id for item in agentic.policy.validations} == {item.rule_id for item in baseline.policy.validations}
    deterministic = [item for item in agentic.findings if item.origin != "investigator"]
    assert sorted((i.change_category, i.changed_object, i.severity.value, i.status.value, i.fingerprint) for i in deterministic) == _finding_keys(baseline)

    leads = {item.attributes["question_id"]: item for item in agentic.findings if item.attributes.get("blind_spot")}
    assert set(leads) == {"BS-1", "BS-2"}
    assert all(lead.status.value == "potential" and lead.attributes["blocks_eligibility"] is False for lead in leads.values())
    assert leads["BS-1"].changed_lines_test == [5, 6]
    assert {"environment": "production", "object": "APP.FX_RATES", "result": "points to REF.FX_RATES_T"} in leads["BS-2"].attributes["checked"]
    assert [entry["tool"] for entry in agentic.tool_log][-2:] == ["read_source_lines", "read_synonym"]
    assert all(entry["ok"] for entry in agentic.tool_log)


def test_existing_deterministic_runs_complete_with_the_investigator_off(tmp_path):
    from oracle_impact.fixtures.demo_catalog import build_demo_catalogs
    from oracle_impact.infrastructure.oracle.gateway import FixtureGateway
    from oracle_impact.infrastructure.storage.run_store import RunStore
    from oracle_impact.models.domain import PackageRef
    from oracle_impact.services.analysis_service import AgenticUnavailable, AnalysisService
    from tests.support import settings

    test_catalog, prod_catalog = build_demo_catalogs()
    service = AnalysisService(
        settings(tmp_path),
        RunStore(tmp_path),
        FixtureGateway("test", "fixture-test", "TEST", test_catalog),
        FixtureGateway("production", "fixture-production", "PRODUCTION", prod_catalog),
    )
    run_id = service.create_run([PackageRef(owner="DEMO", name="PKG_STATUS")])
    request = service.store.read_json(run_id, "request.json")
    assert request["mode"] == "deterministic"
    del request["mode"]
    service.store.write_json(run_id, "request.json", request)
    service.execute(run_id)
    report = service.store.read_json(run_id, "report.json")
    assert report["run_mode"] == "deterministic"
    assert report["ai_used"] is False
    assert report["agent_summary"] == ""

    try:
        service.create_run([PackageRef(owner="DEMO", name="PKG_STATUS")], mode="agentic")
    except AgenticUnavailable:
        pass
    else:
        raise AssertionError("an agentic run was accepted while egress is off")


def test_dispatch_fixture_covers_the_recent_checks(tmp_path):
    """DEMO.PKG_DISPATCH is the fixture for dynamic SQL, synonyms, new dependencies, privileges, and the error contract."""
    from oracle_impact.analysis.blind_spots import DYNAMIC_SQL, SYNONYM, collect_blind_spots
    from tests.support import run_demo

    report = run_demo(tmp_path, ["DEMO.PKG_DISPATCH"])

    assert report.recommendation == Recommendation.NOT_RECOMMENDED
    assert _readiness(report)["decision"] == "on_hold"
    missing = {item.affected_object: item for item in report.findings if item.rule_id == "DEP-MISSING-OBJECT"}
    assert set(missing) == {"DEMO.DISPATCH_SEQ", "DEMO.NEXT_BATCH"}
    assert {item.rule_id for item in report.policy.blockers} == {"BLK-MISSING-OBJECT"}

    [dynamic] = [item for item in report.findings if item.change_category == "dynamic_sql"]
    assert dynamic.status.value == "unknown" and dynamic.attributes["routine"] == "SEND_BATCH"
    [authid] = [item for item in report.findings if item.attributes.get("authid_changed")]
    assert "DEFINER to CURRENT_USER" in authid.evidence
    contracts = [item.evidence for item in report.findings if item.change_category == "error_contract"]
    assert any("-20001" in text and "-20041" in text for text in contracts)
    assert any("QUEUE_FULL" in text and "BATCH_REJECTED" in text for text in contracts)
    assert any("E_CLOSED now maps to -20110 instead of -20100" in text for text in contracts)
    [grant] = [item for item in report.findings if item.attributes.get("grant_difference") == "missing_in_production"]
    assert "EXECUTE to DEMO_DISPATCHER" in grant.evidence
    rules = {item.rule_id for item in report.policy.validations}
    assert {"VAL-DYNAMIC-SQL", "VAL-ERROR-HANDLING", "VAL-SECURITY"} <= rules

    synonyms = [edge for edge in report.dependency_edges if edge.referenced_type == "SYNONYM" and edge.referenced_name == "QUOTE_BOOK"]
    assert {edge.database for edge in synonyms} >= {"test", "production"}
    spots, left_out = collect_blind_spots(report.findings, report.dependency_edges, 6)
    assert left_out == 0
    assert [(spot.kind, spot.id) for spot in spots] == [(DYNAMIC_SQL, "BS-1"), (SYNONYM, "BS-2")]
    assert spots[1].environments == ["test", "production"]

    assert report.compilation is not None and report.compilation.evaluated and report.compilation.errors == []
    valid = {(item.environment, item.owner, item.name, item.object_type): item.status for item in report.compilation.objects}
    assert valid[("test", "DEMO", "PKG_DISPATCH", "PACKAGE BODY")] == "VALID"
    assert valid[("test", "DEMO", "PKG_MAILER", "PACKAGE BODY")] == "VALID"

    assert not _dispatch_body(report).unchanged
    assert {env.environment for state in report.object_states for env in state.environments} == {"test", "production"}


def test_recommended_actions_come_with_scripts_built_from_the_evidence(tmp_path):
    from oracle_impact.controllers.web import TEMPLATES, _sections
    from tests.support import run_demo

    dispatch = run_demo(tmp_path / "dispatch", ["DEMO.PKG_DISPATCH"])
    scripts = {item.affected_object or item.rule_id: item.attributes["action_scripts"] for item in dispatch.findings}
    sequence = "\n".join(script["sql"] for script in scripts["DEMO.DISPATCH_SEQ"])
    assert "AND object_type IN ('SEQUENCE')" in sequence and "DBMS_METADATA.GET_DDL" in sequence
    [grant] = next(item.attributes["action_scripts"] for item in dispatch.findings if item.attributes.get("grant_difference"))
    assert "GRANT EXECUTE ON DEMO.PKG_DISPATCH TO DEMO_DISPATCHER;" in grant["sql"]
    for item in dispatch.findings:
        if item.change_category in {"dynamic_sql", "error_contract", "business_logic"} or item.attributes.get("authid_changed"):
            assert item.attributes["action_scripts"] == []

    billing = run_demo(tmp_path / "billing", ["DEMO.PKG_BILLING"])
    [compile_failure] = [item for item in billing.findings if item.change_category == "compilation"]
    assert "ALTER PACKAGE DEMO.PKG_LEGACY COMPILE BODY;" in compile_failure.attributes["action_scripts"][0]["sql"]
    [breaking] = [item for item in billing.findings if item.attributes.get("breaking")]
    assert "ALTER PACKAGE DEMO.PKG_LEGACY COMPILE BODY;" in breaking.attributes["action_scripts"][0]["sql"]
    [history] = [item for item in billing.findings if item.affected_object == "DEMO.BILLING_HISTORY" and item.attributes.get("missing_object")]
    assert "AND object_type IN ('TABLE', 'VIEW')" in history.attributes["action_scripts"][0]["sql"]

    page = TEMPLATES.get_template("report_body.html").render({"report": dispatch, "sections": _sections(dispatch)})
    assert "for this action" in page and "GRANT EXECUTE ON DEMO.PKG_DISPATCH TO DEMO_DISPATCHER;" in page


def _dispatch_body(report):
    return next(diff for diff in report.diffs if diff.owner == "DEMO" and diff.name == "PKG_DISPATCH" and diff.object_type == "PACKAGE BODY")
