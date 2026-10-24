"""One bounded tool-calling investigator.

The loop stops on a final message, a budget breach, a timeout, or a provider
error. Deterministic findings are never replaced by this component.
"""

import json
import re
import time
from datetime import datetime, timezone

from oracle_impact.infrastructure.evidence.validator import validate_findings
from oracle_impact.infrastructure.llm.plain_text import simplify_change, simplify_test, simplify_unknown
from oracle_impact.infrastructure.llm.provider import BudgetExceeded, LlmProvider
from oracle_impact.infrastructure.llm.tools import TOOL_SCHEMAS, ToolContext, dispatch_tool
from oracle_impact.models.domain import Snapshot
from oracle_impact.models.report import Finding

_MAX_CASE_ITEMS = 12

_SYSTEM = """You explain Oracle findings for a developer who did not write the package.
The deployment recommendation is already decided. Do not approve it and do not restate the finding in abstract words.
Use the tools to read the changed procedure and its caller first.
Then return one JSON object:
{"reviews":[{"finding_id":"","object_owner":"","object_name":"","object_type":"PACKAGE BODY","changed_lines_test":[],"business_rule":"","test_cases":[],"unknowns":[],"evidence":""}]}
Copy finding_id, object_owner, object_name, and changed_lines_test from the case file.
Write one review for every finding_id in the case file.
test_cases and unknowns are JSON lists of strings, even when there is only one item.
business_rule: two short sentences at most. First say what the procedure now does differently. Then say who calls it and what they will see.
test_cases: one or two steps. Each step names the call to run and the input to use.
unknowns: one short sentence about anything you could not see, or an empty list.
Write for a tester who does not read PL/SQL. Keep each sentence under 20 words. Do not use semicolons.
Use everyday words. Say "saves" instead of "commits", "ignores the error" instead of "swallows the exception", and "builds SQL while it runs" instead of "dynamic SQL".
Do not use the words hypothesis, impact, functionality, inconsistencies, or "compiled against".
When you call a tool, first write one short sentence about what you are going to read. Do not include source text in that sentence.
"""


class Investigator:
    def __init__(
        self,
        provider: LlmProvider,
        max_calls_per_package: int,
        max_calls_per_run: int,
        timeout_seconds: int,
        token_budget: int,
        max_depth: int,
    ) -> None:
        self.provider = provider
        self.max_calls_per_package = max_calls_per_package
        self.max_calls_per_run = max_calls_per_run
        self.timeout_seconds = timeout_seconds
        self.token_budget = token_budget
        self.max_depth = max_depth

    def available_models(self) -> list[str]:
        lister = getattr(self.provider, "list_models", None)
        return list(lister()) if lister is not None else []

    def with_model(self, model: str) -> "Investigator":
        switch = getattr(self.provider, "with_model", None)
        if switch is None or not model:
            return self
        return Investigator(
            switch(model),
            self.max_calls_per_package,
            self.max_calls_per_run,
            self.timeout_seconds,
            self.token_budget,
            self.max_depth,
        )

    def investigate(
        self,
        snapshot: Snapshot,
        context: ToolContext,
        summaries: list[str],
        on_progress=None,
        case_file: str = "",
        requires_tools: bool = False,
        anchors: list[Finding] | None = None,
    ) -> tuple[list[Finding], list[dict], list[str]]:
        limitations: list[str] = []
        tool_log: list[dict] = []
        brief = case_file or " | ".join(summaries[:12])
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": "Selected packages: "
                + ", ".join(snapshot.packages)
                + ".\nDeterministic case file:\n"
                + brief
                + "\nRead the changed procedure and any named caller, then write the reviews.",
            },
        ]
        started = time.monotonic()
        tokens = 0
        calls = 0
        per_package = {package: 0 for package in snapshot.packages}
        def note(message: str, kind: str = "status") -> None:
            if on_progress is not None:
                on_progress(message, kind)

        tool_retry_used = False
        try:
            note("The investigator is deciding what to read.", "status")
            while True:
                if time.monotonic() - started > self.timeout_seconds:
                    raise BudgetExceeded("Investigation timed out.")
                if tokens > self.token_budget:
                    raise BudgetExceeded("Investigation token budget was exhausted.")
                response = self.provider.complete(messages, TOOL_SCHEMAS)
                tokens += response.prompt_tokens + response.completion_tokens
                thought = _visible_thought(response.reasoning or response.content)
                if thought:
                    note(thought, "thought")
                if response.tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content or "",
                            "tool_calls": [
                                {
                                    "id": call.call_id or f"call-{calls}",
                                    "type": "function",
                                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                                }
                                for call in response.tool_calls
                            ],
                        }
                    )
                    for call in response.tool_calls:
                        calls += 1
                        package = _package_for_call(call.arguments, snapshot)
                        if package:
                            per_package[package] = per_package.get(package, 0) + 1
                            if per_package[package] > self.max_calls_per_package:
                                raise BudgetExceeded("Per-package tool-call limit was reached.")
                        if calls > self.max_calls_per_run:
                            raise BudgetExceeded("Run tool-call limit was reached.")
                        note(_tool_progress(call), "action")
                        payload = dispatch_tool(call.name, call.arguments, context)
                        tool_log.append(
                            {
                                "tool": call.name,
                                "target": _tool_target(call),
                                "ok": "error" not in payload,
                                "result_bytes": len(json.dumps(payload)),
                                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.call_id or f"call-{calls}",
                                "content": json.dumps(payload),
                            }
                        )
                    note("The model is using what it just read.", "status")
                    continue
                if requires_tools and calls == 0 and not tool_retry_used:
                    tool_retry_used = True
                    note("The model answered before reading the source. Asking it to read the caller and the changed procedure.", "status")
                    messages.append({"role": "assistant", "content": response.content or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": "Read the caller and the changed procedure with the tools, then answer. Do not answer from memory.",
                        }
                    )
                    continue
                note("Checking the investigator's citations against the snapshot.")
                drafted, unreviewed = _present_reviews(
                    _parse_findings(response.content or "", anchors or []), anchors or []
                )
                limitations.extend(_unreviewed_notes(unreviewed, brief))
                return validate_findings(drafted, snapshot), tool_log, limitations
        except BudgetExceeded as exc:
            limitations.append(str(exc) + " Deterministic findings were kept.")
            return [], tool_log, limitations
        except Exception:
            limitations.append("The investigator was unavailable. Deterministic findings were kept.")
            return [], tool_log, limitations


