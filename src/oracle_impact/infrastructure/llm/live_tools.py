"""Live allowlisted reads for the agentic gather loop, and its required-evidence checklist.

Each tool calls one read-only gateway method that already exists. The full result is
recorded for the deterministic scorers; the model receives a short summary and never
package source. The checklist is the set of reads the scorers would make that have not
been collected yet, so it grows as dependency reads reveal more objects.
"""

from dataclasses import dataclass

from oracle_impact.analysis.compilation import collect_compilation
from oracle_impact.analysis.dependencies import discover_dependencies
from oracle_impact.analysis.existence import existence_candidates
from oracle_impact.analysis.identifiers import ORACLE_MAINTAINED_SCHEMAS, is_identifier
from oracle_impact.analysis.plsql_scanner import scan_package
from oracle_impact.infrastructure.oracle.gateway import OracleAccessError, SourceRecord
from oracle_impact.infrastructure.oracle.recorded import Miss, RecordedReads, ReplayGateway
from oracle_impact.models.domain import PackageRef

PACKAGE_TYPES = ("PACKAGE", "PACKAGE BODY")
SOURCE_TYPES = ("PACKAGE", "PACKAGE BODY", "TRIGGER", "PROCEDURE", "FUNCTION", "VIEW")
DIRECTIONS = ("referenced", "referenced_by")
SCORED_ENVIRONMENTS = ("test", "production")
MAX_CALLER_SOURCES = 8
_UNRESOLVED = {"SYNONYM", "DATABASE LINK"}
_MAX_LISTED = 40
_NAME_FIELD = {
    "get_object_status": "name",
    "get_package_source": "package",
    "get_object_source": "name",
    "get_arguments": "package",
    "get_dependencies": "object_name",
    "get_table_metadata": "table",
    "get_grants": "name",
    "get_compile_errors": "name",
}
LIVE_TOOL_NAMES = frozenset(_NAME_FIELD)


def live_tool_schemas(environments: list[str]) -> list[dict]:
    env = {
        "type": "string",
        "enum": [*environments, "all"],
        "description": "One environment, or all to read every connected environment in one call.",
    }

    def tool(name: str, description: str, extra: dict | None = None) -> dict:
        properties = {"env": env, "owner": {"type": "string"}, _NAME_FIELD[name]: {"type": "string"}, **(extra or {})}
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": list(properties)},
            },
        }

    object_type = {"object_type": {"type": "string", "enum": list(SOURCE_TYPES)}}
    return [
        tool("get_object_status", "Read the validity and last DDL time of every object with this name."),
        tool("get_package_source", "Read and checksum a package specification and body. Returns line counts, not source."),
        tool("get_object_source", "Read the source of one allowlisted object. Returns a line count, not source.", object_type),
        tool("get_arguments", "Read the argument metadata of every routine in a package."),
        tool(
            "get_dependencies",
            "Read dictionary dependencies of one object. Objects it returns become readable.",
            {**object_type, "direction": {"type": "string", "enum": list(DIRECTIONS)}},
        ),
        tool("get_table_metadata", "Read columns, constraints, and triggers of a table. No row data."),
        tool("get_grants", "Read object privileges granted on an object."),
        tool("get_compile_errors", "Read compilation errors recorded for an object."),
    ]


class LiveToolContext:
    """Gateways, allowlist, and recorded reads for one agentic run."""

    def __init__(self, gateways: dict, packages: list[PackageRef], max_depth: int) -> None:
        self.gateways = dict(gateways)
        self.packages = list(packages)
        self.max_depth = max_depth
        self.reads = RecordedReads()
        self.unavailable: list[str] = []
        self.allowed_objects = {package.qualified for package in packages}

    @property
    def environments(self) -> list[str]:
        return [environment for environment in self.gateways if environment not in self.unavailable]

    def allowed(self, owner: str, name: str) -> bool:
        return owner not in ORACLE_MAINTAINED_SCHEMAS and f"{owner}.{name}" in self.allowed_objects

    def allow(self, owner: str, name: str) -> None:
        if owner and name and owner not in ORACLE_MAINTAINED_SCHEMAS:
            self.allowed_objects.add(f"{owner}.{name}")

    def is_selected(self, owner: str, name: str) -> bool:
        return any(package.owner == owner and package.name == name for package in self.packages)

    def replay(self, environment: str, misses: list[Miss] | None = None) -> ReplayGateway:
        gateway = self.gateways[environment]
        return ReplayGateway(
            environment,
            getattr(gateway, "database_id", environment),
            getattr(gateway, "label", environment.upper()),
            self.reads,
            misses,
        )


