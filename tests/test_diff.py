import pytest

from oracle_impact.analysis.diff_engine import build_object_diff
from oracle_impact.analysis.identifiers import IdentifierError, parse_package_list
from oracle_impact.analysis.normalize import normalize_plsql


def test_rejects_lowercase_and_unqualified_names():
    with pytest.raises(IdentifierError):
        parse_package_list("demo.pkg_status")
    with pytest.raises(IdentifierError):
        parse_package_list("PKG_STATUS")
    parsed = parse_package_list("DEMO.PKG_STATUS, DEMO.PKG_BILLING\nDEMO.PKG_STATUS")
    assert [item.qualified for item in parsed] == ["DEMO.PKG_STATUS", "DEMO.PKG_BILLING"]


def test_side_by_side_marks_added_and_removed_lines():
    diff = build_object_diff(
        "DEMO",
        "PKG_STATUS",
        "PACKAGE BODY",
        "A\nB\nC\n",
        "A\nC\n",
        "test-hash",
        "prod-hash",
    )
    assert diff.added == 1
    assert diff.removed == 0
    assert any(row.test_text == "B" and row.test_kind == "added" for row in diff.rows)
    assert any(row.test_line_no == 1 and row.prod_line_no == 1 for row in diff.rows)


def test_comment_and_whitespace_differences_are_formatting_only():
    prod = "BEGIN\n  NULL;\nEND;\n"
    test = "BEGIN -- note\n\nNULL;\nEND;\n"
    assert normalize_plsql(prod) == normalize_plsql(test)
    diff = build_object_diff("DEMO", "PKG", "PACKAGE BODY", test, prod, "a", "b")
    assert diff.formatting_only is True
