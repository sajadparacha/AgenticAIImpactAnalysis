"""Lightweight PL/SQL scanner.

This is not a parser. It finds routine bounds and statement shapes that the
deterministic checks need, and it leaves genuinely dynamic SQL unresolved.
"""

from dataclasses import dataclass, field
import re

from oracle_impact.analysis.normalize import mask_non_code

_ROUTINE = re.compile(r"\b(PROCEDURE|FUNCTION)\s+([A-Z][A-Z0-9_$#]*)", re.IGNORECASE)
_SQL = re.compile(
    r"\b(INSERT\s+INTO|DELETE\s+FROM|MERGE\s+INTO|UPDATE|FROM|JOIN)\s+"
    r"([A-Z][A-Z0-9_$#]*(?:\.[A-Z][A-Z0-9_$#]*)?)",
    re.IGNORECASE,
)
_COMMIT = re.compile(r"\bCOMMIT\b", re.IGNORECASE)
_ROLLBACK = re.compile(r"\bROLLBACK\b", re.IGNORECASE)
_SAVEPOINT = re.compile(r"\bSAVEPOINT\b", re.IGNORECASE)
_AUTONOMOUS = re.compile(r"\bPRAGMA\s+AUTONOMOUS_TRANSACTION\b", re.IGNORECASE)
_DYNAMIC = re.compile(r"\bEXECUTE\s+IMMEDIATE\b|\bOPEN\b[^;]*\bFOR\b", re.IGNORECASE)
_WHEN = re.compile(r"\bWHEN\s+([A-Z][A-Z0-9_$#]*|OTHERS)\b", re.IGNORECASE)
_THRESHOLD = re.compile(
    r"\b([A-Z][A-Z0-9_$#]*)\s*(>=|<=|<>|!=|=|>|<)\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_FINANCIAL = re.compile(r"TAX|RATE|AMOUNT|FEE|PRICE|DISCOUNT|TOTAL", re.IGNORECASE)
_SKIP_TABLES = {"DUAL", "SELECT"}


@dataclass
class SqlRef:
    operation: str
    owner: str | None
    table: str
    line: int
    statement: str

    @property
    def qualified(self) -> str:
        if self.owner:
            return f"{self.owner}.{self.table}"
        return self.table


@dataclass
class RoutineScan:
    name: str
    kind: str
    start_line: int
    end_line: int
    sql_refs: list[SqlRef] = field(default_factory=list)
    commit_lines: list[int] = field(default_factory=list)
    rollback_lines: list[int] = field(default_factory=list)
    savepoint_lines: list[int] = field(default_factory=list)
    autonomous_lines: list[int] = field(default_factory=list)
    dynamic_lines: list[int] = field(default_factory=list)
    swallowed_lines: list[int] = field(default_factory=list)
    handler_lines: list[int] = field(default_factory=list)
    thresholds: list[tuple[int, str, str, str]] = field(default_factory=list)
    events: list[tuple[int, int, str]] = field(default_factory=list)

    @property
    def transaction_sequence(self) -> tuple[str, ...]:
        ordered = sorted(self.events, key=lambda item: (item[0], item[1]))
        return tuple(kind for _, _, kind in ordered)


@dataclass
class PackageScan:
    routines: list[RoutineScan]
    masked_lines: list[str]

    def routine_at(self, line: int) -> RoutineScan | None:
        for routine in self.routines:
            if routine.start_line <= line <= routine.end_line:
                return routine
        return None


def _operation(keyword: str) -> str:
    upper = " ".join(keyword.upper().split())
    if upper == "INSERT INTO":
        return "INSERT"
    if upper == "DELETE FROM":
        return "DELETE"
    if upper == "MERGE INTO":
        return "MERGE"
    if upper == "UPDATE":
        return "UPDATE"
    return "SELECT"


def _statement(lines: list[str], index: int) -> str:
    start = index
    while start > 0 and ";" not in lines[start - 1]:
        start -= 1
        if index - start > 30:
            break
    end = index
    while end < len(lines) and ";" not in lines[end]:
        end += 1
        if end - index > 30:
            break
    return " ".join(line.strip() for line in lines[start : end + 1] if line.strip())


def _split_table(token: str) -> tuple[str | None, str]:
    if "." in token:
        owner, table = token.split(".", 1)
        return owner.upper(), table.upper()
    return None, token.upper()


def scan_package(source: str) -> PackageScan:
    masked = mask_non_code(source)
    lines = masked.splitlines()
    original = source.splitlines()
    headers = [
        (match.start(), match.group(1).upper(), match.group(2).upper())
        for match in _ROUTINE.finditer(masked)
    ]
    line_starts = []
    cursor = 0
    for line in lines:
        line_starts.append(cursor)
        cursor += len(line) + 1

    def line_number(offset: int) -> int:
        number = 1
        for index, start in enumerate(line_starts):
            if start <= offset:
                number = index + 1
            else:
                break
        return number

    ranges: list[tuple[int, int, str, str]] = []
    for index, (offset, kind, name) in enumerate(headers):
        start = line_number(offset)
        end = len(lines)
        if index + 1 < len(headers):
            end = line_number(headers[index + 1][0]) - 1
        ranges.append((start, max(start, end), kind, name))
    if not ranges and lines:
        ranges.append((1, len(lines), "PACKAGE", "PACKAGE"))

    routines: list[RoutineScan] = []
    for start, end, kind, name in ranges:
        routine = RoutineScan(name=name, kind=kind, start_line=start, end_line=end)
        in_exception = False
        pending_when: int | None = None
        for line_no in range(start, end + 1):
            if line_no - 1 >= len(lines):
                break
            text = lines[line_no - 1]
            if re.search(r"\bEXCEPTION\b", text, re.IGNORECASE):
                in_exception = True
            if in_exception:
                when = _WHEN.search(text)
                if when:
                    routine.handler_lines.append(line_no)
                    pending_when = line_no
                    if re.search(r"\bNULL\s*;", text[when.end() :], re.IGNORECASE):
                        routine.swallowed_lines.append(pending_when)
                        pending_when = None
                elif pending_when and re.search(r"\bNULL\s*;", text, re.IGNORECASE):
                    routine.swallowed_lines.append(pending_when)
                    pending_when = None
            for match in _SQL.finditer(text):
                operation = _operation(match.group(1))
                owner, table = _split_table(match.group(2))
                if table in _SKIP_TABLES:
                    continue
                routine.sql_refs.append(
                    SqlRef(
                        operation=operation,
                        owner=owner,
                        table=table,
                        line=line_no,
                        statement=_statement(original, line_no - 1),
                    )
                )
                if operation != "SELECT":
                    routine.events.append((line_no, match.start(), operation))
            for match in _COMMIT.finditer(text):
                routine.commit_lines.append(line_no)
                routine.events.append((line_no, match.start(), "COMMIT"))
            for match in _ROLLBACK.finditer(text):
                routine.rollback_lines.append(line_no)
                routine.events.append((line_no, match.start(), "ROLLBACK"))
            for match in _SAVEPOINT.finditer(text):
                routine.savepoint_lines.append(line_no)
                routine.events.append((line_no, match.start(), "SAVEPOINT"))
            if _AUTONOMOUS.search(text):
                routine.autonomous_lines.append(line_no)
            if _DYNAMIC.search(text):
                routine.dynamic_lines.append(line_no)
            for match in _THRESHOLD.finditer(text):
                routine.thresholds.append(
                    (line_no, match.group(1).upper(), match.group(2), match.group(3))
                )
        routines.append(routine)
    return PackageScan(routines=routines, masked_lines=lines)


def financial_text(text: str) -> bool:
    return bool(_FINANCIAL.search(text))
