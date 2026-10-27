"""Read-only presentation model for the report page.

Built from a saved Report and the run status. It groups, counts, and labels
what the report already contains. It never changes findings, severities, or
the recommendation, and it does not fill gaps: data the run did not record is
returned as an explicit "not recorded" state.
"""

import re
from collections import Counter
from datetime import datetime

from oracle_impact.infrastructure.llm.plain_text import ai_sources, blind_spot_leads, issue_list, review_lines
from oracle_impact.models.enums import Recommendation
from oracle_impact.models.report import ObjectDiff, Report

SEVERITY_ORDER = ("critical", "high", "medium", "low", "informational", "unrated")
SEVERITY_LABELS = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "informational": "Informational",
    "unrated": "Not rated",
}
EVIDENCE_LABELS = {
    "confirmed": "Confirmed by source comparison",
    "potential": "Potential, not confirmed",
    "unknown": "Could not be checked",
}
SOURCE_LABELS = {
    "deterministic": "Deterministic",
    "combined": "Deterministic + AI note",
    "ai": "AI assessment only",
}
CATEGORY_LABELS = {
    "compilation": "Compilation",
    "interface": "Interface",
    "business": "Business rules",
    "data": "Data",
    "dependency": "Dependencies",
    "transaction": "Transactions",
    "security": "Security",
    "coverage": "Coverage gap",
    "other": "Other",
}
TABS = ("overview", "findings", "objects", "source", "dependencies", "validation", "audit")
STAGES = (
    ("queued", "Queued"),
    ("snapshotting", "Source snapshot"),
    ("comparing", "Code comparison"),
    ("dependencies", "Dependencies"),
    ("investigating", "AI investigation"),
    ("validating", "Recommendation"),
    ("completed", "Complete"),
)
AGENTIC_STAGES = (
    ("queued", "Queued"),
    ("investigating", "Agentic review"),
    ("snapshotting", "Source snapshot"),
    ("comparing", "Code comparison"),
    ("dependencies", "Dependencies"),
    ("validating", "Recommendation"),
    ("completed", "Complete"),
)
_TOOL_ENVIRONMENTS = {"test": "Test", "production": "Production", "all": "All connected"}
CONTEXT_LINES = 3
TOP_FINDINGS = 5

_RECOMMENDATION = {
    Recommendation.NOT_RECOMMENDED: {
        "tone": "danger",
        "summary": "The analyzer found blocking issues. Resolve them before a promotion review.",
        "driver": "Blocking",
    },
    Recommendation.REQUIRES_INVESTIGATION: {
        "tone": "warning",
        "summary": "The analyzer found issues that need investigation before a promotion review.",
        "driver": "Needs investigation",
    },
    Recommendation.ELIGIBLE_FOR_REVIEW: {
        "tone": "success",
        "summary": "The analyzer found no blocker. The change can go to a promotion review after testing.",
        "driver": "Cited",
    },
}
_IMPACT_TONES = {
    "Affected": "danger",
    "Potentially affected": "warning",
    "Unknown": "neutral",
}
_TOOL_LABELS = {
    "get_package_source": "Package source",
    "get_object_source": "Source code",
    "get_dependencies": "Dependencies",
    "get_table_metadata": "Table definition",
    "get_procedure_arguments": "Procedure arguments",
    "get_arguments": "Procedure arguments",
    "get_object_status": "Object status",
    "get_grants": "Grants",
    "get_compile_errors": "Compile errors",
    "read_source_lines": "Source lines",
    "read_synonym": "Synonym target",
    "check_object": "Object exists",
}


def build_report_view(report: Report, status: dict | None = None, decision: dict | None = None, session: dict | None = None) -> dict:
    """Everything the report templates display, derived from saved run data and recorded review facts only."""
    from oracle_impact.services.decision_service import offline_context
    from oracle_impact.services.decision_view import apply_decision

    view = _base_view(report, status)
    apply_decision(view, report, decision or offline_context(report))
    view["session"] = session or {"role": None, "csrf": "", "approvals_enabled": False, "interactive": False}
    return view


