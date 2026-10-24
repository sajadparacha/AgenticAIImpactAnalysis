"""Statement-shape profiles of changed routines and the modified-SQL inventory.

Profiles list what the scanner observed in each version. They do not predict
runtime behavior; predicted effects stay on the linked findings.
"""

from oracle_impact.analysis.impacts import _pair_routines
from oracle_impact.analysis.normalize import normalize_plsql
from oracle_impact.analysis.plsql_scanner import PackageScan, RoutineScan
from oracle_impact.models.assessment import ModifiedStatement, PerformanceAssessment, RoutineFacts, TransactionProfile
from oracle_impact.models.report import Finding, ObjectDiff

_WRITES = ("INSERT", "UPDATE", "DELETE", "MERGE")
NOT_EVALUATED = (
    "Performance impact was not evaluated. Execution plans and runtime metrics are not collected: "
    "EXPLAIN PLAN writes to PLAN_TABLE, which the read-only session does not allow; AWR views (DBA_HIST_*) "
    "require the Oracle Diagnostics Pack license; V$SQL_PLAN needs extra grants and a cursor that matches the new text. "
    "The statements below changed and need performance validation in Test."
)


def facts(routine: RoutineScan) -> RoutineFacts:
    writes: dict[str, list[str]] = {}
    reads: list[str] = []
    for ref in routine.sql_refs:
        if ref.operation == "SELECT":
            if ref.qualified not in reads:
                reads.append(ref.qualified)
        else:
            tables = writes.setdefault(ref.operation, [])
            if ref.qualified not in tables:
                tables.append(ref.qualified)
    return RoutineFacts(
        start_line=routine.start_line,
        end_line=routine.end_line,
        reads=sorted(reads),
        writes={key: sorted(value) for key, value in sorted(writes.items())},
        commits=list(routine.commit_lines),
        rollbacks=list(routine.rollback_lines),
        savepoints=list(routine.savepoint_lines),
        autonomous=bool(routine.autonomous_lines),
        dynamic_sql=list(routine.dynamic_lines),
        handlers=list(routine.handler_lines),
        swallowed=list(routine.swallowed_lines),
        sequence=list(routine.transaction_sequence),
    )


def _changed_lines(diffs: list[ObjectDiff], owner: str, name: str) -> tuple[set[int], set[int]]:
    test, prod = set(), set()
    for diff in diffs:
        if diff.owner != owner or diff.name != name or diff.object_type != "PACKAGE BODY":
            continue
        for row in diff.rows:
            if row.test_kind == "added" and row.test_line_no:
                test.add(row.test_line_no)
            if row.prod_kind == "removed" and row.prod_line_no:
                prod.add(row.prod_line_no)
    return test, prod


def touched_routines(test_scan: PackageScan, prod_scan: PackageScan, diffs, owner, name):
    """(test routine or None, prod routine or None, change) for routines whose text changed."""
    changed_test, changed_prod = _changed_lines(diffs, owner, name)
    pairs = _pair_routines(test_scan, prod_scan)
    paired_prod = {id(item) for item in pairs.values()}
    found = []
    for routine in test_scan.routines:
        prod = pairs.get(id(routine))
        if prod is None:
            found.append((routine, None, "added"))
            continue
        test_hit = set(range(routine.start_line, routine.end_line + 1)) & changed_test
        prod_hit = set(range(prod.start_line, prod.end_line + 1)) & changed_prod
        if test_hit or prod_hit:
            found.append((routine, prod, "modified"))
    for routine in prod_scan.routines:
        if id(routine) not in paired_prod:
            found.append((None, routine, "removed"))
    return found


def build_profiles(packages, scans: dict, diffs: list[ObjectDiff], findings: list[Finding]) -> list[TransactionProfile]:
    profiles: list[TransactionProfile] = []
    for owner, name in packages:
        test_scan = scans.get(("test", owner, name)) or PackageScan([], [])
        prod_scan = scans.get(("production", owner, name)) or PackageScan([], [])
        if test_scan.routines and len(test_scan.routines) == 1 and test_scan.routines[0].kind == "PACKAGE":
            continue
        for test, prod, change in touched_routines(test_scan, prod_scan, diffs, owner, name):
            routine = (test or prod).name
            test_facts = facts(test) if test else None
            prod_facts = facts(prod) if prod else None
            profiles.append(
                TransactionProfile(
                    owner=owner,
                    name=name,
                    routine=routine,
                    change=change,
                    test=test_facts,
                    production=prod_facts,
                    differences=_differences(test_facts, prod_facts),
                    finding_ids=_linked(findings, owner, name, routine, test),
                )
            )
    return profiles


