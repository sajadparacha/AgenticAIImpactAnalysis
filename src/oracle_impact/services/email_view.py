"""Email drafts built from a saved report for the development and support teams.

Every item comes from the report's policy items, findings, and readiness. Nothing
is sent from the application: the draft is copied, opened in a mail client, or
downloaded as an unsent .eml message.
"""

import re
from email.message import EmailMessage
from urllib.parse import quote

from oracle_impact.infrastructure.llm.plain_text import simplify_change
from oracle_impact.models.report import Finding, Report
from oracle_impact.services.readiness import FIX_AND_REANALYZE, INDETERMINATE, RECOMMENDED, RESOLUTIONS

DEVELOPER, SUPPORT = "developer", "support"
AUDIENCES = {DEVELOPER: "Developer version", SUPPORT: "Support version"}
MAILTO_BODY_LIMIT = 1800
_AI_NOTES_LISTED = 5
_ADDRESS = re.compile(r"[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+")

_DEVELOPER_OWNERS = {
    "interface": "Development",
    "compilation": "Development",
    "dependency": "Development, with the DBA for the release order",
    "exception": "Development",
    "error_contract": "Development",
    "dynamic_sql": "Development",
    "transaction": "Development, with a DBA review",
    "business_logic": "Development, with the business owner",
    "security": "Development, with the DBA",
    "coverage": "DBA",
}

# Support wording per finding category: a heading and what support is asked to do.
_SUPPORT_ASKS = {
    "error_contract": (
        "New error code reaches callers",
        "Please add the new error code to your runbooks and alerting, so the first occurrence is recognised.",
    ),
    "exception": (
        "Errors may now be hidden",
        "Failures inside this code may no longer reach the caller or monitoring. Please plan a data check after deployment instead of relying on error alerts.",
    ),
    "interface": (
        "Callers must be updated",
        "Please confirm which jobs, applications, or scripts outside the database call this code, so they can be updated or rescheduled.",
    ),
    "transaction": (
        "Work can be left partly done",
        "If this code fails part way, earlier changes may already be saved. Please make sure the team knows how to spot and reconcile partly completed records.",
    ),
    "business_logic": (
        "Behaviour changes for users",
        "Please let the help desk know about the changed rule, so related questions are expected after deployment.",
    ),
    "dynamic_sql": (
        "Runtime statements are not visible to the analysis",
        "Please watch the logs for errors from this code after deployment. The objects it touches at run time were not checked.",
    ),
    "data": (
        "Stored data changes",
        "Please note which tables this change writes to, so incidents involving them can be triaged quickly.",
    ),
    "dependency": (
        "Release order matters",
        "Please confirm with the DBA that the referenced object is deployed before the package.",
    ),
    "security": (
        "Access may change",
        "Please confirm the application accounts that call this code still have the access they need.",
    ),
    "coverage": (
        "Part of the change was not analysed",
        "Please help confirm the unanalysed objects manually, or restore dictionary access so they can be analysed.",
    ),
}


_CATEGORY_TITLES = {
    "interface": "Interface change",
    "compilation": "Compilation",
    "dependency": "New dependency",
    "exception": "Error handling change",
    "error_contract": "New or changed error code",
    "dynamic_sql": "Dynamic SQL",
    "transaction": "Transaction change",
    "business_logic": "Business rule change",
    "data": "Data change",
    "security": "Security change",
}


def _category(finding: Finding) -> str:
    """Release-order wording applies to any object missing in Production, whichever check found it."""
    return "dependency" if finding.attributes.get("missing_object") else finding.change_category


def recipients(value: str) -> list[str]:
    """Addresses from a comma-separated setting. Anything that does not look like one address is dropped."""
    return [part for part in (item.strip() for item in (value or "").split(",")) if _ADDRESS.fullmatch(part)]


