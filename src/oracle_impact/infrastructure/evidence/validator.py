"""Validate investigator findings against the captured snapshot.

A model citation that does not resolve cannot stay confirmed, and it cannot
drive the deployment recommendation.
"""

from oracle_impact.models.domain import Snapshot
from oracle_impact.models.enums import Confidence, EvidenceStatus, Severity
from oracle_impact.models.report import Finding


def validate_findings(findings: list[Finding], snapshot: Snapshot) -> list[Finding]:
    validated: list[Finding] = []
    for finding in findings:
        checked = finding.model_copy(deep=True)
        checked.origin = "investigator"
        if not _citation_resolves(checked, snapshot):
            checked.status = EvidenceStatus.POTENTIAL
            checked.confidence = Confidence.LOW
            checked.severity = Severity.LOW
            checked.attributes["citation_unconfirmed"] = True
            checked.attributes["blocks_eligibility"] = False
            checked.recommended_action = "Read this with the deterministic finding it reviews."
            validated.append(checked)
            continue
        if checked.status == EvidenceStatus.CONFIRMED:
            checked.status = EvidenceStatus.POTENTIAL
            checked.explanation += " Status is potential: the investigator cannot confirm a finding."
            checked.attributes["downgraded"] = True
        checked.attributes["blocks_eligibility"] = False
        validated.append(checked)
    return validated


def _citation_resolves(finding: Finding, snapshot: Snapshot) -> bool:
    if not finding.object_owner or not finding.object_name:
        return False
    source = ""
    for object_type in ("PACKAGE BODY", "PACKAGE", finding.object_type):
        text = snapshot.text("test", finding.object_owner, finding.object_name, object_type)
        if text:
            source = text
            break
        text = snapshot.text("production", finding.object_owner, finding.object_name, object_type)
        if text:
            source = text
            break
    if not source:
        return False
    line_count = len(source.splitlines())
    lines = finding.changed_lines_test or finding.corresponding_lines_prod
    if not lines:
        return False
    return all(1 <= line <= line_count for line in lines)
