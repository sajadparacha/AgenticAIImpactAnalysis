"""Read-only Oracle gateway contracts and the fixture implementation."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from oracle_impact.models.domain import (
    Argument,
    Constraint,
    DependencyEdge,
    TableMetadata,
    Trigger,
)


class OracleAccessError(RuntimeError):
    """Safe connection or privilege failure. The message must not include secrets."""


class OracleUnavailable(OracleAccessError):
    """The database could not be reached at all. The run must stop rather than report objects as missing."""


class OracleNotConfigured(OracleAccessError):
    """Raised when fixture mode is off and an environment has no DSN."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceRecord:
    owner: str
    name: str
    object_type: str
    found: bool
    inaccessible: bool
    source: str
    sha256: str | None
    retrieved_at: str


@dataclass
class DependencyFetch:
    inaccessible: bool = False
    edges: list[DependencyEdge] = field(default_factory=list)


@dataclass
class ArgumentFetch:
    inaccessible: bool = False
    arguments: list[Argument] = field(default_factory=list)


@dataclass
class StoredEdge:
    dependent_owner: str
    dependent_name: str
    dependent_type: str
    referenced_owner: str
    referenced_name: str
    referenced_type: str


_PACKAGE_TYPES = {"PACKAGE", "PACKAGE BODY"}


def _same_object(
    owner: str,
    name: str,
    object_type: str,
    other_owner: str,
    other_name: str,
    other_type: str,
) -> bool:
    if owner != other_owner or name != other_name:
        return False
    if object_type in _PACKAGE_TYPES and other_type in _PACKAGE_TYPES:
        return True
    return object_type == other_type


@dataclass
class Catalog:
    """In-memory dictionary data used by tests and fixture mode."""

    sources: dict[tuple[str, str, str], str] = field(default_factory=dict)
    inaccessible_sources: set[tuple[str, str, str]] = field(default_factory=set)
    arguments: dict[tuple[str, str], list[Argument]] = field(default_factory=dict)
    inaccessible_arguments: set[tuple[str, str]] = field(default_factory=set)
    edges: list[StoredEdge] = field(default_factory=list)
    inaccessible_dependencies: set[tuple[str, str, str]] = field(default_factory=set)
    tables: dict[tuple[str, str], TableMetadata] = field(default_factory=dict)
    inaccessible_tables: set[tuple[str, str]] = field(default_factory=set)
    drift: set[tuple[str, str, str]] = field(default_factory=set)

    def add_source(self, owner: str, name: str, object_type: str, source: str) -> None:
        self.sources[(owner, name, object_type)] = source

    def add_arguments(self, owner: str, package: str, arguments: list[Argument]) -> None:
        self.arguments[(owner, package)] = arguments

    def add_dependency(
        self,
        dependent: tuple[str, str, str],
        referenced: tuple[str, str, str],
    ) -> None:
        self.edges.append(
            StoredEdge(
                dependent_owner=dependent[0],
                dependent_name=dependent[1],
                dependent_type=dependent[2],
                referenced_owner=referenced[0],
                referenced_name=referenced[1],
                referenced_type=referenced[2],
            )
        )

    def add_table(
        self,
        owner: str,
        name: str,
        columns: list | None = None,
        constraints: list[Constraint] | None = None,
        triggers: list[Trigger] | None = None,
    ) -> None:
        self.tables[(owner, name)] = TableMetadata(
            owner=owner,
            name=name,
            exists=True,
            columns=list(columns or []),
            constraints=list(constraints or []),
            triggers=list(triggers or []),
        )


