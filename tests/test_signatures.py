from oracle_impact.analysis.signatures import compare_package_signatures
from oracle_impact.models.domain import PackageRef, Snapshot
from tests.support import argument


def _snapshot() -> Snapshot:
    return Snapshot(packages=["APP.PKG_API"], captured_at="2026-10-21T00:00:00+00:00")


def test_optional_suffix_is_not_a_break():
    prod = [argument("RUN", "P_ID", 1, "NUMBER")]
    test = prod + [argument("RUN", "P_FLAG", 2, "BOOLEAN", defaulted=True)]
    changes = compare_package_signatures(
        PackageRef(owner="APP", name="PKG_API"),
        test,
        prod,
        False,
        False,
        _snapshot(),
        {"APP.PKG_API"},
        [],
    )
    assert len(changes) == 1
    assert changes[0].kind == "compatible_optional"
    assert changes[0].breaking is False


def test_mandatory_parameter_with_external_caller_breaks():
    prod = [argument("RUN", "P_ID", 1, "NUMBER")]
    test = prod + [argument("RUN", "P_MODE", 2, "VARCHAR2", defaulted=False)]
    changes = compare_package_signatures(
        PackageRef(owner="APP", name="PKG_API"),
        test,
        prod,
        False,
        False,
        _snapshot(),
        {"APP.PKG_API"},
        [("APP", "PKG_LEGACY")],
    )
    assert changes[0].breaking is True
    assert "APP.PKG_LEGACY" in changes[0].external_callers


def _compare(prod, test, callers=()):
    return compare_package_signatures(
        PackageRef(owner="APP", name="PKG_API"), test, prod, False, False, _snapshot(), {"APP.PKG_API"}, list(callers)
    )


def test_reordered_parameters_break_positional_calls():
    prod = [argument("RUN", "P_ID", 1, "NUMBER"), argument("RUN", "P_CODE", 2, "VARCHAR2")]
    test = [argument("RUN", "P_CODE", 1, "VARCHAR2"), argument("RUN", "P_ID", 2, "NUMBER")]
    changes = _compare(prod, test, [("APP", "PKG_LEGACY")])
    assert [item.kind for item in changes] == ["reordered_parameters"]
    assert changes[0].breaking and "Positional calls" in changes[0].detail


def test_renamed_parameter_breaks_named_notation():
    prod = [argument("RUN", "P_ID", 1, "NUMBER")]
    test = [argument("RUN", "P_ACCOUNT_ID", 1, "NUMBER")]
    changes = _compare(prod, test)
    assert [item.kind for item in changes] == ["renamed_parameter"]
    assert changes[0].breaking and "P_ID to P_ACCOUNT_ID" in changes[0].detail


def test_removed_default_breaks_and_added_default_does_not():
    plain = [argument("RUN", "P_ID", 1, "NUMBER")]
    defaulted = [argument("RUN", "P_ID", 1, "NUMBER", defaulted=True)]
    removed = _compare(defaulted, plain)
    assert [item.kind for item in removed] == ["default_removed"] and removed[0].breaking
    added = _compare(plain, defaulted)
    assert [item.kind for item in added] == ["default_added"] and not added[0].breaking
