"""Readiness, classification, history, and review actions for one report.

Controllers call this service; the rules for what may be recorded live here.
Every write is attributed to the signed-in role and a self-declared name,
because development sign-in does not identify a person.
"""

import logging
from datetime import datetime, timezone

from oracle_impact.models.report import Report
from oracle_impact.services.change_classification import classify_changes
from oracle_impact.services.history import (
    age_text,
    compare_runs,
    freshness,
    previous_run,
    related_runs,
    stale_reason,
    superseded_by,
)
from oracle_impact.services.readiness import RECOMMENDED, evaluate_readiness, policy_for
from oracle_impact.infrastructure.storage.review_store import APPROVAL_DECISIONS

logger = logging.getLogger(__name__)
MAX_TEXT = 2000


class ActionError(ValueError):
    """A rejected review action. `code` is a fixed message key, never user input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def actor(role: str, name: str) -> dict:
    cleaned = " ".join(str(name or "").split())[:80]
    return {"role": role, "name": cleaned or "Not given", "declared": True}


def _text(value: str, limit: int = MAX_TEXT) -> str:
    value = str(value or "").strip()
    if len(value) > limit:
        raise ActionError("too_long")
    return value


class DecisionService:
    def __init__(self, settings, store, reviews) -> None:
        self.settings = settings
        self.store = store
        self.reviews = reviews

    def load(self, run_id: str) -> Report | None:
        try:
            payload = self.store.read_json(run_id, "report.json")
        except ValueError:
            return None
        return None if payload is None else Report.model_validate(payload)

    def context(self, report: Report, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        review = self.reviews.read(report.run_id)
        classification = classify_changes(report)
        runs = self.store.list_runs()
        newer = superseded_by(runs, report)
        earlier = previous_run(runs, report)
        comparison = None
        if earlier:
            previous = self.load(earlier)
            if previous is not None:
                comparison = compare_runs(report, previous)
        stale = stale_reason(report, self.settings.snapshot_stale_hours, now)
        policy, computed = policy_for(report)
        readiness = evaluate_readiness(report, policy, [], [], newer, stale)
        approvals = list(review["approvals"])
        latest = approvals[-1] if approvals else None
        return {
            "review": review,
            "classification": classification,
            "readiness": readiness,
            "policy": policy,
            "policy_computed_now": computed,
            "approval": {
                "enabled": self.settings.approvals_enabled,
                "latest": latest,
                "history": approvals,
                "stale": bool(latest and latest.get("readiness") != readiness["decision"]),
            },
            "history": {
                "runs": [
                    {**run, "freshness": freshness(run.get("created_at"), self.settings.snapshot_stale_hours, now)}
                    for run in related_runs(runs, report.packages)
                ],
                "superseded_by": newer,
                "previous": earlier,
                "comparison": comparison,
                "stale_reason": stale,
                "age": age_text(report, now),
            },
        }

    def record_approval(self, report: Report, form: dict, who: dict) -> dict:
        if not self.settings.approvals_enabled:
            raise ActionError("approvals_disabled")
        if who.get("role") != "approver":
            raise ActionError("approver_required")
        decision = str(form.get("decision") or "")
        if decision not in APPROVAL_DECISIONS:
            raise ActionError("invalid_decision")
        notes = _text(form.get("notes"))
        if not notes:
            raise ActionError("reason_required")
        context = self.context(report)
        readiness = context["readiness"]
        if decision == "approved" and readiness["decision"] != RECOMMENDED:
            raise ActionError("not_recommended")
        record = {
            "decision": decision,
            "notes": notes,
            "readiness": readiness["decision"],
            "policy_version": readiness["policy_version"],
            "open_blockers": readiness["open_blockers"],
            "open_validations": readiness["open_validations"],
        }
        return self.reviews.save_approval(report.run_id, record, who)


def offline_context(report: Report) -> dict:
    """The same context without stores: no records and no history. Used for standalone rendering."""
    from oracle_impact.infrastructure.storage.review_store import empty_review

    classification = classify_changes(report)
    policy, computed = policy_for(report)
    readiness = evaluate_readiness(report, policy, [], [], None, None)
    return {
        "review": empty_review(),
        "classification": classification,
        "readiness": readiness,
        "policy": policy,
        "policy_computed_now": computed,
        "approval": {"enabled": False, "latest": None, "history": [], "stale": False},
        "history": {
            "runs": [],
            "superseded_by": None,
            "previous": None,
            "comparison": None,
            "stale_reason": None,
            "age": age_text(report),
        },
    }


ACTION_MESSAGES = {
    "approval_saved": "Deployment decision recorded.",
    "form_expired": "The form expired. Submit it again.",
    "invalid_decision": "Choose a decision.",
    "reason_required": "Enter a reason.",
    "too_long": "One of the fields is too long.",
    "approvals_disabled": "Approval recording is off because no approver access token is configured.",
    "approver_required": "Sign in with the approver access token to do this.",
    "not_recommended": "Deployment can only be approved when readiness is Deployment recommended. Record On hold or Rejected instead.",
}
ERROR_CODES = {code for code in ACTION_MESSAGES if not code.endswith("_saved")}
