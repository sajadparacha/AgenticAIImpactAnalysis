"""Presentation of readiness, classification, coverage, and review records.

Everything here is derived from the saved report and recorded facts. Counts
are computed, never typed into templates, and missing data is labelled as
not recorded rather than filled in.
"""

from oracle_impact.analysis.compilation import missing_errors_text
from oracle_impact.models.report import Report
from oracle_impact.services.readiness import (
    FIX_AND_REANALYZE,
    INDETERMINATE,
    ON_HOLD,
    RECOMMENDED,
    RESOLUTIONS,
)

EVIDENCE_CLASSES = {
    "deterministic": "Deterministic, verified",
    "hypothesis": "Unverified hypothesis",
    "ai": "AI interpretation",
}
APPROVAL_LABELS = {"approved": "Approved", "rejected": "Rejected", "on_hold": "On hold"}
VERDICT_TITLES = {
    RECOMMENDED: "Recommended, pending approval",
    ON_HOLD: "On hold",
    INDETERMINATE: "Can't decide yet",
}
ITEM_STATES = {
    "resolved": ("Resolved", "success"),
    "awaiting_review": ("Awaiting reviewer decision", "warning"),
    "unresolved": ("Unresolved", "danger"),
}
CHANGE_TYPE_LABELS = {
    "modified": "Modified",
    "created": "Created in Test",
    "deleted": "Deleted in Test",
    "formatting": "Formatting only",
    "unchanged": "Unchanged",
    "absent": "Not present",
    "unknown": "Not readable",
}
ENVIRONMENT_LABELS = {"test": "Test", "production": "Production"}
COVERAGE_LABELS = {
    "analyzed": "Analyzed",
    "partial": "Partially analyzed",
    "skipped": "Skipped",
    "inaccessible": "Inaccessible",
    "failed": "Failed",
}


def apply_decision(view: dict, report: Report, ctx: dict) -> None:
    """Add decision-support sections to a report view in place."""
    readiness = ctx["readiness"]
    classification = ctx["classification"]
    review = ctx["review"]
    labels = _env_labels(report)
    items = {entry["item"].finding_id: {**entry, "kind": "blocker"} for entry in readiness["blockers"]}
    items.update({entry["item"].finding_id: {**entry, "kind": "validation"} for entry in readiness["validations"]})
    by_object = {item["key"]: item for item in classification["objects"]}
    for row in view["rows"]:
        _decorate_row(row, items, by_object, labels)
    view["filters"].update(_filters(view["rows"]))
    hold = _hold_rows(view, readiness, items)
    approval = ctx["approval"]
    latest = approval["latest"]
    view["human_decision"] = _human_decision(latest, approval)
    coverage = _coverage(report)
    view["verdict"] = _verdict(readiness, view["human_decision"])
    view["unresolved"] = _unresolved(view, coverage)
    view["decision"] = {
        "readiness": readiness,
        "hold_rows": hold,
        "next_actions": _next_actions(view, readiness, approval, coverage),
        "states": _states(report, readiness, coverage, view["human_decision"]),
        "policy_computed_now": ctx["policy_computed_now"],
        "policy_version": readiness["policy_version"],
        "resolutions": RESOLUTIONS,
    }
    view["classification"] = classification
    view["objects"] = _objects(report, view, classification, labels)
    view["change_counts"] = _change_counts(view["objects"])
    view["measures"] = _measures(view, readiness, coverage)
    view["coverage"] = coverage
    view["compilation"] = _compilation(report, labels)
    view["transactions"] = _transactions(report, view)
    view["performance"] = _performance(report)
    view["review"] = _review(view, review, readiness)
    view["approval"] = {**approval, "labels": APPROVAL_LABELS}
    view["history"] = _history(ctx["history"])
    view["edges"] = _edges(report, view, labels)
    view["dep_tree"] = _dep_tree(report, view["edges"])
    view["external_checks"] = [item.model_dump() for item in report.external_checks]
    view["environment_labels"] = labels
    view["audit"]["metadata"].append({"label": "Blocking policy", "value": readiness["policy_version"]})
    view["counts"]["objects"] = sum(1 for item in view["objects"] if item["change_type"] not in {"unchanged", "absent"})
    view["next_steps"] = _merged_next_steps(view)


