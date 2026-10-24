"""Run history for the same package set, run-to-run comparison, and freshness."""

from datetime import datetime, timedelta, timezone

from oracle_impact.analysis.annotate import fingerprint
from oracle_impact.models.report import Report
from oracle_impact.services.readiness import policy_for


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def related_runs(runs: list[dict], packages: list[str]) -> list[dict]:
    """Completed runs that analyzed exactly the same packages, newest first."""
    wanted = sorted(packages)
    return [
        run
        for run in runs
        if run.get("status") == "completed" and sorted(run.get("packages") or []) == wanted and run.get("created_at")
    ]


def _order(runs: list[dict], report: Report) -> tuple[tuple | None, list[tuple[tuple, dict]]]:
    """Each related run keyed by (snapshot time, request time). Snapshot times have one-second resolution."""
    created = _parse(report.created_at)
    if created is None:
        return None, []
    own = next((run.get("requested_ns", 0) for run in runs if run.get("run_id") == report.run_id), 0)
    others = [
        ((_parse(run["created_at"]) or created, run.get("requested_ns", 0)), run)
        for run in related_runs(runs, report.packages)
        if run["run_id"] != report.run_id
    ]
    return (created, own), others


def superseded_by(runs: list[dict], report: Report) -> str | None:
    key, others = _order(runs, report)
    newer = [run for other, run in others if key is not None and other > key]
    return newer[0]["run_id"] if newer else None


def previous_run(runs: list[dict], report: Report) -> str | None:
    key, others = _order(runs, report)
    older = [run for other, run in others if key is not None and other < key]
    return older[0]["run_id"] if older else None


def stale_reason(report: Report, hours: int, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    created = _parse(report.created_at)
    if created is None or hours <= 0:
        return None
    age = now - created
    if age <= timedelta(hours=hours):
        return None
    return f"The snapshot is {_age(age)} old, beyond the {hours} hour freshness limit. Run a new analysis."


def age_text(report: Report, now: datetime | None = None) -> str:
    created = _parse(report.created_at)
    if created is None:
        return "unknown"
    return _age((now or datetime.now(timezone.utc)) - created)


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


def _fingerprints(report: Report) -> dict[str, object]:
    result = {}
    for finding in report.findings:
        result[finding.fingerprint or fingerprint(finding)] = finding
    return result


def compare_runs(current: Report, previous: Report) -> dict:
    """Findings matched by fingerprint, blockers by the fingerprint of their finding, objects by checksum."""
    now_findings = _fingerprints(current)
    before_findings = _fingerprints(previous)
    new = [now_findings[key] for key in now_findings if key not in before_findings]
    resolved = [before_findings[key] for key in before_findings if key not in now_findings]
    unchanged = [now_findings[key] for key in now_findings if key in before_findings]
    severity_changed = [
        {"finding": now_findings[key], "before": before_findings[key].severity.value, "after": now_findings[key].severity.value}
        for key in now_findings
        if key in before_findings and before_findings[key].severity != now_findings[key].severity
    ]
    blockers_now = _blocker_prints(current)
    blockers_before = _blocker_prints(previous)
    hashes_now = _hashes(current)
    hashes_before = _hashes(previous)
    hash_changes = [
        {"key": key, "environment": key[0], "object": f"{key[1]}.{key[2]} {key[3]}", "before": hashes_before.get(key), "after": hashes_now.get(key)}
        for key in sorted(set(hashes_now) | set(hashes_before))
        if hashes_now.get(key) != hashes_before.get(key)
    ]
    return {
        "previous_run_id": previous.run_id,
        "previous_created_at": previous.created_at,
        "new": new,
        "resolved": resolved,
        "unchanged": unchanged,
        "severity_changed": severity_changed,
        "blockers_new": sorted(blockers_now[key] for key in set(blockers_now) - set(blockers_before)),
        "blockers_resolved": sorted(blockers_before[key] for key in set(blockers_before) - set(blockers_now)),
        "hash_changes": hash_changes,
        "recommendation_before": previous.recommendation_label,
        "recommendation_after": current.recommendation_label,
    }


def _blocker_prints(report: Report) -> dict[str, str]:
    policy, _ = policy_for(report)
    by_id = {finding.id: finding for finding in report.findings}
    result = {}
    for item in policy.blockers:
        finding = by_id.get(item.finding_id)
        key = (finding.fingerprint or fingerprint(finding)) if finding else item.finding_id
        result[key] = f"{item.rule_id}: {finding.changed_object if finding else item.finding_id}"
    return result


def _hashes(report: Report) -> dict[tuple, str | None]:
    result = {}
    for state in report.object_states:
        for item in state.environments:
            result[(item.environment, state.owner, state.name, state.object_type)] = item.sha256
    if not result:
        for diff in report.diffs:
            result[("test", diff.owner, diff.name, diff.object_type)] = diff.test_hash
            result[("production", diff.owner, diff.name, diff.object_type)] = diff.prod_hash
    return result
