"""Rule identifiers, severity rationale, and run-independent fingerprints for findings.

Finding ids include the run id, so they cannot be compared across runs. The
fingerprint uses only the object identity, the rule, and the evidence text.
"""

import hashlib

from oracle_impact.models.report import Finding

_SIGNATURE_RATIONALE = {
    True: "Critical: Oracle argument metadata confirms an incompatible interface change. Existing calls can fail to compile or bind to different parameters.",
    False: "Low: the interface change keeps existing calls valid.",
}


def annotate(findings: list[Finding]) -> None:
    for finding in findings:
        if finding.origin == "investigator":
            continue
        rule_id, rationale = classify(finding)
        finding.rule_id = finding.rule_id or rule_id
        finding.severity_rationale = finding.severity_rationale or rationale
        finding.fingerprint = finding.fingerprint or fingerprint(finding)


def fingerprint(finding: Finding) -> str:
    rule_id = finding.rule_id or classify(finding)[0]
    parts = [
        finding.object_owner,
        finding.object_name,
        finding.object_type,
        finding.change_category,
        rule_id,
        " ".join(finding.evidence.split()),
    ]
    return "FP-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16].upper()


def classify(finding: Finding) -> tuple[str, str]:
    attributes = finding.attributes or {}
    category = finding.change_category
    if category == "interface":
        kind = str(attributes.get("signature_kind") or "unknown").replace("_", "-")
        if kind == "metadata-gap":
            return "SIG-METADATA-GAP", "Medium: argument metadata was unreadable, so interface compatibility was not decided."
        if kind == "added-overload":
            return "SIG-ADDED-OVERLOAD", "Medium: overload resolution for existing calls is not proven."
        if attributes.get("coordinated"):
            return f"SIG-{kind.upper()}", "Medium: the visible database callers were updated in this change set; callers outside the database are unknown."
        return f"SIG-{kind.upper()}", _SIGNATURE_RATIONALE[bool(attributes.get("breaking"))]
    if category == "compilation":
        scope = str(attributes.get("compile_scope") or "in_scope")
        return f"CMP-{scope.upper()}", finding.severity_rationale or "Oracle reports the object invalid or with compilation errors."
    if category == "coverage":
        if attributes.get("snapshot_drift"):
            return "COV-SNAPSHOT-DRIFT", "Medium: the object changed while it was captured, so the comparison may mix versions."
        return "COV-METADATA-GAP", "Medium: part of the scope could not be analyzed and cannot be treated as safe."
    if category == "data":
        if attributes.get("missing_object"):
            return "DATA-MISSING-OBJECT", "Critical: a table the new code writes to does not exist in Production."
        if attributes.get("new_table_unverified"):
            return "DATA-NEW-TABLE", "High for writes, medium for reads: the table is newly referenced and its Production structure or migration order is unverified."
        return "DATA-MODIFIED-DML", "High when the statement involves amounts or rates, otherwise medium: a changed write alters stored data."
    if category == "dependency":
        return "DEP-MISSING-OBJECT", "Critical: an object the new code depends on does not exist in Production, so the body cannot compile there."
    if category == "transaction":
        if attributes.get("partial_commit_hazard"):
            return "TXN-PARTIAL-COMMIT", "High: a commit placement change can leave earlier statements durable when a later one fails."
        return "TXN-SEQUENCE", "High: transaction boundaries decide which changes survive a failure."
    if category == "exception":
        return "EXC-HANDLER", "High: changed error handling alters what callers observe when something fails."
    if category == "dynamic_sql":
        return "DYN-SQL", "High: dynamic SQL targets are invisible to static analysis."
    if category == "business_logic":
        if attributes.get("threshold"):
            return "BIZ-THRESHOLD", "High for amounts or rates, otherwise medium: a changed comparison alters which cases a rule accepts."
        return "BIZ-LOGIC", "High when amounts or rates change, otherwise low: executable text changed and its effect is not inferred."
    if category == "error_contract":
        kind = str(attributes.get("error_contract") or "changed")
        if kind == "added":
            return "ERR-ADDED", "Low: a new error code reaches callers that do not handle it yet."
        return f"ERR-{kind.upper().replace('_', '-')}", "High: callers or monitoring that handle the old error contract stop receiving it."
    if category == "security":
        if attributes.get("authid_changed"):
            return "SEC-AUTHID", "High: the privilege and name-resolution model of every call changes."
        kind = str(attributes.get("grant_difference") or "grant")
        if kind == "missing_in_production":
            return "SEC-GRANT-MISSING", "Medium: accounts that use the package in Test lack the grant in Production."
        return "SEC-GRANT-EXTRA", "Low: Production grants are kept by CREATE OR REPLACE but are not covered by Test."
    if category == "format":
        return "FMT-ONLY", "Informational: normalized source is unchanged."
    return f"OTHER-{category.upper()}", ""
