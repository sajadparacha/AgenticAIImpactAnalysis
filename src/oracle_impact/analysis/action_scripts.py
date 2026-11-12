"""Pasteable scripts that carry out a finding's recommended action. Nothing here is executed.

Scripts are built only from facts the deterministic checks recorded: object names, object
types, grants, and callers. Actions that need a person's judgment, such as reviewing a
business rule or an error contract, get no script.

Each script names the release stage it belongs to, so a fix plan can put them in order:
fixes made in Test, work in Production before the package is deployed, and work after it.
"""

import re
import textwrap

from oracle_impact.analysis.identifiers import is_identifier
from oracle_impact.models.domain import Snapshot
from oracle_impact.models.report import Finding

_PRIVILEGE = re.compile(r"^[A-Z]+(?: [A-Z]+)*$")
_COMPILE = {
    "PACKAGE": "ALTER PACKAGE {name} COMPILE;",
    "PACKAGE BODY": "ALTER PACKAGE {name} COMPILE BODY;",
    "PROCEDURE": "ALTER PROCEDURE {name} COMPILE;",
    "FUNCTION": "ALTER FUNCTION {name} COMPILE;",
    "TRIGGER": "ALTER TRIGGER {name} COMPILE;",
    "VIEW": "ALTER VIEW {name} COMPILE;",
    "TYPE": "ALTER TYPE {name} COMPILE;",
    "TYPE BODY": "ALTER TYPE {name} COMPILE BODY;",
    "MATERIALIZED VIEW": "ALTER MATERIALIZED VIEW {name} COMPILE;",
}
_DDL_TYPES = {
    "TABLE", "VIEW", "SEQUENCE", "FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE BODY",
    "TYPE", "TYPE BODY", "SYNONYM", "TRIGGER", "MATERIALIZED VIEW", "INDEX",
}
_CALLER_TYPES = ("PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "TYPE BODY", "VIEW")
FIX_IN_TEST, BEFORE_RELEASE, AFTER_RELEASE = "fix_in_test", "before_release", "after_release"


def apply_action_scripts(findings: list[Finding], edges=(), snapshot: Snapshot | None = None) -> None:
    types = known_types(edges, snapshot)
    for finding in findings:
        if finding.origin == "investigator":
            continue
        finding.attributes["action_scripts"] = action_scripts_for(finding, types)


def known_types(edges=(), snapshot: Snapshot | None = None) -> dict[tuple[str, str], str]:
    """Compilable object type per (owner, name), from dependency edges and captured source."""
    found: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        if edge.direction == "referenced_by":
            found.setdefault((edge.owner, edge.name), set()).add(edge.object_type)
    for item in snapshot.objects if snapshot is not None else []:
        if item.found:
            found.setdefault((item.owner, item.name), set()).add(item.object_type)
    types = {}
    for key, kinds in found.items():
        chosen = next((kind for kind in _CALLER_TYPES if kind in kinds), None)
        if chosen is not None:
            types[key] = chosen
    return types


def action_scripts_for(finding: Finding, types: dict[tuple[str, str], str] | None = None) -> list[dict[str, str]]:
    attributes = finding.attributes or {}
    if attributes.get("grant_difference"):
        return _grant_scripts(finding)
    if attributes.get("missing_object"):
        return _missing_object_scripts(finding)
    if attributes.get("dependency_unverified"):
        return _existence_check_scripts(finding)
    if finding.change_category == "compilation":
        return _compilation_scripts(finding)
    if finding.change_category == "interface":
        return _caller_scripts(finding, types or {})
    return []


def _grant_scripts(finding: Finding) -> list[dict[str, str]]:
    owner, name = _split(f"{finding.object_owner}.{finding.object_name}")
    if owner is None:
        return []
    grants = []
    for item in finding.attributes.get("grants") or []:
        grantee = str(item.get("grantee", "")).upper()
        privilege = " ".join(str(item.get("privilege", "")).upper().split())
        if is_identifier(grantee) and _PRIVILEGE.match(privilege):
            grants.append(f"GRANT {privilege} ON {owner}.{name} TO {grantee};")
    if not grants:
        return []
    if finding.attributes.get("grant_difference") == "missing_in_production":
        note = "Add these grants to the release, after the package is created in Production."
    else:
        note = "Run these only if the deployment drops and re-creates the package. CREATE OR REPLACE keeps existing grants."
    sql = _banner(finding, "production", note)
    sql += "\n".join(grants) + "\n\n"
    sql += _comment("Check the grants afterwards.")
    sql += (
        "SELECT grantee, privilege\n"
        "  FROM all_tab_privs\n"
        f" WHERE table_schema = '{owner}'\n"
        f"   AND table_name = '{name}'\n"
        " ORDER BY grantee, privilege;\n"
    )
    return [{"title": f"Grant access to {owner}.{name} in Production", "environment": "production", "stage": AFTER_RELEASE, "sql": sql}]


def _missing_object_scripts(finding: Finding) -> list[dict[str, str]]:
    owner, name = _split(finding.affected_object or "")
    if owner is None:
        return []
    object_type = str(finding.attributes.get("missing_type") or "").upper()
    if object_type and object_type not in _DDL_TYPES:
        return []
    types = [object_type] if object_type else ["TABLE", "VIEW"]
    label = object_type or "table or view"

    notes = [
        f"Part 1 of 2. This prints the DDL of {label} {owner}.{name} from Test.",
        "Your account needs SELECT_CATALOG_ROLE or must own the object.",
    ]
    if "TABLE" in types:
        notes.append("Table DDL creates an empty table. Copying rows, if any are needed, is a separate step.")
    if "SEQUENCE" in types:
        notes.append("Sequence DDL starts at the current Test value. Set START WITH to the value Production needs.")
    extract = _banner(finding, "test", *notes)
    extract += (
        "SET LONG 1000000\n"
        "SET PAGESIZE 0\n"
        "EXEC DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', TRUE);\n"
        "SELECT DBMS_METADATA.GET_DDL(REPLACE(object_type, ' ', '_'), object_name, owner)\n"
        "  FROM all_objects\n"
        f" WHERE owner = '{owner}'\n"
        f"   AND object_name = '{name}'\n"
        f"   AND object_type IN ({_quoted(types)});\n"
    )
    verify = _banner(
        finding,
        "production",
        "Part 2 of 2. Run the DDL that part 1 printed in Production, before the package is deployed.",
        f"Then confirm {owner}.{name} exists there and is VALID.",
    )
    verify += _status_query(owner, name, types)
    return [
        {"title": f"Extract the DDL of {owner}.{name} from Test", "environment": "test", "stage": BEFORE_RELEASE, "sql": extract},
        {"title": f"Create {owner}.{name} in Production and confirm it", "environment": "production", "stage": BEFORE_RELEASE, "sql": verify},
    ]


def _existence_check_scripts(finding: Finding) -> list[dict[str, str]]:
    owner, name = _split(finding.affected_object or "")
    if owner is None:
        return []
    object_type = str(finding.attributes.get("dependency_type") or "").upper()
    types = [object_type] if object_type in _DDL_TYPES else []
    sql = _banner(
        finding,
        "production",
        "The analysis account could not see this object. Run this with an account that can.",
        "No row means the object is missing and must be deployed first.",
    )
    sql += _status_query(owner, name, types)
    return [{"title": f"Check that {owner}.{name} exists in Production", "environment": "production", "stage": BEFORE_RELEASE, "sql": sql}]


def _compilation_scripts(finding: Finding) -> list[dict[str, str]]:
    owner, name = _split(f"{finding.object_owner}.{finding.object_name}")
    if owner is None:
        return []
    object_type = (finding.object_type or "").upper()
    scope = finding.attributes.get("compile_scope")
    if scope == "production":
        sql = _banner(finding, "production", "Read-only. Shows why the object is invalid in Production today.")
        sql += _status_query(owner, name, [object_type] if object_type in _COMPILE else [])
        sql += "\n" + _errors_query(owner, name, object_type)
        return [{"title": f"Inspect {owner}.{name} in Production", "environment": "production", "stage": BEFORE_RELEASE, "sql": sql}]
    statement = _COMPILE.get(object_type)
    notes = ["Fix the source first. Recompiling unchanged source returns the same errors."]
    if statement is None:
        notes.append(f"No ALTER statement is generated for object type {object_type or 'unknown'}.")
    sql = _banner(finding, "test", *notes)
    if statement:
        sql += statement.format(name=f"{owner}.{name}") + "\n\n"
    sql += _status_query(owner, name, [object_type] if statement else [])
    sql += "\n" + _errors_query(owner, name, object_type)
    return [{"title": f"Recompile {owner}.{name} in Test and read its errors", "environment": "test", "stage": FIX_IN_TEST, "sql": sql}]


def _caller_scripts(finding: Finding, types: dict[tuple[str, str], str]) -> list[dict[str, str]]:
    callers = []
    for qualified in [*finding.attributes.get("callers", []), *finding.attributes.get("coordinated_callers", [])]:
        owner, name = _split(str(qualified))
        if owner is not None and (owner, name) not in callers:
            callers.append((owner, name))
    if not callers:
        return []
    statements = []
    unknown = []
    for owner, name in callers:
        object_type = types.get((owner, name))
        if object_type is None:
            unknown.append(f"{owner}.{name}")
            continue
        statements.append(_COMPILE[object_type].format(name=f"{owner}.{name}"))
    notes = ["Update each caller to the new interface first. These statements show whether each one compiles in Test."]
    if unknown:
        notes.append(f"The object type of {', '.join(unknown)} was not captured, so no ALTER is generated for it.")
    sql = _banner(finding, "test", *notes)
    if statements:
        sql += "\n".join(statements) + "\n\n"
    in_list = ", ".join(f"('{owner}', '{name}')" for owner, name in callers)
    sql += (
        "SELECT owner, object_name, object_type, status\n"
        "  FROM all_objects\n"
        f" WHERE (owner, object_name) IN ({in_list})\n"
        " ORDER BY owner, object_name, object_type;\n\n"
        "SELECT owner, name, type, line, position, text\n"
        "  FROM all_errors\n"
        f" WHERE (owner, name) IN ({in_list})\n"
        " ORDER BY owner, name, type, sequence;\n"
    )
    return [{"title": "Compile the callers in Test", "environment": "test", "stage": FIX_IN_TEST, "sql": sql}]


def _status_query(owner: str, name: str, types: list[str]) -> str:
    sql = (
        "SELECT object_type, status, last_ddl_time\n"
        "  FROM all_objects\n"
        f" WHERE owner = '{owner}'\n"
        f"   AND object_name = '{name}'\n"
    )
    if types:
        sql += f"   AND object_type IN ({_quoted(types)})\n"
    return sql.rstrip("\n") + ";\n"


def _errors_query(owner: str, name: str, object_type: str) -> str:
    sql = (
        "SELECT line, position, text\n"
        "  FROM all_errors\n"
        f" WHERE owner = '{owner}'\n"
        f"   AND name = '{name}'\n"
    )
    if object_type in _COMPILE:
        sql += f"   AND type = '{object_type}'\n"
    return sql + " ORDER BY sequence;\n"


def _quoted(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _split(qualified: str) -> tuple[str | None, str | None]:
    parts = qualified.upper().split(".")
    if len(parts) == 2 and is_identifier(parts[0]) and is_identifier(parts[1]):
        return parts[0], parts[1]
    return None, None


def _banner(finding: Finding, environment: str, *notes: str) -> str:
    place = "Test" if environment == "test" else "Production"
    lines = [f"Run in {place} ({finding.id}). This application does not run this script.", *notes]
    return "".join(_comment(line) for line in lines if line)


def _comment(text: str) -> str:
    wrapped = textwrap.wrap(" ".join(text.split()), width=76) or [""]
    return "".join(f"-- {line}\n" for line in wrapped)
