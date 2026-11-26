"""A step-by-step fix plan built from a saved report.

Steps come from the report's open policy items and from the scripts the deterministic
checks generated for each finding. They are put in release order: fixes in Test, work
in Production before the package is deployed, tests to run, a new analysis, then the
release itself. Investigator leads are not confirmed, so they never become steps.
Nothing here is executed: scripts are shown and downloaded for a person to run.
"""

import textwrap
from urllib.parse import quote

from oracle_impact.analysis.action_scripts import AFTER_RELEASE, BEFORE_RELEASE, FIX_IN_TEST
from oracle_impact.models.report import Finding, Report
from oracle_impact.services.readiness import FIX_AND_REANALYZE, INDETERMINATE, RESOLUTIONS

TEST, PRODUCTION = "test", "production"
ENVIRONMENTS = {TEST: "Test", PRODUCTION: "Production"}
VALIDATE, REANALYZE = "validate", "reanalyze"
_PHASES = (
    (FIX_IN_TEST, "Fix the code in Test", "Change the code in Test until it compiles and every caller works with it."),
    (BEFORE_RELEASE, "Prepare Production", "Get change approval through your normal change process before running these. They make Production changes before the package is deployed, so everything it needs is already there."),
    (VALIDATE, "Test or justify", "Each item needs test evidence or a short written justification. The result is tracked outside this report."),
    (REANALYZE, "Confirm with a new analysis", "Only a new analysis can clear a blocking item."),
    (AFTER_RELEASE, "Release", "Deploy only after a person has approved the change."),
)
_RULE = "-- " + "=" * 74 + "\n"


def _prerequisite(stage: str, environment: str) -> str | None:
    """The change-process gate that must come before a Production action."""
    if environment != PRODUCTION:
        return None
    if stage == BEFORE_RELEASE:
        return "Get change approval through your normal change process before running this in Production."
    if stage == AFTER_RELEASE:
        return "Complete the deployment approval and deploy the package before running this in Production."
    return None


def build_fix_plan(report: Report, readiness: dict, *, report_url: str) -> dict:
    findings = {finding.id: finding for finding in report.findings if finding.origin != "investigator"}
    open_entries = [
        (kind, entry)
        for kind, key in (("blocker", "blockers"), ("validation", "validations"))
        for entry in readiness[key]
        if entry["state"] != "resolved" and entry["item"].finding_id in findings
    ]
    policy_kind = {entry["item"].finding_id: kind for kind, entry in open_entries}
    phases: dict[str, list[dict]] = {key: [] for key, _title, _intro in _PHASES}

    ordered = [findings[entry["item"].finding_id] for _kind, entry in open_entries]
    ordered += [finding for finding in report.findings if finding.id in findings and finding.id not in policy_kind]
    for finding in ordered:
        kind = policy_kind.get(finding.id, "other")
        for index, script in enumerate(finding.attributes.get("action_scripts") or []):
            stage = script.get("stage") or (FIX_IN_TEST if script["environment"] == TEST else BEFORE_RELEASE)
            phases[stage].append(
                _step(
                    finding,
                    kind,
                    report_url,
                    title=script["title"],
                    action=finding.recommended_action if index == 0 else "",
                    environment=script["environment"],
                    sql=script["sql"],
                    prerequisite=_prerequisite(stage, script["environment"]),
                )
            )

    for kind, entry in open_entries:
        item = entry["item"]
        finding = findings[item.finding_id]
        scripted = bool(finding.attributes.get("action_scripts"))
        if item.resolution == FIX_AND_REANALYZE:
            if scripted:
                continue
            stage = BEFORE_RELEASE if finding.attributes.get("missing_object") else FIX_IN_TEST
            environment = PRODUCTION if stage == BEFORE_RELEASE else TEST
            phases[stage].append(_step(finding, kind, report_url, title=item.title, action=item.required_action, prerequisite=_prerequisite(stage, environment)))
        else:
            phases[VALIDATE].append(
                _step(
                    finding,
                    kind,
                    report_url,
                    title=item.title,
                    action=item.required_action,
                    closing=RESOLUTIONS.get(item.resolution, ""),
                    tests=list(finding.proposed_tests[:3]),
                )
            )

    packages = ", ".join(report.packages)
    blocking = any(kind == "blocker" for kind, _entry in open_entries)
    if blocking or phases[FIX_IN_TEST] or phases[BEFORE_RELEASE]:
        phases[REANALYZE].append(
            {
                "title": f"Run a new analysis of {packages}",
                "action": "When the steps above are done, analyze the same packages again. The new report shows whether anything is still open.",
                "link": {"href": "/?packages=" + quote("\n".join(report.packages), safe=""), "label": "Start a new analysis"},
            }
        )
    release = [
        {
            "title": "Get the deployment approved",
            "action": "An approver records the decision on the report. A recommendation from the analysis is not an approval.",
            "link": {"href": report_url, "label": "Open the report"},
        },
        {
            "title": f"Deploy {packages} to Production",
            "action": "Use your normal release process. This application does not deploy anything.",
        },
    ]
    phases[AFTER_RELEASE] = release + phases[AFTER_RELEASE]

    number = 0
    sections = []
    for key, title, intro in _PHASES:
        steps = [{"kind": "manual", "environment": "", "sql": "", "tests": [], "closing": "", "link": None, "finding": None, "prerequisite": None, **step} for step in phases[key]]
        if not steps:
            continue
        for step in steps:
            number += 1
            step["number"] = number
            step["kind"] = "script" if step["sql"] else "manual"
        sections.append({"key": key, "title": title, "intro": intro, "steps": steps})

    steps = [step for section in sections for step in section["steps"]]
    plan = {
        "run_id": report.run_id,
        "packages": packages,
        "report_url": report_url,
        "decision": readiness["decision"],
        "label": readiness["label"],
        "warnings": list(readiness.get("indeterminate_reasons") or []) if readiness["decision"] == INDETERMINATE else [],
        "sections": sections,
        "total": len(steps),
        "scripted": sum(1 for step in steps if step["kind"] == "script"),
        "manual": sum(1 for step in steps if step["kind"] == "manual"),
        "blockers": sum(1 for kind, _entry in open_entries if kind == "blocker"),
        "validations": sum(1 for kind, _entry in open_entries if kind == "validation"),
        "scripts": {env: sum(1 for step in steps if step["environment"] == env) for env in ENVIRONMENTS},
    }
    plan["text"] = render_text(plan)
    return plan


