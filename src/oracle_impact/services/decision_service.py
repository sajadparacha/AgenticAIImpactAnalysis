"""Readiness, classification, history, and review actions for one report.

Controllers call this service; the rules for what may be recorded live here.
Every write is attributed to the signed-in role and a self-declared name,
because development sign-in does not identify a person.
"""

import logging
from datetime import datetime, timezone

from oracle_impact.models.report import Report
from oracle_impact.services import business_impact as impact
from oracle_impact.services.change_classification import classify_changes, object_key
from oracle_impact.services.history import (
    age_text,
    compare_runs,
    previous_run,
    related_runs,
    stale_reason,
    superseded_by,
)
from oracle_impact.services.readiness import RECOMMENDED, evaluate_readiness, policy_for
from oracle_impact.infrastructure.storage.review_store import APPROVAL_DECISIONS

logger = logging.getLogger(__name__)
MAX_TEXT = 2000
MAX_SHORT = 200
BASELINE_ENVIRONMENTS = ("production",)


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
    def __init__(self, settings, store, registry, reviews) -> None:
        self.settings = settings
        self.store = store
        self.registry = registry
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
        classification = classify_changes(
            report, self.registry.baselines(), self.registry.manifest(), self.settings.baseline_max_age_days, now
        )
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
        readiness = evaluate_readiness(
            report,
            policy,
            [],
            [],
            classification["drift_objects"],
            newer,
            stale,
        )
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
                "runs": related_runs(runs, report.packages),
                "superseded_by": newer,
                "previous": earlier,
                "comparison": comparison,
                "stale_reason": stale,
                "age": age_text(report, now),
            },
            "business": impact.business_impact(report, self.registry.mappings()),
            "baselines": [item for item in self.registry.baselines() if _in_report(item, report)],
            "manifest": [item for item in self.registry.manifest() if _in_report(item, report)],
            "registry_events": [
                event for event in self.registry.events() if event.get("detail", {}).get("source_run") == report.run_id
            ],
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
            "open_drift": readiness["open_drift"],
        }
        return self.reviews.save_approval(report.run_id, record, who)

    def _require_approver(self, who: dict) -> None:
        if not self.settings.approvals_enabled:
            raise ActionError("approvals_disabled")
        if who.get("role") != "approver":
            raise ActionError("approver_required")

    def set_baseline(self, report: Report, form: dict, who: dict) -> list[dict]:
        self._require_approver(who)
        environment = str(form.get("environment") or "")
        if environment not in BASELINE_ENVIRONMENTS:
            raise ActionError("invalid_environment")
        wanted = str(form.get("object_key") or "")
        note = _text(form.get("note"))
        if not note:
            raise ActionError("reason_required")
        recorded = []
        for state in report.object_states:
            key = object_key(state.owner, state.name, state.object_type)
            if wanted not in {"all", key}:
                continue
            captured = state.in_environment(environment)
            if captured is None or captured.inaccessible or captured.capture_drift:
                continue
            recorded.append(
                self.registry.set_baseline(
                    {
                        "environment": environment,
                        "owner": state.owner,
                        "name": state.name,
                        "object_type": state.object_type,
                        "sha256": captured.sha256 if captured.found else None,
                        "captured_at": captured.captured_at,
                        "source_run": report.run_id,
                        "note": note,
                    },
                    who,
                )
            )
        if not recorded:
            raise ActionError("unknown_object")
        return recorded

    def approve_manifest(self, report: Report, form: dict, who: dict) -> dict:
        self._require_approver(who)
        wanted = str(form.get("object_key") or "")
        change_ref = _text(form.get("change_ref"), MAX_SHORT)
        if not change_ref:
            raise ActionError("change_ref_required")
        description = _text(form.get("description"))
        for state in report.object_states:
            if object_key(state.owner, state.name, state.object_type) != wanted:
                continue
            captured = state.in_environment("test")
            if captured is None or captured.inaccessible or captured.capture_drift:
                raise ActionError("unknown_object")
            return self.registry.add_manifest(
                {
                    "owner": state.owner,
                    "name": state.name,
                    "object_type": state.object_type,
                    "sha256": captured.sha256 if captured.found else None,
                    "change_kind": "delete" if not captured.found else "create_or_modify",
                    "change_ref": change_ref,
                    "description": description,
                    "source_run": report.run_id,
                },
                who,
            )
        raise ActionError("unknown_object")

    def withdraw_manifest(self, entry_id: str, form: dict, who: dict) -> None:
        self._require_approver(who)
        reason = _text(form.get("reason"))
        if not reason:
            raise ActionError("reason_required")
        if not self.registry.withdraw_manifest(entry_id, who, reason):
            raise ActionError("unknown_record")

    def clear_baseline(self, entry_id: str, form: dict, who: dict) -> None:
        self._require_approver(who)
        reason = _text(form.get("reason"))
        if not reason:
            raise ActionError("reason_required")
        if not self.registry.clear_baseline(entry_id, who, reason):
            raise ActionError("unknown_record")

    def add_mapping(self, form: dict, who: dict) -> dict:
        self._require_approver(who)
        from oracle_impact.analysis.identifiers import IdentifierError, parse_package_list

        try:
            parsed = parse_package_list(str(form.get("object") or ""))
        except IdentifierError:
            raise ActionError("invalid_object") from None
        if len(parsed) != 1:
            raise ActionError("invalid_object")
        criticality = str(form.get("criticality") or "")
        if criticality not in impact.CRITICALITIES:
            raise ActionError("invalid_criticality")
        application = _text(form.get("application"), MAX_SHORT)
        process = _text(form.get("business_process"), MAX_SHORT)
        if not application or not process:
            raise ActionError("mapping_fields_required")
        return self.registry.add_mapping(
            {
                "owner": parsed[0].owner,
                "name": parsed[0].name,
                "application": application,
                "business_process": process,
                "business_owner": _text(form.get("business_owner"), MAX_SHORT),
                "support_team": _text(form.get("support_team"), MAX_SHORT),
                "criticality": criticality,
                "notes": _text(form.get("notes")),
            },
            who,
        )

    def retire_mapping(self, mapping_id: str, who: dict) -> None:
        self._require_approver(who)
        if not self.registry.retire_mapping(mapping_id, who):
            raise ActionError("unknown_record")


