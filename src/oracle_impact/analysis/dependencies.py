"""Direct and depth-capped dependency walks."""

from dataclasses import dataclass, field

from oracle_impact.analysis.identifiers import ORACLE_MAINTAINED_SCHEMAS
from oracle_impact.models.domain import DependencyEdge

_EXPANDABLE = {"PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "VIEW"}
_UNRESOLVED = {"SYNONYM", "DATABASE LINK"}


@dataclass
class CoverageGap:
    environment: str
    owner: str
    name: str
    object_type: str
    reason: str


@dataclass
class DependencyGraph:
    edges: list[DependencyEdge] = field(default_factory=list)
    gaps: list[CoverageGap] = field(default_factory=list)
    skipped_system_objects: int = 0


def discover_dependencies(gateway, roots: list[tuple[str, str, str]], direction: str, max_depth: int) -> DependencyGraph:
    graph = DependencyGraph()
    queue: list[tuple[str, str, str, int]] = [
        (owner, name, object_type, 0) for owner, name, object_type in roots
    ]
    seen: set[tuple[str, str, str, str]] = set()
    recorded: set[tuple] = set()
    while queue:
        owner, name, object_type, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        marker = (direction, owner, name, object_type)
        if marker in seen:
            continue
        seen.add(marker)
        fetched = gateway.fetch_dependencies(owner, name, object_type)
        if fetched.inaccessible:
            graph.gaps.append(
                CoverageGap(
                    environment=gateway.environment,
                    owner=owner,
                    name=name,
                    object_type=object_type,
                    reason="Dependency metadata could not be read.",
                )
            )
            continue
        for edge in fetched.edges:
            if edge.direction != direction:
                continue
            other_owner = edge.referenced_owner if direction == "referenced" else edge.owner
            other_name = edge.referenced_name if direction == "referenced" else edge.name
            other_type = edge.referenced_type if direction == "referenced" else edge.object_type
            if other_owner in ORACLE_MAINTAINED_SCHEMAS:
                graph.skipped_system_objects += 1
                continue
            key = (
                direction,
                edge.owner,
                edge.name,
                edge.object_type,
                edge.referenced_owner,
                edge.referenced_name,
                edge.referenced_type,
            )
            if key in recorded:
                continue
            recorded.add(key)
            stored = edge.model_copy(update={"depth": depth + 1, "database": gateway.environment})
            graph.edges.append(stored)
            if other_type in _UNRESOLVED:
                graph.gaps.append(
                    CoverageGap(
                        environment=gateway.environment,
                        owner=other_owner,
                        name=other_name,
                        object_type=other_type,
                        reason=f"{other_type.title()} {other_owner}.{other_name} is not resolved by static dependency data.",
                    )
                )
                continue
            if other_type in _EXPANDABLE:
                queue.append((other_owner, other_name, other_type, depth + 1))
    return graph
