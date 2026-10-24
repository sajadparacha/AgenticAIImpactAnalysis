"""Live read-only Oracle access. SQL text comes only from the query catalog."""

import logging

import oracledb

from oracle_impact.analysis.hashing import source_hash
from oracle_impact.infrastructure.oracle.gateway import (
    ArgumentFetch,
    DependencyFetch,
    ErrorFetch,
    GrantFetch,
    OracleAccessError,
    OracleUnavailable,
    SourceRecord,
    StatusFetch,
    utc_now,
)
from oracle_impact.infrastructure.oracle.queries import QUERIES, SESSION_SQL, apply_dictionary_views
from oracle_impact.models.domain import (
    Argument,
    Column,
    Constraint,
    DependencyEdge,
    TableMetadata,
    Trigger,
)

logger = logging.getLogger(__name__)

_PACKAGE_TYPES = {"PACKAGE", "PACKAGE BODY"}
# ORA-00942 and ORA-01031: the login cannot read DBA_ views, so ALL_ views are the only option.
_NO_DBA_VIEW_CODES = {942, 1031}


class OracledbGateway:
    """Thin-mode client. Each call opens a short read-only transaction."""

    def __init__(
        self,
        environment: str,
        label: str,
        dsn: str,
        user: str,
        password: str,
        edition: str = "",
    ) -> None:
        self.environment = environment
        self.label = label
        self.database_id = label
        self._dsn = dsn
        self._user = user
        self._password = password
        self._edition = edition
        self._dictionary_prefix = "all"
        self._dictionary_ready = False

    def fetch_source(self, owner: str, name: str, object_type: str) -> SourceRecord:
        retrieved = utc_now()
        try:
            rows = self._select("source", owner=owner, name=name, object_type=object_type)
        except OracleUnavailable:
            raise
        except OracleAccessError:
            return SourceRecord(owner, name, object_type, False, True, "", None, retrieved)
        if not rows:
            return SourceRecord(owner, name, object_type, False, False, "", None, retrieved)
        text = "".join(row[1] or "" for row in rows)
        if text and not text.endswith("\n"):
            text += "\n"
        return SourceRecord(
            owner, name, object_type, True, False, text, source_hash(text), retrieved
        )

    def verify_hash(self, owner: str, name: str, object_type: str) -> str | None:
        return self.fetch_source(owner, name, object_type).sha256

    def fetch_dependencies(self, owner: str, name: str, object_type: str) -> DependencyFetch:
        try:
            outgoing = self._select(
                "outgoing_dependencies",
                owner=owner,
                name=name,
                object_type=object_type,
            )
            incoming = self._select("incoming_dependencies", owner=owner, name=name)
        except OracleAccessError:
            return DependencyFetch(inaccessible=True)
        edges: list[DependencyEdge] = []
        for referenced_owner, referenced_name, referenced_type, link_name in outgoing:
            if link_name:
                referenced_type = "DATABASE LINK"
            edges.append(
                DependencyEdge(
                    direction="referenced",
                    owner=owner,
                    name=name,
                    object_type=object_type,
                    referenced_owner=referenced_owner or "",
                    referenced_name=referenced_name or "",
                    referenced_type=referenced_type or "",
                    database=self.environment,
                )
            )
        for dep_owner, dep_name, dep_type, ref_owner, ref_name, ref_type in incoming:
            if object_type in _PACKAGE_TYPES and ref_type not in _PACKAGE_TYPES:
                continue
            if object_type not in _PACKAGE_TYPES and ref_type != object_type:
                continue
            edges.append(
                DependencyEdge(
                    direction="referenced_by",
                    owner=dep_owner,
                    name=dep_name,
                    object_type=dep_type,
                    referenced_owner=ref_owner,
                    referenced_name=ref_name,
                    referenced_type=ref_type,
                    database=self.environment,
                )
            )
        return DependencyFetch(edges=edges)

    def fetch_arguments(self, owner: str, package: str) -> ArgumentFetch:
        try:
            rows = self._select("arguments", owner=owner, package_name=package)
        except OracleAccessError:
            return ArgumentFetch(inaccessible=True)
        arguments = [
            Argument(
                object_name=row[0],
                overload="" if row[1] is None else str(row[1]),
                argument_name=row[2],
                position=int(row[3]),
                data_type=row[4] or "",
                in_out=row[5] or "IN",
                defaulted=(row[6] == "Y"),
            )
            for row in rows
        ]
        return ArgumentFetch(arguments=arguments)

    def fetch_grants(self, owner: str, name: str) -> GrantFetch:
        self._ensure_dictionary_prefix()
        if self._dictionary_prefix == "dba":
            try:
                rows = self._select("object_grants_dba", owner=owner, name=name)
                return GrantFetch(complete=True, grants=[(row[0], row[1]) for row in rows])
            except OracleUnavailable:
                raise
            except OracleAccessError:
                pass
        try:
            rows = self._select("object_grants", owner=owner, name=name)
        except OracleUnavailable:
            raise
        except OracleAccessError:
            return GrantFetch(inaccessible=True, complete=False)
        return GrantFetch(complete=False, grants=[(row[0], row[1]) for row in rows])

    def fetch_object_status(self, owner: str, name: str) -> StatusFetch:
        try:
            rows = self._select("object_validity", owner=owner, name=name)
        except OracleUnavailable:
            raise
        except OracleAccessError:
            return StatusFetch(inaccessible=True)
        return StatusFetch(objects=[(row[0] or "", row[1] or "", row[2]) for row in rows])

    def fetch_compile_errors(self, owner: str, name: str) -> ErrorFetch:
        try:
            rows = self._select("compile_errors", owner=owner, name=name)
        except OracleUnavailable:
            raise
        except OracleAccessError:
            return ErrorFetch(inaccessible=True)
        return ErrorFetch(
            errors=[
                (row[0] or "", int(row[1] or 0), int(row[2] or 0), (row[3] or "").strip(), row[4] or "ERROR")
                for row in rows
            ]
        )

    def fetch_table(self, owner: str, table: str) -> TableMetadata:
        try:
            columns = self._select("columns", owner=owner, table_name=table)
            constraints = self._select("constraints", owner=owner, table_name=table)
            triggers = self._select("triggers", owner=owner, table_name=table)
        except OracleAccessError:
            return TableMetadata(owner=owner, name=table, exists=False, inaccessible=True)
        if not columns and not constraints and not triggers:
            status = self._select(
                "object_status",
                owner=owner,
                name=table,
                object_type="TABLE",
            )
            if not status:
                return TableMetadata(owner=owner, name=table, exists=False)
        grouped: dict[str, Constraint] = {}
        for name, constraint_type, referenced, _status, column_name, _position in constraints:
            item = grouped.get(name)
            if item is None:
                item = Constraint(
                    name=name,
                    constraint_type=constraint_type,
                    columns=[],
                    referenced_table=None,
                )
                grouped[name] = item
            if column_name and column_name not in item.columns:
                item.columns.append(column_name)
            if referenced and item.referenced_table is None:
                item.referenced_table = self._constraint_table(owner, referenced)
        return TableMetadata(
            owner=owner,
            name=table,
            exists=True,
            columns=[
                Column(name=row[0], data_type=row[1] or "", nullable=(row[2] == "Y"))
                for row in columns
            ],
            constraints=list(grouped.values()),
            triggers=[
                Trigger(
                    name=row[0],
                    trigger_type=row[1] or "",
                    event=row[2] or "",
                    status=row[3] or "",
                )
                for row in triggers
            ],
        )

    def _constraint_table(self, owner: str, constraint_name: str) -> str | None:
        try:
            rows = self._select(
                "referenced_constraint",
                owner=owner,
                constraint_name=constraint_name,
            )
        except OracleAccessError:
            return None
        if not rows:
            return None
        return rows[0][0]

    def _select(self, query_name: str, **binds: str) -> list[tuple]:
        self._ensure_dictionary_prefix()
        sql = apply_dictionary_views(QUERIES[query_name], self._dictionary_prefix)
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(SESSION_SQL)
                    cursor.execute(sql, binds)
                    return list(cursor.fetchall())
        except oracledb.Error as exc:
            logger.warning(
                "Oracle read failed environment=%s query=%s error_type=%s",
                self.environment,
                query_name,
                type(exc).__name__,
            )
            raise OracleAccessError(
                f"The {self.label} environment could not be read. "
                "Check connectivity and dictionary grants."
            ) from exc

    def _ensure_dictionary_prefix(self) -> None:
        if self._dictionary_ready:
            return
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(SESSION_SQL)
                    cursor.execute("SELECT 1 FROM dba_objects WHERE ROWNUM = 1")
                    cursor.fetchone()
            self._dictionary_prefix = "dba"
        except oracledb.Error as exc:
            error = exc.args[0] if exc.args else None
            if getattr(error, "code", None) not in _NO_DBA_VIEW_CODES:
                logger.warning(
                    "Oracle dictionary probe failed environment=%s error_type=%s",
                    self.environment,
                    type(exc).__name__,
                )
                raise OracleUnavailable(
                    f"The {self.label} environment could not be reached. Check that the database is running."
                ) from exc
            self._dictionary_prefix = "all"
        self._dictionary_ready = True

    def _connect(self):
        if self._edition:
            return oracledb.connect(
                user=self._user,
                password=self._password,
                dsn=self._dsn,
                edition=self._edition,
            )
        return oracledb.connect(user=self._user, password=self._password, dsn=self._dsn)
