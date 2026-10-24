"""Deployment blocking rules and the readiness decision.

Blocking rules run on deterministic findings when an analysis finishes and
their output is saved with the report. Readiness is then evaluated whenever
the report is shown, from that saved output plus recorded validation results,
reviewer decisions, coverage, drift classification, and freshness.

Readiness is a recommendation. Human approval is recorded separately and is
never inferred from it.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from oracle_impact.models.assessment import PolicyEvaluation, PolicyItem
from oracle_impact.models.enums import EvidenceStatus, Severity
from oracle_impact.models.report import Finding, Report

logger = logging.getLogger(__name__)

POLICY_VERSION = "2026.10-1"
FIX_AND_REANALYZE = "fix_and_reanalyze"
VALIDATION_AND_REVIEW = "validation_and_review"
VALIDATION = "validation"
RESOLUTIONS = {
    FIX_AND_REANALYZE: "Fix the code or environment and run a new analysis. Review notes cannot clear it.",
    VALIDATION_AND_REVIEW: "Record a passed validation and a reviewer decision of Accepted, or a reviewer decision of Not an issue with a reason.",
    VALIDATION: "Record a passed validation, or a reviewer decision of Not an issue with a reason.",
}
RECOMMENDED, ON_HOLD, INDETERMINATE = "recommended", "on_hold", "indeterminate"
READINESS_LABELS = {
    RECOMMENDED: "Deployment recommended",
    ON_HOLD: "Deployment on hold",
    INDETERMINATE: "Indeterminate",
}


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    kind: str
    resolution: str
    action: str
    match: Callable[[Finding], bool]
    enabled: bool = True

    def describe(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "resolution": self.resolution,
            "action": self.action,
            "enabled": self.enabled,
        }


def _attr(name: str) -> Callable[[Finding], bool]:
    return lambda finding: bool((finding.attributes or {}).get(name))


def _category(*names: str) -> Callable[[Finding], bool]:
    return lambda finding: finding.change_category in names


DEFAULT_RULES: tuple[Rule, ...] = (
    Rule(
        "BLK-INTERFACE-BREAK",
        "Incompatible interface change",
        "blocker",
        FIX_AND_REANALYZE,
        "Restore compatibility or update every caller, then run a new analysis.",
        lambda f: bool(f.attributes.get("breaking")) and f.status == EvidenceStatus.CONFIRMED,
    ),
    Rule(
        "BLK-MISSING-OBJECT",
        "Referenced object missing in Production",
        "blocker",
        FIX_AND_REANALYZE,
        "Deploy the referenced object first or remove the reference, then run a new analysis.",
        _attr("missing_object"),
    ),
    Rule(
        "BLK-COMPILE-FAILURE",
        "Object does not compile in Test",
        "blocker",
        FIX_AND_REANALYZE,
        "Fix the compilation errors in Test, then run a new analysis.",
        _attr("compile_invalid"),
    ),
    Rule(
        "BLK-PARTIAL-COMMIT",
        "Transaction boundary hazard",
        "blocker",
        VALIDATION_AND_REVIEW,
        "Test the failure path in Test and have a reviewer accept the transaction design.",
        _attr("partial_commit_hazard"),
    ),
    Rule(
        "BLK-SNAPSHOT-DRIFT",
        "Source changed while it was captured",
        "blocker",
        FIX_AND_REANALYZE,
        "Run a new analysis when the object is stable.",
        _attr("snapshot_drift"),
    ),
    Rule(
        "VAL-METADATA-GAP",
        "Part of the scope could not be analyzed",
        "validation",
        VALIDATION,
        "Restore dictionary access or inspect the object manually and record the result.",
        lambda f: f.change_category == "coverage" and not f.attributes.get("snapshot_drift"),
    ),
    Rule(
        "VAL-DYNAMIC-SQL",
        "Changed dynamic SQL",
        "validation",
        VALIDATION,
        "Capture the runtime SQL in Test and confirm the objects it touches.",
        _category("dynamic_sql"),
    ),
    Rule(
        "VAL-DATA-CHANGE",
        "Changed or new data modification",
        "validation",
        VALIDATION,
        "Run the affected transaction in Test and confirm the stored data.",
        lambda f: f.change_category == "data" and not f.attributes.get("missing_object"),
    ),
    Rule(
        "VAL-ERROR-HANDLING",
        "Changed error handling or error contract",
        "validation",
        VALIDATION,
        "Exercise the error paths and confirm what callers receive.",
        lambda f: f.change_category in {"exception", "error_contract"} and f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM},
    ),
    Rule(
        "VAL-SECURITY",
        "Privilege model or grant difference",
        "validation",
        VALIDATION,
        "Call the package as each application account that uses it.",
        lambda f: f.change_category == "security" and f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM},
    ),
    Rule(
        "VAL-BUSINESS-RULE",
        "Changed business rule",
        "validation",
        VALIDATION,
        "Run boundary tests for the changed rule with the business owner's expected results.",
        lambda f: f.change_category == "business_logic" and bool(f.attributes.get("blocks_eligibility")),
    ),
    Rule(
        "VAL-INTERFACE-REVIEW",
        "Interface change that needs caller review",
        "validation",
        VALIDATION,
        "Confirm every caller, including those outside the database, against the new interface.",
        lambda f: f.change_category == "interface" and bool(f.attributes.get("blocks_eligibility")),
    ),
    Rule(
        "VAL-TRANSACTION",
        "Changed transaction sequence",
        "validation",
        VALIDATION,
        "Force a failure after each write and confirm which changes remain.",
        lambda f: f.change_category == "transaction" and not f.attributes.get("partial_commit_hazard"),
    ),
    Rule(
        "VAL-SEVERITY-REVIEW",
        "Medium or higher finding without a specific rule",
        "validation",
        VALIDATION,
        "Review the finding and record how it was validated.",
        lambda f: f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM} or bool(f.attributes.get("blocks_eligibility")),
    ),
)


def load_rules(policy_file: Path | None) -> tuple[Rule, ...]:
    """Apply an optional JSON override: {"rules": {"RULE-ID": {"enabled": false, "resolution": "..."}}}."""
    if policy_file is None:
        return DEFAULT_RULES
    try:
        payload = json.loads(Path(policy_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("policy file could not be read; using the default rules")
        return DEFAULT_RULES
    overrides = payload.get("rules") if isinstance(payload, dict) else None
    if not isinstance(overrides, dict):
        return DEFAULT_RULES
    rules = []
    for rule in DEFAULT_RULES:
        change = overrides.get(rule.id)
        if isinstance(change, dict):
            enabled = change.get("enabled", rule.enabled)
            resolution = change.get("resolution", rule.resolution)
            if resolution not in RESOLUTIONS:
                resolution = rule.resolution
            rule = replace(rule, enabled=bool(enabled), resolution=resolution)
        rules.append(rule)
    return tuple(rules)


def evaluate_policy(findings: list[Finding], rules: tuple[Rule, ...] = DEFAULT_RULES) -> PolicyEvaluation:
    """Each deterministic finding is assigned to the first enabled rule that matches it."""
    evaluation = PolicyEvaluation(policy_version=POLICY_VERSION, rules=[rule.describe() for rule in rules])
    for finding in findings:
        if finding.origin == "investigator":
            continue
        rule = next((item for item in rules if item.enabled and item.match(finding)), None)
        if rule is None:
            continue
        item = PolicyItem(
            rule_id=rule.id,
            title=rule.title,
            finding_id=finding.id,
            resolution=rule.resolution,
            explanation=" ".join(finding.explanation.split()),
            required_action=finding.recommended_action or rule.action,
        )
        if rule.kind == "blocker":
            evaluation.blockers.append(item)
        else:
            evaluation.validations.append(item)
    return evaluation


def policy_for(report: Report) -> tuple[PolicyEvaluation, bool]:
    """The saved evaluation, or one computed now for reports saved before rules existed."""
    if report.policy is not None:
        return report.policy, False
    return evaluate_policy(report.findings), True


def item_status(item: PolicyItem, validations: list[dict], reviews: list[dict]) -> dict:
    """Whether one blocker or validation item is resolved, and why. Only recorded facts count."""
    related = [record for record in validations if record.get("finding_id") == item.finding_id]
    latest = _latest_by_case(related)
    passed = [record for record in latest if record.get("status") == "passed"]
    failed = [record for record in latest if record.get("status") == "failed"]
    review = next((record for record in reversed(reviews) if record.get("finding_id") == item.finding_id), None)
    decision = (review or {}).get("decision", "")
    if item.resolution == FIX_AND_REANALYZE:
        state, reason = "unresolved", "Needs a fix and a new analysis."
    elif failed:
        state, reason = "unresolved", f"{len(failed)} validation(s) failed."
    elif decision == "not_an_issue":
        state, reason = "resolved", "A reviewer recorded that this is not an issue."
    elif decision == "confirmed_issue":
        state, reason = "unresolved", "A reviewer confirmed the issue."
    elif item.resolution == VALIDATION_AND_REVIEW:
        if passed and decision == "accepted":
            state, reason = "resolved", "Validation passed and a reviewer accepted it."
        elif passed:
            state, reason = "awaiting_review", "Validation passed. A reviewer decision is still needed."
        else:
            state, reason = "unresolved", "No passed validation is recorded."
    elif passed:
        state, reason = "resolved", "Validation passed."
    else:
        state, reason = "unresolved", "No passed validation is recorded."
    return {
        "state": state,
        "reason": reason,
        "passed": len(passed),
        "failed": len(failed),
        "recorded": len(latest),
        "decision": decision,
    }


def _latest_by_case(records: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for record in records:
        latest[record.get("id") or str(len(latest))] = record
    return list(latest.values())


def evaluate_readiness(
    report: Report,
    policy: PolicyEvaluation,
    validations: list[dict],
    reviews: list[dict],
    drift_objects: list[dict],
    superseded_by: str | None,
    stale_reason: str | None = None,
) -> dict:
    """Recommended, on hold, or indeterminate, with every reason listed."""
    blockers = [{"item": item, **item_status(item, validations, reviews)} for item in policy.blockers]
    checks = [{"item": item, **item_status(item, validations, reviews)} for item in policy.validations]
    drift = [
        {
            "key": entry["key"],
            "qualified": entry["qualified"],
            "object_type": entry["object_type"],
            "reasons": entry["reasons"],
            "accepted": entry.get("accepted", False),
        }
        for entry in drift_objects
    ]
    open_blockers = [item for item in blockers if item["state"] != "resolved"]
    open_checks = [item for item in checks if item["state"] != "resolved"]
    open_drift = [item for item in drift if not item["accepted"]]
    indeterminate: list[str] = []
    if report.status != "completed":
        indeterminate.append(f"The analysis did not complete (status {report.status}).")
    if report.coverage is None:
        indeterminate.append("This report version did not record analysis coverage.")
    else:
        coverage = report.coverage
        if coverage.inaccessible:
            indeterminate.append(f"{coverage.inaccessible} in-scope object(s) could not be read.")
        if coverage.failed:
            indeterminate.append(f"{coverage.failed} in-scope object(s) failed analysis.")
    if superseded_by:
        indeterminate.append("A newer analysis of the same packages exists. Use the latest run.")
    if stale_reason:
        indeterminate.append(stale_reason)
    if indeterminate:
        decision = INDETERMINATE
        summary = "The analyzer cannot make a recommendation from this run."
    elif open_blockers or open_checks or open_drift:
        decision = ON_HOLD
        parts = []
        if open_blockers:
            parts.append(f"{len(open_blockers)} blocking finding{'s' if len(open_blockers) != 1 else ''}")
        if open_checks:
            parts.append(f"{len(open_checks)} required validation{'s' if len(open_checks) != 1 else ''}")
        if open_drift:
            parts.append(f"{len(open_drift)} unexpected drift item{'s' if len(open_drift) != 1 else ''}")
        summary = "Unresolved: " + ", ".join(parts) + "."
    else:
        decision = RECOMMENDED
        summary = (
            "No unresolved blocker, required validation, or unexpected drift remains under policy "
            f"{policy.policy_version}. Human approval is still required."
        )
    return {
        "decision": decision,
        "label": READINESS_LABELS[decision],
        "tone": {RECOMMENDED: "success", ON_HOLD: "danger", INDETERMINATE: "neutral"}[decision],
        "summary": summary,
        "indeterminate_reasons": indeterminate,
        "blockers": blockers,
        "validations": checks,
        "drift": drift,
        "open_blockers": len(open_blockers),
        "open_validations": len(open_checks),
        "open_drift": len(open_drift),
        "policy_version": policy.policy_version,
    }