def _base_view(report: Report, status: dict | None) -> dict:
    issues = issue_list(report)
    diffs = _diffs(report)
    diff_lookup = {item["key"]: item for item in diffs}
    driver_ids = _driver_ids(report)
    rec = _RECOMMENDATION[report.recommendation]
    rows = _finding_rows(report, issues, diff_lookup, driver_ids, rec["driver"])
    deterministic = [row for row in rows if row["source"] != "ai"]
    dependencies = _dependencies(report, rows)
    for row in rows:
        row["dependencies"] = [
            {"dom_id": dep["dom_id"], "label": dep["qualified"], "direction": dep["direction_label"]}
            for dep in dependencies
            if row["id"] in dep["finding_ids"]
        ]
    for diff in diffs:
        related = [row for row in rows if row["diff_link"] and row["diff_link"]["key"] == diff["key"]]
        diff["findings"] = related
        diff["level"] = _top_level(related)
    severity = Counter(row["level"] for row in deterministic)
    evidence = Counter(row["evidence_status"] for row in deterministic)
    drivers = [row for row in rows if row["driver"]]
    view = {
        "run_id": report.run_id,
        "base_url": f"/analyses/{report.run_id}/report",
        "header": _header(report),
        "recommendation": {
            "value": report.recommendation.value,
            "label": report.recommendation_label,
            "tone": rec["tone"],
            "summary": rec["summary"],
            "driver_label": rec["driver"],
            "reasons": _reasons(report, rows),
        },
        "human_decision": {
            "state": "not_recorded",
            "label": "Not recorded",
            "detail": (
                "This application does not store deployment approvals. The analyzer recommendation "
                "is not an approval or a rejection. Record the decision in your change-management process."
            ),
        },
        "metrics": {
            "packages": len(report.packages),
            "objects_compared": len(diffs),
            "objects_changed": sum(1 for diff in diffs if diff["state"] not in {"unchanged", "formatting"}),
            "objects_formatting": sum(1 for diff in diffs if diff["state"] == "formatting"),
            "objects_by_type": _ordered_counts(
                Counter(diff["object_type"] for diff in diffs if diff["state"] not in {"unchanged", "formatting"})
            ),
            "lines_added": report.statistics.lines_added,
            "lines_removed": report.statistics.lines_removed,
            "dependencies": len(dependencies),
            "dependencies_by_impact": _ordered_counts(Counter(dep["impact"] for dep in dependencies)),
            "callers": sum(1 for dep in dependencies if dep["direction"] == "caller"),
            "referenced": sum(1 for dep in dependencies if dep["direction"] == "uses"),
            "findings": len(deterministic),
            "severity": [
                {"level": level, "label": SEVERITY_LABELS[level], "count": severity.get(level, 0)}
                for level in SEVERITY_ORDER[:-1]
            ],
            "evidence": [
                {"status": status_key, "label": label, "count": evidence.get(status_key, 0)}
                for status_key, label in EVIDENCE_LABELS.items()
            ],
            "ai_notes_attached": sum(1 for row in deterministic if row["note_id"]),
            "ai_only": len(rows) - len(deterministic),
            "drivers": len(drivers),
            "verified": None,
            "saved_total": sum(report.statistics.finding_counts.values()),
            "reconciled": sum(report.statistics.finding_counts.values()) == len(deterministic),
        },
        "top_findings": sorted(drivers, key=_row_rank)[:TOP_FINDINGS],
        "required_actions": list(report.required_actions),
        "suggested_checks": _suggested_checks(drivers),
        "packages": _packages(report, deterministic, diffs),
        "rows": rows,
        "filters": _filters(rows),
        "diffs": diffs,
        "dependencies": dependencies,
        "ai": _ai(report, issues),
        "ai_leads": blind_spot_leads(report),
        "unverified": _unverified(report, deterministic, issues),
        "tests": _tests(rows),
        "audit": _audit(report, status, diffs),
        "limitations": list(report.limitations)
        + [f"{row['id']}: {row['finding'].explanation}" for row in deterministic if row["category"] == "coverage"],
        "fixture": report.test_environment.fixture or report.production_environment.fixture,
        "complete": report.status == "completed",
        "counts": {
            "findings": len(rows),
            "changed_objects": sum(1 for diff in diffs if diff["state"] != "unchanged"),
            "dependencies": len(dependencies),
        },
    }
    return view