def build_email(report: Report, readiness: dict, audience: str, *, to: list[str], report_url: str) -> dict:
    audience = audience if audience in AUDIENCES else DEVELOPER
    findings = {finding.id: finding for finding in report.findings}
    open_blockers = [entry for entry in readiness["blockers"] if entry["state"] != "resolved"]
    open_checks = [entry for entry in readiness["validations"] if entry["state"] != "resolved"]
    closed = [entry for entry in readiness["blockers"] + readiness["validations"] if entry["state"] == "resolved"]
    in_policy = {entry["item"].finding_id for entry in readiness["blockers"] + readiness["validations"]}
    awareness = [
        finding
        for finding in report.findings
        if finding.origin == "deterministic" and finding.id not in in_policy and finding.change_category not in {"coverage", "format"}
    ]
    packages = ", ".join(report.packages)
    labels = {info.role: info.label for info in report.environments}
    context = {
        "packages": packages,
        "test": labels.get("source", "Test"),
        "production": labels.get("baseline", "Production"),
        "report_url": report_url,
    }
    build = _developer if audience == DEVELOPER else _support
    email = build(report, readiness, findings, open_blockers, open_checks, closed, awareness, context)
    email.update({"audience": audience, "audience_label": AUDIENCES[audience], "to": list(to), "report_url": report_url})
    email["text"] = render_text(email)
    email["mailto"], email["mailto_truncated"] = _mailto(email)
    return email


def _developer(report, readiness, findings, open_blockers, open_checks, closed, awareness, context) -> dict:
    blockers, checks = len(open_blockers), len(open_checks)
    if readiness["decision"] == INDETERMINATE:
        subject = f"Impact analysis for {context['packages']}: the analysis needs attention"
    elif blockers:
        subject = f"Impact analysis for {context['packages']}: {_count(blockers, 'blocking item')} to fix before deployment"
    elif checks:
        subject = f"Impact analysis for {context['packages']}: {_count(checks, 'item')} to test or justify"
    else:
        subject = f"Impact analysis for {context['packages']}: ready for review"

    sections = []
    if open_blockers:
        sections.append(
            {
                "title": "Must fix before deployment (blocking)",
                "intro": "These items stop the change from going to Production until they are fixed and a new analysis is run.",
                "items": [_developer_item(entry, findings) for entry in open_blockers],
            }
        )
    if open_checks:
        sections.append(
            {
                "title": "Please test or justify (validation items)",
                "intro": "These don't necessarily block deployment, but each needs test evidence or a short justification.",
                "items": [_developer_item(entry, findings) for entry in open_checks],
            }
        )
    if awareness:
        sections.append(
            {
                "title": "For awareness",
                "intro": "No action is required unless these affect your work.",
                "items": [_finding_item(finding, finding.recommended_action, _DEVELOPER_OWNERS.get(_category(finding), "Development")) for finding in awareness],
            }
        )
    notes = [finding for finding in report.findings if finding.origin == "investigator"]
    if notes:
        sections.append(
            {
                "title": "Notes from the AI investigator (not confirmed)",
                "intro": "These are leads from the AI review, not confirmed findings. They can help explain the items above.",
                "items": [
                    {"id": "", "title": _shorten(finding.evidence or finding.explanation, 320), "where": finding.changed_object, "detail": "", "action": "", "owner": "", "tests": []}
                    for finding in notes[:_AI_NOTES_LISTED]
                ],
                "more": max(0, len(notes) - _AI_NOTES_LISTED),
            }
        )
    if closed:
        sections.append(
            {
                "title": "Already closed",
                "intro": "These items are no longer open.",
                "items": [{"id": entry["item"].finding_id, "title": entry["item"].title, "where": "", "detail": entry["reason"], "action": "", "owner": "", "tests": []} for entry in closed],
            }
        )

    example = (open_blockers or open_checks or [None])[0]
    return {
        "subject": subject,
        "greeting": "Hi all,",
        "opening": [
            "I hope you're all well! We've run an impact analysis on the latest change to "
            f"{context['packages']}, comparing {context['test']} with {context['production']}. "
            f"The full report is here: {context['report_url']}",
            _short_version(readiness, blockers, checks),
        ],
        "reply": (
            "For each item, please reply with Fixed, Justified (with a short reason) or Not applicable, "
            f"quoting its reference number (for example {example['item'].finding_id}), so we can track everything in one place."
            if example
            else ""
        ),
        "sections": sections,
        "next_steps": _developer_next_steps(blockers, checks),
        "closing": "Thanks so much for your help with this. Please reach out if anything is unclear, or if you'd like to go through the report together.",
    }


