"""Pasteable Test scripts for a finding. Nothing here is executed."""

import re
import textwrap

from oracle_impact.analysis.identifiers import _IDENTIFIER
from oracle_impact.analysis.normalize import mask_non_code
from oracle_impact.models.domain import Snapshot
from oracle_impact.models.report import Finding

_ROUTINE = re.compile(r"\b(?:PROCEDURE|FUNCTION)\s+([A-Z][A-Z0-9_$#]*)", re.IGNORECASE)
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


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
    current = (test_params or prod_params).get(routine, [])
    previous = prod_params.get(routine, current)
    new_names = [item["name"] for item in current if item["name"] not in {row["name"] for row in previous}]
    caller = _caller(finding)
    scripts: list[dict[str, str]] = []
    if finding.change_category == "interface" and caller and caller != (owner, package):
        scripts.append(_caller_script(finding, caller, snapshot, previous, new_names))
    scripts.append(_procedure_script(finding, owner, package, routine, current, new_names))
    query = _query(finding)
    if query:
        scripts.append(query)
    return [item for item in scripts if item["sql"].strip()]


def _package(finding: Finding) -> tuple[str | None, str | None]:
    owner = (finding.object_owner or "").upper()
    name = (finding.object_name or "").upper()
    if _safe(owner) and _safe(name):
        return owner, name
    return None, None


def _signature(snapshot: Snapshot, environment: str, owner: str, package: str) -> dict[str, list[dict]]:
    source = snapshot.text(environment, owner, package, "PACKAGE")
    if not source.strip():
        source = snapshot.text(environment, owner, package, "PACKAGE BODY")
    return _parse_signature(source)


def _parse_signature(source: str) -> dict[str, list[dict]]:
    masked = mask_non_code(source)
    found: dict[str, list[dict]] = {}
    for match in _ROUTINE.finditer(masked):
        name = match.group(1).upper()
        rest = masked[match.end() :].lstrip()
        params: list[dict] = []
        if rest.startswith("("):
            body, _ = _take_parens(rest)
            params = _split_params(body[1:-1])
        found.setdefault(name, params)
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
        if len(tokens) < 3 or not _safe(tokens[0].upper()):
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


def _caller_script(finding, caller, snapshot, old_params, new_names) -> dict[str, str]:
    owner, package = caller
    signatures = _signature(snapshot, "test", owner, package) or _signature(snapshot, "production", owner, package)
    routine = next(iter(signatures)) if len(signatures) == 1 else None
    if routine:
        call = _call(owner, package, routine, signatures[routine])
    else:
        call = _old_caller_call(finding, owner, package, old_params, new_names)
    missing = ", ".join(new_names) or "the new argument"
    sql = _banner(finding, f"{owner}.{package} still calls without {missing}. Check whether it still compiles and runs.")
    sql += _block(call)
    sql += f"\nALTER PACKAGE {owner}.{package} COMPILE BODY;\n"
    return {"title": f"Call {owner}.{package} the way Production does", "sql": sql}


def _old_caller_call(finding, caller_owner, caller_package, old_params, new_names) -> str:
    routine = None
    for step in finding.dependency_path:
        tail = step.split("->")[-1].strip().split(".")[-1].upper()
        if _safe(tail):
            routine = tail
            break
    if routine is None:
        return (
            f"  -- {caller_owner}.{caller_package} was not captured in this snapshot.\n"
            "  -- Run its existing procedure in Test. It still uses the old argument list."
        )
    kept = [item for item in old_params if item["name"] not in new_names or item["defaulted"]]
    return _call(finding.object_owner.upper(), finding.object_name.upper(), routine, kept)


def _procedure_script(finding, owner, package, routine, params, new_names) -> dict[str, str]:
    call = _call(owner, package, routine, params)
    if finding.change_category == "transaction":
        call = (
            f"{call.rstrip()}\n"
            "EXCEPTION\n"
            "  WHEN OTHERS THEN\n"
            "    DBMS_OUTPUT.PUT_LINE(SQLERRM);\n"
            "    -- Work saved before this error stays saved. Check the table before you roll back.\n"
            "    RAISE;\n"
        )
    target = (finding.affected_object or "").upper()
    notes = []
    if new_names:
        notes.append("New arguments: " + ", ".join(new_names) + ".")
    if _object_missing(finding):
        notes.append(
            f"{target or 'The referenced object'} is missing, so this call is expected to fail in Test. "
            "Production would fail the same way after the release. Do not run this in Production."
        )
    else:
        notes.append("The values below are samples. Replace them with real Test data.")
    numbers = _boundary_numbers(finding)
    if numbers:
        notes.append("Try values around " + ", ".join(numbers) + ".")
    if finding.change_category == "dynamic_sql":
        notes.append("This procedure builds SQL while it runs. Turn on logging to see which tables that SQL changes.")
    sql = _banner(finding, *notes)
    sql += _block(call)
    return {"title": f"Call {owner}.{package}.{routine}", "sql": sql}


def _query(finding: Finding) -> dict[str, str] | None:
    target = (finding.affected_object or "").upper()
    parts = target.split(".")
    if len(parts) != 2 or not _safe(parts[0]) or not _safe(parts[1]):
        return None
    if finding.change_category not in {"data", "transaction"}:
        return None
    if _object_missing(finding):
        note = (
            f"{target} is missing, so this select is expected to fail in Test. "
            "Production would fail the same way after the release. Do not run this in Production."
        )
    else:
        note = f"Run this after the call to see the rows in {target}."
    sql = _banner(finding, note)
    sql += f"SELECT *\n  FROM {parts[0]}.{parts[1]}\n WHERE ROWNUM <= 20;\n"
    return {"title": f"Look at {target}", "sql": sql}


def _call(owner: str, package: str, routine: str, params: list[dict]) -> str:
    incoming = [item for item in params if item["mode"] != "OUT" and _safe(item["name"])]
    if not incoming:
        return f"  {owner}.{package}.{routine};"
    lines = [f"    {item['name']} => {_literal(item)}" for item in incoming]
    return f"  {owner}.{package}.{routine}(\n" + ",\n".join(lines) + "\n  );"


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


def _block(call: str) -> str:
    return "SET SERVEROUTPUT ON\nBEGIN\n" + call.rstrip() + "\nEND;\n/\n"


def _object_missing(finding: Finding) -> bool:
    if finding.attributes.get("missing_object"):
        return True
    text = f"{finding.explanation} {finding.evidence}".lower()
    return any(
        phrase in text
        for phrase in ("not found", "does not exist", "doesn't exist", "does not have", "not in production")
    )


def _banner(finding: Finding, *notes: str) -> str:
    goal = " ".join(item for item in finding.proposed_tests if len(item.split()) >= 4)
    if not goal and finding.origin != "investigator":
        goal = finding.recommended_action
    lines = [f"Run in Test only ({finding.id}). This application does not run this script."]
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