def initial_state(view: dict, params) -> dict:
    """Validate deep-link query parameters against the report. Unknown values fall back to defaults."""
    tab = str(params.get("tab") or "")
    finding = str(params.get("finding") or "")
    obj = str(params.get("object") or "")
    if not any(row["id"] == finding for row in view["rows"]):
        finding = ""
    keys = [diff["key"] for diff in view["diffs"]]
    if obj not in keys:
        obj = ""
    if tab not in TABS:
        tab = "findings" if finding else "source" if obj else "overview"
    if not obj:
        changed = [diff["key"] for diff in view["diffs"] if diff["state"] != "unchanged"]
        obj = (changed or keys or [""])[0]
    line = str(params.get("line") or "")
    side = str(params.get("side") or "t")
    return {
        "tab": tab,
        "finding": finding,
        "object": obj,
        "line": int(line) if line.isdigit() else None,
        "side": side if side in {"t", "p"} else "t",
    }


def format_timestamp(value: str | None) -> str:
    if not value:
        return "Not recorded"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    offset = parsed.utcoffset()
    zone = "" if offset is None else "UTC" if not offset.total_seconds() else parsed.strftime("%z")
    return f"{parsed.strftime('%Y-%m-%d %H:%M:%S')} {zone}".strip()


def _header(report: Report) -> dict:
    test = report.test_environment
    prod = report.production_environment
    return {
        "run_id": report.run_id,
        "created_at": format_timestamp(report.created_at),
        "created_raw": report.created_at,
        "packages": list(report.packages),
        "proposed": {"label": test.label, "database_id": test.database_id, "role": "Proposed version"},
        "baseline": {"label": prod.label, "database_id": prod.database_id, "role": "Production baseline"},
        "analyzer": report.analyzer,
        "report_version": report.report_version,
        "ai_used": report.ai_used,
        "model_name": report.model_name,
        "status": report.status,
        "run_mode": report.run_mode,
        "run_mode_label": "AI investigated" if report.run_mode == "agentic" else "Checks only",
    }


def _driver_ids(report: Report) -> list[str]:
    ids = {finding.id for finding in report.findings}
    found = []
    for reason in report.recommendation_reasons:
        finding_id, separator, _ = str(reason).partition(": ")
        if separator and finding_id in ids and finding_id not in found:
            found.append(finding_id)
    return found


def _reasons(report: Report, rows: list[dict]) -> list[dict]:
    by_id = {row["id"]: row for row in rows}
    items = []
    for reason in report.recommendation_reasons:
        finding_id, separator, rest = str(reason).partition(": ")
        row = by_id.get(finding_id) if separator else None
        if row is None:
            items.append({"id": "", "text": str(reason), "level": "", "level_label": ""})
        else:
            items.append(
                {
                    "id": row["id"],
                    "text": row["headline"] or rest,
                    "level": row["level"],
                    "level_label": row["level_label"],
                }
            )
    return items


def _finding_rows(report: Report, issues: dict, diff_lookup: dict, driver_ids: list[str], driver_label: str) -> list[dict]:
    rows = []
    for item in issues["rows"]:
        finding = item["finding"]
        note = item["note"]
        ai = review_lines(note, report) if note is not None else None
        rows.append(
            _row(
                finding,
                level=item["level"],
                category=item["group"],
                headline=item["headline"],
                where=item["where"],
                source="combined" if note is not None else "deterministic",
                note=note if item["explanation"] else None,
                ai_reviewed=note is not None,
                ai_explanation=item["explanation"],
                ai_todo=ai["todo"] if ai else "",
                ai_unchecked=ai["unchecked"] if ai else "",
                todo=item["todo"],
                todo_from_ai=item["todo_from_ai"],
                diff_lookup=diff_lookup,
                driver=finding.id in driver_ids,
                driver_label=driver_label,
            )
        )
    for finding in report.findings:
        if finding.origin == "investigator" or finding.change_category != "coverage":
            continue
        own = review_lines(finding)
        rows.append(
            _row(
                finding,
                level=finding.severity.value,
                category="coverage",
                headline=own["change"],
                where="",
                source="deterministic",
                note=None,
                ai_reviewed=False,
                ai_explanation="",
                ai_todo="",
                ai_unchecked="",
                todo=own["todo"],
                todo_from_ai=False,
                diff_lookup=diff_lookup,
                driver=finding.id in driver_ids,
                driver_label=driver_label,
            )
        )
    for note in issues["extra"]:
        lines = review_lines(note, report)
        rows.append(
            _row(
                note,
                level="unrated",
                category=_category(note.change_category),
                headline=lines["change"] or " ".join(note.explanation.split()),
                where="",
                source="ai",
                note=note,
                ai_reviewed=True,
                ai_explanation=lines["change"],
                ai_todo=lines["todo"],
                ai_unchecked=lines["unchecked"],
                todo=lines["todo"],
                todo_from_ai=True,
                diff_lookup=diff_lookup,
                driver=False,
                driver_label=driver_label,
            )
        )
    rows.sort(key=_row_rank)
    return rows


