"""Dictionary reads collected during an agentic run, replayed to the deterministic scorers.

A read that was never made is answered as inaccessible, so the scorers record a
coverage gap instead of treating the object as absent. Nothing here opens a connection.
"""

from dataclasses import dataclass, field

from oracle_impact.infrastructure.oracle.gateway import (
    ArgumentFetch,
    DependencyFetch,
    ErrorFetch,
    GrantFetch,
    SourceRecord,
    StatusFetch,
    utc_now,
)
from oracle_impact.models.domain import TableMetadata


@dataclass
class RecordedReads:
    """Gateway results keyed by environment and object. Dependency reads hold both directions."""

    sources: dict[tuple[str, str, str, str], SourceRecord] = field(default_factory=dict)
    hashes: dict[tuple[str, str, str, str], str | None] = field(default_factory=dict)
    dependencies: dict[tuple[str, str, str, str], DependencyFetch] = field(default_factory=dict)
    arguments: dict[tuple[str, str, str], ArgumentFetch] = field(default_factory=dict)
    grants: dict[tuple[str, str, str], GrantFetch] = field(default_factory=dict)
    status: dict[tuple[str, str, str], StatusFetch] = field(default_factory=dict)
    errors: dict[tuple[str, str, str], ErrorFetch] = field(default_factory=dict)
    tables: dict[tuple[str, str, str], TableMetadata] = field(default_factory=dict)


@dataclass(frozen=True)
class Miss:
    """A read the scorers asked for that was not collected."""

    kind: str
    environment: str
    owner: str
    name: str
    object_type: str = ""


class ReplayGateway:
    """Read-only gateway over RecordedReads for one environment."""

    def __init__(self, environment: str, database_id: str, label: str, reads: RecordedReads, misses: list[Miss] | None = None) -> None:
        self.environment = environment
        self.database_id = database_id
        self.label = label
        self.reads = reads
        self.misses = misses

    def _miss(self, kind: str, owner: str, name: str, object_type: str = "") -> None:
        if self.misses is not None:
            self.misses.append(Miss(kind, self.environment, owner, name, object_type))

    def fetch_source(self, owner: str, name: str, object_type: str) -> SourceRecord:
        record = self.reads.sources.get((self.environment, owner, name, object_type))
        if record is None:
            self._miss("source", owner, name, object_type)
            return SourceRecord(owner, name, object_type, False, True, "", None, utc_now())
        return record

    def verify_hash(self, owner: str, name: str, object_type: str) -> str | None:
        return self.reads.hashes.get((self.environment, owner, name, object_type))

    def fetch_dependencies(self, owner: str, name: str, object_type: str) -> DependencyFetch:
        fetched = self.reads.dependencies.get((self.environment, owner, name, object_type))
        if fetched is None:
            self._miss("dependencies", owner, name, object_type)
            return DependencyFetch(inaccessible=True)
        return DependencyFetch(inaccessible=fetched.inaccessible, edges=[edge.model_copy() for edge in fetched.edges])

    def fetch_arguments(self, owner: str, package: str) -> ArgumentFetch:
        fetched = self.reads.arguments.get((self.environment, owner, package))
        if fetched is None:
            self._miss("arguments", owner, package)
            return ArgumentFetch(inaccessible=True)
        return fetched

    def fetch_grants(self, owner: str, name: str) -> GrantFetch:
        fetched = self.reads.grants.get((self.environment, owner, name))
        if fetched is None:
            self._miss("grants", owner, name)
            return GrantFetch(inaccessible=True, complete=False)
        return fetched

    def fetch_object_status(self, owner: str, name: str) -> StatusFetch:
        fetched = self.reads.status.get((self.environment, owner, name))
        if fetched is None:
            self._miss("status", owner, name)
            return StatusFetch(inaccessible=True)
        return fetched

    def fetch_compile_errors(self, owner: str, name: str) -> ErrorFetch:
        fetched = self.reads.errors.get((self.environment, owner, name))
        if fetched is None:
            self._miss("errors", owner, name)
            return ErrorFetch(inaccessible=True, not_read=True)
        return fetched

    def fetch_table(self, owner: str, table: str) -> TableMetadata:
        fetched = self.reads.tables.get((self.environment, owner, table))
        if fetched is None:
            self._miss("table", owner, table)
            return TableMetadata(owner=owner, name=table, exists=False, inaccessible=True)
        return fetched.model_copy(deep=True)