def _merged_next_steps(view: dict) -> list[dict]:
    """One de-duplicated, ordered list for the Summary tab, merged from the policy
    next actions, the recorded required actions, and the suggested checks.

    Attribution is kept as a badge so the AI's unverified suggestions stay distinct
    from the deterministic ("Confirmed") guidance.
    """
    steps: list[dict] = []
    seen: set[str] = set()

    def add(step: dict) -> None:
        key = step["text"].strip().lower()
        if key in seen:
            return
        seen.add(key)
        steps.append(step)

    for action in view["decision"]["next_actions"]:
        add({**action, "badge": "Confirmed", "badge_key": "confirmed"})
    for action in view["required_actions"]:
        add({"text": action, "kind": "required", "tab": "details", "badge": "Confirmed", "badge_key": "confirmed"})
    for check in view["suggested_checks"]:
        add(
            {
                "text": check["text"],
                "kind": "suggested",
                "finding_id": check["finding_id"],
                "level": check["level"],
                "level_label": check["level_label"],
                "badge": "AI lead · not verified" if check["from_ai"] else "Confirmed",
                "badge_key": "ai" if check["from_ai"] else "confirmed",
            }
        )
    return steps


def _env_labels(report: Report) -> dict[str, str]:
    return {"test": report.test_environment.label, "production": report.production_environment.label}


def _decorate_row(row: dict, items: dict, by_object: dict, labels: dict) -> None:
    finding = row["finding"]
    if row["source"] == "ai":
        evidence_class = "ai"
    elif row["evidence_status"] == "confirmed":
        evidence_class = "deterministic"
    else:
        evidence_class = "hypothesis"
    policy = items.get(row["id"])
    key = f"{finding.object_owner}.{finding.object_name}:{finding.object_type}"
    obj = by_object.get(key) or by_object.get(f"{finding.object_owner}.{finding.object_name}:PACKAGE BODY") or by_object.get(
        f"{finding.object_owner}.{finding.object_name}:PACKAGE"
    )
    environment = finding.source_database if finding.source_database in ENVIRONMENT_LABELS else "test"
    row.update(
        {
            "evidence_class": evidence_class,
            "evidence_class_label": EVIDENCE_CLASSES[evidence_class],
            "policy": policy,
            "policy_state_label": ITEM_STATES[policy["state"]][0] if policy else "",
            "policy_state_tone": ITEM_STATES[policy["state"]][1] if policy else "",
            "blocking": policy["kind"] if policy else "",
            "blocking_label": {"blocker": "Blocks deployment", "validation": "Requires validation"}.get(policy["kind"], "") if policy else "Does not block",
            "environment": environment,
            "environment_label": labels.get(environment, environment.capitalize()),
            "change_type": obj["change_type"] if obj else "",
            "change_type_label": CHANGE_TYPE_LABELS.get(obj["change_type"], "") if obj else "Not an in-scope object",
            "rule_id": finding.rule_id,
            "fingerprint": finding.fingerprint,
            "severity_rationale": finding.severity_rationale,
            "attributes": finding.attributes or {},
        }
    )
    if row["source"] == "ai":
        badge, badge_key = "AI lead · not verified", "ai"
    elif row["evidence_status"] == "confirmed":
        badge, badge_key = "Confirmed", "confirmed"
    elif row["evidence_status"] == "potential":
        badge, badge_key = "Possible", "potential"
    else:
        badge, badge_key = "Not checkable", "unknown"
    row["evidence_badge"] = badge
    row["evidence_badge_key"] = badge_key
    row["search"] = f"{row['search']} {finding.rule_id.lower()} {finding.fingerprint.lower()}".strip()