def _row(finding, *, level, category, headline, where, source, note, ai_reviewed, ai_explanation, ai_todo,
         ai_unchecked, todo, todo_from_ai, diff_lookup, driver, driver_label) -> dict:
    attributes = finding.attributes or {}
    flags = []
    if note is not None and (note.attributes or {}).get("citation_unconfirmed"):
        flags.append("The AI's citation could not be matched to captured source.")
    if note is not None and (note.attributes or {}).get("downgraded"):
        flags.append("The evidence validator downgraded this AI claim.")
    status_value = finding.status.value
    diff_link = _diff_link(finding, diff_lookup)
    search = " ".join(
        str(part)
        for part in (
            finding.id,
            headline,
            finding.changed_object,
            finding.object_type,
            finding.affected_object or "",
            CATEGORY_LABELS.get(category, category),
            " ".join(finding.dependency_path),
            note.id if note is not None else "",
        )
    ).lower()
    return {
        "id": finding.id,
        "dom_id": f"issue-{finding.id}",
        "finding": finding,
        "level": level,
        "level_label": SEVERITY_LABELS.get(level, level.capitalize()),
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, category.replace("_", " ").capitalize()),
        "headline": headline or " ".join(finding.explanation.split()),
        "where": where,
        "object": finding.changed_object if finding.changed_object.strip(".") else "",
        "object_type": finding.object_type,
        "affected_object": finding.affected_object or "",
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "evidence_status": status_value,
        "evidence_label": EVIDENCE_LABELS.get(status_value, status_value),
        "confidence": finding.confidence.value,
        "driver": driver,
        "driver_label": driver_label if driver else "",
        "note": note,
        "note_id": note.id if note is not None else "",
        "ai_reviewed": ai_reviewed,
        "ai_explanation": ai_explanation,
        "ai_todo": ai_todo,
        "ai_unchecked": ai_unchecked,
        "ai_flags": flags,
        "todo": todo,
        "todo_from_ai": todo_from_ai,
        "recommended_action": finding.recommended_action if source != "ai" else "",
        "proposed_tests": list(finding.proposed_tests) if source != "ai" else [],
        "snippets": len(attributes.get("test_snippets") or []),
        "action_scripts": list(attributes.get("action_scripts") or []) if source != "ai" else [],
        "diff_link": diff_link,
        "dependencies": [],
        "search": search,
    }


def _row_rank(row: dict) -> tuple:
    level = SEVERITY_ORDER.index(row["level"]) if row["level"] in SEVERITY_ORDER else len(SEVERITY_ORDER)
    return (level, 0 if row["driver"] else 1, 0 if row["source"] != "ai" else 1)


def _category(change_category: str) -> str:
    return {
        "interface": "interface",
        "business_logic": "business",
        "data": "data",
        "dependency": "dependency",
        "transaction": "transaction",
        "exception": "transaction",
        "error_contract": "interface",
        "security": "security",
        "coverage": "coverage",
        "compilation": "compilation",
    }.get(change_category, "other")


def _diff_key(owner: str, name: str, object_type: str) -> str:
    return f"{owner}.{name}:{object_type}"


def _diff_link(finding, diff_lookup: dict) -> dict | None:
    candidates = [finding.object_type]
    candidates += [kind for kind in ("PACKAGE BODY", "PACKAGE") if kind != finding.object_type]
    for kind in candidates:
        diff = diff_lookup.get(_diff_key(finding.object_owner, finding.object_name, kind))
        if diff is None:
            continue
        if kind != finding.object_type and not (finding.changed_lines_test or finding.corresponding_lines_prod):
            continue
        if finding.changed_lines_test:
            line, side = finding.changed_lines_test[0], "t"
        elif finding.corresponding_lines_prod:
            line, side = finding.corresponding_lines_prod[0], "p"
        else:
            line, side = None, "t"
        side_label = "Test" if side == "t" else "Production"
        return {
            "key": diff["key"],
            "dom_id": diff["dom_id"],
            "line": line,
            "side": side,
            "label": f"{diff['label']} ({diff['object_type'].lower()})"
            + (f", {side_label} line {line}" if line else ""),
        }
    return None