def build_case_file(findings: list[Finding]) -> tuple[str, bool]:
    """Questions for the investigator, plus whether a blocker requires a tool read."""
    lines = []
    requires_tools = False
    for finding in findings:
        if finding.origin == "investigator":
            continue
        important = finding.severity.value in {"critical", "high"} or finding.change_category in {
            "interface",
            "transaction",
            "data",
            "dynamic_sql",
        }
        if not important:
            continue
        if finding.severity.value in {"critical", "high"}:
            requires_tools = True
        path = ", ".join(finding.dependency_path) or "none recorded"
        lines.append(
            f"- finding_id {finding.id}; object {finding.object_owner}.{finding.object_name}; "
            f"type {finding.object_type}; lines {finding.changed_lines_test or [1]}; "
            f"{finding.explanation} Caller path: {path}."
        )
    if not lines:
        return "No critical or high blocker. Read the changed package and describe the business effect only.", False
    return "\n".join(lines[:_MAX_CASE_ITEMS]), requires_tools


def _unreviewed_notes(unreviewed: list[str], brief: str) -> list[str]:
    if not unreviewed:
        return []
    sent = re.findall(r"finding_id (\S+);", brief)
    skipped = [item for item in unreviewed if sent and item not in sent]
    missed = [item for item in unreviewed if item not in skipped]
    notes = []
    if skipped:
        reason = f" The AI is given at most {_MAX_CASE_ITEMS} issues per run." if len(sent) >= _MAX_CASE_ITEMS else ""
        notes.append(
            "The AI was not asked about " + ", ".join(skipped) + "." + reason + " Those findings are deterministic only."
        )
    if missed:
        notes.append(
            "The investigator did not return a review for " + ", ".join(missed) + ". Those findings are deterministic only."
        )
    return notes


def _tool_target(call) -> str:
    arguments = call.arguments or {}
    env = str(arguments.get("env", "")).lower()
    owner = str(arguments.get("owner", "")).upper()
    name = str(
        arguments.get("package")
        or arguments.get("object_name")
        or arguments.get("name")
        or arguments.get("table")
        or ""
    ).upper()
    target = f"{owner}.{name}" if owner and name else "allowlisted object"
    return f"{env} {target}".strip()