def _in_report(entry: dict, report: Report) -> bool:
    return f"{entry.get('owner')}.{entry.get('name')}" in report.packages


def offline_context(report: Report, max_age_days: int = 30) -> dict:
    """The same context without stores: no records, no registry, no history. Used for standalone rendering."""
    from oracle_impact.infrastructure.storage.review_store import empty_review

    classification = classify_changes(report, [], [], max_age_days)
    policy, computed = policy_for(report)
    readiness = evaluate_readiness(report, policy, [], [], classification["drift_objects"], None, None)
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
        "business": impact.business_impact(report, []),
        "baselines": [],
        "manifest": [],
        "registry_events": [],
    }


ACTION_MESSAGES = {
    "approval_saved": "Deployment decision recorded.",
    "baseline_saved": "Trusted baseline recorded.",
    "baseline_cleared": "Trusted baseline cleared.",
    "manifest_saved": "Approved change recorded in the manifest.",
    "manifest_withdrawn": "Manifest entry withdrawn.",
    "mapping_saved": "Business mapping saved.",
    "mapping_retired": "Business mapping retired.",
    "form_expired": "The form expired. Submit it again.",
    "unknown_record": "That record was not found.",
    "unknown_object": "That object is not in this report, or its checksum could not be used.",
    "invalid_environment": "Choose an environment.",
    "invalid_decision": "Choose a decision.",
    "invalid_object": "Enter one object as OWNER.NAME.",
    "invalid_criticality": "Choose a criticality.",
    "reason_required": "Enter a reason.",
    "change_ref_required": "Enter the change reference, for example the ticket number.",
    "mapping_fields_required": "Enter the application and the business process.",
    "too_long": "One of the fields is too long.",
    "approvals_disabled": "Approval recording is off because no approver access token is configured.",
    "approver_required": "Sign in with the approver access token to do this.",
    "not_recommended": "Deployment can only be approved when readiness is Deployment recommended. Record On hold or Rejected instead.",
}
ERROR_CODES = {code for code in ACTION_MESSAGES if not code.endswith(("_saved", "_cleared", "_withdrawn", "_retired"))}
