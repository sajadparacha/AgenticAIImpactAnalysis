"""Turn investigator notes into short lines a reviewer can scan."""

import re

_BOILERPLATE_UNKNOWNS = {
    "programs outside the database were not read",
    "programs outside the database were not visible",
    "callers outside the database were not visible",
    "callers outside the database were not read",
}


def plain_reasons(report) -> list[dict[str, str]]:
    """Short reason lines for the recommendation section, including reports already saved."""
    findings = {finding.id: finding for finding in getattr(report, "findings", [])}
    items: list[dict[str, str]] = []
    for reason in getattr(report, "recommendation_reasons", []):
        finding_id, separator, _rest = str(reason).partition(": ")
        finding = findings.get(finding_id) if separator else None
        if finding is None:
            items.append({"id": "", "text": str(reason)})
            continue
        items.append({"id": finding.id, "text": simplify_change(finding, finding.explanation)})
    return items


_SECTION = {
    "business_logic": "business",
    "data": "data",
    "transaction": "transaction",
    "exception": "transaction",
}


def review_lines(finding, report=None) -> dict[str, str]:
    """Short card lines. An investigator note drops text that repeats the finding it reviews."""
    text = " ".join((getattr(finding, "explanation", "") or "").split())
    if getattr(finding, "origin", "") == "investigator":
        change = simplify_change(finding, _slice(text, "What changes:", ("What to do:", "Not checked:")) or text)
        todo = _readable(simplify_test(finding, _slice(text, "What to do:", ("Not checked:", "What changes:"))))
        unchecked = _readable(simplify_unknown(_slice(text, "Not checked:", ("What changes:", "What to do:"))))
        anchor = _anchor(finding, report)
        if anchor is not None:
            anchor_lines = review_lines(anchor)
            if _similar(change, anchor_lines["change"]):
                change = ""
            if _similar(todo, anchor_lines["todo"]):
                todo = ""
        reviews = anchor.id if anchor is not None else str((getattr(finding, "attributes", None) or {}).get("review_of") or "")
        show_beside = not _same_section(finding, anchor) or bool(change or todo)
        return {
            "change": change,
            "todo": todo,
            "unchecked": unchecked,
            "reviews": reviews,
            "show_beside": show_beside,
        }
    tests = list(getattr(finding, "proposed_tests", None) or [])
    todo = simplify_test(finding, tests[0]) if tests else " ".join((getattr(finding, "recommended_action", "") or "").split())
    return {
        "change": simplify_change(finding, text),
        "todo": todo,
        "unchecked": "",
        "reviews": "",
        "show_beside": True,
    }


def severity_label(finding, report=None) -> dict[str, str]:
    """Display-only severity. An investigator review shows the level of the finding it reviews;
    its own stored severity stays low so the recommendation cannot depend on model output."""
    anchor = _anchor(finding, report)
    if anchor is not None:
        level = anchor.severity.value
        return {"level": level, "text": f"reviews {anchor.id} · {level}"}
    level = finding.severity.value
    return {"level": level, "text": level}


_LEVELS = ("critical", "high", "medium", "low", "informational")
_GROUPS = {
    "interface": ("interface", "Interface"),
    "business_logic": ("business", "Business rules"),
    "data": ("data", "Data"),
    "transaction": ("transaction", "Transactions"),
    "exception": ("transaction", "Transactions"),
    "error_contract": ("interface", "Interface"),
    "security": ("security", "Security"),
}
_GROUP_ORDER = ("interface", "business", "data", "transaction", "security", "other")


def issue_list(report) -> dict:
    """One row per deterministic finding with the AI note that explains it attached.

    Coverage findings are left to the limitations section. AI notes without a
    matching finding are returned separately so nothing is listed twice.
    """
    findings = list(getattr(report, "findings", []) or [])
    explained: dict[str, object] = {}
    extra = []
    for finding in findings:
        if getattr(finding, "origin", "") != "investigator":
            continue
        anchor = _anchor(finding, report)
        if anchor is None or anchor.id in explained:
            extra.append(finding)
        else:
            explained[anchor.id] = finding
    rows = []
    blind_spots: dict[str, list[str]] = {}
    for finding in findings:
        if finding.origin == "investigator" or finding.change_category == "coverage":
            continue
        own = review_lines(finding)
        note = explained.get(finding.id)
        ai = review_lines(note, report) if note is not None else None
        for sentence in _sentences(ai["unchecked"] if ai else ""):
            ids = blind_spots.setdefault(_impersonal(sentence), [])
            if finding.id not in ids:
                ids.append(finding.id)
        key, label = _GROUPS.get(finding.change_category, ("other", "Other"))
        rows.append(
            {
                "finding": finding,
                "level": finding.severity.value,
                "group": key,
                "group_label": label,
                "headline": own["change"],
                "where": _where(finding),
                "note": note,
                "explanation": ai["change"] if ai else "",
                "todo": (ai["todo"] if ai and ai["todo"] else own["todo"]),
                "todo_from_ai": bool(ai and ai["todo"]),
            }
        )
    rows.sort(key=lambda row: _LEVELS.index(row["level"]) if row["level"] in _LEVELS else len(_LEVELS))
    counts = {key: 0 for key in _GROUP_ORDER}
    labels = {"other": "Other"}
    for row in rows:
        counts[row["group"]] += 1
        labels[row["group"]] = row["group_label"]
    groups = [{"key": key, "label": labels[key], "count": counts[key]} for key in _GROUP_ORDER if counts[key]]
    serious = [row for row in rows if row["level"] in {"critical", "high", "medium"}]
    return {
        "rows": rows,
        "groups": groups,
        "extra": extra,
        "explained": len(explained),
        "serious": len(serious),
        "serious_explained": sum(1 for row in serious if row["note"] is not None),
        "unexplained": [{"id": row["finding"].id, "level": row["level"]} for row in serious if row["note"] is None],
        "blind_spots": [{"text": text, "issues": ids} for text, ids in blind_spots.items()],
    }


