"""Allowlisted investigator tools. Arguments are validated before any read."""

import json

from oracle_impact.analysis.identifiers import ORACLE_MAINTAINED_SCHEMAS, is_identifier
from oracle_impact.models.domain import Snapshot

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_package_source",
            "description": "Read a snapshotted package specification and body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env": {"type": "string", "enum": ["test", "production"]},
                    "owner": {"type": "string"},
                    "package": {"type": "string"},
                },
                "required": ["env", "owner", "package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dependencies",
            "description": "Read dependency edges already discovered for an allowlisted object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env": {"type": "string", "enum": ["test", "production"]},
                    "owner": {"type": "string"},
                    "object_name": {"type": "string"},
                    "direction": {"type": "string", "enum": ["referenced", "referenced_by"]},
                    "depth": {"type": "integer"},
                },
                "required": ["env", "owner", "object_name", "direction", "depth"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_object_source",
            "description": "Read source for an allowlisted dependent object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env": {"type": "string", "enum": ["test", "production"]},
                    "owner": {"type": "string"},
                    "name": {"type": "string"},
                    "object_type": {"type": "string"},
                },
                "required": ["env", "owner", "name", "object_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_table_metadata",
            "description": "Read columns, keys, constraints, and triggers. No row data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env": {"type": "string", "enum": ["test", "production"]},
                    "owner": {"type": "string"},
                    "table": {"type": "string"},
                },
                "required": ["env", "owner", "table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_procedure_arguments",
            "description": "Read signature metadata for a package.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env": {"type": "string", "enum": ["test", "production"]},
                    "owner": {"type": "string"},
                    "package": {"type": "string"},
                    "procedure": {"type": "string"},
                },
                "required": ["env", "owner", "package", "procedure"],
            },
        },
    },
]


class ToolContext:
    def __init__(
        self,
        snapshot: Snapshot,
        graph_edges: list[dict],
        tables: dict,
        arguments: dict,
        max_depth: int,
        readers: dict | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.graph_edges = graph_edges
        self.tables = tables
        self.arguments = arguments
        self.max_depth = max_depth
        self.readers = readers or {}
        self.allowed_objects = {package for package in snapshot.packages}
        for edge in graph_edges:
            self.allowed_objects.add(f"{edge['owner']}.{edge['name']}")
            self.allowed_objects.add(f"{edge['referenced_owner']}.{edge['referenced_name']}")
        for key in tables:
            self.allowed_objects.add(key)

    def allowed(self, owner: str, name: str) -> bool:
        if owner in ORACLE_MAINTAINED_SCHEMAS:
            return False
        return f"{owner}.{name}" in self.allowed_objects


def dispatch_tool(name: str, arguments: dict, context: ToolContext) -> dict:
    if name not in {schema["function"]["name"] for schema in TOOL_SCHEMAS}:
        return {"error": "tool is not allowlisted"}
    try:
        env = arguments["env"]
        if env not in {"test", "production"}:
            return {"error": "environment is not allowlisted"}
        if name == "get_package_source":
            _idents(arguments["owner"], arguments["package"])
            return _package_source(context, env, arguments["owner"], arguments["package"])
        if name == "get_dependencies":
            _idents(arguments["owner"], arguments["object_name"])
            depth = int(arguments["depth"])
            if depth < 1 or depth > context.max_depth:
                return {"error": "depth exceeds the configured limit"}
            if arguments["direction"] not in {"referenced", "referenced_by"}:
                return {"error": "direction is not allowlisted"}
            return _dependencies(context, env, arguments["owner"], arguments["object_name"], arguments["direction"], depth)
        if name == "get_object_source":
            _idents(arguments["owner"], arguments["name"])
            if arguments["object_type"] not in {"PACKAGE", "PACKAGE BODY", "TRIGGER", "PROCEDURE", "FUNCTION", "VIEW"}:
                return {"error": "object type is not allowlisted"}
            return _object_source(context, env, arguments["owner"], arguments["name"], arguments["object_type"])
        if name == "get_table_metadata":
            _idents(arguments["owner"], arguments["table"])
            return _table(context, arguments["owner"], arguments["table"])
        if name == "get_procedure_arguments":
            _idents(arguments["owner"], arguments["package"], arguments["procedure"])
            return _arguments(context, env, arguments["owner"], arguments["package"], arguments["procedure"])
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": "invalid tool arguments", "detail": str(exc)}
    return {"error": "tool is not allowlisted"}


def _idents(*values: str) -> None:
    for value in values:
        if not is_identifier(value):
            raise ValueError("identifier rejected")


def _deny_unless_allowed(context: ToolContext, owner: str, name: str) -> dict | None:
    if not context.allowed(owner, name):
        return {"error": "object is outside the investigation allowlist"}
    return None


def _package_source(context: ToolContext, env: str, owner: str, package: str) -> dict:
    denied = _deny_unless_allowed(context, owner, package)
    if denied:
        return denied
    _ensure_source(context, env, owner, package, "PACKAGE")
    _ensure_source(context, env, owner, package, "PACKAGE BODY")
    return {
        "specification": context.snapshot.text(env, owner, package, "PACKAGE"),
        "body": context.snapshot.text(env, owner, package, "PACKAGE BODY"),
    }


def _object_source(context: ToolContext, env: str, owner: str, name: str, object_type: str) -> dict:
    denied = _deny_unless_allowed(context, owner, name)
    if denied:
        return denied
    _ensure_source(context, env, owner, name, object_type)
    return {"source": context.snapshot.text(env, owner, name, object_type)}


def _ensure_source(context: ToolContext, env: str, owner: str, name: str, object_type: str) -> None:
    if context.snapshot.text(env, owner, name, object_type):
        return
    reader = context.readers.get(env)
    if reader is None:
        return
    record = reader.fetch_source(owner, name, object_type)
    if not record.found or record.inaccessible:
        return
    from oracle_impact.analysis.hashing import source_hash
    from oracle_impact.infrastructure.oracle.gateway import utc_now
    from oracle_impact.models.domain import CapturedObject

    context.snapshot.objects.append(
        CapturedObject(
            environment=env,
            database_id=getattr(reader, "database_id", env),
            owner=owner,
            name=name,
            object_type=object_type,
            found=True,
            source=record.source,
            sha256=record.sha256 or source_hash(record.source),
            retrieved_at=record.retrieved_at or utc_now(),
        )
    )


def _dependencies(context: ToolContext, env: str, owner: str, object_name: str, direction: str, depth: int) -> dict:
    denied = _deny_unless_allowed(context, owner, object_name)
    if denied:
        return denied
    edges = [
        edge
        for edge in context.graph_edges
        if edge["database"] == env
        and edge["direction"] == direction
        and edge["depth"] <= depth
        and (
            (edge["owner"] == owner and edge["name"] == object_name)
            or (edge["referenced_owner"] == owner and edge["referenced_name"] == object_name)
        )
    ]
    return {"edges": edges}


def _table(context: ToolContext, owner: str, table: str) -> dict:
    denied = _deny_unless_allowed(context, owner, table)
    if denied:
        return denied
    metadata = context.tables.get(f"{owner}.{table}")
    if metadata is None:
        return {"error": "table is outside the investigation allowlist"}
    return metadata


def _arguments(context: ToolContext, env: str, owner: str, package: str, procedure: str) -> dict:
    denied = _deny_unless_allowed(context, owner, package)
    if denied:
        return denied
    rows = context.arguments.get(f"{env}:{owner}.{package}", [])
    return {"arguments": [row for row in rows if row["object_name"].upper() == procedure.upper()]}


def tool_result_json(payload: dict) -> str:
    return json.dumps(payload)
