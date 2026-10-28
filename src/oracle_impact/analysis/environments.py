"""How the two compared environments are named in findings and on screen.

A configured label (`OIA_TEST_ORACLE_LABEL`, `OIA_PROD_ORACLE_LABEL`) names the
database a report points at. It is not a role name, and people can set the same
label for both environments — a local setup often points Test and Production at
one database. Naming a finding only by label then produces a sentence such as
"is INVALID in LOCAL but VALID in LOCAL", which says nothing.

So prose always names the role, Test or Production. The label is added in
brackets only when the two labels differ, because that is the only case where
it tells the reader which database is meant.
"""

ROLE_NAMES = {"test": "Test", "production": "Production"}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def environment_name(role: str, labels: dict[str, str] | None = None) -> str:
    """`Production` or `Production (FINPRD)`, never a bare label."""
    name = ROLE_NAMES.get(role) or role.replace("_", " ").capitalize()
    labels = labels or {}
    label = _clean(labels.get(role))
    if not label or label.casefold() == name.casefold():
        return name
    distinct = {_clean(labels.get(key)).casefold() for key in ROLE_NAMES}
    return f"{name} ({label})" if len(distinct) > 1 else name