def _filters(rows: list[dict]) -> dict:
    def present(key: str, labels: dict) -> list[dict]:
        return [{"value": value, "label": label} for value, label in labels.items() if any(row[key] == value for row in rows)]

    return {
        "evidence_class": present("evidence_class", EVIDENCE_CLASSES),
        "blocking": [
            {"value": value, "label": label}
            for value, label in (("blocker", "Blocks deployment"), ("validation", "Requires validation"), ("none", "Does not block"))
            if any((row["blocking"] or "none") == value for row in rows)
        ],
        "environment": present("environment", ENVIRONMENT_LABELS),
        "change_type": present("change_type", CHANGE_TYPE_LABELS),
    }


def _hold_rows(view: dict, readiness: dict, items: dict) -> list[dict]:
    rows_by_id = {row["id"]: row for row in view["rows"]}
    hold = []
    for entry in readiness["blockers"] + readiness["validations"]:
        if entry["state"] == "resolved":
            continue
        item = entry["item"]
        row = rows_by_id.get(item.finding_id)
        hold.append(
            {
                "kind": "blocker" if entry in readiness["blockers"] else "validation",
                "finding_id": item.finding_id,
                "rule_id": item.rule_id,
                "title": item.title,
                "headline": row["headline"] if row else item.explanation,
                "object": row["object"] if row else "",
                "object_type": row["object_type"] if row else "",
                "level": row["level"] if row else "",
                "level_label": row["level_label"] if row else "",
                "evidence_status": row["evidence_status"] if row else "",
                "evidence_label": row["evidence_label"] if row else "",
                "state": entry["state"],
                "state_label": ITEM_STATES[entry["state"]][0],
                "state_reason": entry["reason"],
                "action": item.required_action,
                "resolution": RESOLUTIONS.get(item.resolution, ""),
                "fix_required": item.resolution == FIX_AND_REANALYZE,
            }
        )
    return hold


def _human_decision(latest: dict | None, approval: dict) -> dict:
    if latest is None:
        detail = (
            "No one has recorded a deployment decision for this run in this application. The readiness result is a recommendation, not an approval."
            if approval["enabled"]
            else "Approval recording is off because no approver access token is configured. Record the decision in your change-management process."
        )
        return {
            "state": "not_recorded",
            "label": "Not recorded",
            "detail": detail,
            "record": None,
            "chip": "No decision recorded",
            "chip_tone": "neutral",
        }
    who = latest.get("actor") or {}
    decision = latest.get("decision", "")
    name = who.get("name", "").strip()
    chip = f"{APPROVAL_LABELS.get(decision, 'Recorded')} by {name}" if name else APPROVAL_LABELS.get(decision, "Recorded")
    chip_tone = {"approved": "success", "rejected": "danger", "on_hold": "warning"}.get(decision, "neutral")
    return {
        "state": decision,
        "label": APPROVAL_LABELS.get(decision, "Recorded"),
        "detail": (
            f"Recorded {latest.get('at', '')[:16].replace('T', ' ')} UTC by {who.get('name', 'Not given')} "
            f"(self-declared, {who.get('role', 'unknown')} sign-in). Readiness at the time: {latest.get('readiness', 'not recorded')}."
        ),
        "record": latest,
        "stale": approval["stale"],
        "chip": chip,
        "chip_tone": chip_tone,
    }


