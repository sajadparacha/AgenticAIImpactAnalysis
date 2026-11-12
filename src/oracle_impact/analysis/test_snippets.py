"""Pasteable Test scripts for a finding. Nothing here is executed."""

import re
import textwrap

from oracle_impact.analysis.identifiers import _IDENTIFIER
from oracle_impact.analysis.normalize import mask_non_code
from oracle_impact.models.domain import Snapshot
from oracle_impact.models.report import Finding

_ROUTINE = re.compile(r"\b(PROCEDURE|FUNCTION)\s+([A-Z][A-Z0-9_$#]*)", re.IGNORECASE)
_RETURN = re.compile(r"^\s*RETURN\s+([A-Za-z][A-Za-z0-9_$#.]*(?:\s*\([^)]*\))?)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+([A-Za-z][A-Za-z0-9_$#]*(?:\s*\.\s*[A-Za-z][A-Za-z0-9_$#]*)?)", re.IGNORECASE)
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_PRINTABLE = {"NUMBER", "INTEGER", "VARCHAR2", "VARCHAR", "CHAR", "NCHAR", "NVARCHAR2", "DATE", "CLOB", "NCLOB"}


def apply_test_snippets(findings: list[Finding], snapshot: Snapshot | None) -> None:
    if snapshot is None:
        return
    for finding in findings:
        finding.attributes["test_snippets"] = snippets_for(finding, snapshot)


def snippets_for(finding: Finding, snapshot: Snapshot) -> list[dict[str, str]]:
    owner, package = _package(finding)
    if owner is None or package is None:
        return []
    test_params = _signature(snapshot, "test", owner, package)
    prod_params = _signature(snapshot, "production", owner, package)
    routine = _routine_name(finding, test_params or prod_params)
    if routine is None:
        return []
    current = (test_params or prod_params).get(routine, _EMPTY_SPEC)
    previous = prod_params.get(routine, _EMPTY_SPEC)
    new_names = [item["name"] for item in current["params"] if item["name"] not in {row["name"] for row in previous["params"]}]
    caller = _caller(finding)
    scripts: list[dict[str, str]] = []
    if finding.change_category == "interface" and caller and caller != (owner, package):
        scripts.append(_caller_script(finding, caller, snapshot, current, previous, new_names))
    body = _body(snapshot, "test", owner, package) or _body(snapshot, "production", owner, package)
    scripts.append(_procedure_script(finding, owner, package, routine, current, new_names, body))
    query = _query(finding)
    if query:
        scripts.append(query)
    return [item for item in scripts if item["sql"].strip()]


_EMPTY_SPEC = {"kind": "PROCEDURE", "return_type": None, "params": []}


def _package(finding: Finding) -> tuple[str | None, str | None]:
    owner = (finding.object_owner or "").upper()
    name = (finding.object_name or "").upper()
    if _safe(owner) and _safe(name):
        return owner, name
    return None, None


def _signature(snapshot: Snapshot, environment: str, owner: str, package: str) -> dict[str, dict]:
    source = snapshot.text(environment, owner, package, "PACKAGE")
    if not source.strip():
        source = snapshot.text(environment, owner, package, "PACKAGE BODY")
    return _parse_signature(source)


def _body(snapshot: Snapshot, environment: str, owner: str, package: str) -> str:
    source = snapshot.text(environment, owner, package, "PACKAGE BODY")
    if source.strip():
        return source
    return snapshot.text(environment, owner, package, "PACKAGE")


def _find_lookups(source: str, owner: str, params: list[dict]) -> dict[str, tuple[str, str]]:
    """Parameter -> (table, column) for `SELECT ... INTO ... FROM table WHERE column = parameter`.

    The script can then read a value that the routine itself accepts, instead of
    inventing one that matches no row.
    """
    masked = mask_non_code(source)
    wanted = [item["name"] for item in params if item["mode"] != "OUT"]
    found: dict[str, tuple[str, str]] = {}
    if not wanted:
        return found
    for statement in masked.split(";"):
        upper = " ".join(statement.upper().split())
        if " INTO " not in upper or " FROM " not in upper:
            continue
        match = _FROM.search(statement)
        if match is None:
            continue
        table = re.sub(r"\s+", "", match.group(1)).upper()
        if not all(_safe(part) for part in table.split(".")):
            continue
        where = statement[match.end() :]
        for name in wanted:
            if name in found:
                continue
            column = _compared_column(where, name)
            if column is None:
                continue
            found[name] = (table if "." in table else f"{owner}.{table}", column)
    return found