def _support(report, readiness, findings, open_blockers, open_checks, closed, awareness, context) -> dict:
    subject = f"Impact analysis for {context['packages']}: what support needs to know ({readiness['label'].lower()})"
    asks = []
    seen: set[str] = set()
    for finding in [findings.get(entry["item"].finding_id) for entry in open_blockers + open_checks] + awareness:
        if finding is None or finding.id in seen or _category(finding) not in _SUPPORT_ASKS:
            continue
        if finding.change_category == "interface" and not finding.attributes.get("breaking"):
            continue
        seen.add(finding.id)
        heading, action = _SUPPORT_ASKS[_category(finding)]
        asks.append(
            {
                "id": finding.id,
                "title": heading,
                "where": _where(finding),
                "detail": _plain(finding),
                "action": action,
                "owner": "",
                "tests": [],
            }
        )
    sections = []
    if asks:
        sections.append(
            {
                "title": "What we need from you",
                "intro": "Each item below needs a quick look from the support team before or after deployment.",
                "items": asks,
            }
        )
    if open_blockers:
        sections.append(
            {
                "title": "What developers are fixing first",
                "intro": "No action is needed from support on these. They are listed so you know why the deployment is waiting.",
                "items": [
                    {
                        "id": entry["item"].finding_id,
                        "title": entry["item"].title,
                        "where": _where(findings.get(entry["item"].finding_id)),
                        "detail": _plain(findings[entry["item"].finding_id]) if entry["item"].finding_id in findings else entry["item"].explanation,
                        "action": "",
                        "owner": "",
                        "tests": [],
                    }
                    for entry in open_blockers
                ],
            }
        )
    return {
        "subject": subject,
        "greeting": "Hi team,",
        "opening": [
            f"I hope you're having a good day! We've run an impact analysis on the latest change to {context['packages']}. "
            "This version of the email covers what the change means for support, without the code detail. "
            f"The full report is here: {context['report_url']}",
            _short_version(readiness, len(open_blockers), len(open_checks)),
        ],
        "reply": "For each item under \"What we need from you\", please reply with Done or Not applicable, quoting its reference number." if asks else "",
        "sections": sections,
        "next_steps": [
            "Please reply with your status for each item by [date].",
            "We'll let you know when a new analysis confirms the developer fixes.",
            "Nothing will be deployed until the report shows the change as ready and it has human approval.",
        ],
        "closing": "Thanks so much for your help! Please reach out if anything is unclear.",
    }


def _short_version(readiness: dict, blockers: int, checks: int) -> str:
    parts = []
    if blockers:
        parts.append(f"{_count(blockers, 'blocking item')} that need a fix")
    if checks:
        parts.append(f"{_count(checks, 'item')} to test or justify")
    if readiness.get("open_drift"):
        parts.append(f"{_count(readiness['open_drift'], 'unexpected change')} to classify")
    found = ("There " + ("are " if _plural(parts) else "is ") + " and ".join(parts) + ".") if parts else ""
    if readiness["decision"] == INDETERMINATE:
        reasons = " ".join(readiness.get("indeterminate_reasons") or [])
        text = f"The short version: this run can't give a final recommendation. {reasons}"
        return f"{text} It still found items worth fixing: {found[0].lower()}{found[1:]}" if found else text
    if readiness["decision"] == RECOMMENDED:
        return "The short version: good news, no blocking items or required validations remain. Human approval is still needed before deployment."
    return f"The short version: the change is not ready for Production yet. {found}".strip()


def _developer_next_steps(blockers: int, checks: int) -> list[str]:
    steps = ["Owners: please reply with your status for each reference number by [date]."]
    if blockers:
        steps.append("Once the blocking items are fixed in Test, we'll run a new analysis to confirm everything is clear.")
    if checks:
        steps.append("For validation items, reply on this email with the test result or a short justification.")
    if not blockers and not checks:
        steps.append("Please review the report before it goes for approval.")
    return steps


