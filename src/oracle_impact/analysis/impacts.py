"""Turn snapshots, diffs, signatures, and metadata into evidence-backed findings."""

from oracle_impact.analysis.hashing import finding_id
from oracle_impact.analysis.normalize import normalize_plsql
from oracle_impact.analysis.plsql_scanner import PackageScan, RoutineScan, financial_text, scan_package
from oracle_impact.analysis.signatures import SignatureChange
from oracle_impact.models.domain import Snapshot, TableMetadata
from oracle_impact.models.enums import Confidence, EvidenceStatus, Severity
from oracle_impact.models.report import Finding, ObjectDiff


def _lines_of(scan: PackageScan | None, routine: str | None) -> list[int]:
    if scan is None or routine is None:
        return []
    for item in scan.routines:
        if item.name == routine:
            return list(range(item.start_line, min(item.start_line + 3, item.end_line + 1)))
    return []


def build_deterministic_findings(
    run_id: str,
    snapshot: Snapshot,
    diffs: list[ObjectDiff],
    signature_changes: list[SignatureChange],
    scans: dict[tuple[str, str, str], PackageScan],
    table_lookup,
    dependency_gaps: list[str],
    drift_objects: list[str],
) -> list[Finding]:
    findings: list[Finding] = []
    formatting = _formatting_packages(diffs)
    for change in signature_changes:
        findings.append(_signature_finding(run_id, snapshot, change))
    if dependency_gaps:
        for gap in dependency_gaps:
            findings.append(_gap_finding(run_id, gap))
    for qualified in drift_objects:
        findings.append(_drift_finding(run_id, qualified))

    packages = {(diff.owner, diff.name) for diff in diffs}
    for owner, name in sorted(packages):
        if f"{owner}.{name}" in formatting:
            findings.append(_format_finding(run_id, snapshot, owner, name, diffs))
            continue
        test_body = scans.get(("test", owner, name))
        prod_body = scans.get(("production", owner, name))
        if test_body is None and prod_body is None:
            continue
        findings.extend(
            _body_findings(
                run_id,
                snapshot,
                owner,
                name,
                test_body or PackageScan([], []),
                prod_body or PackageScan([], []),
                diffs,
                table_lookup,
            )
        )
    if not findings:
        findings.append(_no_change_finding(run_id, snapshot))
    return findings


def _formatting_packages(diffs: list[ObjectDiff]) -> set[str]:
    by_package: dict[str, list[ObjectDiff]] = {}
    for diff in diffs:
        by_package.setdefault(f"{diff.owner}.{diff.name}", []).append(diff)
    selected: set[str] = set()
    for qualified, items in by_package.items():
        if items and all(item.formatting_only or item.unchanged for item in items) and any(
            item.formatting_only for item in items
        ):
            selected.add(qualified)
    return selected


def _hash_for(snapshot: Snapshot, owner: str, name: str) -> str | None:
    item = snapshot.find("test", owner, name, "PACKAGE BODY") or snapshot.find(
        "test", owner, name, "PACKAGE"
    )
    return None if item is None else item.sha256


def _base_finding(run_id: str, snapshot: Snapshot, owner: str, name: str, **kwargs) -> Finding:
    return Finding(
        id=finding_id(run_id, owner, name, kwargs.get("change_category", ""), kwargs.get("evidence", "")),
        changed_object=f"{owner}.{name}",
        object_owner=owner,
        object_name=name,
        object_type=kwargs.pop("object_type", "PACKAGE BODY"),
        source_database="test",
        version_hash=_hash_for(snapshot, owner, name),
        **kwargs,
    )