def _compared_column(where: str, param: str) -> str | None:
    """The column compared with a parameter, in either order."""
    for pattern in (
        rf"\b([A-Za-z][A-Za-z0-9_$#]*)\s*=\s*{re.escape(param)}\b",
        rf"\b{re.escape(param)}\s*=\s*([A-Za-z][A-Za-z0-9_$#]*)\b",
    ):
        match = re.search(pattern, where, re.IGNORECASE)
        if match is not None and _safe(match.group(1).upper()):
            return match.group(1).upper()
    return None


def _parse_signature(source: str) -> dict[str, dict]:
    """Routine name -> kind, return type, and parameters. A name declared twice keeps the first."""
    masked = mask_non_code(source)
    found: dict[str, dict] = {}
    for match in _ROUTINE.finditer(masked):
        kind = match.group(1).upper()
        name = match.group(2).upper()
        rest = masked[match.end() :].lstrip()
        params: list[dict] = []
        tail = rest
        if rest.startswith("("):
            body, tail = _take_parens(rest)
            params = _split_params(body[1:-1])
        return_type = None
        if kind == "FUNCTION":
            declared = _RETURN.match(tail.split(";")[0])
            if declared is not None:
                return_type = " ".join(declared.group(1).upper().split())
        found.setdefault(name, {"kind": kind, "return_type": return_type, "params": params})
    return found


def _take_parens(text: str) -> tuple[str, str]:
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[: index + 1], text[index + 1 :]
    return text, ""


def _split_params(text: str) -> list[dict]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    params = []
    for part in parts:
        tokens = part.split()
        if len(tokens) < 2 or not _safe(tokens[0].upper()):
            continue
        mode = "IN"
        type_at = 1
        if tokens[1].upper() in {"IN", "OUT"}:
            mode = "OUT" if tokens[1].upper() == "OUT" else "IN"
            if len(tokens) > 2 and tokens[1].upper() == "IN" and tokens[2].upper() == "OUT":
                mode = "IN OUT"
                type_at = 3
            else:
                type_at = 2
        if type_at >= len(tokens):
            continue
        data_type = tokens[type_at].upper()
        defaulted = "DEFAULT" in part.upper() or ":=" in part
        params.append({"name": tokens[0].upper(), "mode": mode, "data_type": data_type, "defaulted": defaulted})
    return params


def _routine_name(finding: Finding, signatures: dict[str, list[dict]]) -> str | None:
    if not signatures:
        return None
    blob = f"{finding.explanation} {finding.evidence}".upper()
    named = [name for name in signatures if name in blob]
    if len(named) == 1:
        return named[0]
    for step in finding.dependency_path:
        tail = step.split("->")[-1].strip().split(".")[-1].upper()
        if tail in signatures:
            return tail
    if len(signatures) == 1:
        return next(iter(signatures))
    return named[0] if named else None


def _caller(finding: Finding) -> tuple[str, str] | None:
    for step in finding.dependency_path:
        left = step.split("->")[0].strip().upper()
        parts = left.split(".")
        if len(parts) >= 2 and _safe(parts[0]) and _safe(parts[1]):
            return parts[0], parts[1]
    affected = (finding.affected_object or "").upper()
    parts = affected.split(".")
    if len(parts) == 2 and _safe(parts[0]) and _safe(parts[1]) and finding.change_category == "interface":
        return parts[0], parts[1]
    return None


