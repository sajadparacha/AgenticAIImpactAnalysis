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
class GrantFetch:
    """Object privileges on one object. complete is False when the dictionary only shows
    grants that involve the connected account (ALL_TAB_PRIVS)."""

    inaccessible: bool = False
    complete: bool = True
    grants: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class StatusFetch:
    """ALL_OBJECTS rows for one name: (object_type, status, last_ddl_time)."""

    inaccessible: bool = False
    objects: list[tuple[str, str, str | None]] = field(default_factory=list)


@dataclass
class ErrorFetch:
    """ALL_ERRORS rows for one name: (object_type, line, position, text, attribute).
    not_read is True when an agentic run never made this read, as opposed to a denied read."""

    inaccessible: bool = False
    errors: list[tuple[str, int, int, str, str]] = field(default_factory=list)
    not_read: bool = False


@dataclass
class SynonymFetch:
    """ALL_SYNONYMS row for one synonym. db_link is set when the target is in another database."""

    inaccessible: bool = False
    found: bool = False
    target_owner: str = ""
    target_name: str = ""
    db_link: str = ""


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
    grants: dict[tuple[str, str], list[tuple[str, str]]] = field(default_factory=dict)
    inaccessible_grants: set[tuple[str, str]] = field(default_factory=set)
    grants_complete: bool = True
    object_status: dict[tuple[str, str, str], str] = field(default_factory=dict)
    compile_errors: dict[tuple[str, str], list[tuple[str, int, int, str, str]]] = field(default_factory=dict)
    inaccessible_status: set[tuple[str, str]] = field(default_factory=set)
    objects: set[tuple[str, str, str]] = field(default_factory=set)
    """Objects without source or table metadata, such as sequences."""
    synonyms: dict[tuple[str, str], tuple[str, str, str]] = field(default_factory=dict)
    inaccessible_synonyms: set[tuple[str, str]] = field(default_factory=set)

    def add_object(self, owner: str, name: str, object_type: str) -> None:
        self.objects.add((owner, name, object_type))

    def add_synonym(self, owner: str, name: str, target_owner: str, target_name: str, db_link: str = "") -> None:
        self.synonyms[(owner, name)] = (target_owner, target_name, db_link)
        self.objects.add((owner, name, "SYNONYM"))

    def set_status(self, owner: str, name: str, object_type: str, status: str) -> None:
        self.object_status[(owner, name, object_type)] = status

    def add_compile_error(self, owner: str, name: str, object_type: str, line: int, text: str, position: int = 1) -> None:
        self.compile_errors.setdefault((owner, name), []).append((object_type, line, position, text, "ERROR"))

    def add_grant(self, owner: str, name: str, grantee: str, privilege: str) -> None:
        self.grants.setdefault((owner, name), []).append((grantee, privilege))

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

    def fetch_grants(self, owner: str, name: str) -> GrantFetch:
        if (owner, name) in self.catalog.inaccessible_grants:
            return GrantFetch(inaccessible=True, complete=False)
        return GrantFetch(complete=self.catalog.grants_complete, grants=list(self.catalog.grants.get((owner, name), [])))

    def fetch_object_status(self, owner: str, name: str) -> StatusFetch:
        if (owner, name) in self.catalog.inaccessible_status:
            return StatusFetch(inaccessible=True)
        rows = []
        for (src_owner, src_name, object_type) in sorted(self.catalog.sources):
            if src_owner == owner and src_name == name:
                status = self.catalog.object_status.get((owner, name, object_type), "VALID")
                rows.append((object_type, status, None))
        table = self.catalog.tables.get((owner, name))
        if table is not None and table.exists:
            rows.append(("TABLE", "VALID", None))
        rows.extend((kind, "VALID", None) for (obj_owner, obj_name, kind) in sorted(self.catalog.objects) if (obj_owner, obj_name) == (owner, name))
        return StatusFetch(objects=rows)

    def fetch_compile_errors(self, owner: str, name: str) -> ErrorFetch:
        if (owner, name) in self.catalog.inaccessible_status:
            return ErrorFetch(inaccessible=True)
        return ErrorFetch(errors=list(self.catalog.compile_errors.get((owner, name), [])))

    def fetch_table(self, owner: str, table: str) -> TableMetadata:
        if (owner, table) in self.catalog.inaccessible_tables:
            return TableMetadata(owner=owner, name=table, exists=False, inaccessible=True)
        found = self.catalog.tables.get((owner, table))
        if found is None:
            return TableMetadata(owner=owner, name=table, exists=False)
        return found.model_copy(deep=True)

    def fetch_synonym(self, owner: str, name: str) -> SynonymFetch:
        if (owner, name) in self.catalog.inaccessible_synonyms:
            return SynonymFetch(inaccessible=True)
        target = self.catalog.synonyms.get((owner, name))
        if target is None:
            return SynonymFetch()
        return SynonymFetch(found=True, target_owner=target[0], target_name=target[1], db_link=target[2])


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

    def fetch_grants(self, owner: str, name: str) -> GrantFetch:
        raise OracleNotConfigured(self._message)

    def fetch_object_status(self, owner: str, name: str) -> StatusFetch:
        raise OracleNotConfigured(self._message)

    def fetch_compile_errors(self, owner: str, name: str) -> ErrorFetch:
        raise OracleNotConfigured(self._message)

    def fetch_table(self, owner: str, table: str) -> TableMetadata:
        raise OracleNotConfigured(self._message)

    def fetch_synonym(self, owner: str, name: str) -> SynonymFetch:
        raise OracleNotConfigured(self._message)
