"""Compare object privileges on each selected package between Test and Production."""

from dataclasses import dataclass, field

from oracle_impact.models.domain import PackageRef


@dataclass
class GrantChange:
    package: str
    kind: str
    grants: list[str] = field(default_factory=list)
    complete: bool = True
    pairs: list[tuple[str, str]] = field(default_factory=list)
    """(grantee, privilege) for each entry in grants."""


def compare_grants(package: PackageRef, test_fetch, prod_fetch) -> tuple[list[GrantChange], list[str]]:
    """Return grant differences and limitation notes. Unreadable grants are a limitation, never 'no difference'."""
    unreadable = [label for label, fetch in (("Test", test_fetch), ("Production", prod_fetch)) if fetch.inaccessible]
    if unreadable:
        return [], [
            f"Grants on {package.qualified} could not be read in {' and '.join(unreadable)}. "
            "Privilege differences were not checked."
        ]
    notes = []
    complete = test_fetch.complete and prod_fetch.complete
    if not complete:
        notes.append(
            "Grant comparison used ALL_TAB_PRIVS, which lists only grants that involve the analysis account. "
            "Grants to other users may be missing from the comparison."
        )
    test_grants = {(grantee.upper(), privilege.upper()) for grantee, privilege in test_fetch.grants}
    prod_grants = {(grantee.upper(), privilege.upper()) for grantee, privilege in prod_fetch.grants}
    changes = []
    for kind, missing in (
        ("missing_in_production", test_grants - prod_grants),
        ("missing_in_test", prod_grants - test_grants),
    ):
        if missing:
            changes.append(
                GrantChange(
                    package=package.qualified,
                    kind=kind,
                    grants=[f"{privilege} to {grantee}" for grantee, privilege in sorted(missing)],
                    complete=complete,
                    pairs=sorted(missing),
                )
            )
    return changes, notes
