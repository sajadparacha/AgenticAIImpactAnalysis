"""Production existence of objects only the Test version depends on.

The Test body's dictionary dependencies are compared with Production's. An object
that only the Test version references is looked up in Production's ALL_OBJECTS.
If it is absent, the deployed body cannot compile or resolve it there.
"""

from oracle_impact.analysis.hashing import finding_id
from oracle_impact.models.enums import Confidence, EvidenceStatus, Severity
from oracle_impact.models.report import Finding

_SKIPPED_TYPES = {"SYNONYM", "DATABASE LINK", "NON-EXISTENT"}
MAX_EXISTENCE_CHECKS = 50


def existence_candidates(packages: list[tuple[str, str]], test_edges, prod_edges) -> list[tuple[str, str, str, str]]:
    """(owner, name, object_type, dependent package) for objects the Test bodies reference directly
    that no Production dependency row names. A Production dependency row proves the object exists there."""
    selected = set(packages)
    in_production = {
        (edge.referenced_owner, edge.referenced_name, edge.referenced_type) for edge in prod_edges if edge.direction == "referenced"
    }
    candidates: dict[tuple[str, str, str], str] = {}
    for edge in test_edges:
        if edge.direction != "referenced" or edge.depth != 1 or (edge.owner, edge.name) not in selected:
            continue
        key = (edge.referenced_owner, edge.referenced_name, edge.referenced_type)
        if (key[0], key[1]) in selected or key[2] in _SKIPPED_TYPES or key in in_production:
            continue
        candidates.setdefault(key, f"{edge.owner}.{edge.name}")
    return [(*key, dependent) for key, dependent in sorted(candidates.items())][:MAX_EXISTENCE_CHECKS]


def existence_findings(run_id: str, candidates, gateway, labels: dict[str, str], reported: set[str]) -> list[Finding]:
    """One finding per candidate missing from Production. `reported` holds objects another
    check already reported missing, so they are not listed twice."""
    reader = getattr(gateway, "fetch_object_status", None)
    if reader is None:
        return []
    production = labels.get("production", "Production")
    findings: list[Finding] = []
    for owner, name, object_type, dependent in candidates:
        qualified = f"{owner}.{name}"
        if qualified in reported:
            continue
        fetched = reader(owner, name)
        package_owner, package_name = dependent.split(".", 1)
        if fetched.inaccessible:
            detail = f"{dependent} now depends on {object_type} {qualified}. Its existence in {production} could not be checked."
            findings.append(
                _finding(
                    run_id, package_owner, package_name, qualified, object_type, detail,
                    severity=Severity.MEDIUM,
                    status=EvidenceStatus.UNKNOWN,
                    action=f"Confirm that {object_type} {qualified} exists in {production} before deployment.",
                    attributes={"blocks_eligibility": True, "dependency_unverified": True, "dependency_type": object_type},
                    category="coverage",
                    rationale="Medium: an object the new code depends on could not be looked up in Production.",
                )
            )
            continue
        types = sorted({kind for kind, _status, _ddl in fetched.objects})
        if object_type in types:
            continue
        other = f" {production} has {qualified} only as {', '.join(types)}." if types else ""
        detail = f"{dependent} now depends on {object_type} {qualified}, which was not found in {production}.{other}"
        findings.append(
            _finding(
                run_id, package_owner, package_name, qualified, object_type, detail,
                severity=Severity.CRITICAL,
                status=EvidenceStatus.CONFIRMED,
                action=f"Deploy {object_type} {qualified} to {production} first, or remove the reference, then run a new analysis.",
                attributes={"missing_object": True, "missing_type": object_type},
                category="dependency",
                rationale="Critical: an object the new code depends on does not exist in Production, so the body cannot compile there.",
            )
        )
    return findings


def _finding(run_id, owner, name, qualified, object_type, detail, *, severity, status, action, attributes, category, rationale) -> Finding:
    return Finding(
        id=finding_id(run_id, "existence", "production", qualified, object_type),
        changed_object=f"{owner}.{name}",
        object_owner=owner,
        object_name=name,
        object_type="PACKAGE BODY",
        source_database="test",
        change_category=category,
        affected_object=qualified,
        dependency_path=[f"{owner}.{name}", qualified],
        evidence=detail,
        severity=severity,
        confidence=Confidence.HIGH if status == EvidenceStatus.CONFIRMED else Confidence.MEDIUM,
        status=status,
        explanation=detail + " Existence is read from Production's ALL_OBJECTS; an object the analysis account cannot see is reported as absent.",
        recommended_action=action,
        proposed_tests=[f"Confirm ALL_OBJECTS in Production lists {object_type} {qualified} before the package is deployed."],
        attributes=attributes,
        severity_rationale=rationale,
    )