def _coverage(report: Report) -> dict:
    coverage = report.coverage
    if coverage is None:
        return {"recorded": False}
    complete = coverage.in_scope > 0 and coverage.analyzed == coverage.in_scope
    percent = round(coverage.analyzed * 100 / coverage.in_scope) if coverage.in_scope else None
    return {
        "recorded": True,
        "in_scope": coverage.in_scope,
        "analyzed": coverage.analyzed,
        "partial": coverage.partial,
        "skipped": coverage.skipped,
        "inaccessible": coverage.inaccessible,
        "failed": coverage.failed,
        "percent": percent,
        "complete": complete,
        "denominator": "the specification and body of each selected package",
        "items": [
            {**item.model_dump(), "label": COVERAGE_LABELS.get(item.state, item.state), "qualified": f"{item.owner}.{item.name}"}
            for item in coverage.items
        ],
        "dynamic_sql": [item.model_dump() for item in coverage.dynamic_sql],
        "dynamic_changed": sum(1 for item in coverage.dynamic_sql if item.changed),
        "dependency": coverage.dependency.model_dump(),
        "environments": coverage.environments,
        "completed_at": coverage.completed_at,
    }


def _verdict(readiness: dict, human: dict) -> dict:
    decision = readiness["decision"]
    return {
        "decision": decision,
        "title": VERDICT_TITLES.get(decision, readiness["label"]),
        "label": readiness["label"],
        "tone": readiness["tone"],
        "summary": readiness["summary"],
        "indeterminate_reasons": readiness["indeterminate_reasons"],
        "policy_version": readiness["policy_version"],
        "blocking": decision in {ON_HOLD, INDETERMINATE},
        "chip": human.get("chip", "No decision recorded"),
        "chip_tone": human.get("chip_tone", "neutral"),
    }


def _measures(view: dict, readiness: dict, coverage: dict) -> list[dict]:
    metrics = view["metrics"]
    changed = sum(1 for item in view["objects"] if item["change_type"] not in {"unchanged", "absent"})
    affected = sum(1 for dep in view["dependencies"] if dep["impact"] == "Affected")
    return [
        {
            "key": "findings",
            "label": "Findings",
            "value": metrics["findings"],
            "severity": metrics["severity"],
            "blocker": readiness["open_blockers"] > 0,
            "tab": "findings",
        },
        {
            "key": "objects",
            "label": "Changed objects",
            "value": changed,
            "lines_added": metrics["lines_added"],
            "lines_removed": metrics["lines_removed"],
            "tab": "objects",
        },
        {
            "key": "dependencies",
            "label": "Affected dependencies",
            "value": affected,
            "tab": "dependencies",
        },
        {
            "key": "coverage",
            "label": "Coverage",
            "value": f"{coverage['analyzed']} of {coverage['in_scope']} objects" if coverage["recorded"] else None,
            "note": "in-scope objects fully analyzed" if coverage["recorded"] else "Not recorded in this report version.",
            "tab": "details",
        },
    ]


def _unresolved(view: dict, coverage: dict) -> dict:
    leads = len(view["ai_leads"])
    dynamic = len(coverage.get("dynamic_sql", [])) if coverage["recorded"] else 0
    synonyms = coverage["dependency"]["unresolved"] if coverage["recorded"] else 0
    gaps = 0
    if coverage["recorded"]:
        gaps = coverage["partial"] + coverage["skipped"] + coverage["inaccessible"] + coverage["failed"]
    total = dynamic + synonyms + gaps + leads
    return {"total": total, "ai_leads": leads, "has_leads": leads > 0}


def _states(report: Report, readiness: dict, coverage: dict, human: dict) -> list[dict]:
    completed = report.status == "completed"
    if not coverage["recorded"]:
        coverage_state = ("Not recorded", "neutral", "This report version did not record coverage.")
    elif coverage["complete"]:
        coverage_state = ("Complete", "success", f"{coverage['analyzed']} of {coverage['in_scope']} in-scope objects analyzed.")
    else:
        coverage_state = ("Incomplete", "warning", f"{coverage['analyzed']} of {coverage['in_scope']} in-scope objects fully analyzed.")
    return [
        {
            "label": "Analysis completed",
            "value": "Yes" if completed else "No",
            "tone": "success" if completed else "danger",
            "note": "Every pipeline step finished." if completed else f"Run status: {report.status}.",
        },
        {"label": "Coverage complete", "value": coverage_state[0], "tone": coverage_state[1], "note": coverage_state[2]},
        {
            "label": "Deployment recommended",
            "value": {RECOMMENDED: "Yes", INDETERMINATE: "Cannot tell"}.get(readiness["decision"], "No"),
            "tone": readiness["tone"],
            "note": readiness["label"] + ". From policy " + readiness["policy_version"] + ".",
        },
        {
            "label": "Human approval",
            "value": "Yes" if human["state"] == "approved" else human["label"] if human["state"] != "not_recorded" else "Not recorded",
            "tone": "success" if human["state"] == "approved" else "danger" if human["state"] == "rejected" else "neutral",
            "note": "A person records this. The analyzer never approves.",
        },
    ]