def _signature_finding(run_id: str, snapshot: Snapshot, change: SignatureChange) -> Finding:
    owner, name = change.package.split(".", 1)
    if change.unresolved and change.kind == "metadata_gap":
        severity = Severity.MEDIUM
        status = EvidenceStatus.UNKNOWN
        confidence = Confidence.LOW
        attributes = {"blocks_eligibility": True}
        action = "Grant read access to argument metadata or review the specification manually."
    elif change.kind == "compatible_optional":
        severity = Severity.LOW
        status = EvidenceStatus.CONFIRMED
        confidence = Confidence.HIGH
        attributes = {"breaking": False}
        action = "Compile dependent objects and run the existing caller tests. The added parameter has a default."
    elif change.kind == "added_overload":
        severity = Severity.MEDIUM
        status = EvidenceStatus.POTENTIAL
        confidence = Confidence.MEDIUM
        attributes = {"blocks_eligibility": True}
        action = "Check every caller for overload ambiguity before promotion."
    elif change.breaking:
        severity = Severity.CRITICAL
        status = EvidenceStatus.CONFIRMED
        confidence = Confidence.HIGH
        attributes = {"breaking": True}
        action = "Update every caller, including clients outside the database, before this can be reviewed for promotion."
    elif change.coordinated_callers:
        severity = Severity.MEDIUM
        status = EvidenceStatus.POTENTIAL
        confidence = Confidence.MEDIUM
        attributes = {"blocks_eligibility": True, "coordinated": True}
        action = "Confirm callers outside the database. The visible database callers in this change set were updated."
    else:
        severity = Severity.LOW
        status = EvidenceStatus.CONFIRMED
        confidence = Confidence.HIGH
        attributes = {}
        action = "Review the new routine and its tests."
    path = []
    if change.external_callers:
        path = [f"{caller} -> {change.package}.{change.routine}" for caller in change.external_callers]
    elif change.coordinated_callers:
        path = [f"{caller} -> {change.package}.{change.routine}" for caller in change.coordinated_callers]
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        object_type="PACKAGE",
        changed_lines_test=change.test_lines,
        corresponding_lines_prod=change.prod_lines,
        change_category="interface",
        affected_object=change.external_callers[0] if change.external_callers else change.package,
        dependency_path=path,
        evidence=change.detail,
        severity=severity,
        confidence=confidence,
        status=status,
        explanation=change.detail,
        recommended_action=action,
        proposed_tests=[
            "Compile packages that call this interface in Test.",
            "Execute existing callers with positional and named arguments.",
        ],
        attributes=attributes,
    )


def _gap_finding(run_id: str, gap: str) -> Finding:
    return Finding(
        id=finding_id(run_id, "coverage", gap),
        changed_object=gap,
        object_owner="",
        object_name="",
        object_type="METADATA",
        source_database="",
        change_category="coverage",
        evidence=gap,
        severity=Severity.MEDIUM,
        confidence=Confidence.LOW,
        status=EvidenceStatus.UNKNOWN,
        explanation=gap,
        recommended_action="Restore dictionary visibility or inspect this object manually. Do not treat the gap as safe.",
        proposed_tests=["Re-run the analysis with the required grants."],
        attributes={"blocks_eligibility": True},
    )


def _drift_finding(run_id: str, qualified: str) -> Finding:
    owner, _, rest = qualified.partition(".")
    name = rest.split()[0] if rest else qualified
    return Finding(
        id=finding_id(run_id, "drift", qualified),
        changed_object=qualified,
        object_owner=owner,
        object_name=name,
        object_type="PACKAGE",
        source_database="test",
        change_category="coverage",
        evidence=f"{qualified} changed between the initial read and the verification read.",
        severity=Severity.MEDIUM,
        confidence=Confidence.HIGH,
        status=EvidenceStatus.UNKNOWN,
        explanation=(
            "The shared Test environment changed while the snapshot was being captured. "
            "This run may mix two versions of the object."
        ),
        recommended_action="Repeat the analysis when the package is stable.",
        proposed_tests=["Re-run the snapshot and confirm the checksums match."],
        attributes={"blocks_eligibility": True, "snapshot_drift": True},
    )