def _caller_script(finding, caller, snapshot, changed_spec, previous, new_names) -> dict[str, str]:
    """Show what the caller does with the new interface. The script calls what it can see."""
    owner, package = caller
    changed = f"{finding.object_owner.upper()}.{finding.object_name.upper()}"
    changed_routine = _changed_routine(finding)
    caller_specs = _signature(snapshot, "test", owner, package) or _signature(snapshot, "production", owner, package)
    routine = next(iter(caller_specs)) if len(caller_specs) == 1 else None
    notes: list[str] = []
    if routine:
        script = _call_script(
            finding, owner, package, routine, caller_specs[routine],
            _body(snapshot, "test", owner, package) or _body(snapshot, "production", owner, package),
        )
        title = f"Call {owner}.{package}.{routine} in Test"
        notes.append(f"{owner}.{package} is a caller of {changed} and was captured, so this calls it directly.")
    elif changed_routine:
        kept = [item for item in previous["params"] if item["name"] not in new_names or item["defaulted"]]
        mandatory = [item for item in changed_spec["params"] if item["name"] in new_names and not item["defaulted"]]
        script = _call_script(
            finding, finding.object_owner.upper(), finding.object_name.upper(), changed_routine,
            {"kind": changed_spec["kind"], "return_type": changed_spec["return_type"], "params": kept},
            "",
            catch_errors=not mandatory,
        )
        title = f"Call {changed}.{changed_routine} with the Production argument list"
        notes.append(
            f"{owner}.{package} was not captured in this snapshot, so this calls {changed} directly with the "
            "argument list that caller passes today."
        )
        if mandatory:
            notes.append(
                f"{changed_routine} now requires {', '.join(item['name'] for item in mandatory)}, so this block does "
                "not compile. PLS-00306 is the error that caller gets, and it is the expected result here."
            )
            script["expectation"] = ""
    else:
        return {"title": f"Call {owner}.{package} in Test", "sql": ""}
    notes.append(script.pop("expectation", ""))
    sql = _banner(finding, *[note for note in notes if note])
    sql += script["sql"]
    compile_statement = _compile_statement(snapshot, owner, package)
    if compile_statement:
        sql += f"\n{compile_statement}\n"
    else:
        sql += "\n"
        sql += _comment(f"The object type of {owner}.{package} was not captured, so no compile statement is generated. Compile it in your IDE and read ALL_ERRORS.")
    return {"title": title, "sql": sql}


def _changed_routine(finding: Finding) -> str | None:
    for step in finding.dependency_path:
        parts = step.split("->")[-1].strip().split(".")
        if parts and _safe(parts[-1]):
            return parts[-1].upper()
    return None


def _compile_statement(snapshot: Snapshot, owner: str, name: str) -> str | None:
    """Only emit ALTER when the captured snapshot proves the object type."""
    for environment in ("test", "production"):
        if snapshot.text(environment, owner, name, "PACKAGE").strip() or snapshot.text(environment, owner, name, "PACKAGE BODY").strip():
            return f"ALTER PACKAGE {owner}.{name} COMPILE BODY;"
        if snapshot.text(environment, owner, name, "PROCEDURE").strip():
            return f"ALTER PROCEDURE {owner}.{name} COMPILE;"
        if snapshot.text(environment, owner, name, "FUNCTION").strip():
            return f"ALTER FUNCTION {owner}.{name} COMPILE;"
    return None


def _procedure_script(finding, owner, package, routine, spec, new_names, body: str) -> dict[str, str]:
    script = _call_script(finding, owner, package, routine, spec, body)
    target = (finding.affected_object or "").upper()
    missing = _missing_from_production(finding)
    notes = [script["expectation"]]
    if new_names:
        notes.append("New arguments: " + ", ".join(new_names) + ".")
    if missing:
        notes.append(
            f"{target or 'The referenced object'} was not found in Production. Test has it and "
            "the new package; Production still has the old one. This call runs in Test, so it "
            "succeeds there and cannot reproduce that failure. In Production it calls the old "
            "package, so a clean run there does not clear it either. Deploy the object to "
            "Production before the new package body, or that body fails to compile there."
        )
    if script["invented"]:
        notes.append(
            "The analyzer could not read a value for "
            + ", ".join(script["invented"])
            + " from the package source, so those are samples. Edit them, then run the script again."
        )
    numbers = _boundary_numbers(finding)
    if numbers:
        notes.append("Try values around " + ", ".join(numbers) + ".")
    if finding.change_category == "dynamic_sql":
        notes.append("This procedure builds SQL while it runs. Turn on logging to see which tables that SQL changes.")
    sql = _banner(finding, *notes, goal=_test_goal(finding))
    sql += script["sql"]
    return {"title": f"Call {owner}.{package}.{routine}", "sql": sql}