def dispatch_live_tool(name: str, arguments: dict, context: LiveToolContext) -> dict:
    """Validate one call and run it. Oracle errors in Test or Production propagate and stop the run."""
    if name not in LIVE_TOOL_NAMES:
        return {"error": "tool is not allowlisted"}
    try:
        env = str(arguments["env"])
        if env == "all":
            environments = context.environments
        elif env in context.environments:
            environments = [env]
        else:
            return {"error": "environment is not allowlisted"}
        owner = arguments["owner"]
        target = arguments[_NAME_FIELD[name]]
        _idents(owner, target)
        object_type = ""
        direction = ""
        if name in {"get_object_source", "get_dependencies"}:
            object_type = arguments["object_type"]
            if object_type not in SOURCE_TYPES:
                return {"error": "object type is not allowlisted"}
        if name == "get_dependencies":
            direction = arguments["direction"]
            if direction not in DIRECTIONS:
                return {"error": "direction is not allowlisted"}
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": "invalid tool arguments", "detail": str(exc)}
    if not context.allowed(owner, target):
        return {"error": "object is outside the investigation allowlist"}
    results = {}
    for environment in environments:
        try:
            results[environment] = _READERS[name](context, environment, owner, target, object_type, direction)
        except OracleAccessError:
            if environment in SCORED_ENVIRONMENTS:
                raise
            context.unavailable.append(environment)
            results[environment] = {"error": "This environment could not be read. It is dropped from the checklist."}
    return {"results": results}


def _idents(*values: object) -> None:
    for value in values:
        if not isinstance(value, str) or not is_identifier(value):
            raise ValueError("identifier rejected")


def _read_status(context, environment, owner, name, object_type, direction) -> dict:
    fetched = context.gateways[environment].fetch_object_status(owner, name)
    context.reads.status[(environment, owner, name)] = fetched
    if fetched.inaccessible:
        return {"inaccessible": True}
    return {"objects": [{"type": kind, "status": status} for kind, status, _ in fetched.objects]}


def _read_package_source(context, environment, owner, package, object_type, direction) -> dict:
    gateway = context.gateways[environment]
    summary = {}
    for kind, label in (("PACKAGE", "specification"), ("PACKAGE BODY", "body")):
        record = gateway.fetch_source(owner, package, kind)
        context.reads.sources[(environment, owner, package, kind)] = record
        context.reads.hashes[(environment, owner, package, kind)] = gateway.verify_hash(owner, package, kind)
        summary[label] = _source_summary(record)
    if context.is_selected(owner, package):
        for table_owner, table in body_tables(context.reads.sources[(environment, owner, package, "PACKAGE BODY")], owner):
            context.allow(table_owner, table)
    return summary


def body_tables(record: SourceRecord | None, owner: str) -> list[tuple[str, str]]:
    """Tables named in a package body's static SQL. The scorers look these up even without a dictionary edge."""
    if record is None or not record.found or record.inaccessible:
        return []
    try:
        scan = scan_package(record.source)
    except Exception:
        return []
    tables = {
        (ref.owner or owner, ref.table)
        for routine in scan.routines
        for ref in routine.sql_refs
        if is_identifier(ref.owner or owner) and is_identifier(ref.table)
    }
    return sorted(tables)


def _read_object_source(context, environment, owner, name, object_type, direction) -> dict:
    record = context.gateways[environment].fetch_source(owner, name, object_type)
    context.reads.sources[(environment, owner, name, object_type)] = record
    return _source_summary(record)


def _source_summary(record: SourceRecord) -> dict:
    if record.inaccessible:
        return {"inaccessible": True}
    if not record.found:
        return {"found": False}
    return {"found": True, "lines": len(record.source.splitlines())}


def _read_arguments(context, environment, owner, package, object_type, direction) -> dict:
    fetched = context.gateways[environment].fetch_arguments(owner, package)
    context.reads.arguments[(environment, owner, package)] = fetched
    if fetched.inaccessible:
        return {"inaccessible": True}
    routines = sorted({item.object_name for item in fetched.arguments})
    return {"routines": routines[:_MAX_LISTED], "arguments": len(fetched.arguments)}