def _format_finding(run_id, snapshot, owner, name, diffs: list[ObjectDiff]) -> Finding:
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=[
            row.test_line_no
            for diff in diffs
            if diff.owner == owner and diff.name == name
            for row in diff.rows
            if row.test_kind == "added" and row.test_line_no
        ][:8],
        change_category="format",
        evidence="Normalized source matches Production. The raw text differs by comments, whitespace, or identifier case.",
        severity=Severity.INFORMATIONAL,
        confidence=Confidence.HIGH,
        status=EvidenceStatus.CONFIRMED,
        explanation="No interface, SQL, transaction, or literal change was detected after normalization.",
        recommended_action="No functional retest is indicated by the text comparison alone.",
        proposed_tests=["Optional compile check to confirm the formatted source is valid."],
        attributes={},
    )


def _no_change_finding(run_id: str, snapshot: Snapshot) -> Finding:
    package = snapshot.packages[0] if snapshot.packages else "selected packages"
    owner, _, name = package.partition(".")
    return Finding(
        id=finding_id(run_id, "unchanged", package),
        changed_object=package,
        object_owner=owner,
        object_name=name,
        object_type="PACKAGE",
        source_database="test",
        change_category="format",
        evidence="Test and Production source and signatures match for the selected packages.",
        severity=Severity.INFORMATIONAL,
        confidence=Confidence.HIGH,
        status=EvidenceStatus.CONFIRMED,
        explanation="The deterministic comparison found no source or interface difference.",
        recommended_action="Promotion still requires the normal approval path. This run did not find a code change.",
        proposed_tests=[],
        attributes={},
    )


def _body_findings(
    run_id: str,
    snapshot: Snapshot,
    owner: str,
    name: str,
    test_scan: PackageScan,
    prod_scan: PackageScan,
    diffs: list[ObjectDiff],
    table_lookup,
) -> list[Finding]:
    findings: list[Finding] = []
    changed_test_lines = {
        row.test_line_no
        for diff in diffs
        if diff.owner == owner and diff.name == name and diff.object_type == "PACKAGE BODY"
        for row in diff.rows
        if row.test_kind == "added" and row.test_line_no
    }
    changed_prod_lines = {
        row.prod_line_no
        for diff in diffs
        if diff.owner == owner and diff.name == name and diff.object_type == "PACKAGE BODY"
        for row in diff.rows
        if row.prod_kind == "removed" and row.prod_line_no
    }
    body_changed = bool(changed_test_lines or changed_prod_lines)
    prod_by_name = {routine.name: routine for routine in prod_scan.routines}
    for routine in test_scan.routines:
        prod_routine = prod_by_name.get(routine.name)
        if not body_changed and prod_routine is not None:
            continue
        findings.extend(
            _routine_findings(
                run_id,
                snapshot,
                owner,
                name,
                routine,
                prod_routine,
                changed_test_lines,
                changed_prod_lines,
                table_lookup,
            )
        )
    return [item for item in findings if item is not None]