def _call_script(finding, owner, package, routine, spec, body: str, catch_errors: bool = True) -> dict:
    """A block that supplies real inputs, runs the routine, and prints what happened.

    Guessing a literal made the call stop with ORA-01403 before the changed code
    ran, so every value the source lets us read is read inside the script. The
    block prints the outcome instead of stopping, and never leaves the caller
    guessing which line was the result.
    """
    params = [item for item in spec["params"] if _safe(item["name"])]
    lookups = _find_lookups(body, owner, params)
    declare: list[str] = []
    fetch: list[str] = []
    args: list[str] = []
    invented: list[str] = []
    prints: list[str] = []
    for item in params:
        variable = f"v_{item['name'].lower()}"
        if item["mode"] in {"OUT", "IN OUT"}:
            declare.append(f"  {variable} {item['data_type']};")
            args.append(f"    {item['name']} => {variable}")
            if _printable(item):
                prints.append(f"  DBMS_OUTPUT.PUT_LINE('{item['name']} = ' || {variable});")
            continue
        lookup = lookups.get(item["name"])
        if lookup is not None:
            table, column = lookup
            declare.append(f"  {variable} {_declared_type(item)};")
            fetch.append(
                f"    SELECT {column} INTO {variable}\n"
                f"      FROM {table}\n"
                f"     WHERE {column} IS NOT NULL\n"
                f"       AND ROWNUM = 1;"
            )
            args.append(f"    {item['name']} => {variable}")
            continue
        args.append(f"    {item['name']} => {_literal(item)}")
        if item["data_type"].split("(")[0].strip().upper() != "BOOLEAN":
            invented.append(item["name"])
    call = f"{owner}.{package}.{routine}"
    lines = ["SET SERVEROUTPUT ON"]
    if declare or spec["kind"] == "FUNCTION":
        lines.append("DECLARE")
        lines.extend(declare)
        if spec["kind"] == "FUNCTION":
            lines.append(f"  v_result {_safe_return(spec['return_type'])};")
    lines.append("BEGIN")
    if fetch:
        lines.append("  -- Read values the routine accepts, so it does not stop early.")
        lines.append("  BEGIN")
        lines.extend(fetch)
        lines.append("  EXCEPTION")
        lines.append("    WHEN NO_DATA_FOUND THEN")
        lines.append("      DBMS_OUTPUT.PUT_LINE(")
        lines.append("        'No usable row in the lookup table. Add one, then run again.');")
        lines.append("      RETURN;")
        lines.append("  END;")
    assignment = "  v_result := " if spec["kind"] == "FUNCTION" else "  "
    if args:
        lines.append(f"{assignment}{call}(")
        for index, argument in enumerate(args):
            lines.append(argument + ("," if index < len(args) - 1 else ""))
        lines.append("  );")
    else:
        lines.append(f"{assignment}{call};")
    if spec["kind"] == "FUNCTION" and _printable({"data_type": spec["return_type"] or ""}):
        lines.append("  DBMS_OUTPUT.PUT_LINE('Returned: ' || v_result);")
    lines.extend(prints)
    if catch_errors:
        lines.append("  DBMS_OUTPUT.PUT_LINE('The call completed without an error.');")
        lines.append("EXCEPTION")
        lines.append("  WHEN OTHERS THEN")
        lines.append("    DBMS_OUTPUT.PUT_LINE('The call raised: ' || SQLERRM);")
        if finding.change_category == "transaction":
            lines.append("    -- Work saved before this error stays saved.")
            lines.append("    -- Check the table before you roll back.")
    lines.append("END;")
    lines.append("/")
    if not catch_errors:
        expectation = "This block does not compile while the argument list is short. The error is the expected result."
    elif spec["kind"] == "FUNCTION":
        expectation = "The script prints the value the function returned, or the error it raised. It does not stop."
    else:
        expectation = "The script prints whether the call completed or which error it raised. It does not stop."
    return {"sql": "\n".join(lines) + "\n", "invented": invented, "expectation": expectation}