def _next_actions(view: dict, readiness: dict, approval: dict, coverage: dict) -> list[dict]:
    actions = []
    for reason in readiness["indeterminate_reasons"]:
        actions.append({"text": reason, "tab": "overview", "kind": "indeterminate"})
    fix = [entry for entry in readiness["blockers"] if entry["state"] != "resolved" and entry["item"].resolution == FIX_AND_REANALYZE]
    if fix:
        actions.append(
            {"text": f"Fix {len(fix)} blocking finding{'s' if len(fix) != 1 else ''} in Test, then run a new analysis.", "tab": "details", "kind": "blocker"}
        )
    pending = [
        entry
        for entry in readiness["blockers"] + readiness["validations"]
        if entry["state"] == "unresolved" and entry["item"].resolution != FIX_AND_REANALYZE
    ]
    if pending:
        actions.append(
            {
                "text": f"Test {len(pending)} finding{'s' if len(pending) != 1 else ''} before deployment. Results are not recorded in this report.",
                "tab": "details",
                "kind": "validation",
            }
        )
    if readiness["decision"] == RECOMMENDED and approval["latest"] is None:
        actions.append({"text": "Record the human deployment decision.", "tab": "overview", "kind": "approval"})
    return actions


CHANGE_TYPE_TONES = {
    "modified": "warning",
    "created": "warning",
    "deleted": "danger",
    "formatting": "neutral",
    "unchanged": "neutral",
    "absent": "neutral",
    "unknown": "danger",
}


