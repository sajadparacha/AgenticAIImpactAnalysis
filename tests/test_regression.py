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
    _expect(tmp_path, test_catalog, prod_catalog, Recommendation.NOT_RECOMMENDED, ("data",))


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