def _visible_thought(text: str | None) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned or cleaned[0] in "{[":
        return ""
    lowered = cleaned.lower()
    if "package body" in lowered or "create or replace" in lowered:
        return ""
    if len(cleaned) > 420:
        cleaned = cleaned[:417].rstrip() + "..."
    return cleaned


def _tool_progress(call) -> str:
    arguments = call.arguments or {}
    env = str(arguments.get("env", "")).lower()
    owner = str(arguments.get("owner", "")).upper()
    name = str(
        arguments.get("package")
        or arguments.get("object_name")
        or arguments.get("name")
        or arguments.get("table")
        or ""
    ).upper()
    target = f"{owner}.{name}" if owner and name else "an allowlisted object"
    place = "Test" if env == "test" else "Production" if env == "production" else "the database"
    labels = {
        "get_package_source": f"Reading the package source for {target} in {place}.",
        "get_object_source": f"Reading {target} in {place}.",
        "get_dependencies": f"Checking what {target} depends on in {place}.",
        "get_table_metadata": f"Reading the table definition for {target}.",
        "get_procedure_arguments": f"Reading the arguments of {target} in {place}.",
    }
    return labels.get(call.name, f"Using {call.name} on {target} in {place}.")


def _package_for_call(arguments: dict, snapshot: Snapshot) -> str | None:
    owner = str(arguments.get("owner", "")).upper()
    name = str(arguments.get("package") or arguments.get("object_name") or arguments.get("name") or "").upper()
    qualified = f"{owner}.{name}"
    if qualified in snapshot.packages:
        return qualified
    return snapshot.packages[0] if snapshot.packages else None


def _parse_findings(content: str, anchors: list[Finding]) -> list[Finding]:
    payload = _json_object(content)
    if not payload:
        return []
    if payload.get("reviews"):
        return _reviews_to_findings(payload["reviews"], anchors)
    findings = []
    for index, raw in enumerate(payload.get("findings") or []):
        try:
            findings.append(
                Finding(
                    id=f"AI-{index + 1}",
                    changed_object=f"{raw.get('object_owner', '')}.{raw.get('object_name', '')}",
                    object_owner=str(raw.get("object_owner", "")),
                    object_name=str(raw.get("object_name", "")),
                    object_type=str(raw.get("object_type") or "PACKAGE BODY"),
                    source_database="test",
                    changed_lines_test=[int(line) for line in raw.get("changed_lines_test") or []],
                    change_category=str(raw.get("change_category") or "business_logic"),
                    affected_object=raw.get("affected_object"),
                    dependency_path=list(raw.get("dependency_path") or []),
                    evidence=str(raw.get("evidence") or ""),
                    severity=raw.get("severity") or "low",
                    confidence=raw.get("confidence") or "low",
                    status=raw.get("status") or "potential",
                    explanation=str(raw.get("explanation") or ""),
                    recommended_action=str(raw.get("recommended_action") or "Review the cited source."),
                    origin="investigator",
                )
            )
        except (TypeError, ValueError):
            continue
    return findings


def _json_object(content: str) -> dict | None:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        payload = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _reviews_to_findings(reviews: list, anchors: list[Finding]) -> list[Finding]:
    by_id = {finding.id: finding for finding in anchors}
    findings = []
    for index, raw in enumerate(reviews):
        if not isinstance(raw, dict):
            continue
        review_of = str(raw.get("finding_id") or "")
        anchor = by_id.get(review_of)
        owner = str(raw.get("object_owner") or (anchor.object_owner if anchor else ""))
        name = str(raw.get("object_name") or (anchor.object_name if anchor else ""))
        object_type = str(raw.get("object_type") or (anchor.object_type if anchor else "PACKAGE BODY"))
        try:
            lines = [int(line) for line in raw.get("changed_lines_test") or []]
        except (TypeError, ValueError):
            lines = []
        if not lines and anchor and anchor.changed_lines_test:
            lines = list(anchor.changed_lines_test)
        tests = [_sentence(item) for item in _text_items(raw.get("test_cases"))]
        unknowns = [_sentence(item) for item in _text_items(raw.get("unknowns"))]
        business = _sentence(str(raw.get("business_rule") or ""))
        if not business:
            continue
        findings.append(
            Finding(
                id=f"AI-{index + 1}",
                changed_object=f"{owner}.{name}",
                object_owner=owner,
                object_name=name,
                object_type=object_type,
                source_database="test",
                changed_lines_test=lines,
                change_category="business_logic",
                affected_object=anchor.affected_object if anchor else None,
                dependency_path=list(anchor.dependency_path) if anchor else [],
                evidence=_sentence(str(raw.get("evidence") or business)),
                severity="low",
                confidence="low",
                status="potential",
                explanation=business,
                recommended_action="Run the test below. This note does not change the deployment recommendation.",
                proposed_tests=tests[:2],
                attributes={
                    "review_of": review_of,
                    "unknowns": unknowns[:2],
                    "blocks_eligibility": False,
                },
                origin="investigator",
            )
        )
    return findings