def _routine_findings(
    run_id,
    snapshot,
    owner,
    name,
    routine: RoutineScan,
    prod: RoutineScan | None,
    changed_test_lines: set[int],
    changed_prod_lines: set[int],
    table_lookup,
) -> list[Finding]:
    findings: list[Finding] = []
    routine_lines = set(range(routine.start_line, routine.end_line + 1))
    touches = bool(routine_lines & changed_test_lines) or (
        prod is not None and bool(set(range(prod.start_line, prod.end_line + 1)) & changed_prod_lines)
    )
    if not touches and prod is not None:
        return findings

    test_sql = {(ref.operation, ref.qualified, normalize_plsql(ref.statement)) for ref in routine.sql_refs}
    prod_sql = set()
    if prod is not None:
        prod_sql = {(ref.operation, ref.qualified, normalize_plsql(ref.statement)) for ref in prod.sql_refs}
    new_refs = [ref for ref in routine.sql_refs if (ref.operation, ref.qualified, normalize_plsql(ref.statement)) not in prod_sql]
    old_refs = []
    if prod is not None:
        old_refs = [ref for ref in prod.sql_refs if (ref.operation, ref.qualified, normalize_plsql(ref.statement)) not in test_sql]

    seen_tables: set[tuple[str, str]] = set()
    for ref in new_refs:
        if ref.operation == "SELECT":
            continue
        key = (ref.operation, ref.qualified)
        if key in seen_tables:
            continue
        seen_tables.add(key)
        table_owner = ref.owner or owner
        metadata = table_lookup("production", table_owner, ref.table)
        prod_had_table = any(item.table == ref.table and item.operation == ref.operation for item in (prod.sql_refs if prod else []))
        findings.append(
            _data_finding(
                run_id,
                snapshot,
                owner,
                name,
                routine,
                ref,
                metadata,
                modified=prod_had_table,
            )
        )

    if prod is not None and routine.transaction_sequence != prod.transaction_sequence:
        findings.append(_transaction_finding(run_id, snapshot, owner, name, routine, prod))
    elif routine.autonomous_lines and (prod is None or not prod.autonomous_lines):
        findings.append(_autonomous_finding(run_id, snapshot, owner, name, routine))

    test_swallowed = set(routine.swallowed_lines)
    prod_swallowed = set(prod.swallowed_lines if prod else [])
    if test_swallowed != prod_swallowed:
        findings.append(_exception_finding(run_id, snapshot, owner, name, routine, prod, added=bool(test_swallowed - prod_swallowed)))

    dynamic_changed = [
        line for line in routine.dynamic_lines if line in changed_test_lines or prod is None
    ]
    if prod is not None:
        dynamic_changed.extend(line for line in prod.dynamic_lines if line in changed_prod_lines)
    if dynamic_changed or (routine.dynamic_lines and prod is None):
        findings.append(_dynamic_finding(run_id, snapshot, owner, name, routine))

    logic_lines = [
        line
        for line in sorted(routine_lines & changed_test_lines)
        if line not in routine.commit_lines
        and line not in routine.dynamic_lines
        and line not in routine.autonomous_lines
        and not any(ref.line == line for ref in routine.sql_refs)
    ]
    threshold_changes = _threshold_changes(routine, prod)
    if threshold_changes:
        findings.append(_threshold_finding(run_id, snapshot, owner, name, routine, threshold_changes))
    elif logic_lines and not any(item.change_category == "business_logic" for item in findings):
        findings.append(_logic_finding(run_id, snapshot, owner, name, routine, logic_lines))
    return findings


def _data_finding(run_id, snapshot, owner, name, routine, ref, metadata: TableMetadata, modified: bool) -> Finding:
    trigger_names = ", ".join(item.name for item in metadata.triggers) or "none visible"
    constraint_names = ", ".join(item.name for item in metadata.constraints) or "none visible"
    if metadata.inaccessible:
        status = EvidenceStatus.UNKNOWN
        severity = Severity.HIGH
        attributes = {"blocks_eligibility": True, "new_table_unverified": True}
        detail = (
            f"{ref.operation} on {ref.qualified} could not be checked in Production. "
            "Triggers and constraints are unknown."
        )
    elif not metadata.exists and not modified:
        status = EvidenceStatus.CONFIRMED
        severity = Severity.CRITICAL
        attributes = {"missing_object": True}
        detail = (
            f"{ref.operation} references {metadata.owner}.{metadata.name}, which was not found in Production."
        )
    elif not modified:
        status = EvidenceStatus.POTENTIAL
        severity = Severity.HIGH if ref.operation != "SELECT" else Severity.MEDIUM
        attributes = {"blocks_eligibility": True, "new_table_unverified": True}
        detail = (
            f"Test adds {ref.operation} against {metadata.owner}.{metadata.name}. "
            f"Visible triggers: {trigger_names}. Visible constraints: {constraint_names}. "
            "Migration ordering was not verified."
        )
    else:
        status = EvidenceStatus.POTENTIAL
        severity = Severity.HIGH if financial_text(ref.statement) else Severity.MEDIUM
        attributes = {"blocks_eligibility": True, "modified_dml": True}
        detail = (
            f"{ref.operation} against {metadata.owner}.{metadata.name} changed. "
            f"Visible triggers: {trigger_names}. Visible constraints: {constraint_names}."
        )
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=[ref.line],
        change_category="data",
        affected_object=f"{metadata.owner}.{metadata.name}",
        dependency_path=[f"{owner}.{name}.{routine.name}", f"{metadata.owner}.{metadata.name}"],
        evidence=detail,
        severity=severity,
        confidence=Confidence.HIGH if status == EvidenceStatus.CONFIRMED else Confidence.MEDIUM,
        status=status,
        explanation=detail + " Static inspection cannot prove which rows the statement changes at runtime.",
        recommended_action="Review the table change with the data owner and run the affected business transaction in Test.",
        proposed_tests=[
            f"Execute {routine.name} against a Test copy of {metadata.owner}.{metadata.name}.",
            "Confirm triggers and constraints still enforce the expected integrity rules.",
        ],
        attributes=attributes,
    )