def _read_dependencies(context, environment, owner, name, object_type, direction) -> dict:
    fetched = context.gateways[environment].fetch_dependencies(owner, name, object_type)
    context.reads.dependencies[(environment, owner, name, object_type)] = fetched
    if fetched.inaccessible:
        return {"inaccessible": True}
    listed = []
    skipped = 0
    for edge in fetched.edges:
        outgoing = edge.direction == "referenced"
        other = (
            (edge.referenced_owner, edge.referenced_name, edge.referenced_type)
            if outgoing
            else (edge.owner, edge.name, edge.object_type)
        )
        if other[0] in ORACLE_MAINTAINED_SCHEMAS:
            skipped += 1
            continue
        if other[2] not in _UNRESOLVED:
            context.allow(other[0], other[1])
        if edge.direction == direction:
            entry = {"owner": other[0], "name": other[1], "type": other[2]}
            if other[2] in _UNRESOLVED:
                entry["note"] = "not followed"
            listed.append(entry)
    summary = {"objects": listed[:_MAX_LISTED]}
    if len(listed) > _MAX_LISTED:
        summary["more"] = len(listed) - _MAX_LISTED
    if skipped:
        summary["oracle_supplied_skipped"] = skipped
    return summary


def _read_table(context, environment, owner, table, object_type, direction) -> dict:
    metadata = context.gateways[environment].fetch_table(owner, table)
    context.reads.tables[(environment, owner, table)] = metadata
    if metadata.inaccessible:
        return {"inaccessible": True}
    if not metadata.exists:
        return {"exists": False}
    for trigger in metadata.triggers:
        context.allow(owner, trigger.name)
    return {
        "exists": True,
        "columns": len(metadata.columns),
        "constraints": len(metadata.constraints),
        "triggers": [trigger.name for trigger in metadata.triggers],
    }


def _read_grants(context, environment, owner, name, object_type, direction) -> dict:
    fetched = context.gateways[environment].fetch_grants(owner, name)
    context.reads.grants[(environment, owner, name)] = fetched
    if fetched.inaccessible:
        return {"inaccessible": True}
    return {"grants": len(fetched.grants), "complete": fetched.complete}


def _read_errors(context, environment, owner, name, object_type, direction) -> dict:
    fetched = context.gateways[environment].fetch_compile_errors(owner, name)
    context.reads.errors[(environment, owner, name)] = fetched
    if fetched.inaccessible:
        return {"inaccessible": True}
    return {
        "errors": len(fetched.errors),
        "lines": [{"type": kind, "line": line} for kind, line, _, _, _ in fetched.errors[:5]],
    }


_READERS = {
    "get_object_status": _read_status,
    "get_package_source": _read_package_source,
    "get_object_source": _read_object_source,
    "get_arguments": _read_arguments,
    "get_dependencies": _read_dependencies,
    "get_table_metadata": _read_table,
    "get_grants": _read_grants,
    "get_compile_errors": _read_errors,
}


@dataclass(frozen=True)
class RequiredRead:
    tool: str
    env: str
    owner: str
    name: str
    object_type: str = ""
    direction: str = ""

    def call(self) -> dict:
        arguments = {"env": self.env, "owner": self.owner, _NAME_FIELD[self.tool]: self.name}
        if self.object_type:
            arguments["object_type"] = self.object_type
        if self.direction:
            arguments["direction"] = self.direction
        return {"tool": self.tool, "arguments": arguments}

    def describe(self) -> str:
        what = {
            "get_object_status": "status",
            "get_package_source": "specification and body",
            "get_object_source": f"{self.object_type.lower()} source",
            "get_arguments": "arguments",
            "get_dependencies": "callers" if self.direction == "referenced_by" else "referenced objects",
            "get_table_metadata": "table definition",
            "get_grants": "grants",
            "get_compile_errors": "compile errors",
        }[self.tool]
        return f"{self.env} {self.owner}.{self.name} {what}"


def suggested_calls(remaining: list[RequiredRead], environments: list[str]) -> list[dict]:
    """Tool calls that cover the remaining reads. An item needed in every environment becomes one env "all" call."""
    groups: dict[tuple[str, str, str, str, str], list[RequiredRead]] = {}
    for item in remaining:
        groups.setdefault((item.tool, item.owner, item.name, item.object_type, item.direction), []).append(item)
    calls = []
    for items in groups.values():
        if len(environments) > 1 and {item.env for item in items} == set(environments):
            call = items[0].call()
            call["arguments"]["env"] = "all"
            calls.append(call)
        else:
            calls.extend(item.call() for item in items)
    return calls


_MISS_TOOLS = {
    "arguments": "get_arguments",
    "grants": "get_grants",
    "status": "get_object_status",
    "errors": "get_compile_errors",
    "table": "get_table_metadata",
}


