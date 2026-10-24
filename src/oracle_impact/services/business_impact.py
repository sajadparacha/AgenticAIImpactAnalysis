"""Business process impact from maintained object-to-process mappings.

Impact is only stated for objects someone has mapped. The level comes from
what this run established about the mapped object; nothing is inferred from
names. Objects without a mapping are listed as "business impact not established".
"""

from oracle_impact.models.enums import EvidenceStatus, Severity
from oracle_impact.models.report import Report

CONFIRMED, POTENTIAL, NONE_FOUND, NOT_ANALYZED = "confirmed", "potential", "none_identified", "not_analyzed"
LABELS = {
    CONFIRMED: "Confirmed impact",
    POTENTIAL: "Potential impact",
    NONE_FOUND: "No impact identified within analyzed scope",
    NOT_ANALYZED: "Not analyzed",
}
TONES = {CONFIRMED: "danger", POTENTIAL: "warning", NONE_FOUND: "success", NOT_ANALYZED: "neutral"}
CRITICALITIES = ("low", "medium", "high", "critical")
_SERIOUS = {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}


def _qualified(owner: str, name: str) -> str:
    return f"{owner}.{name}"


def business_impact(report: Report, mappings: list[dict]) -> dict:
    active = [item for item in mappings if item.get("status") == "active"]
    in_scope = set(report.packages)
    analyzed = set(in_scope)
    for edge in report.dependency_edges:
        analyzed.add(_qualified(edge.owner, edge.name))
        if edge.referenced_owner:
            analyzed.add(_qualified(edge.referenced_owner, edge.referenced_name))
    for dep in report.dependencies:
        analyzed.add(_qualified(dep.owner, dep.object_name))
    changed = {
        _qualified(state.owner, state.name)
        for state in report.object_states
        if state.change_type in {"modified", "created", "deleted"}
    }
    if not report.object_states:
        changed = {f"{diff.owner}.{diff.name}" for diff in report.diffs if not diff.unchanged and not diff.formatting_only}
    deterministic = [finding for finding in report.findings if finding.origin != "investigator"]
    rows = []
    for mapping in active:
        qualified = _qualified(mapping.get("owner", ""), mapping.get("name", ""))
        level, basis = _level(qualified, in_scope, analyzed, changed, deterministic)
        rows.append(
            {
                "mapping": mapping,
                "qualified": qualified,
                "level": level,
                "label": LABELS[level],
                "tone": TONES[level],
                "basis": basis,
            }
        )
    mapped = {row["qualified"] for row in rows}
    unmapped = sorted(item for item in in_scope | {_qualified(f.object_owner, f.object_name) for f in deterministic if f.object_owner} if item not in mapped)
    order = (CONFIRMED, POTENTIAL, NONE_FOUND, NOT_ANALYZED)
    rows.sort(key=lambda row: (order.index(row["level"]), CRITICALITIES[::-1].index(row["mapping"].get("criticality", "low")) if row["mapping"].get("criticality") in CRITICALITIES else 9, row["qualified"]))
    return {
        "rows": [row for row in rows if row["level"] != NOT_ANALYZED],
        "not_analyzed": [row for row in rows if row["level"] == NOT_ANALYZED],
        "unmapped": unmapped,
        "counts": {key: sum(1 for row in rows if row["level"] == key) for key in order},
        "established": bool(rows) and any(row["level"] != NOT_ANALYZED for row in rows),
    }


def _level(qualified: str, in_scope: set[str], analyzed: set[str], changed: set[str], findings) -> tuple[str, str]:
    related = [
        finding
        for finding in findings
        if finding.changed_object == qualified or (finding.affected_object or "").upper() == qualified
    ]
    confirmed = [item for item in related if item.status == EvidenceStatus.CONFIRMED and item.severity in _SERIOUS]
    if confirmed:
        ids = ", ".join(item.id for item in confirmed[:3])
        return CONFIRMED, f"Deterministic evidence confirms a medium or higher finding on this object ({ids})."
    if related:
        ids = ", ".join(item.id for item in related[:3])
        return POTENTIAL, f"Findings mention this object but none is a confirmed medium or higher issue ({ids})."
    if qualified in changed:
        return POTENTIAL, "The object changed in this change set. No specific finding links it to a behavior change."
    if qualified in analyzed:
        if qualified in in_scope:
            return NONE_FOUND, "The object was analyzed and is unchanged or has no finding."
        return NONE_FOUND, "The object is in the analyzed dependency graph and no finding references it."
    return NOT_ANALYZED, "The object is outside the packages and dependencies this run read."