def _declared_type(item: dict) -> str:
    """The type the routine declares for the parameter, which is what it accepts.

    Using the column type instead would break when the body reads a different
    table from the one the statement names first.
    """
    declared = item["data_type"].strip()
    return declared or "VARCHAR2(4000)"


def _safe_return(data_type: str | None) -> str:
    if not data_type:
        return "VARCHAR2(4000)"
    base = data_type.split("(")[0].strip()
    if _safe(base) and base in _PRINTABLE | {"BOOLEAN", "PLS_INTEGER", "BINARY_INTEGER", "BINARY_FLOAT", "BINARY_DOUBLE"}:
        if "(" not in data_type and ("CHAR" in base or base in {"CLOB", "NCLOB"}):
            return f"{data_type}(4000)"
        return data_type
    return "VARCHAR2(4000)"


def _printable(item: dict) -> bool:
    return item["data_type"].split("(")[0].strip().upper() in _PRINTABLE


def _query(finding: Finding) -> dict[str, str] | None:
    target = (finding.affected_object or "").upper()
    parts = target.split(".")
    if len(parts) != 2 or not _safe(parts[0]) or not _safe(parts[1]):
        return None
    if finding.change_category not in {"data", "transaction"}:
        return None
    if _missing_from_production(finding):
        note = (
            f"{target} was not found in Production. Test has it; Production still has the old "
            "schema. This select runs in Test, so it succeeds there and does not show that "
            "failure. Deploy the object to Production before the release."
        )
    else:
        note = f"Run this after the call to see the rows in {target}."
    sql = _banner(finding, note)
    sql += f"SELECT *\n  FROM {parts[0]}.{parts[1]}\n WHERE ROWNUM <= 20;\n"
    return {"title": f"Look at {target}", "sql": sql}


def _literal(param: dict) -> str:
    kind = param["data_type"].upper()
    if param["defaulted"] and param["name"].endswith("RECALC"):
        return "FALSE"
    if kind in {"NUMBER", "INTEGER", "PLS_INTEGER", "BINARY_INTEGER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}:
        return "1"
    if kind == "DATE":
        return "DATE '2026-01-01'"
    if kind.startswith("TIMESTAMP"):
        return "TIMESTAMP '2026-01-01 00:00:00'"
    if kind == "BOOLEAN":
        return "TRUE" if not param["defaulted"] else "FALSE"
    if "CHAR" in kind or kind in {"CLOB", "NCLOB"}:
        return "'X'"
    return "NULL"


def _missing_from_production(finding: Finding) -> bool:
    """The object was not found in Production. It is the target of the release, not of a Test run."""
    if finding.attributes.get("missing_object"):
        return True
    text = f"{finding.explanation} {finding.evidence}".lower()
    return any(
        phrase in text
        for phrase in ("not found", "does not exist", "doesn't exist", "does not have", "not in production")
    )


def _goal(finding: Finding) -> str:
    goal = " ".join(item for item in finding.proposed_tests if len(item.split()) >= 4)
    if not goal and finding.origin != "investigator":
        goal = finding.recommended_action
    return goal


def _test_goal(finding: Finding) -> str:
    """The goal line belongs in the banner only when it describes something Test can show."""
    goal = _goal(finding)
    return "" if "production" in goal.lower() else goal


def _banner(finding: Finding, *notes: str, goal: str | None = None) -> str:
    if goal is None:
        goal = _goal(finding)
    lines = [f"Run in Test only ({finding.id}). Do not run it in Production. This application does not run this script."]
    if goal:
        lines.append("Goal: " + goal)
    lines.extend(note for note in notes if note)
    return "".join(_comment(line) for line in lines)


def _comment(text: str) -> str:
    wrapped = textwrap.wrap(" ".join(text.split()), width=76) or [""]
    return "".join(f"-- {line}\n" for line in wrapped)


def _boundary_numbers(finding: Finding) -> list[str]:
    if "comparison" not in finding.explanation.lower():
        return []
    return _NUMBER.findall(finding.explanation)[:4]


def _safe(value: str) -> bool:
    return bool(_IDENTIFIER.match(value or ""))
