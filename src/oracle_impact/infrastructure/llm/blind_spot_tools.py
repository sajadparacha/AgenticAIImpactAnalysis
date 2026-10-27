"""Allowlisted reads for the blind-spot questions.

The investigator may read source lines of the packages named in a question, the
ALL_SYNONYMS row of a named synonym, and the ALL_OBJECTS rows of a name that the
cited source or a synonym read mentions. Every read goes through an existing
gateway method and the fixed query catalog.
"""

import re

from oracle_impact.analysis.blind_spots import SYNONYM, BlindSpot
from oracle_impact.analysis.identifiers import ORACLE_MAINTAINED_SCHEMAS, is_identifier
from oracle_impact.infrastructure.oracle.gateway import OracleAccessError
from oracle_impact.models.domain import Snapshot

ENVIRONMENTS = ("test", "production")
MAX_LINES_PER_READ = 60
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")


def blind_spot_tool_schemas() -> list[dict]:
    env = {"type": "string", "enum": list(ENVIRONMENTS)}

    def tool(name: str, description: str, extra: dict | None = None) -> dict:
        properties = {"env": env, "owner": {"type": "string"}, "name": {"type": "string"}, **(extra or {})}
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": list(properties)},
            },
        }

    return [
        tool(
            "read_source_lines",
            f"Read numbered lines of a package body named in a question. At most {MAX_LINES_PER_READ} lines per call.",
            {"start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        ),
        tool("read_synonym", "Read which object a synonym named in a question points to."),
        tool("check_object", "Check whether an object exists. The name must appear in the cited source or a synonym read."),
    ]


class BlindSpotContext:
    def __init__(self, snapshot: Snapshot, gateways: dict, spots: list[BlindSpot]) -> None:
        self.snapshot = snapshot
        self.gateways = {env: gateway for env, gateway in gateways.items() if env in ENVIRONMENTS}
        self.spots = list(spots)
        self.sources = {(spot.owner, spot.name) for spot in spots}
        self.synonyms = {(spot.synonym_owner, spot.synonym_name) for spot in spots if spot.kind == SYNONYM}
        self.mentioned: set[str] = set()
        for owner, name in self.sources:
            self.mentioned.update({owner, name})
            for env in ENVIRONMENTS:
                self.mentioned.update(word.upper() for word in _WORD.findall(snapshot.text(env, owner, name, "PACKAGE BODY")))
        for owner, name in self.synonyms:
            self.mentioned.update({owner, name})
        self.facts: dict[tuple[str, str, str], str] = {}


def dispatch_blind_spot_tool(name: str, arguments: dict, context: BlindSpotContext) -> dict:
    try:
        env = str(arguments["env"])
        owner = str(arguments["owner"]).upper()
        target = str(arguments["name"]).upper()
        if env not in context.gateways:
            return {"error": "environment is not allowlisted"}
        if not (is_identifier(owner) and is_identifier(target)) or owner in ORACLE_MAINTAINED_SCHEMAS:
            return {"error": "identifier rejected"}
        if name == "read_source_lines":
            return _source_lines(context, env, owner, target, int(arguments["start_line"]), int(arguments["end_line"]))
        if name == "read_synonym":
            return _synonym(context, env, owner, target)
        if name == "check_object":
            return _check(context, env, owner, target)
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": "invalid tool arguments", "detail": str(exc)}
    return {"error": "tool is not allowlisted"}


def _source_lines(context: BlindSpotContext, env: str, owner: str, name: str, start: int, end: int) -> dict:
    if (owner, name) not in context.sources:
        return {"error": "object is not named in a question"}
    lines = context.snapshot.text(env, owner, name, "PACKAGE BODY").splitlines()
    if not lines:
        return {"error": "no captured source for this object"}
    start = max(1, start)
    end = min(len(lines), end, start + MAX_LINES_PER_READ - 1)
    if end < start:
        return {"error": "line range is outside the source", "line_count": len(lines)}
    return {"line_count": len(lines), "lines": [{"line": number, "text": lines[number - 1]} for number in range(start, end + 1)]}


def _synonym(context: BlindSpotContext, env: str, owner: str, name: str) -> dict:
    if (owner, name) not in context.synonyms:
        return {"error": "synonym is not named in a question"}
    try:
        fetched = context.gateways[env].fetch_synonym(owner, name)
    except OracleAccessError:
        context.facts[(env, owner, name)] = "could not be read"
        return {"error": "the synonym could not be read"}
    if fetched.inaccessible:
        context.facts[(env, owner, name)] = "could not be read"
        return {"inaccessible": True}
    if not fetched.found:
        context.facts[(env, owner, name)] = "synonym not found"
        return {"found": False}
    target = f"{fetched.target_owner}.{fetched.target_name}"
    if fetched.db_link:
        context.facts[(env, owner, name)] = f"points to {target}@{fetched.db_link} in another database"
    else:
        context.facts[(env, owner, name)] = f"points to {target}"
        context.mentioned.update({fetched.target_owner, fetched.target_name})
    return {"found": True, "target_owner": fetched.target_owner, "target_name": fetched.target_name, "db_link": fetched.db_link}


def _check(context: BlindSpotContext, env: str, owner: str, name: str) -> dict:
    if name not in context.mentioned or owner not in context.mentioned:
        return {"error": "name does not appear in the cited source or a synonym read"}
    try:
        fetched = context.gateways[env].fetch_object_status(owner, name)
    except OracleAccessError:
        context.facts[(env, owner, name)] = "could not be read"
        return {"error": "the object could not be read"}
    if fetched.inaccessible:
        context.facts[(env, owner, name)] = "could not be read"
        return {"inaccessible": True}
    types = sorted({kind for kind, _status, _ddl in fetched.objects})
    context.facts[(env, owner, name)] = f"exists ({', '.join(types).lower()})" if types else "not found"
    return {"exists": bool(types), "types": types}