_TOOL_LABELS = {
    "get_package_source": "Package source",
    "get_object_source": "Source code",
    "get_dependencies": "Dependencies",
    "get_table_metadata": "Table definition",
    "get_procedure_arguments": "Procedure arguments",
}


def ai_sources(report) -> dict:
    """The investigator's tool log, one row per object and kind of read."""
    rows: dict[tuple[str, str], dict] = {}
    log = list(getattr(report, "tool_log", []) or [])
    for entry in log:
        env, _, name = str(entry.get("target") or "").partition(" ")
        if env not in {"test", "production"}:
            env, name = "", f"{env} {name}".strip()
        tool = str(entry.get("tool") or "")
        what = _TOOL_LABELS.get(tool, tool.replace("_", " ").capitalize())
        row = rows.setdefault(
            (name, what),
            {"object": name or "Unknown object", "what": what, "test": False, "production": False, "refused": False},
        )
        if not entry.get("ok", True):
            row["refused"] = True
        elif env:
            row[env] = True
    ordered = sorted(rows.values(), key=lambda row: (row["refused"], row["object"], row["what"]))
    return {
        "rows": ordered,
        "reads": len(log),
        "objects": len({row["object"] for row in ordered}),
        "refused": sum(1 for row in ordered if row["refused"]),
    }


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if len(part.strip()) > 3]


def _impersonal(sentence: str) -> str:
    """Model notes say "I could not see X"; the report reads better as "Could not see X"."""
    match = re.match(r"I\s+(could not|couldn't|cannot|can't|was unable to|did not|didn't)\s+", sentence, re.I)
    if not match:
        return sentence
    verb = "Did not" if match.group(1).lower() in {"did not", "didn't"} else "Could not"
    return f"{verb} {sentence[match.end():]}"


def _where(finding) -> str:
    parts = []
    for label, lines in (("Test", finding.changed_lines_test), ("Production", finding.corresponding_lines_prod)):
        shown = [str(line) for line in list(lines or [])[:4]]
        if shown:
            parts.append(f"{label} line{'s' if len(shown) > 1 else ''} {', '.join(shown)}")
    return " · ".join(parts)


def investigator_notes(report) -> dict:
    """Split investigator notes into new comments and reviews that only repeat a finding."""
    unique = []
    repeated = []
    unchecked: list[str] = []
    for finding in getattr(report, "findings", []) or []:
        if getattr(finding, "origin", "") != "investigator":
            continue
        lines = review_lines(finding, report)
        if lines["change"] or lines["todo"]:
            unique.append(finding)
            continue
        repeated.append({"id": finding.id, "reviews": lines["reviews"]})
        if lines["unchecked"] and lines["unchecked"] not in unchecked:
            unchecked.append(lines["unchecked"])
    return {"unique": unique, "repeated": repeated, "unchecked": unchecked}