def _diffs(report: Report) -> list[dict]:
    items = []
    for index, diff in enumerate(report.diffs):
        items.append(_diff(index, diff))
    return items


def _diff(index: int, diff: ObjectDiff) -> dict:
    has_test = any(row.test_line_no for row in diff.rows)
    has_prod = any(row.prod_line_no for row in diff.rows)
    if diff.unchanged:
        state, label = "unchanged", "Unchanged"
    elif diff.formatting_only:
        state, label = "formatting", "Formatting only"
    elif has_test and not has_prod:
        state, label = "added", "Only in Test"
    elif has_prod and not has_test:
        state, label = "removed", "Only in Production"
    elif not has_test and not has_prod:
        state, label = "missing", "No source captured"
    else:
        state, label = "changed", "Changed"
    dom_id = f"obj-{index}"
    changed = [i for i, row in enumerate(diff.rows) if row.test_kind != "context" or row.prod_kind != "context"]
    near = set()
    for position in changed:
        near.update(range(max(0, position - CONTEXT_LINES), position + CONTEXT_LINES + 1))
    items = []
    hidden = 0
    hunks = 0
    previous_changed = False
    for position, row in enumerate(diff.rows):
        is_change = position in changed
        far = position not in near
        if far:
            hidden += 1
        elif hidden:
            items.append({"gap": hidden})
            hidden = 0
        if is_change and not previous_changed:
            hunks += 1
        previous_changed = is_change
        items.append(
            {
                "gap": 0,
                "id": f"{dom_id}-t{row.test_line_no}" if row.test_line_no else "",
                "test_no": row.test_line_no,
                "test_text": row.test_text,
                "test_kind": row.test_kind,
                "prod_no": row.prod_line_no,
                "prod_text": row.prod_text,
                "prod_kind": row.prod_kind,
                "change": is_change,
                "hunk_start": is_change and (position == 0 or position - 1 not in changed),
                "far": far,
            }
        )
    if hidden:
        items.append({"gap": hidden})
    return {
        "index": index,
        "key": _diff_key(diff.owner, diff.name, diff.object_type),
        "dom_id": dom_id,
        "label": f"{diff.owner}.{diff.name}",
        "owner": diff.owner,
        "name": diff.name,
        "object_type": diff.object_type,
        "added": diff.added,
        "removed": diff.removed,
        "state": state,
        "state_label": label,
        "test_hash": diff.test_hash,
        "prod_hash": diff.prod_hash,
        "rows": items,
        "row_count": len(diff.rows),
        "hunks": hunks,
        "findings": [],
        "level": "",
    }


def _top_level(rows: list[dict]) -> str:
    levels = [row["level"] for row in rows if row["level"] in SEVERITY_ORDER]
    return min(levels, key=SEVERITY_ORDER.index) if levels else ""


def _mentions(qualified: str, text: str) -> bool:
    return bool(re.search(rf"(?<![\w$#]){re.escape(qualified)}(?![\w$#])", text or ""))


def _dependencies(report: Report, rows: list[dict]) -> list[dict]:
    items = []
    for index, dep in enumerate(report.dependencies):
        qualified = f"{dep.owner}.{dep.object_name}"
        related = [
            row
            for row in rows
            if row["source"] != "ai"
            and (
                _mentions(qualified, row["affected_object"])
                or row["object"] == qualified
                or any(_mentions(qualified, step) for step in row["finding"].dependency_path)
            )
        ]
        caller = dep.direction == "caller"
        items.append(
            {
                "dom_id": f"dep-{index}",
                "qualified": qualified,
                "direction": dep.direction,
                "direction_label": "Calls the selected package" if caller else "Used by the selected package",
                "object_type": dep.object_type,
                "usage": dep.usage,
                "impact": dep.impact,
                "impact_tone": _IMPACT_TONES.get(dep.impact, "ok"),
                "depth": dep.depth,
                "database": dep.database or "Not recorded",
                "finding_ids": [row["id"] for row in related],
                "findings": [{"id": row["id"], "level": row["level"], "level_label": row["level_label"]} for row in related],
                "search": f"{qualified} {dep.object_type} {dep.usage} {dep.impact}".lower(),
            }
        )
    return items


