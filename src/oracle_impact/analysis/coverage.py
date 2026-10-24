"""Object coverage, dependency coverage, and dependency edge provenance.

Object coverage and dependency coverage are kept apart: the object
denominator is the specification and body of each selected package, and
dependency figures are counts of edges and gaps, not a percentage.
"""

from oracle_impact.analysis.plsql_scanner import PackageScan
from oracle_impact.models.assessment import (
    CoverageItem,
    CoverageSummary,
    DependencyCoverage,
    DependencyEdgeRecord,
    DynamicSqlRef,
)
from oracle_impact.models.domain import Snapshot

PACKAGE_TYPES = ("PACKAGE", "PACKAGE BODY")
_UNRESOLVED = {"SYNONYM", "DATABASE LINK"}


def build_coverage(
    packages: list[tuple[str, str]],
    snapshot: Snapshot,
    environments: list[str],
    failures: dict[tuple[str, str, str], str],
    argument_gaps: set[tuple[str, str]],
    dependency_gaps: list,
    edges: list[DependencyEdgeRecord],
    scans: dict,
    changed_test_lines: dict[tuple[str, str], set[int]],
    skipped_system_objects: int,
    max_depth: int,
    completed_at: str,
) -> CoverageSummary:
    summary = CoverageSummary(environments=list(environments), completed_at=completed_at)
    for owner, name in packages:
        for object_type in PACKAGE_TYPES:
            summary.items.append(
                _item(owner, name, object_type, snapshot, environments, failures, argument_gaps, dependency_gaps)
            )
    summary.in_scope = len(summary.items)
    for state in ("analyzed", "partial", "skipped", "inaccessible", "failed"):
        setattr(summary, state, sum(1 for item in summary.items if item.state == state))
    for owner, name in packages:
        scan = scans.get(("test", owner, name))
        if scan is None:
            continue
        changed = changed_test_lines.get((owner, name), set())
        for routine in scan.routines:
            for line in routine.dynamic_lines:
                summary.dynamic_sql.append(
                    DynamicSqlRef(owner=owner, name=name, routine=routine.name, line=line, changed=line in changed)
                )
    summary.dependency = DependencyCoverage(
        edges=len(edges),
        verified_edges=sum(1 for edge in edges if edge.provenance == "dictionary"),
        inferred_edges=sum(1 for edge in edges if edge.provenance == "source_scan"),
        gaps=sum(1 for gap in dependency_gaps if gap.object_type not in _UNRESOLVED),
        unresolved=sum(1 for edge in edges if edge.provenance == "unresolved"),
        skipped_system_objects=skipped_system_objects,
        max_depth=max_depth,
    )
    return summary


def _item(owner, name, object_type, snapshot, environments, failures, argument_gaps, dependency_gaps) -> CoverageItem:
    captured = {env: snapshot.find(env, owner, name, object_type) for env in environments}
    required = [env for env in ("test", "production") if env in environments]
    reasons: list[str] = []
    inaccessible = [env for env in required if captured.get(env) is not None and captured[env].inaccessible]
    if inaccessible:
        return CoverageItem(
            owner=owner,
            name=name,
            object_type=object_type,
            state="inaccessible",
            reasons=[f"Source could not be read in {env}." for env in inaccessible],
        )
    if not any(captured.get(env) is not None and captured[env].found for env in required):
        return CoverageItem(
            owner=owner,
            name=name,
            object_type=object_type,
            state="skipped",
            reasons=["The object does not exist in Test or Production, so there was nothing to compare."],
        )
    failure = failures.get((owner, name, object_type))
    if failure:
        return CoverageItem(owner=owner, name=name, object_type=object_type, state="failed", reasons=[failure])
    for env, item in captured.items():
        if item is None:
            continue
        if item.drift:
            reasons.append(f"The {env} source changed while it was captured.")
        if env not in required and item.inaccessible:
            reasons.append(f"Source could not be read in {env}.")
    if object_type == "PACKAGE" and (owner, name) in argument_gaps:
        reasons.append("Argument metadata could not be read, so signatures were not compared.")
    for gap in dependency_gaps:
        if gap.owner == owner and gap.name == name and gap.object_type == object_type and gap.object_type not in _UNRESOLVED:
            reasons.append(f"Dependency metadata could not be read in {gap.environment}.")
    return CoverageItem(
        owner=owner,
        name=name,
        object_type=object_type,
        state="partial" if reasons else "analyzed",
        reasons=reasons,
    )


def edge_records(graph_edges, packages: list[tuple[str, str]], scans: dict) -> list[DependencyEdgeRecord]:
    records: list[DependencyEdgeRecord] = []
    seen = set()
    for edge in graph_edges:
        key = (edge.direction, edge.owner, edge.name, edge.object_type, edge.referenced_owner, edge.referenced_name, edge.referenced_type, edge.database)
        if key in seen:
            continue
        seen.add(key)
        other_type = edge.referenced_type if edge.direction == "referenced" else edge.object_type
        unresolved = other_type in _UNRESOLVED
        records.append(
            DependencyEdgeRecord(
                direction=edge.direction,
                owner=edge.owner,
                name=edge.name,
                object_type=edge.object_type,
                referenced_owner=edge.referenced_owner,
                referenced_name=edge.referenced_name,
                referenced_type=edge.referenced_type,
                depth=edge.depth,
                database=edge.database,
                provenance="unresolved" if unresolved else "dictionary",
                note=f"{other_type.title()} target is not followed by static dependency data." if unresolved else "ALL_DEPENDENCIES",
            )
        )
    referenced = {
        (record.owner, record.name, record.referenced_name)
        for record in records
        if record.direction == "referenced"
    }
    for owner, name in packages:
        for environment in ("test", "production"):
            scan: PackageScan | None = scans.get((environment, owner, name))
            if scan is None:
                continue
            for routine in scan.routines:
                for ref in routine.sql_refs:
                    if (owner, name, ref.table) in referenced:
                        continue
                    referenced.add((owner, name, ref.table))
                    records.append(
                        DependencyEdgeRecord(
                            direction="referenced",
                            owner=owner,
                            name=name,
                            object_type="PACKAGE BODY",
                            referenced_owner=ref.owner or owner,
                            referenced_name=ref.table,
                            referenced_type="TABLE OR VIEW",
                            depth=1,
                            database=environment,
                            provenance="source_scan",
                            note=f"{ref.operation} in {routine.name} line {ref.line}; not listed in ALL_DEPENDENCIES of {environment}.",
                        )
                    )
                for line in routine.dynamic_lines:
                    records.append(
                        DependencyEdgeRecord(
                            direction="referenced",
                            owner=owner,
                            name=name,
                            object_type="PACKAGE BODY",
                            referenced_owner="",
                            referenced_name=f"Dynamic SQL in {routine.name}",
                            referenced_type="DYNAMIC SQL",
                            depth=1,
                            database=environment,
                            provenance="unresolved",
                            note=f"Line {line}. The statement text and its targets are built at runtime.",
                        )
                    )
    return records