def _developer_item(entry: dict, findings: dict[str, Finding]) -> dict:
    item = entry["item"]
    finding = findings.get(item.finding_id)
    owner = _DEVELOPER_OWNERS.get(_category(finding), "Development") if finding else "Development"
    action = item.required_action
    if item.resolution in RESOLUTIONS and item.resolution != FIX_AND_REANALYZE:
        action += " To close it: " + RESOLUTIONS[item.resolution]
    result = _finding_item(finding, action, owner) if finding else {
        "id": item.finding_id, "title": item.title, "where": "", "detail": item.explanation, "action": action, "owner": owner, "tests": []
    }
    result["title"] = item.title
    return result


def _finding_item(finding: Finding, action: str, owner: str) -> dict:
    return {
        "id": finding.id,
        "title": _CATEGORY_TITLES.get(finding.change_category, finding.change_category.replace("_", " ").capitalize()),
        "where": _where(finding),
        "detail": finding.evidence,
        "action": action,
        "owner": owner,
        "tests": list(finding.proposed_tests[:2]),
    }


def _plain(finding: Finding) -> str:
    return simplify_change(finding, finding.evidence or finding.explanation)


def _where(finding: Finding | None) -> str:
    if finding is None:
        return ""
    lines = sorted(set(finding.changed_lines_test))
    place = finding.changed_object
    if lines:
        place += f", line{'s' if len(lines) > 1 else ''} " + ", ".join(str(line) for line in lines[:6])
    if finding.affected_object and finding.affected_object != finding.changed_object:
        place += f" (affects {finding.affected_object})"
    return place


def render_text(email: dict) -> str:
    """The plain-text body shown in the preview, copied, and placed in the .eml draft."""
    out = [email["greeting"], ""]
    for paragraph in email["opening"]:
        out += [paragraph, ""]
    if email["reply"]:
        out += [email["reply"], ""]
    for number, section in enumerate(email["sections"], start=1):
        out += ["-" * 60, f"{number}. {section['title']}", ""]
        if section.get("intro"):
            out += [section["intro"], ""]
        for index, item in enumerate(section["items"], start=1):
            reference = f" (ref {item['id']})" if item["id"] else ""
            out.append(f"{number}.{index} {item['title']}{reference}")
            for label, key in (("Where", "where"), ("What we found", "detail"), ("What's needed", "action"), ("Suggested owner", "owner")):
                if item.get(key):
                    out.append(f"   - {label}: {item[key]}")
            for test in item.get("tests") or []:
                out.append(f"   - Test: {test}")
            out.append("")
        if section.get("more"):
            out += [f"{section['more']} more note(s) are in the report.", ""]
    out += ["-" * 60, "Next steps", ""]
    out += [f"{index}. {step}" for index, step in enumerate(email["next_steps"], start=1)]
    out += ["", email["closing"], "", "Best regards,", "[Your name]"]
    return "\n".join(out) + "\n"


def build_eml(email: dict) -> bytes:
    """An unsent draft. Mail clients such as Outlook open it ready to edit and send."""
    message = EmailMessage()
    if email["to"]:
        message["To"] = ", ".join(email["to"])
    message["Subject"] = _header(email["subject"])
    message["X-Unsent"] = "1"
    message.set_content(email["text"])
    return message.as_bytes()


def _mailto(email: dict) -> tuple[str, bool]:
    target = ",".join(email["to"])
    query = "subject=" + quote(email["subject"], safe="")
    truncated = len(email["text"]) > MAILTO_BODY_LIMIT
    if not truncated:
        query += "&body=" + quote(email["text"], safe="")
    return f"mailto:{target}?{query}", truncated


def _header(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def _shorten(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}{'' if value == 1 else 's'}"


def _plural(parts: list[str]) -> bool:
    return len(parts) > 1 or (bool(parts) and not parts[0].startswith("1 "))