def _step(finding: Finding, kind: str, report_url: str, *, title: str, action: str, **extra) -> dict:
    where = finding.changed_object
    if finding.affected_object and finding.affected_object != finding.changed_object:
        where += f" (affects {finding.affected_object})"
    return {
        "title": title,
        "action": action,
        "finding": {
            "id": finding.id,
            "kind": kind,
            "where": where,
            "href": f"{report_url}?tab=findings&finding={quote(finding.id, safe='')}",
        },
        **extra,
    }


def combined_script(plan: dict, environment: str) -> str:
    """Every script step for one environment, in plan order, as one file to review and run by hand."""
    place = ENVIRONMENTS[environment]
    lines = [
        f"Fix plan for {plan['packages']}, run {plan['run_id']}. Scripts for {place} only.",
        "Built from the evidence in the report. This application does not run this script.",
        "Run it one section at a time, in step order, and read each section's notes first.",
        "Steps without a script sit between these sections. The fix plan page lists every step.",
    ]
    out = "".join(_comment(line) for line in lines)
    steps = [step for section in plan["sections"] for step in section["steps"] if step["environment"] == environment]
    if not steps:
        return out + "\n" + _comment(f"This plan has no script steps for {place}.")
    for step in steps:
        reference = f" ({step['finding']['id']})" if step["finding"] else ""
        out += "\n" + _RULE + _comment(f"Step {step['number']} of {plan['total']}: {step['title']}{reference}") + _RULE
        out += step["sql"]
    return out


def render_text(plan: dict) -> str:
    """A plain checklist to paste into a ticket or a chat."""
    out = [f"Fix plan for {plan['packages']} ({plan['label']})", f"Report: {plan['report_url']}", ""]
    for warning in plan["warnings"]:
        out.append(f"Note: {warning}")
    if plan["warnings"]:
        out.append("")
    for section in plan["sections"]:
        out += [section["title"], "-" * len(section["title"])]
        for step in section["steps"]:
            reference = f" (ref {step['finding']['id']})" if step["finding"] else ""
            place = f" [{ENVIRONMENTS[step['environment']]} script]" if step["environment"] else ""
            out.append(f"[ ] {step['number']}. {step['title']}{reference}{place}")
            if step["finding"]:
                out.append(f"      Where: {step['finding']['where']}")
            if step.get("prerequisite"):
                out.append(f"      Before this: {step['prerequisite']}")
            if step["action"]:
                out.append(f"      What to do: {step['action']}")
            out += [f"      Test: {test}" for test in step["tests"]]
            if step["closing"]:
                out.append(f"      To close it: {step['closing']}")
        out.append("")
    return "\n".join(out)


def _comment(text: str) -> str:
    wrapped = textwrap.wrap(" ".join(text.split()), width=76) or [""]
    return "".join(f"-- {line}\n" for line in wrapped)