def _text_items(value: object) -> list[str]:
    """Models sometimes return a single string where the schema asks for a list."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _sentence(text: str) -> str:
    cleaned = " ".join(text.split())
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


_VAGUE = ("can impact", "can lead", "may impact", "functionality", "inconsistencies", "hypothesis")


def _important_anchors(anchors: list[Finding]) -> list[Finding]:
    return [
        finding
        for finding in anchors
        if finding.origin != "investigator"
        and finding.change_category != "format"
        and finding.severity.value != "informational"
    ]


def _present_reviews(findings: list[Finding], anchors: list[Finding]) -> tuple[list[Finding], list[str]]:
    """Bind model reviews to their findings. Never write a note the model did not produce."""
    important = _important_anchors(anchors)
    by_id = {finding.id: finding for finding in important}
    presented: list[Finding] = []
    used: set[str] = set()
    for index, finding in enumerate(findings):
        review_of = str(finding.attributes.get("review_of") or "")
        anchor = by_id.get(review_of)
        if anchor is None and index < len(important):
            anchor = important[index]
        if anchor is not None:
            finding = _bind(finding, anchor)
            used.add(anchor.id)
        finding.explanation = _easy_text(finding, anchor)
        finding.attributes["blocks_eligibility"] = False
        presented.append(finding)
    unreviewed = [anchor.id for anchor in important if anchor.id not in used]
    return presented, unreviewed


def _bind(finding: Finding, anchor: Finding) -> Finding:
    finding.object_owner = anchor.object_owner
    finding.object_name = anchor.object_name
    finding.object_type = anchor.object_type
    finding.changed_object = f"{anchor.object_owner}.{anchor.object_name}"
    if anchor.changed_lines_test:
        finding.changed_lines_test = list(anchor.changed_lines_test)
    finding.affected_object = anchor.affected_object
    finding.dependency_path = list(anchor.dependency_path)
    finding.attributes["review_of"] = anchor.id
    return finding


def _easy_text(finding: Finding, anchor: Finding | None) -> str:
    source = anchor or finding
    model_text = finding.explanation
    vague = not model_text or any(phrase in model_text.lower() for phrase in _VAGUE)
    what = simplify_change(source, _sentence(source.explanation if vague else model_text))
    tests = list(finding.proposed_tests)
    if not tests and anchor is not None:
        tests = [_default_test(anchor)]
    unknowns = [str(item) for item in finding.attributes.get("unknowns") or []]
    if not unknowns:
        unknowns = ["Programs outside the database."]
    finding.proposed_tests = [simplify_test(source, _sentence(item)) for item in tests[:2]]
    finding.attributes["unknowns"] = [simplify_unknown(_sentence(item)) for item in unknowns[:2]]
    return _readable_review(what, finding.proposed_tests, finding.attributes["unknowns"])


def _caller_label(anchor: Finding) -> str:
    if anchor.dependency_path:
        return anchor.dependency_path[0].split("->")[0].strip()
    return anchor.affected_object or anchor.changed_object


def _default_test(anchor: Finding) -> str:
    caller = _caller_label(anchor)
    routine = anchor.object_name or anchor.changed_object
    if anchor.change_category == "interface":
        return f"Call {caller} with the arguments Production uses today."
    if anchor.change_category == "transaction":
        return f"Run {routine} and make the statement after the save fail. See which changes remain."
    if anchor.change_category == "data":
        target = anchor.affected_object or "the table it changes"
        return f"Run {routine} and look at {target}."
    return f"Run {routine} with a Production example."


def _readable_review(business: str, tests: list[str], unknowns: list[str]) -> str:
    parts = [f"What changes: {business}"]
    if tests:
        parts.append("What to do: " + " ".join(tests[:2]))
    if unknowns:
        parts.append("Not checked: " + " ".join(unknowns[:2]))
    return "\n".join(parts)
