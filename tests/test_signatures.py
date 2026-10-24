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