def _differences(test: RoutineFacts | None, prod: RoutineFacts | None) -> list[str]:
    if test is None:
        return ["The routine exists in Production and is absent from Test."]
    if prod is None:
        lines = ["The routine is new in Test."]
        prod = RoutineFacts()
    else:
        lines = []
    for operation in _WRITES:
        before = set(prod.writes.get(operation, []))
        after = set(test.writes.get(operation, []))
        for table in sorted(after - before):
            lines.append(f"Now runs {operation} on {table}.")
        for table in sorted(before - after):
            lines.append(f"No longer runs {operation} on {table}.")
    for table in sorted(set(test.reads) - set(prod.reads)):
        lines.append(f"Now reads {table}.")
    for table in sorted(set(prod.reads) - set(test.reads)):
        lines.append(f"No longer reads {table}.")
    for label, before, after in (
        ("COMMIT", prod.commits, test.commits),
        ("ROLLBACK", prod.rollbacks, test.rollbacks),
        ("SAVEPOINT", prod.savepoints, test.savepoints),
        ("dynamic SQL statement", prod.dynamic_sql, test.dynamic_sql),
        ("exception handler", prod.handlers, test.handlers),
        ("handler that discards the error (THEN NULL)", prod.swallowed, test.swallowed),
    ):
        if len(before) != len(after):
            lines.append(f"{label} count changed from {len(before)} to {len(after)}.")
    if test.autonomous != prod.autonomous:
        lines.append("PRAGMA AUTONOMOUS_TRANSACTION was " + ("added." if test.autonomous else "removed."))
    if test.sequence != prod.sequence and (test.sequence or prod.sequence):
        lines.append(
            "Write and transaction-control order changed from "
            f"{' → '.join(prod.sequence) or 'none'} to {' → '.join(test.sequence) or 'none'}."
        )
    return lines


def _linked(findings: list[Finding], owner, name, routine, test: RoutineScan | None) -> list[str]:
    linked = []
    qualified = f"{owner}.{name}"
    for finding in findings:
        if finding.origin == "investigator" or finding.changed_object != qualified:
            continue
        in_range = test is not None and any(test.start_line <= line <= test.end_line for line in finding.changed_lines_test)
        named = routine.upper() in finding.evidence.upper()
        if in_range or named:
            linked.append(finding.id)
    return linked


def modified_statements(packages, scans: dict, diffs: list[ObjectDiff]) -> list[ModifiedStatement]:
    found: list[ModifiedStatement] = []
    for owner, name in packages:
        test_scan = scans.get(("test", owner, name)) or PackageScan([], [])
        prod_scan = scans.get(("production", owner, name)) or PackageScan([], [])
        for test, prod, _change in touched_routines(test_scan, prod_scan, diffs, owner, name):
            test_refs = list(test.sql_refs) if test else []
            prod_refs = list(prod.sql_refs) if prod else []
            prod_keys = {(ref.operation, ref.qualified, normalize_plsql(ref.statement)) for ref in prod_refs}
            test_keys = {(ref.operation, ref.qualified, normalize_plsql(ref.statement)) for ref in test_refs}
            new = [ref for ref in test_refs if (ref.operation, ref.qualified, normalize_plsql(ref.statement)) not in prod_keys]
            old = [ref for ref in prod_refs if (ref.operation, ref.qualified, normalize_plsql(ref.statement)) not in test_keys]
            routine = (test or prod).name
            seen = set()
            for ref in new:
                key = (ref.operation, ref.qualified, ref.line)
                if key in seen:
                    continue
                seen.add(key)
                match = next((item for item in old if item.operation == ref.operation and item.qualified == ref.qualified), None)
                found.append(
                    ModifiedStatement(
                        owner=owner,
                        name=name,
                        routine=routine,
                        operation=ref.operation,
                        table=ref.qualified,
                        change="modified" if match else "added",
                        test_line=ref.line,
                        prod_line=match.line if match else None,
                        statement=ref.statement[:600],
                    )
                )
            for ref in old:
                if any(item.operation == ref.operation and item.qualified == ref.qualified for item in new):
                    continue
                found.append(
                    ModifiedStatement(
                        owner=owner,
                        name=name,
                        routine=routine,
                        operation=ref.operation,
                        table=ref.qualified,
                        change="removed",
                        prod_line=ref.line,
                        statement=ref.statement[:600],
                    )
                )
    return found


def performance_assessment(statements: list[ModifiedStatement], plan_source: str) -> PerformanceAssessment:
    reason = NOT_EVALUATED
    if plan_source and plan_source != "none":
        reason = f"The configured plan source '{plan_source}' is not implemented. " + NOT_EVALUATED
    return PerformanceAssessment(evaluated=False, reason=reason, plan_source=plan_source or "none", statements=statements)