def _transaction_finding(run_id, snapshot, owner, name, routine: RoutineScan, prod: RoutineScan) -> Finding:
    test_has_mid_commit = _commit_before_last_dml(routine)
    prod_has_mid_commit = _commit_before_last_dml(prod)
    autonomous = bool(routine.autonomous_lines) != bool(prod.autonomous_lines)
    hazard = test_has_mid_commit != prod_has_mid_commit or autonomous or (
        len(routine.commit_lines) != len(prod.commit_lines) and test_has_mid_commit
    )
    detail = (
        f"{routine.name} transaction sequence changed from {prod.transaction_sequence or ('none',)} "
        f"to {routine.transaction_sequence or ('none',)}."
    )
    if autonomous:
        detail += " Autonomous transaction pragma presence changed."
    attributes = {"partial_commit_hazard": True} if hazard else {"blocks_eligibility": True}
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=routine.commit_lines + routine.rollback_lines + routine.autonomous_lines,
        corresponding_lines_prod=prod.commit_lines + prod.rollback_lines + prod.autonomous_lines,
        change_category="transaction",
        evidence=detail,
        severity=Severity.HIGH,
        confidence=Confidence.MEDIUM,
        status=EvidenceStatus.POTENTIAL,
        explanation=detail + " A moved commit can leave earlier statements durable if a later statement fails.",
        recommended_action="Review the transaction boundary with a DBA and test the failure path.",
        proposed_tests=["Force an error after each DML statement and inspect which changes remain."],
        attributes=attributes,
    )


def _autonomous_finding(run_id, snapshot, owner, name, routine: RoutineScan) -> Finding:
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=routine.autonomous_lines,
        change_category="transaction",
        evidence=f"{routine.name} declares PRAGMA AUTONOMOUS_TRANSACTION.",
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        status=EvidenceStatus.POTENTIAL,
        explanation="An autonomous transaction commits independently of the caller.",
        recommended_action="Confirm the caller can tolerate a commit that survives a later rollback.",
        proposed_tests=["Roll back the caller and verify which autonomous changes remain."],
        attributes={"partial_commit_hazard": True},
    )


def _exception_finding(run_id, snapshot, owner, name, routine, prod, added: bool) -> Finding:
    lines = routine.swallowed_lines or (prod.handler_lines if prod else [])
    if added:
        detail = f"{routine.name} contains an exception handler that swallows the error with NULL."
        action = "Replace the swallowed handler with explicit handling and a regression test for the error path."
    else:
        detail = f"{routine.name} no longer has the same swallowed exception handler as Production."
        action = "Confirm callers can handle the exception that is no longer discarded."
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=lines,
        change_category="exception",
        evidence=detail,
        severity=Severity.HIGH,
        confidence=Confidence.MEDIUM,
        status=EvidenceStatus.POTENTIAL,
        explanation=detail + " Static inspection cannot show which errors occur at runtime.",
        recommended_action=action,
        proposed_tests=["Raise the previously handled error and inspect the caller outcome."],
        attributes={"blocks_eligibility": True},
    )