def _required(miss: Miss, direction: str) -> RequiredRead:
    if miss.kind == "source":
        if miss.object_type in PACKAGE_TYPES:
            return RequiredRead("get_package_source", miss.environment, miss.owner, miss.name)
        return RequiredRead("get_object_source", miss.environment, miss.owner, miss.name, miss.object_type)
    if miss.kind == "dependencies":
        return RequiredRead("get_dependencies", miss.environment, miss.owner, miss.name, miss.object_type, direction)
    return RequiredRead(_MISS_TOOLS[miss.kind], miss.environment, miss.owner, miss.name)


def outstanding_reads(context: LiveToolContext) -> list[RequiredRead]:
    """Reads the deterministic scorers need that have not been collected, in the order they would be made."""
    required: list[RequiredRead] = []

    def collect(environment: str, read, direction: str = ""):
        misses: list[Miss] = []
        result = read(context.replay(environment, misses))
        required.extend(_required(miss, direction) for miss in misses)
        return result

    environments = context.environments
    pairs = [(package.owner, package.name) for package in context.packages]
    for owner, name in pairs:
        for environment in environments:
            for object_type in PACKAGE_TYPES:
                collect(environment, lambda gateway, t=object_type, o=owner, n=name: gateway.fetch_source(o, n, t))
        for environment in SCORED_ENVIRONMENTS:
            collect(environment, lambda gateway, o=owner, n=name: gateway.fetch_arguments(o, n))
            collect(environment, lambda gateway, o=owner, n=name: gateway.fetch_grants(o, n))

    bodies = [(owner, name, "PACKAGE BODY") for owner, name in pairs]
    specs = [(owner, name, "PACKAGE") for owner, name in pairs]
    incoming_depth = {"production": context.max_depth, "test": 1}
    outgoing = {}
    incoming = {}
    for environment in SCORED_ENVIRONMENTS:
        outgoing[environment] = collect(
            environment,
            lambda gateway: discover_dependencies(gateway, bodies, "referenced", context.max_depth),
            "referenced",
        )
    for environment in SCORED_ENVIRONMENTS:
        incoming[environment] = collect(
            environment,
            lambda gateway, depth=incoming_depth[environment]: discover_dependencies(gateway, specs, "referenced_by", depth),
            "referenced_by",
        )

    tables = sorted(
        {
            (edge.referenced_owner, edge.referenced_name)
            for graph in outgoing.values()
            for edge in graph.edges
            if edge.referenced_type == "TABLE"
        }
    )
    for owner, table in tables:
        for environment in SCORED_ENVIRONMENTS:
            collect(environment, lambda gateway, o=owner, t=table: gateway.fetch_table(o, t))
    for owner, name in pairs:
        for table_owner, table in body_tables(context.reads.sources.get(("test", owner, name, "PACKAGE BODY")), owner):
            if (table_owner, table) not in tables:
                collect("production", lambda gateway, o=table_owner, t=table: gateway.fetch_table(o, t))
    for owner, name, _type, _dependent in existence_candidates(pairs, outgoing["test"].edges, outgoing["production"].edges):
        collect("production", lambda gateway, o=owner, n=name: gateway.fetch_object_status(o, n))

    callers = sorted(
        {
            (edge.owner, edge.name)
            for graph in incoming.values()
            for edge in graph.edges
            if edge.depth == 1 and edge.direction == "referenced_by"
        }
    )
    for environment in environments:
        collect(
            environment,
            lambda gateway, e=environment: collect_compilation({e: gateway}, pairs, {e: callers}),
        )

    seen: set[tuple[str, str]] = set()
    for edge in incoming["production"].edges:
        if edge.depth != 1 or edge.object_type not in PACKAGE_TYPES or (edge.owner, edge.name) in seen:
            continue
        if len(seen) >= MAX_CALLER_SOURCES:
            break
        seen.add((edge.owner, edge.name))
        for environment in SCORED_ENVIRONMENTS:
            for object_type in PACKAGE_TYPES:
                collect(environment, lambda gateway, t=object_type, o=edge.owner, n=edge.name: gateway.fetch_source(o, n, t))

    unique: list[RequiredRead] = []
    keys: set[tuple[str, str, str, str, str]] = set()
    for item in required:
        key = (item.tool, item.env, item.owner, item.name, item.object_type)
        if key not in keys:
            keys.add(key)
            unique.append(item)
    return unique