class FixtureGateway:
    """Dictionary gateway over a Catalog. It never opens a database connection."""

    def __init__(self, environment: str, database_id: str, label: str, catalog: Catalog) -> None:
        self.environment = environment
        self.database_id = database_id
        self.label = label
        self.catalog = catalog

    def fetch_source(self, owner: str, name: str, object_type: str) -> SourceRecord:
        key = (owner, name, object_type)
        retrieved = utc_now()
        if key in self.catalog.inaccessible_sources:
            return SourceRecord(owner, name, object_type, False, True, "", None, retrieved)
        source = self.catalog.sources.get(key)
        if source is None:
            return SourceRecord(owner, name, object_type, False, False, "", None, retrieved)
        from oracle_impact.analysis.hashing import source_hash

        return SourceRecord(
            owner, name, object_type, True, False, source, source_hash(source), retrieved
        )

    def verify_hash(self, owner: str, name: str, object_type: str) -> str | None:
        record = self.fetch_source(owner, name, object_type)
        if record.inaccessible or not record.found or record.sha256 is None:
            return record.sha256
        if (owner, name, object_type) in self.catalog.drift:
            return record.sha256 + "-drift"
        return record.sha256

    def fetch_dependencies(self, owner: str, name: str, object_type: str) -> DependencyFetch:
        key = (owner, name, object_type)
        if key in self.catalog.inaccessible_dependencies:
            return DependencyFetch(inaccessible=True)
        edges: list[DependencyEdge] = []
        for stored in self.catalog.edges:
            if _same_object(
                owner,
                name,
                object_type,
                stored.dependent_owner,
                stored.dependent_name,
                stored.dependent_type,
            ):
                edges.append(
                    DependencyEdge(
                        direction="referenced",
                        owner=stored.dependent_owner,
                        name=stored.dependent_name,
                        object_type=stored.dependent_type,
                        referenced_owner=stored.referenced_owner,
                        referenced_name=stored.referenced_name,
                        referenced_type=stored.referenced_type,
                        database=self.environment,
                    )
                )
            if _same_object(
                owner,
                name,
                object_type,
                stored.referenced_owner,
                stored.referenced_name,
                stored.referenced_type,
            ):
                edges.append(
                    DependencyEdge(
                        direction="referenced_by",
                        owner=stored.dependent_owner,
                        name=stored.dependent_name,
                        object_type=stored.dependent_type,
                        referenced_owner=stored.referenced_owner,
                        referenced_name=stored.referenced_name,
                        referenced_type=stored.referenced_type,
                        database=self.environment,
                    )
                )
        return DependencyFetch(edges=edges)

    def fetch_arguments(self, owner: str, package: str) -> ArgumentFetch:
        if (owner, package) in self.catalog.inaccessible_arguments:
            return ArgumentFetch(inaccessible=True)
        return ArgumentFetch(arguments=list(self.catalog.arguments.get((owner, package), [])))

    def fetch_table(self, owner: str, table: str) -> TableMetadata:
        if (owner, table) in self.catalog.inaccessible_tables:
            return TableMetadata(owner=owner, name=table, exists=False, inaccessible=True)
        found = self.catalog.tables.get((owner, table))
        if found is None:
            return TableMetadata(owner=owner, name=table, exists=False)
        return found.model_copy(deep=True)


class UnconfiguredGateway:
    """Placeholder used when fixture mode is off and no DSN is configured."""

    def __init__(self, environment: str, label: str) -> None:
        self.environment = environment
        self.database_id = "not-configured"
        self.label = label
        self._message = (
            f"The {label} Oracle environment is not configured. "
            "Set the DSN and user, or enable fixture mode."
        )

    def fetch_source(self, owner: str, name: str, object_type: str) -> SourceRecord:
        raise OracleNotConfigured(self._message)

    def verify_hash(self, owner: str, name: str, object_type: str) -> str | None:
        raise OracleNotConfigured(self._message)

    def fetch_dependencies(self, owner: str, name: str, object_type: str) -> DependencyFetch:
        raise OracleNotConfigured(self._message)

    def fetch_arguments(self, owner: str, package: str) -> ArgumentFetch:
        raise OracleNotConfigured(self._message)

    def fetch_table(self, owner: str, table: str) -> TableMetadata:
        raise OracleNotConfigured(self._message)
