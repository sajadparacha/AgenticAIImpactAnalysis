"""Planned change, unexpected drift, or unclassified, per in-scope object.

A difference is only *planned* when an approved change manifest entry pins the
exact Test checksum. Existing in Test is not enough. *Unexpected drift* is a
checksum that contradicts a recorded fact: Production no longer matches its
trusted baseline, or Test no longer matches the checksum that was approved.
Everything else that differs is *unclassified*.
"""

from datetime import datetime, timedelta, timezone

from oracle_impact.models.report import Report

PLANNED, DRIFT, UNCLASSIFIED, NO_CHANGE, UNKNOWN = "planned", "unexpected_drift", "unclassified", "no_change", "unknown"
LABELS = {
    PLANNED: "Planned change",
    DRIFT: "Unexpected drift",
    UNCLASSIFIED: "Unclassified",
    NO_CHANGE: "No change",
    UNKNOWN: "Not determined",
}
TONES = {PLANNED: "success", DRIFT: "danger", UNCLASSIFIED: "warning", NO_CHANGE: "neutral", UNKNOWN: "neutral"}
_CHANGED = {"modified", "created", "deleted", "formatting"}
_ENVIRONMENTS = ("test", "production")


def object_key(owner: str, name: str, object_type: str) -> str:
    return f"{owner}.{name}:{object_type}"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _matches(entry: dict, owner: str, name: str, object_type: str) -> bool:
    return entry.get("owner") == owner and entry.get("name") == name and entry.get("object_type") == object_type


def baseline_state(entry: dict | None, max_age_days: int, now: datetime) -> str:
    if entry is None:
        return "unavailable"
    set_at = _parse(entry.get("set_at"))
    if set_at is None or now - set_at > timedelta(days=max_age_days):
        return "outdated"
    return "available"


def classify_changes(
    report: Report,
    baselines: list[dict],
    manifest: list[dict],
    max_age_days: int,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    active_baselines = [item for item in baselines if item.get("status") == "active"]
    approved = [item for item in manifest if item.get("status") == "approved"]
    objects = []
    for state in report.object_states:
        objects.append(_classify(state, active_baselines, approved, max_age_days, now))
    counts = {key: sum(1 for item in objects if item["classification"] == key) for key in LABELS}
    states = [item["baseline"]["state"] for item in objects if "production" in item["hashes"]]
    if not objects or not states:
        overall = "not_recorded" if not report.object_states else "unavailable"
    elif all(state == "available" for state in states):
        overall = "available"
    elif all(state == "unavailable" for state in states):
        overall = "unavailable"
    elif any(state == "outdated" for state in states):
        overall = "outdated"
    else:
        overall = "partial"
    return {
        "objects": objects,
        "counts": counts,
        "production_only": sum(1 for item in objects if item["production_only"]),
        "baseline_state": overall,
        "baseline_note": _baseline_note(overall, max_age_days),
        "recorded": bool(report.object_states),
        "drift_objects": [
            {
                "key": item["key"],
                "qualified": item["qualified"],
                "object_type": item["object_type"],
                "reasons": item["reasons"],
                "accepted": False,
            }
            for item in objects
            if item["classification"] == DRIFT
        ],
    }


def _baseline_note(state: str, max_age_days: int) -> str:
    return {
        "available": "Every captured Production object has a trusted baseline.",
        "partial": "Some Production objects have no trusted baseline. Production-only changes to them cannot be detected.",
        "outdated": f"At least one trusted baseline is older than {max_age_days} days. Confirm it before relying on drift results.",
        "unavailable": "No trusted baseline is recorded. Production-only changes cannot be detected until an approver sets one.",
        "not_recorded": "This report version did not record per-environment checksums.",
    }[state]


def _classify(state, baselines: list[dict], manifest: list[dict], max_age_days: int, now: datetime) -> dict:
    owner, name, object_type = state.owner, state.name, state.object_type
    hashes = {
        item.environment: (item.sha256 if item.found else None)
        for item in state.environments
        if not item.inaccessible and item.environment in _ENVIRONMENTS
    }
    reasons: list[str] = []
    drift = False
    production_only = False
    production_baseline = next(
        (item for item in reversed(baselines) if item.get("environment") == "production" and _matches(item, owner, name, object_type)),
        None,
    )
    if production_baseline is not None and "production" in hashes and hashes["production"] != (production_baseline.get("sha256") or None):
        drift = True
        reasons.append(
            f"Production no longer matches the trusted baseline set on {production_baseline.get('set_at', 'an unknown date')[:10]}. "
            "It was changed outside the recorded release path."
        )
        production_only = hashes.get("production") != hashes.get("test")
    entries = [item for item in manifest if _matches(item, owner, name, object_type)]
    test_hash = hashes.get("test")
    pinned = next((item for item in reversed(entries) if (item.get("sha256") or None) == test_hash), None)
    if entries and pinned is None and state.change_type in _CHANGED:
        drift = True
        reasons.append("Test no longer matches the checksum recorded when the change was approved.")
    if state.change_type == "unknown":
        classification = UNKNOWN
        reasons.append("The object could not be read in every environment.")
    elif drift:
        classification = DRIFT
    elif state.change_type in _CHANGED and pinned is not None:
        classification = PLANNED
        reference = pinned.get("change_ref") or "an approved change"
        reasons.append(f"Test matches the checksum approved under {reference}.")
    elif state.change_type in _CHANGED:
        classification = UNCLASSIFIED
        reasons.append("No approved change record pins this Test version. Existing in Test does not make it planned.")
    else:
        classification = NO_CHANGE
    return {
        "key": object_key(owner, name, object_type),
        "qualified": f"{owner}.{name}",
        "owner": owner,
        "name": name,
        "object_type": object_type,
        "change_type": state.change_type,
        "classification": classification,
        "label": LABELS[classification],
        "tone": TONES[classification],
        "reasons": reasons,
        "production_only": production_only,
        "hashes": hashes,
        "manifest": pinned or (entries[-1] if entries else None),
        "baseline": {
            "state": baseline_state(production_baseline, max_age_days, now),
            "entry": production_baseline,
        },
        "environments": [item.model_dump() for item in state.environments if item.environment in _ENVIRONMENTS],
    }