def simplify_change(finding, text: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    low = cleaned.lower()
    gained = re.search(
        r"([A-Z][A-Z0-9_]*)\s+gained parameter(?:\(s\))?\s+([A-Z0-9_]+)",
        cleaned,
    )
    if gained:
        caller = _caller(finding, cleaned)
        sentence = f"{gained.group(1)} now requires {gained.group(2)}."
        if caller:
            sentence += f" {caller} still uses the old arguments."
        return sentence
    missing = re.search(r"(?:INSERT|UPDATE|DELETE|MERGE)\s+(?:references|against)\s+([\w.]+)", cleaned, re.I)
    if missing and "not found" in low:
        verb = "inserts into" if cleaned.upper().startswith("INSERT") else "writes to"
        return (
            f"{_routine(finding, cleaned)} {verb} {missing.group(1)}, "
            "but Production does not have that object."
        )
    updated = re.search(r"UPDATE against ([\w.]+)", cleaned, re.I)
    if updated and ("static inspection" in low or "visible trigger" in low):
        return (
            f"{_routine(finding, cleaned)} changes rows in {updated.group(1)}. "
            "The report cannot tell which rows."
        )
    if "transaction sequence" in low or "moved commit" in low:
        routine = _routine(finding, cleaned)
        if "commit" in low and "insert" in low:
            return (
                f"{routine} saves its update and then inserts. "
                "If the insert fails, the update is already saved."
            )
        return (
            f"{routine} saves its work, then runs another statement. "
            "If that statement fails, the earlier save remains."
        )
    if "executable text" in low or "example: procedure" in low or "not a proven runtime outcome" in low:
        return f"The text of {_routine(finding, cleaned)} changed. The exact lines are in the source comparison."
    if "swallows the error" in low:
        return f"{_routine(finding, cleaned)} catches an error and ignores it."
    if "no longer has the same swallowed" in low:
        return f"{_routine(finding, cleaned)} no longer ignores that error."
    if "autonomous transaction" in low:
        return f"{_routine(finding, cleaned)} commits on its own. That save remains if the caller rolls back."
    if "dynamic sql" in low:
        return f"{_routine(finding, cleaned)} builds SQL while it runs. The target objects are not visible."
    if "behavioral effect is a hypothesis" in low:
        rule = cleaned.split(". The behavioral", 1)[0].rstrip(".")
        return f"{rule}."
    return cleaned


def simplify_test(finding, text: str) -> str:
    cleaned = " ".join((text or "").replace("->", " ").split())
    if not cleaned:
        return ""
    low = cleaned.lower()
    if "the way production does today" in low or "no longer matches" in low:
        who = _caller(finding, cleaned) or _routine(finding, cleaned)
        return f"Call {who} with the arguments Production uses today."
    if "force an error after the commit" in low or "which changes remain" in low or "force an error after each dml" in low:
        return (
            f"Run {_routine(finding, cleaned)} and make the statement after the save fail. "
            "See which changes remain."
        )
    if low.startswith("run the procedure and check"):
        target = getattr(finding, "affected_object", None) or "the table it changes"
        return f"Run {_routine(finding, cleaned)} and look at {target}."
    if "inputs used in production" in low:
        return f"Run {_routine(finding, cleaned)} with a Production example."
    return cleaned


def simplify_unknown(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if cleaned.lower().rstrip(".") in _BOILERPLATE_UNKNOWNS:
        return "Programs outside the database."
    return cleaned


def _anchor(finding, report):
    if getattr(finding, "origin", "") != "investigator" or report is None:
        return None
    review_of = str((getattr(finding, "attributes", None) or {}).get("review_of") or "")
    if not review_of:
        return None
    for item in getattr(report, "findings", []) or []:
        if item.id == review_of:
            return item
    return None


def _same_section(finding, anchor) -> bool:
    if anchor is None:
        return False
    left = _SECTION.get(getattr(finding, "change_category", ""))
    right = _SECTION.get(getattr(anchor, "change_category", ""))
    return bool(left) and left == right


def _similar(left: str, right: str) -> bool:
    first = _norm(left)
    second = _norm(right)
    if not first or not second:
        return False
    if first == second or first in second or second in first:
        return True
    shared = [
        token
        for token in re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)+", f"{left} {right}".lower())
        if token in left.lower() and token in right.lower()
    ]
    return bool(shared)


def _readable(text: str) -> str:
    """Drop fragments such as "C. a." left by older runs that split a string into letters."""
    return text if len(re.findall(r"[A-Za-z]{2,}", text or "")) >= 2 else ""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _slice(text: str, label: str, stops: tuple[str, ...]) -> str:
    if label not in text:
        return ""
    rest = text.split(label, 1)[1]
    for stop in stops:
        if stop in rest:
            rest = rest.split(stop, 1)[0]
    return rest.strip()


def _caller(finding, text: str) -> str:
    match = re.search(r"Unupdated callers:\s*([\w.]+)", text)
    if match:
        return match.group(1).rstrip(".")
    path = getattr(finding, "dependency_path", None) or []
    if path:
        return str(path[0]).split("->")[0].strip()
    return ""


_SQL_WORDS = {
    "UPDATE",
    "INSERT",
    "DELETE",
    "MERGE",
    "WHAT",
    "THE",
    "SELECT",
    "PROCEDURE",
    "BEGIN",
    "END",
}


def _routine(finding, text: str) -> str:
    for source in (text, getattr(finding, "evidence", "") or "", getattr(finding, "explanation", "") or ""):
        named = re.match(r"(?:What changes:\s*)?([A-Z][A-Z0-9_]{2,})", source.strip())
        if named and named.group(1) not in _SQL_WORDS:
            return named.group(1)
    package = getattr(finding, "object_name", "") or ""
    for step in getattr(finding, "dependency_path", None) or []:
        tail = str(step).split("->")[-1].strip().rsplit(".", 1)[-1]
        if tail and tail != package:
            return tail
    return package or "This procedure"