def _dynamic_finding(run_id, snapshot, owner, name, routine: RoutineScan) -> Finding:
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=routine.dynamic_lines,
        change_category="dynamic_sql",
        evidence=(
            f"{routine.name} uses dynamic SQL. The runtime statement text and its dependencies "
            "are not visible to static analysis."
        ),
        severity=Severity.HIGH,
        confidence=Confidence.LOW,
        status=EvidenceStatus.UNKNOWN,
        explanation="Dynamic SQL was not marked safe. Its target objects are unknown.",
        recommended_action="Capture the runtime SQL and include those objects in a follow-up analysis.",
        proposed_tests=["Log the statement built by EXECUTE IMMEDIATE or OPEN FOR in Test."],
        attributes={"blocks_eligibility": True},
    )


def _threshold_changes(routine: RoutineScan, prod: RoutineScan | None) -> list[str]:
    prod_map = {}
    if prod is not None:
        for _line, ident, operator, number in prod.thresholds:
            prod_map.setdefault(ident, []).append((operator, number))
    changes = []
    for _line, ident, operator, number in routine.thresholds:
        previous = prod_map.get(ident, [])
        if previous and (operator, number) not in previous:
            changes.append(f"{ident} comparison changed from {previous[0][0]} {previous[0][1]} to {operator} {number}")
        elif not previous and prod is not None and prod.thresholds:
            changes.append(f"{ident} comparison {operator} {number} is new")
    return changes


def _threshold_finding(run_id, snapshot, owner, name, routine, changes: list[str]) -> Finding:
    financial = any(financial_text(item) for item in changes)
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=[line for line, *_ in routine.thresholds],
        change_category="business_logic",
        evidence="; ".join(changes),
        severity=Severity.HIGH if financial else Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        status=EvidenceStatus.POTENTIAL,
        explanation=(
            "; ".join(changes)
            + ". The behavioral effect is a hypothesis until the rule is executed with representative data."
        ),
        recommended_action="Review the changed threshold with the business owner and add a boundary test.",
        proposed_tests=["Execute values immediately below and above the old and new thresholds."],
        attributes={"blocks_eligibility": True, "financial": financial},
    )


def _logic_finding(run_id, snapshot, owner, name, routine, lines: list[int]) -> Finding:
    source = snapshot.text("test", owner, name, "PACKAGE BODY")
    snippets = []
    all_lines = source.splitlines()
    financial = False
    for line in lines[:6]:
        if 1 <= line <= len(all_lines):
            snippets.append(all_lines[line - 1].strip())
            financial = financial or financial_text(all_lines[line - 1])
    removed_executable = False
    detail = f"{routine.name} executable text changed."
    if snippets:
        detail += " Example: " + " | ".join(snippets)
    severity = Severity.HIGH if financial else Severity.LOW
    attributes = {"blocks_eligibility": True, "financial": True} if financial else {}
    return _base_finding(
        run_id,
        snapshot,
        owner,
        name,
        changed_lines_test=lines[:12],
        change_category="business_logic",
        evidence=detail,
        severity=severity,
        confidence=Confidence.MEDIUM if financial else Confidence.LOW,
        status=EvidenceStatus.POTENTIAL,
        explanation=detail + " This describes the source change, not a proven runtime outcome.",
        recommended_action="Read the changed branch with a developer who knows the business rule and test it.",
        proposed_tests=[f"Add a functional test that exercises {routine.name} with the new branch."],
        attributes=attributes,
    )


def _commit_before_last_dml(routine: RoutineScan) -> bool:
    sequence = routine.transaction_sequence
    dml_indexes = [index for index, kind in enumerate(sequence) if kind in {"INSERT", "UPDATE", "DELETE", "MERGE"}]
    commit_indexes = [index for index, kind in enumerate(sequence) if kind == "COMMIT"]
    if not dml_indexes or not commit_indexes:
        return False
    return min(commit_indexes) < max(dml_indexes)
