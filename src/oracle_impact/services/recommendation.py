"""Deployment recommendation policy.

The investigator cannot upgrade this decision. A positive result stays
conditional on testing and human approval.
"""

from oracle_impact.infrastructure.llm.plain_text import simplify_change
from oracle_impact.models.enums import RECOMMENDATION_LABELS, Recommendation, Severity
from oracle_impact.models.report import Finding


def decide(findings: list[Finding]) -> tuple[Recommendation, list[str], list[str]]:
    """Use deterministic findings only. Investigator output cannot move this decision."""
    findings = [finding for finding in findings if finding.origin != "investigator"]
    reasons: list[str] = []
    blocking = [
        finding
        for finding in findings
        if finding.attributes.get("breaking")
        or finding.attributes.get("missing_object")
        or finding.attributes.get("partial_commit_hazard")
        or (finding.attributes.get("compile_invalid") and finding.attributes.get("compile_scope") == "in_scope")
    ]
    if blocking:
        for finding in blocking:
            reasons.append(f"{finding.id}: {simplify_change(finding, finding.explanation)}")
        return Recommendation.NOT_RECOMMENDED, reasons, _actions(findings, Recommendation.NOT_RECOMMENDED)

    holding = [
        finding
        for finding in findings
        if finding.attributes.get("blocks_eligibility")
        or finding.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
    ]
    if holding:
        for finding in holding:
            reasons.append(f"{finding.id}: {simplify_change(finding, finding.explanation)}")
        return (
            Recommendation.REQUIRES_INVESTIGATION,
            reasons,
            _actions(findings, Recommendation.REQUIRES_INVESTIGATION),
        )

    reasons.append(
        "No confirmed blocker was identified. Remaining findings are low or informational."
    )
    return (
        Recommendation.ELIGIBLE_FOR_REVIEW,
        reasons,
        _actions(findings, Recommendation.ELIGIBLE_FOR_REVIEW),
    )


def label_for(recommendation: Recommendation) -> str:
    return RECOMMENDATION_LABELS[recommendation]


def _actions(findings: list[Finding], recommendation: Recommendation) -> list[str]:
    actions = [
        "Obtain human approval before any promotion beyond Test.",
        "Run functional tests in Test for every selected package.",
    ]
    if recommendation == Recommendation.NOT_RECOMMENDED:
        actions.insert(0, "Do not promote this change set until the blocking findings are resolved.")
    elif recommendation == Recommendation.REQUIRES_INVESTIGATION:
        actions.insert(0, "Resolve the investigation items before a promotion review.")
    else:
        actions.append("Record the test evidence with the approval. This result is not a production-safety certification.")
    for finding in findings:
        if finding.recommended_action and finding.recommended_action not in actions:
            if finding.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM} or finding.attributes.get(
                "breaking"
            ):
                actions.append(finding.recommended_action)
    return actions