def _ordered_counts(counter: Counter) -> list[dict]:
    return [{"label": label, "count": count} for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]


def _suggested_checks(drivers: list[dict]) -> list[dict]:
    checks = []
    seen = set()
    for row in sorted(drivers, key=_row_rank):
        if not row["todo"] or row["todo"] in seen:
            continue
        seen.add(row["todo"])
        checks.append(
            {
                "finding_id": row["id"],
                "level": row["level"],
                "level_label": row["level_label"],
                "text": row["todo"],
                "attribution": "AI suggestion, review before use" if row["todo_from_ai"] else "Analyzer rule",
                "from_ai": row["todo_from_ai"],
            }
        )
    return checks[:TOP_FINDINGS]


def _packages(report: Report, rows: list[dict], diffs: list[dict]) -> list[dict]:
    items = []
    for package in report.packages:
        related = [row for row in rows if row["object"] == package]
        package_diffs = [diff for diff in diffs if diff["label"] == package]
        items.append(
            {
                "name": package,
                "findings": len(related),
                "level": _top_level(related),
                "level_label": SEVERITY_LABELS.get(_top_level(related), ""),
                "added": sum(diff["added"] for diff in package_diffs),
                "removed": sum(diff["removed"] for diff in package_diffs),
                "objects": [diff["object_type"] for diff in package_diffs if diff["state"] != "unchanged"],
            }
        )
    others = sorted({row["object"] for row in rows} - set(report.packages))
    for name in others:
        related = [row for row in rows if row["object"] == name]
        items.append(
            {
                "name": name,
                "findings": len(related),
                "level": _top_level(related),
                "level_label": SEVERITY_LABELS.get(_top_level(related), ""),
                "added": None,
                "removed": None,
                "objects": [],
            }
        )
    return items


def _filters(rows: list[dict]) -> dict:
    present_levels = {row["level"] for row in rows}
    return {
        "severity": [
            {"value": level, "label": SEVERITY_LABELS[level]} for level in SEVERITY_ORDER if level in present_levels
        ],
        "object_type": sorted({row["object_type"] for row in rows}),
        "source": [
            {"value": key, "label": label}
            for key, label in SOURCE_LABELS.items()
            if any(row["source"] == key for row in rows)
        ],
        "evidence": [
            {"value": key, "label": label}
            for key, label in EVIDENCE_LABELS.items()
            if any(row["evidence_status"] == key for row in rows)
        ],
        "category": [
            {"value": key, "label": label}
            for key, label in CATEGORY_LABELS.items()
            if any(row["category"] == key for row in rows)
        ],
    }


def _ai(report: Report, issues: dict) -> dict:
    sources = ai_sources(report)
    return {
        "used": report.ai_used,
        "model": report.model_name,
        "serious": issues["serious"],
        "serious_explained": issues["serious_explained"],
        "unexplained": issues["unexplained"] if report.ai_used else [],
        "blind_spots": issues["blind_spots"],
        "reads": sources["reads"],
        "objects": sources["objects"],
        "refused": sources["refused"],
        "source_rows": sources["rows"],
    }


def _unverified(report: Report, rows: list[dict], issues: dict) -> dict:
    return {
        "unknown": sum(1 for row in rows if row["evidence_status"] == "unknown"),
        "potential": sum(1 for row in rows if row["evidence_status"] == "potential"),
        "blind_spots": len(issues["blind_spots"]),
        "unexplained": len(issues["unexplained"]) if report.ai_used else 0,
        "limitations": len(report.limitations) + sum(1 for row in rows if row["category"] == "coverage"),
        "tests_recorded": False,
    }


