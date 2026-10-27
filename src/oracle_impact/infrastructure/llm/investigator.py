"""One bounded tool-calling investigator.

The loop stops on a final message, a budget breach, a timeout, or a provider
error. Deterministic findings are never replaced by this component.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from oracle_impact.analysis.blind_spots import SYNONYM, BlindSpot
from oracle_impact.analysis.identifiers import is_identifier
from oracle_impact.infrastructure.evidence.validator import validate_findings
from oracle_impact.infrastructure.llm.blind_spot_tools import (
    BlindSpotContext,
    blind_spot_tool_schemas,
    dispatch_blind_spot_tool,
)
from oracle_impact.infrastructure.llm.live_tools import (
    LiveToolContext,
    RequiredRead,
    dispatch_live_tool,
    live_tool_schemas,
    outstanding_reads,
    suggested_calls,
)
from oracle_impact.infrastructure.llm.plain_text import simplify_change, simplify_test, simplify_unknown
from oracle_impact.infrastructure.llm.provider import BudgetExceeded, LlmProvider
from oracle_impact.infrastructure.llm.tools import TOOL_SCHEMAS, ToolContext, dispatch_tool
from oracle_impact.infrastructure.oracle.gateway import OracleAccessError
from oracle_impact.models.domain import Snapshot
from oracle_impact.models.report import Finding

_MAX_CASE_ITEMS = 12
_MAX_FINISH_REJECTIONS = 3
_CHECKLIST_SHOWN = 12
_SUMMARY_LIMIT = 800

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

_GATHER_SYSTEM = """You choose the read order for an Oracle change review. You do not decide the outcome.
Deterministic checks score what you collect and choose the deployment recommendation. Nothing you write changes it.
Every tool makes one read-only data dictionary read. Results are short summaries, not source.
The user message lists the required reads. The last tool result of each turn shows how many remain and the next ones.
Pick the next unread item. Use env "all" when the same item is needed in every environment. Batch independent calls.
Before you call a tool, write one short sentence about what you are going to read.
Finish only when no required reads remain. Then reply with two or three plain sentences about what you read. Do not say whether the change is safe.
"""

_BLIND_SPOT_SYSTEM = """You look into parts of an Oracle change that the automatic checks could not resolve.
You do not decide the outcome. Your answers are shown as unverified leads and never change the deployment recommendation.
Each question has an id such as BS-1. Use the tools to read the lines that build the SQL, to read where a synonym points, or to check whether a named object exists.
Only names that appear in the cited source or in a synonym read can be checked.
Then return one JSON object:
{"answers":[{"question_id":"BS-1","likely_targets":["OWNER.NAME"],"explanation":"","evidence_lines":[],"test_case":"","unknowns":""}]}
likely_targets: objects the statement or synonym can reach, written OWNER.NAME. Use an empty list when you cannot tell.
explanation: two short sentences at most, in everyday words. Say "builds SQL while it runs" instead of "dynamic SQL".
evidence_lines: Test line numbers of the package body that support the answer.
test_case: one step a tester can run in Test to confirm the answer.
unknowns: one short sentence about what you could not see, or an empty string.
Write one answer for every question. Do not say whether the change is safe.
Before you call a tool, write one short sentence about what you are going to read. Do not include source text in that sentence.
"""
_MAX_TARGETS = 6


@dataclass
class GatherResult:
    tool_log: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    summary: str = ""
    remaining: list[RequiredRead] = field(default_factory=list)


class Investigator:
    def __init__(
        self,
        provider: LlmProvider,
        max_calls_per_package: int,
        max_calls_per_run: int,
        timeout_seconds: int,
        token_budget: int,
        max_depth: int,
        gather_token_budget: int | None = None,
        gather_max_calls_per_run: int | None = None,
    ) -> None:
        self.provider = provider
        self.max_calls_per_package = max_calls_per_package
        self.max_calls_per_run = max_calls_per_run
        self.timeout_seconds = timeout_seconds
        self.token_budget = token_budget
        self.max_depth = max_depth
        self.gather_token_budget = gather_token_budget if gather_token_budget is not None else token_budget
        self.gather_max_calls_per_run = gather_max_calls_per_run if gather_max_calls_per_run is not None else max_calls_per_run

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
            self.gather_token_budget,
            self.gather_max_calls_per_run,
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

    def gather(self, context: LiveToolContext, on_progress=None) -> GatherResult:
        """Let the model choose the read order until the required-evidence checklist is empty.

        The loop collects reads only. It produces no findings and no recommendation.
        """
        result = GatherResult()
        tools = live_tool_schemas(context.environments)
        remaining = outstanding_reads(context)
        messages = [
            {"role": "system", "content": _GATHER_SYSTEM},
            {
                "role": "user",
                "content": "Selected packages: "
                + ", ".join(package.qualified for package in context.packages)
                + ".\nConnected environments: "
                + ", ".join(context.environments)
                + ".\nRequired reads so far. More appear as dependency reads reveal objects:\n"
                + json.dumps(_checklist(remaining, context.environments)),
            },
        ]
        started = time.monotonic()
        tokens = 0
        calls = 0
        rejections = 0
        per_package: dict[str, int] = {}

        def note(message: str, kind: str = "status") -> None:
            if on_progress is not None:
                on_progress(message, kind)

        try:
            note("The investigator is choosing what to read first.")
            while True:
                if time.monotonic() - started > self.timeout_seconds:
                    raise BudgetExceeded("Investigation timed out.")
                if tokens > self.gather_token_budget:
                    raise BudgetExceeded("Investigation token budget was exhausted.")
                response = self.provider.complete(messages, tools)
                tokens += response.prompt_tokens + response.completion_tokens
                thought = _visible_thought(response.reasoning or response.content)
                if response.tool_calls:
                    if thought:
                        note(thought, "thought")
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content or "",
                            "tool_calls": [
                                {
                                    "id": call.call_id or f"call-{calls + index + 1}",
                                    "type": "function",
                                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                                }
                                for index, call in enumerate(response.tool_calls)
                            ],
                        }
                    )
                    replies = []
                    for call in response.tool_calls:
                        calls += 1
                        selected = _selected_target(call.arguments, context)
                        if selected:
                            per_package[selected] = per_package.get(selected, 0) + 1
                            if per_package[selected] > self.max_calls_per_package:
                                raise BudgetExceeded("Per-package tool-call limit was reached.")
                        if calls > self.gather_max_calls_per_run:
                            raise BudgetExceeded("Run tool-call limit was reached.")
                        note(_tool_progress(call), "action")
                        payload = dispatch_live_tool(call.name, call.arguments or {}, context)
                        result.tool_log.append(
                            {
                                "tool": call.name,
                                "target": _tool_target(call),
                                "ok": "error" not in payload,
                                "result_bytes": len(json.dumps(payload)),
                                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                            }
                        )
                        replies.append((call.call_id or f"call-{calls}", payload))
                    remaining = outstanding_reads(context)
                    replies[-1][1]["checklist"] = _checklist(remaining, context.environments)
                    for call_id, payload in replies:
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)})
                    note(
                        f"{len(remaining)} required read{'s' if len(remaining) != 1 else ''} remain."
                        if remaining
                        else "Every required read has been collected.",
                    )
                    continue
                remaining = outstanding_reads(context)
                if remaining and rejections < _MAX_FINISH_REJECTIONS:
                    rejections += 1
                    note("The investigator tried to finish early. Sending back the reads that are still required.")
                    messages.append({"role": "assistant", "content": response.content or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": "Required reads are still missing. Read them with the tools before you finish:\n"
                            + json.dumps(_checklist(remaining, context.environments)),
                        }
                    )
                    continue
                result.summary = _agent_summary(response.content or "")
                break
        except BudgetExceeded as exc:
            result.limitations.append(str(exc) + " The report was built from the reads collected so far.")
        except OracleAccessError:
            raise
        except Exception:
            result.limitations.append("The investigator was unavailable. The report was built from the reads collected so far.")
        result.remaining = outstanding_reads(context)
        if result.remaining and not result.limitations:
            result.limitations.append("The investigator finished before every required read was collected.")
        return result

    def explore_blind_spots(
        self, spots: list[BlindSpot], context: BlindSpotContext, max_calls: int, on_progress=None
    ) -> tuple[list[Finding], list[dict], list[str]]:
        """Ask one narrow question per blind spot. Answers become potential notes that never block or clear deployment."""
        tool_log: list[dict] = []
        if not spots:
            return [], tool_log, []
        messages = [
            {"role": "system", "content": _BLIND_SPOT_SYSTEM},
            {"role": "user", "content": "Questions:\n" + "\n".join(spot.question() for spot in spots)},
        ]
        tools = blind_spot_tool_schemas()
        started = time.monotonic()
        tokens = 0
        calls = 0

        def note(message: str, kind: str = "status") -> None:
            if on_progress is not None:
                on_progress(message, kind)

        try:
            note(f"The investigator is looking into {len(spots)} item{'s' if len(spots) != 1 else ''} the checks could not resolve.")
            while True:
                if time.monotonic() - started > self.timeout_seconds:
                    raise BudgetExceeded("Blind-spot questions timed out.")
                if tokens > self.token_budget:
                    raise BudgetExceeded("Blind-spot token budget was exhausted.")
                response = self.provider.complete(messages, tools)
                tokens += response.prompt_tokens + response.completion_tokens
                if not response.tool_calls:
                    break
                thought = _visible_thought(response.reasoning or response.content)
                if thought:
                    note(thought, "thought")
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [
                            {
                                "id": call.call_id or f"bs-{calls + index + 1}",
                                "type": "function",
                                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                            }
                            for index, call in enumerate(response.tool_calls)
                        ],
                    }
                )
                for call in response.tool_calls:
                    calls += 1
                    if calls > max_calls:
                        raise BudgetExceeded("Blind-spot tool-call limit was reached.")
                    note(_tool_progress(call), "action")
                    payload = dispatch_blind_spot_tool(call.name, call.arguments or {}, context)
                    tool_log.append(
                        {
                            "tool": call.name,
                            "target": _tool_target(call),
                            "ok": "error" not in payload,
                            "result_bytes": len(json.dumps(payload)),
                            "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                        }
                    )
                    messages.append({"role": "tool", "tool_call_id": call.call_id or f"bs-{calls}", "content": json.dumps(payload)})
        except BudgetExceeded as exc:
            return [], tool_log, [str(exc) + " The items the checks could not resolve stay unresolved."]
        except Exception:
            return [], tool_log, ["The investigator was unavailable for blind-spot questions. Those items stay unresolved."]
        drafted, unanswered = _blind_spot_findings(response.content or "", spots, context)
        limitations = []
        if unanswered:
            limitations.append(
                "The AI did not answer blind-spot question" + ("s " if len(unanswered) > 1 else " ") + ", ".join(unanswered) + ". Those items stay unresolved."
            )
        return validate_findings(drafted, context.snapshot), tool_log, limitations


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


def _checklist(remaining: list[RequiredRead], environments: list[str]) -> dict:
    return {"remaining": len(remaining), "next": suggested_calls(remaining, environments)[:_CHECKLIST_SHOWN]}


def _agent_summary(content: str) -> str:
    cleaned = " ".join(content.split())
    if not cleaned or cleaned[0] in "{[":
        return ""
    if len(cleaned) > _SUMMARY_LIMIT:
        cleaned = cleaned[: _SUMMARY_LIMIT - 3].rstrip() + "..."
    return cleaned


def _selected_target(arguments: dict, context: LiveToolContext) -> str | None:
    arguments = arguments or {}
    owner = str(arguments.get("owner", "")).upper()
    name = str(arguments.get("package") or arguments.get("object_name") or arguments.get("name") or "").upper()
    return f"{owner}.{name}" if context.is_selected(owner, name) else None


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
    place = {
        "test": "Test",
        "production": "Production",
        "all": "every connected environment",
    }.get(env, "the database")
    callers = arguments.get("direction") == "referenced_by"
    labels = {
        "get_package_source": f"Reading the package source for {target} in {place}.",
        "get_object_source": f"Reading {target} in {place}.",
        "get_dependencies": f"Checking {'what calls' if callers else 'what'} {target}{'' if callers else ' depends on'} in {place}.",
        "get_table_metadata": f"Reading the table definition for {target} in {place}.",
        "get_procedure_arguments": f"Reading the arguments of {target} in {place}.",
        "get_arguments": f"Reading the arguments of {target} in {place}.",
        "get_object_status": f"Checking whether {target} is valid in {place}.",
        "get_grants": f"Reading the grants on {target} in {place}.",
        "get_compile_errors": f"Reading compile errors for {target} in {place}.",
        "read_source_lines": f"Reading lines {arguments.get('start_line')} to {arguments.get('end_line')} of {target} in {place}.",
        "read_synonym": f"Reading where synonym {target} points in {place}.",
        "check_object": f"Checking whether {target} exists in {place}.",
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


def _blind_spot_findings(content: str, spots: list[BlindSpot], context: BlindSpotContext) -> tuple[list[Finding], list[str]]:
    payload = _json_object(content) or {}
    by_id = {spot.id: spot for spot in spots}
    answered: dict[str, Finding] = {}
    for raw in payload.get("answers") or []:
        if not isinstance(raw, dict):
            continue
        spot = by_id.get(str(raw.get("question_id") or ""))
        if spot is None or spot.id in answered:
            continue
        source = context.snapshot.text("test", spot.owner, spot.name, "PACKAGE BODY").splitlines()
        lines = []
        for value in raw.get("evidence_lines") or []:
            try:
                line = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= line <= len(source) and line not in lines:
                lines.append(line)
        if not lines:
            lines = list(spot.lines) or _mentions(source, spot.synonym_name)
        targets = _targets(raw.get("likely_targets"), spot.owner)
        explanation = _sentence(str(raw.get("explanation") or ""))
        tests = [_sentence(item) for item in _text_items(raw.get("test_case"))][:1]
        unknowns = [_sentence(item) for item in _text_items(raw.get("unknowns"))][:1]
        attributes = {
            "blind_spot": spot.kind,
            "question_id": spot.id,
            "subject": spot.subject,
            "likely_targets": targets,
            "checked": _checked(spot, targets, context),
            "unknowns": unknowns,
            "blocks_eligibility": False,
        }
        if spot.anchor_id:
            attributes["review_of"] = spot.anchor_id
        answered[spot.id] = Finding(
            id=f"AI-{spot.id}",
            changed_object=f"{spot.owner}.{spot.name}",
            object_owner=spot.owner,
            object_name=spot.name,
            object_type="PACKAGE BODY",
            source_database="test",
            changed_lines_test=lines[:12],
            change_category="dependency" if spot.kind == SYNONYM else "dynamic_sql",
            affected_object=targets[0] if targets else None,
            evidence=explanation or "The AI gave no explanation.",
            severity="low",
            confidence="low",
            status="potential",
            explanation=explanation or "The AI gave no explanation.",
            recommended_action="Confirm this lead with the suggested test. It does not change the deployment recommendation.",
            proposed_tests=tests,
            attributes=attributes,
            origin="investigator",
        )
    unanswered = [spot.id for spot in spots if spot.id not in answered]
    return [answered[spot.id] for spot in spots if spot.id in answered], unanswered


def _targets(value: object, default_owner: str) -> list[str]:
    targets = []
    for item in _text_items(value):
        owner, _, name = item.upper().strip().rpartition(".")
        owner = owner or default_owner
        qualified = f"{owner}.{name}"
        if is_identifier(owner) and is_identifier(name) and qualified not in targets:
            targets.append(qualified)
    return targets[:_MAX_TARGETS]


def _mentions(source: list[str], name: str) -> list[int]:
    if not name:
        return []
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.I)
    return [number for number, text in enumerate(source, start=1) if pattern.search(text)][:3]


def _checked(spot: BlindSpot, targets: list[str], context: BlindSpotContext) -> list[dict]:
    """Results of the reads the investigator made for this question, as recorded by the tools."""
    wanted = {tuple(target.split(".", 1)) for target in targets}
    if spot.kind == SYNONYM:
        wanted.add((spot.synonym_owner, spot.synonym_name))
    return [
        {"environment": env, "object": f"{owner}.{name}", "result": result}
        for (env, owner, name), result in sorted(context.facts.items())
        if (owner, name) in wanted
    ]


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