def _change_counts(objects: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for item in objects:
        counts[item["change_type"]] = counts.get(item["change_type"], 0) + 1
    return [
        {"key": key, "label": label, "count": counts[key], "tone": CHANGE_TYPE_TONES[key]}
        for key, label in CHANGE_TYPE_LABELS.items()
        if counts.get(key)
    ]


def _objects(report: Report, view: dict, classification: dict, labels: dict) -> list[dict]:
    diff_keys = {diff["key"]: diff for diff in view["diffs"]}
    rows = []
    for item in classification["objects"]:
        related = [row for row in view["rows"] if row["source"] != "ai" and f"{row['finding'].object_owner}.{row['finding'].object_name}" == item["qualified"]]
        levels = [row["level"] for row in related]
        order = ("critical", "high", "medium", "low", "informational")
        top = min((level for level in levels if level in order), key=order.index, default="")
        envs = []
        for env in ("test", "production"):
            captured = next((entry for entry in item["environments"] if entry["environment"] == env), None)
            if captured is None:
                continue
            envs.append(
                {
                    "environment": env,
                    "label": labels.get(env, env.upper()),
                    "found": captured["found"],
                    "inaccessible": captured["inaccessible"],
                    "sha256": captured["sha256"],
                    "short": (captured["sha256"] or "")[:12],
                    "compile_status": captured["compile_status"],
                    "captured_at": captured["captured_at"],
                    "drift": captured["capture_drift"],
                }
            )
        diff = diff_keys.get(item["key"])
        rows.append(
            {
                **item,
                "change_label": CHANGE_TYPE_LABELS.get(item["change_type"], item["change_type"]),
                "envs": envs,
                "findings": len(related),
                "level": top,
                "diff_key": item["key"] if diff else "",
                "added": diff["added"] if diff else None,
                "removed": diff["removed"] if diff else None,
            }
        )
    if not rows:
        for diff in view["diffs"]:
            rows.append(
                {
                    "key": diff["key"],
                    "qualified": diff["label"],
                    "object_type": diff["object_type"],
                    "change_type": "modified" if diff["state"] not in {"unchanged", "formatting"} else diff["state"],
                    "change_label": diff["state_label"],
                    "hashes": {},
                    "envs": [],
                    "findings": len(diff["findings"]),
                    "level": diff["level"],
                    "diff_key": diff["key"],
                    "added": diff["added"],
                    "removed": diff["removed"],
                }
            )
    return rows


def _compilation(report: Report, labels: dict) -> dict:
    summary = report.compilation
    if summary is None:
        return {"recorded": False}
    invalid = [item for item in summary.objects if item.status != "VALID"]
    return {
        "recorded": True,
        "evaluated": summary.evaluated,
        "reason": summary.reason,
        "environments": [labels.get(env, env) for env in summary.environments],
        "unavailable": summary.unavailable,
        "checked": len(summary.objects),
        "invalid": [
            {
                **item.model_dump(),
                "environment_label": labels.get(item.environment, item.environment),
                "errors_note": _sentence(missing_errors_text(summary, item.environment, item.owner, item.name)),
                "errors": [
                    error.model_dump()
                    for error in summary.errors
                    if error.environment == item.environment and error.owner == item.owner and error.name == item.name and error.object_type == item.object_type
                ],
            }
            for item in invalid
        ],
    }


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _transactions(report: Report, view: dict) -> list[dict]:
    rows_by_id = {row["id"]: row for row in view["rows"]}
    items = []
    for profile in report.transaction_profiles:
        test, prod = profile.test, profile.production
        items.append(
            {
                "qualified": f"{profile.owner}.{profile.name}",
                "routine": profile.routine,
                "change": profile.change,
                "differences": profile.differences,
                "test": test.model_dump() if test else None,
                "production": prod.model_dump() if prod else None,
                "findings": [rows_by_id[fid] for fid in profile.finding_ids if fid in rows_by_id],
                "testing": [step for fid in profile.finding_ids if fid in rows_by_id for step in rows_by_id[fid]["proposed_tests"]],
            }
        )
    return items


PROVENANCE_LABELS = {
    "dictionary": "Verified (ALL_DEPENDENCIES)",
    "source_scan": "Inferred from source",
    "unresolved": "Unresolved",
}


def _mentions(qualified: str, text: str) -> bool:
    import re

    return bool(re.search(rf"(?<![\w$#]){re.escape(qualified)}(?![\w$#])", text or ""))


def _edges(report: Report, view: dict, labels: dict) -> list[dict]:
    merged: dict[tuple, dict] = {}
    deterministic = [row for row in view["rows"] if row["source"] != "ai"]
    for edge in report.dependency_edges:
        key = (edge.direction, edge.owner, edge.name, edge.object_type, edge.referenced_owner, edge.referenced_name, edge.referenced_type, edge.provenance)
        entry = merged.get(key)
        if entry is None:
            caller = edge.direction == "referenced_by"
            other_owner, other_name, other_type = (
                (edge.owner, edge.name, edge.object_type) if caller else (edge.referenced_owner, edge.referenced_name, edge.referenced_type)
            )
            root = f"{edge.referenced_owner}.{edge.referenced_name}" if caller else f"{edge.owner}.{edge.name}"
            other = f"{other_owner}.{other_name}" if other_owner else other_name
            related = [
                row["id"]
                for row in deterministic
                if other_owner
                and (_mentions(other, row["affected_object"]) or row["object"] == other or any(_mentions(other, step) for step in row["finding"].dependency_path))
            ]
            entry = {
                "dom_id": f"edge-{len(merged)}",
                "direction": "caller" if caller else "uses",
                "direction_label": "Calls" if caller else "Uses",
                "from": f"{edge.owner}.{edge.name}",
                "from_type": edge.object_type,
                "to": f"{edge.referenced_owner}.{edge.referenced_name}" if edge.referenced_owner else edge.referenced_name,
                "to_type": edge.referenced_type,
                "other": other,
                "other_type": other_type,
                "root": root,
                "depth": edge.depth,
                "provenance": edge.provenance,
                "provenance_label": PROVENANCE_LABELS.get(edge.provenance, edge.provenance),
                "note": edge.note,
                "databases": [],
                "finding_ids": related,
                "search": f"{edge.owner}.{edge.name} {edge.object_type} {edge.referenced_owner}.{edge.referenced_name} {edge.referenced_type}".lower(),
            }
            merged[key] = entry
        database = labels.get(edge.database, edge.database or "Not recorded")
        if database not in entry["databases"]:
            entry["databases"].append(database)
    return sorted(merged.values(), key=lambda item: (item["root"], item["direction"], item["depth"], item["other"]))


def _dep_tree(report: Report, edges: list[dict]) -> list[dict]:
    def children(target: str, direction: str, seen: set[str], depth: int) -> list[dict]:
        if depth > 6:
            return []
        nodes = []
        for edge in edges:
            if edge["direction"] != direction:
                continue
            anchor = edge["to"] if direction == "caller" else edge["from"]
            if anchor != target or edge["other"] in seen:
                continue
            nodes.append(
                {
                    "label": edge["other"],
                    "type": edge["other_type"],
                    "provenance": edge["provenance"],
                    "provenance_label": edge["provenance_label"],
                    "dom_id": edge["dom_id"],
                    "databases": edge["databases"],
                    "finding_ids": edge["finding_ids"],
                    "children": children(edge["other"], direction, seen | {edge["other"]}, depth + 1),
                }
            )
        unique = {}
        for node in nodes:
            unique.setdefault(node["label"], node)
        return list(unique.values())

    return [
        {
            "package": package,
            "callers": children(package, "caller", {package}, 1),
            "uses": children(package, "uses", {package}, 1),
        }
        for package in report.packages
    ]


def _performance(report: Report) -> dict:
    perf = report.performance
    if perf is None:
        return {"recorded": False}
    return {"recorded": True, **perf.model_dump()}


def _review(view: dict, review: dict, readiness: dict) -> dict:
    rows_by_id = {row["id"]: row for row in view["rows"]}

    def described(entry: dict, kind: str) -> dict:
        item = entry["item"]
        row = rows_by_id.get(item.finding_id)
        return {
            "kind": kind,
            "item": item,
            "row": row,
            "state": entry["state"],
            "state_label": ITEM_STATES[entry["state"]][0],
            "tone": ITEM_STATES[entry["state"]][1],
            "reason": entry["reason"],
            "resolution": RESOLUTIONS.get(item.resolution, ""),
        }

    return {
        "items": [described(entry, "blocker") for entry in readiness["blockers"]]
        + [described(entry, "validation") for entry in readiness["validations"]],
        "events": list(reversed(review["events"])),
    }


def _history(history: dict) -> dict:
    comparison = history["comparison"]
    if comparison is not None:
        comparison = {
            **comparison,
            "new": [_brief(item) for item in comparison["new"]],
            "resolved": [_brief(item) for item in comparison["resolved"]],
            "unchanged": [_brief(item) for item in comparison["unchanged"]],
            "severity_changed": [
                {**_brief(item["finding"]), "before": item["before"], "after": item["after"]} for item in comparison["severity_changed"]
            ],
        }
    return {**history, "comparison": comparison}


def _brief(finding) -> dict:
    return {
        "id": finding.id,
        "fingerprint": finding.fingerprint,
        "object": finding.changed_object,
        "level": finding.severity.value,
        "rule_id": finding.rule_id,
        "text": " ".join(finding.evidence.split())[:180],
    }