def _tests(rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        steps = []
        for test in row["proposed_tests"]:
            steps.append({"text": test, "attribution": "Analyzer rule", "from_ai": False})
        if row["ai_todo"] and row["ai_todo"] not in {step["text"] for step in steps}:
            steps.append({"text": row["ai_todo"], "attribution": "AI suggestion, review before use", "from_ai": True})
        if not steps and not row["snippets"]:
            continue
        items.append(
            {
                "finding_id": row["id"],
                "level": row["level"],
                "level_label": row["level_label"],
                "headline": row["headline"],
                "steps": steps,
                "snippets": row["snippets"],
            }
        )
    return items


_PHASE_FINDING = re.compile(r"^(F-[0-9A-F]{8})(?: (critical|high|medium|low|informational) ([a-z_]+))?: (.+)$", re.S)
_PHASE_ENVIRONMENT = re.compile(r"^(test|production) (.+)$", re.S)


def _phase_line(line: str, finding_ids: set[str]) -> dict:
    parsed = {"text": line, "finding_id": "", "linked": False, "severity": "", "category": "", "environment": ""}
    match = _PHASE_FINDING.match(line)
    if match:
        finding_id, severity, category, text = match.groups()
        parsed.update(
            finding_id=finding_id,
            linked=finding_id in finding_ids,
            severity=severity or "",
            category=(category or "").replace("_", " ").capitalize(),
            text=text,
        )
        line = text
    match = _PHASE_ENVIRONMENT.match(line)
    if match:
        parsed.update(environment=_TOOL_ENVIRONMENTS[match.group(1)], text=match.group(2))
    return parsed


def _audit(report: Report, status: dict | None, diffs: list[dict]) -> dict:
    steps = (status or {}).get("steps") or {}
    thoughts = {
        str(item.get("text") or "")
        for item in (status or {}).get("activity") or []
        if item.get("kind") == "thought"
    }
    agentic = report.run_mode == "agentic"
    finding_ids = {finding.id for finding in report.findings}
    phases = []
    for key, label in AGENTIC_STAGES if agentic else STAGES:
        step = steps.get(key)
        summary = (step or {}).get("summary") or ""
        phases.append(
            {
                "key": key,
                "label": label,
                "recorded": step is not None,
                "started_at": (step or {}).get("started_at") or "",
                "summary": "" if summary in thoughts else summary,
                "lines": [
                    _phase_line(line, finding_ids)
                    for line in (step or {}).get("lines") or []
                    if line not in thoughts
                ],
            }
        )
    tools = []
    for index, entry in enumerate(report.tool_log, start=1):
        env, _, name = str(entry.get("target") or "").partition(" ")
        if env not in _TOOL_ENVIRONMENTS:
            env, name = "", f"{env} {name}".strip()
        tool = str(entry.get("tool") or "")
        tools.append(
            {
                "index": index,
                "tool": _TOOL_LABELS.get(tool, tool.replace("_", " ").capitalize() or "Unknown tool"),
                "environment": _TOOL_ENVIRONMENTS.get(env, "Not recorded"),
                "target": name or "Not recorded",
                "ok": bool(entry.get("ok", True)),
                "bytes": entry.get("result_bytes"),
                "at": str(entry.get("at") or ""),
            }
        )
    fixture = report.test_environment.fixture or report.production_environment.fixture
    return {
        "status_available": status is not None,
        "run_status": (status or {}).get("status") or report.status,
        "metadata": [
            {"label": "Run ID", "value": report.run_id},
            {"label": "Report schema version", "value": report.report_version},
            {"label": "Source captured at", "value": format_timestamp(report.created_at)},
            {"label": "Analyzer", "value": report.analyzer},
            {
                "label": "Run mode",
                "value": "Agentic read order, deterministic recommendation" if agentic else "Deterministic",
            },
            {
                "label": "AI investigator",
                "value": f"Used ({report.model_name})" if report.ai_used and report.model_name else "Used" if report.ai_used else "Did not run",
            },
            {"label": "Data source", "value": "Bundled fixture catalog" if fixture else "Read-only Oracle connections"},
            {
                "label": "Proposed version",
                "value": f"{report.test_environment.label} · {report.test_environment.database_id}",
            },
            {
                "label": "Production baseline",
                "value": f"{report.production_environment.label} · {report.production_environment.database_id}",
            },
        ],
        "hashes": [
            {
                "key": diff["key"],
                "label": f"{diff['label']} ({diff['object_type'].lower()})",
                "test_hash": diff["test_hash"],
                "prod_hash": diff["prod_hash"],
                "state_label": diff["state_label"],
            }
            for diff in diffs
        ],
        "phases": phases,
        "events": [
            event for event in (status or {}).get("events") or [] if str(event.get("message") or "") not in thoughts
        ],
        "tools": tools,
        "agentic": agentic,
        "agent_summary": report.agent_summary,
    }
