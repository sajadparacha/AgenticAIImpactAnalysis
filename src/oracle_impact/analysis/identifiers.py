"""Oracle identifier rules for Version 1.

Quoted identifiers and lowercase names are rejected. Callers must submit
unquoted, already-uppercase owner.package names.
"""

import re

from oracle_impact.models.domain import PackageRef

_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")

ORACLE_MAINTAINED_SCHEMAS = frozenset(
    {
        "SYS",
        "SYSTEM",
        "PUBLIC",
        "CTXSYS",
        "MDSYS",
        "XDB",
        "WMSYS",
        "OLAPSYS",
        "ORDSYS",
        "ORDDATA",
        "DBSNMP",
        "OUTLN",
        "DVSYS",
        "GSMADMIN_INTERNAL",
        "LBACSYS",
        "OJVMSYS",
        "APPQOSSYS",
        "AUDSYS",
    }
)


class IdentifierError(ValueError):
    """Raised when a package name fails the allowlist."""


def is_identifier(value: str) -> bool:
    return bool(_IDENTIFIER.fullmatch(value))


def parse_package_name(value: str) -> PackageRef:
    raw = value.strip()
    if raw.count(".") != 1:
        raise IdentifierError(
            f"'{value}' must be an uppercase OWNER.PACKAGE name. "
            "Quoted and lowercase identifiers are not accepted."
        )
    owner, name = raw.split(".", 1)
    if not is_identifier(owner) or not is_identifier(name):
        raise IdentifierError(
            f"'{value}' must use unquoted uppercase Oracle identifiers."
        )
    return PackageRef(owner=owner, name=name)


def parse_package_list(value: str) -> list[PackageRef]:
    chunks = re.split(r"[\s,;]+", value.strip())
    names = [chunk for chunk in chunks if chunk]
    if not names:
        raise IdentifierError("Enter at least one OWNER.PACKAGE name.")
    parsed: list[PackageRef] = []
    seen: set[str] = set()
    for name in names:
        package = parse_package_name(name)
        if package.qualified in seen:
            continue
        seen.add(package.qualified)
        parsed.append(package)
    return parsed
