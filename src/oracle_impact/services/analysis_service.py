"""Orchestrate one analysis run. Controllers call this and do not contain analysis rules."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from oracle_impact.analysis.annotate import annotate
from oracle_impact.analysis.blind_spots import collect_blind_spots
from oracle_impact.analysis.compilation import collect_compilation, compilation_findings
from oracle_impact.analysis.coverage import build_coverage, edge_records
from oracle_impact.analysis.dependencies import discover_dependencies
from oracle_impact.analysis.diff_engine import build_object_diff
from oracle_impact.analysis.environments import environment_name
from oracle_impact.analysis.existence import existence_candidates, existence_findings
from oracle_impact.analysis.grants import compare_grants
from oracle_impact.analysis.impacts import build_deterministic_findings
from oracle_impact.analysis.plsql_scanner import scan_package
from oracle_impact.analysis.signatures import compare_package_signatures
from oracle_impact.analysis.transactions import build_profiles, modified_statements, performance_assessment
from oracle_impact.config import Settings
from oracle_impact.infrastructure.llm.blind_spot_tools import BlindSpotContext
from oracle_impact.infrastructure.llm.investigator import build_case_file
from oracle_impact.infrastructure.llm.live_tools import LiveToolContext, RequiredRead
from oracle_impact.infrastructure.llm.tools import ToolContext
from oracle_impact.infrastructure.oracle.gateway import OracleAccessError, utc_now
from oracle_impact.infrastructure.oracle.recorded import RecordedReads
from oracle_impact.infrastructure.storage.run_store import RunStore
from oracle_impact.models.assessment import (
    CompilationSummary,
    EnvironmentObject,
    ExternalCheck,
    ObjectState,
)
from oracle_impact.models.domain import CapturedObject, PackageRef, Snapshot
from oracle_impact.models.enums import Severity
from oracle_impact.models.report import (
    ChangeRow,
    DependencyRow,
    EnvironmentInfo,
    Finding,
    Report,
    ReportStatistics,
)
from oracle_impact.services.readiness import DEFAULT_RULES, evaluate_policy
from oracle_impact.services.recommendation import decide, label_for
from oracle_impact.services.snapshot_service import SnapshotService

logger = logging.getLogger(__name__)
REPORT_VERSION = "1.1"
APPLICATION_SOURCE_NOT_CONFIGURED = (
    "No application source index is configured, so callers in application code (for example Java, .NET, "
    "reports, or scripts kept in TFS) were not searched. Database callers come from ALL_DEPENDENCIES only."
)


DETERMINISTIC, AGENTIC = "deterministic", "agentic"
RUN_MODES = (DETERMINISTIC, AGENTIC)
_UNREAD_LISTED = 10


class UnknownModel(ValueError):
    """The requested model is not served by the configured endpoint."""


class AgenticUnavailable(ValueError):
    """Agentic runs need the investigator, so they are refused while LLM egress is off."""


@dataclass
class _Readers:
    """Gateways the scorers read from: live connections, or replayed agentic reads."""

    test: object
    production: object
    replayed: bool = False

    @property
    def environments(self) -> list[str]:
        return ["test", "production"]

    def gateways(self) -> dict:
        return {"test": self.test, "production": self.production}


@dataclass
class _Collected:
    """Deterministic evidence available to the review step."""

    packages: list[PackageRef]
    findings: list[Finding]
    prod_out: object
    test_out: object
    prod_in: object
    tables: dict


@dataclass
class _Review:
    extra: list[Finding] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    ai_used: bool = False
    model_name: str | None = None
    analyzer: str = ""
    agent_summary: str = ""


class AnalysisService:
    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        test_gateway,
        production_gateway,
        investigator=None,
        registry=None,
        rules=DEFAULT_RULES,
    ) -> None:
        self.settings = settings
        self.store = store
        self.test_gateway = test_gateway
        self.production_gateway = production_gateway
        self.investigator = investigator
        self.registry = registry
        self.rules = rules
        self.snapshots = SnapshotService(test_gateway, production_gateway)

    def labels(self) -> dict[str, str]:
        return {"test": self.settings.test_oracle_label, "production": self.settings.prod_oracle_label}

    def available_models(self) -> list[str]:
        """Models the investigator endpoint serves, with the configured default first."""
        if not self.settings.llm_egress_enabled or self.investigator is None:
            return []
        try:
            served = self.investigator.available_models()
        except Exception:
            logger.warning("model list unavailable")
            served = []
        default = self.settings.llm_model
        ordered = [default] if default else []
        ordered.extend(model for model in served if model != default)
        return ordered

    def agentic_available(self) -> bool:
        return self.settings.llm_egress_enabled and self.investigator is not None

    def create_run(self, packages: list[PackageRef], model: str = "", mode: str = DETERMINISTIC) -> str:
        if mode not in RUN_MODES:
            raise ValueError("Unknown run mode.")
        if mode == AGENTIC and not self.agentic_available():
            raise AgenticUnavailable("Agent-guided analysis needs the AI investigator, and LLM egress is disabled.")
        if model and model not in self.available_models():
            raise UnknownModel(f"Model {model} is not served by the configured endpoint.")
        run_id = str(uuid4())
        self.store.create(run_id, [package.qualified for package in packages], model or self.settings.llm_model, mode)
        return run_id

    def execute(self, run_id: str) -> None:
        request = self.store.read_json(run_id, "request.json") or {}
        packages = [PackageRef(owner=item.split(".", 1)[0], name=item.split(".", 1)[1]) for item in request.get("packages", [])]
        chosen_model = request.get("model") or self.settings.llm_model
        try:
            if request.get("mode") == AGENTIC:
                self._execute_agentic(run_id, packages, chosen_model)
            else:
                self._execute_deterministic(run_id, packages, chosen_model)
        except (LookupError, AgenticUnavailable) as exc:
            self.store.write_status(run_id, "failed", str(exc), detail=str(exc))
        except OracleAccessError as exc:
            self.store.write_status(run_id, "failed", str(exc), detail=str(exc))
        except Exception:
            logger.exception("analysis failed run_id=%s", run_id)
            message = "The analysis failed before a report could be completed."
            self.store.write_status(run_id, "failed", message, detail=message)

    def _execute_deterministic(self, run_id: str, packages: list[PackageRef], chosen_model: str) -> None:
        names = ", ".join(package.qualified for package in packages)
        self._progress(
            run_id,
            "snapshotting",
            f"Reading Test and Production source for {names}.",
            [f"Selected packages: {names}."],
        )
        snapshot = self.snapshots.capture(packages)
        readers = _Readers(self.test_gateway, self.production_gateway)
        self._score(
            run_id,
            packages,
            snapshot,
            readers,
            lambda captured, collected: self._review(run_id, captured, collected, chosen_model),
            DETERMINISTIC,
        )

    def _execute_agentic(self, run_id: str, packages: list[PackageRef], chosen_model: str) -> None:
        """The investigator chooses the read order. The same deterministic scorers then judge what it collected."""
        if not self.agentic_available():
            raise AgenticUnavailable("Agent-guided analysis needs the AI investigator, and LLM egress is disabled.")
        model_name = chosen_model or "configured-model"
        investigator = self.investigator.with_model(chosen_model)
        names = ", ".join(package.qualified for package in packages)
        gateways = {"test": self.test_gateway, "production": self.production_gateway}
        context = LiveToolContext(gateways, packages, self.settings.max_dependency_depth)
        self._progress(
            run_id,
            "investigating",
            f"The investigator is choosing the read order for {names} with {model_name}.",
            [
                f"Selected packages: {names}.",
                "The investigator only chooses what to read. Deterministic checks score the reads and choose the recommendation.",
            ],
        )
        gathered = investigator.gather(context, on_progress=self._activity(run_id))
        unread = _unread_limitation(gathered.remaining)
        self._progress(
            run_id,
            "investigating",
            f"Agentic review finished after {len(gathered.tool_log)} tool calls.",
            [*gathered.limitations, unread] if unread else gathered.limitations or ["Every required read was collected."],
        )

        self._progress(run_id, "snapshotting", "Building the source snapshot from the investigator's reads.")
        readers = _Readers(context.replay("test"), context.replay("production"), replayed=True)
        snapshot = SnapshotService(readers.test, readers.production).capture(packages)
        _relabel_unread_packages(snapshot, packages, context.reads)
        for environment in context.unavailable:
            label = getattr(gateways[environment], "label", environment.upper())
            snapshot.limitations.append(
                f"The {label} environment could not be read. Its comparison is omitted from this report."
            )

        def review(captured: Snapshot, collected: _Collected) -> _Review:
            _attach_read_sources(captured, context.reads, gateways)
            leads, lead_log, lead_limitations = self._blind_spots(run_id, investigator, captured, collected, stage="validating")
            limitations = [*gathered.limitations, unread] if unread else list(gathered.limitations)
            return _Review(
                extra=leads,
                tool_log=[*gathered.tool_log, *lead_log],
                limitations=[*limitations, *lead_limitations],
                ai_used=True,
                model_name=model_name,
                analyzer=f"Agentic read order by the investigator ({model_name}). Recommendation from the deterministic engine.",
                agent_summary=gathered.summary,
            )

        self._score(run_id, packages, snapshot, readers, review, AGENTIC)

    def _score(
        self,
        run_id: str,
        packages: list[PackageRef],
        snapshot: Snapshot,
        readers: _Readers,
        review: Callable[[Snapshot, _Collected], _Review],
        mode: str,
    ) -> None:
        """Every deterministic check, the recommendation, and the report. Both run modes end here."""
        self.store.write_model(run_id, "snapshot.json", snapshot)
        self._progress(
            run_id,
            "snapshotting",
            f"Captured {sum(1 for item in snapshot.objects if item.found)} source objects.",
            _snapshot_lines(snapshot),
        )

        self._progress(run_id, "comparing", "Comparing specifications and bodies.")
        failures: dict[tuple[str, str, str], str] = {}
        diffs = self._diffs(snapshot, packages, failures)
        scans = self._scans(snapshot, packages, failures)
        self._progress(run_id, "comparing", "Code comparison finished.", _diff_lines(diffs))

        walk = "Scoring the dependency reads the investigator collected" if readers.replayed else "Walking callers and referenced objects"
        self._progress(
            run_id,
            "dependencies",
            "Reading dependencies and procedure signatures.",
            [f"{walk} to depth {self.settings.max_dependency_depth}."],
        )
        selected = {package.qualified for package in packages}
        prod_out = discover_dependencies(
            readers.production,
            [(package.owner, package.name, "PACKAGE BODY") for package in packages],
            "referenced",
            self.settings.max_dependency_depth,
        )
        test_out = discover_dependencies(
            readers.test,
            [(package.owner, package.name, "PACKAGE BODY") for package in packages],
            "referenced",
            self.settings.max_dependency_depth,
        )
        prod_in = discover_dependencies(
            readers.production,
            [(package.owner, package.name, "PACKAGE") for package in packages],
            "referenced_by",
            self.settings.max_dependency_depth,
        )
        test_in = discover_dependencies(
            readers.test,
            [(package.owner, package.name, "PACKAGE") for package in packages],
            "referenced_by",
            1,
        )
        signature_changes = []
        argument_gaps: set[tuple[str, str]] = set()
        for package in packages:
            callers = sorted(
                {
                    (edge.owner, edge.name)
                    for edge in prod_in.edges
                    if edge.referenced_name == package.name and edge.referenced_owner == package.owner
                }
            )
            test_args = readers.test.fetch_arguments(package.owner, package.name)
            prod_args = readers.production.fetch_arguments(package.owner, package.name)
            if test_args.inaccessible or prod_args.inaccessible:
                argument_gaps.add((package.owner, package.name))
            signature_changes.extend(
                compare_package_signatures(
                    package,
                    test_args.arguments,
                    prod_args.arguments,
                    test_args.inaccessible,
                    prod_args.inaccessible,
                    snapshot,
                    selected,
                    callers,
                )
            )
        grant_changes = []
        grant_notes: list[str] = []
        for package in packages:
            changes, notes = compare_grants(
                package,
                readers.test.fetch_grants(package.owner, package.name),
                readers.production.fetch_grants(package.owner, package.name),
            )
            grant_changes.extend(changes)
            grant_notes.extend(note for note in notes if note not in grant_notes)
        gaps = [
            f"{gap.environment} {gap.owner}.{gap.name} {gap.object_type}: {gap.reason}"
            for gap in (*prod_out.gaps, *test_out.gaps, *prod_in.gaps)
        ]
        drift = [
            f"{item.environment} {item.owner}.{item.name} {item.object_type}"
            for item in snapshot.objects
            if item.drift and item.environment in {"test", "production"}
        ]
        tables = self._tables(readers, test_out, prod_out)
        findings = build_deterministic_findings(
            run_id,
            snapshot,
            diffs,
            signature_changes,
            scans,
            lambda env, owner, table: tables.get(f"{env}:{owner}.{table}")
            or self._load_table(readers, env, owner, table, tables),
            gaps,
            drift,
            grant_changes,
        )
        findings.extend(
            existence_findings(
                run_id,
                existence_candidates([(package.owner, package.name) for package in packages], test_out.edges, prod_out.edges),
                readers.production,
                self.labels(),
                {item.affected_object for item in findings if item.attributes.get("missing_object") and item.affected_object},
            )
        )
        compilation = self._compilation(readers, packages, prod_in, test_in)
        findings.extend(compilation_findings(run_id, compilation, self.labels()))
        annotate(findings)

        self._progress(
            run_id,
            "dependencies",
            f"Deterministic checks produced {len(findings)} findings.",
            _dependency_lines(prod_out, test_out, prod_in, signature_changes, findings, gaps),
        )
        limitations = list(snapshot.limitations) + grant_notes
        outcome = review(snapshot, _Collected(packages, findings, prod_out, test_out, prod_in, tables))
        findings.extend(outcome.extra)
        limitations.extend(outcome.limitations)
        if prod_out.skipped_system_objects or test_out.skipped_system_objects:
            limitations.append("Oracle-supplied schemas were omitted from the dependency walk.")
        limitations.append(
            "Snapshots of Test and Production are not a single atomic point in time."
        )
        limitations.append(
            "Static inspection does not prove runtime behavior. Dynamic SQL, synonyms, and callers outside the database can remain unresolved."
        )

        from oracle_impact.analysis.action_scripts import apply_action_scripts
        from oracle_impact.analysis.test_snippets import apply_test_snippets

        apply_test_snippets(findings, snapshot)
        apply_action_scripts(findings, [*prod_in.edges, *test_in.edges], snapshot)
        recommendation, reasons, actions = decide(findings)
        policy = evaluate_policy(findings, self.rules)
        self._progress(
            run_id,
            "validating",
            "Checking evidence and choosing the deployment recommendation.",
            reasons or ["No additional reasons."],
        )
        report = self._report(
            run_id,
            snapshot,
            diffs,
            findings,
            prod_in.edges + test_out.edges + prod_out.edges,
            recommendation,
            reasons,
            actions,
            limitations,
            outcome,
            readers,
        )
        report.run_mode = mode
        report.agent_summary = outcome.agent_summary
        selected_pairs = [(package.owner, package.name) for package in packages]
        all_edges = list(prod_in.edges) + list(test_in.edges) + list(test_out.edges) + list(prod_out.edges)
        edges = edge_records(all_edges, selected_pairs, scans)
        report.report_version = REPORT_VERSION
        report.environments = self._environment_infos(snapshot)
        report.object_states = _object_states(snapshot, selected_pairs, diffs, compilation, self.labels())
        report.compilation = compilation
        report.dependency_edges = edges
        report.coverage = build_coverage(
            selected_pairs,
            snapshot,
            [env for env in readers.environments if any(item.environment == env for item in snapshot.objects)],
            failures,
            argument_gaps,
            [*prod_out.gaps, *test_out.gaps, *prod_in.gaps, *test_in.gaps],
            edges,
            scans,
            _changed_test_lines(diffs),
            prod_out.skipped_system_objects + test_out.skipped_system_objects + prod_in.skipped_system_objects,
            self.settings.max_dependency_depth,
            utc_now(),
        )
        report.transaction_profiles = build_profiles(selected_pairs, scans, diffs, findings)
        report.performance = performance_assessment(
            modified_statements(selected_pairs, scans, diffs), self.settings.performance_plan_source
        )
        report.policy = policy
        report.external_checks = [
            ExternalCheck(name="Application source impact", evaluated=False, reason=APPLICATION_SOURCE_NOT_CONFIGURED)
        ]
        if compilation.reason:
            report.limitations.append(compilation.reason)
        self.store.write_model(run_id, "report.json", report)
        self._record_versions(run_id, snapshot)
        self.store.write_json(run_id, "findings.json", [item.model_dump(mode="json") for item in findings])
        self.store.write_json(run_id, "tool_log.json", outcome.tool_log)
        self._progress(run_id, "completed", "Report ready.", [label_for(recommendation)])
        logger.info(
            "analysis completed run_id=%s mode=%s findings=%s recommendation=%s",
            run_id,
            mode,
            len(findings),
            recommendation.value,
        )

    def _review(self, run_id: str, snapshot: Snapshot, collected: _Collected, chosen_model: str) -> _Review:
        """Optional investigator notes on the deterministic findings of a dashboard run."""
        if not self.agentic_available():
            self._progress(
                run_id,
                "investigating",
                "AI investigator is off. The report uses deterministic checks only.",
                ["LLM egress is disabled. No package source was sent to a model."],
            )
            return _Review(
                limitations=["AI investigator did not run because LLM egress is disabled. This report is deterministic-only."]
            )
        model_name = chosen_model or "configured-model"
        investigator = self.investigator.with_model(chosen_model)
        self._progress(run_id, "investigating", f"AI investigator is reviewing the change set with {model_name}.")
        self._capture_callers(snapshot, collected.prod_in)
        context = self._tool_context(
            snapshot, collected.prod_out, collected.test_out, collected.prod_in, collected.tables, collected.packages
        )
        case_file, requires_tools = build_case_file(collected.findings)
        try:
            extra, tool_log, ai_limitations = investigator.investigate(
                snapshot,
                context,
                [finding.explanation for finding in collected.findings],
                on_progress=self._activity(run_id),
                case_file=case_file,
                requires_tools=requires_tools,
                anchors=collected.findings,
            )
        except Exception:
            logger.warning("investigator failed run_id=%s", run_id)
            extra, tool_log, ai_limitations = [], [], ["The investigator failed. Deterministic findings were kept."]
        leads, lead_log, lead_limitations = self._blind_spots(run_id, investigator, snapshot, collected)
        self._progress(run_id, "investigating", "AI investigator finished.")
        return _Review(
            extra=[*extra, *leads],
            tool_log=[*tool_log, *lead_log],
            limitations=[*ai_limitations, *lead_limitations],
            ai_used=True,
            model_name=model_name,
        )

    def _blind_spots(self, run_id: str, investigator, snapshot: Snapshot, collected: _Collected, stage: str = "investigating"):
        """Narrow questions about dynamic SQL and synonyms. Answers are potential leads only.

        Its reads go to the live gateways, never into an agentic run's recorded reads, so they cannot reach the scorers.
        """
        limit = self.settings.blind_spot_max_questions
        spots, left_out = collect_blind_spots(
            collected.findings, [*collected.test_out.edges, *collected.prod_out.edges], limit
        )
        limitations = []
        if left_out:
            limitations.append(
                f"{left_out} more item{'s' if left_out != 1 else ''} the checks could not resolve "
                f"{'were' if left_out != 1 else 'was'} not sent to the AI. The limit is {limit} per run."
            )
        if not spots:
            return [], [], limitations
        self._progress(
            run_id,
            stage,
            f"Asking the AI about {len(spots)} item{'s' if len(spots) != 1 else ''} the checks could not resolve.",
            [spot.subject for spot in spots],
        )
        context = BlindSpotContext(snapshot, {"test": self.test_gateway, "production": self.production_gateway}, spots)
        try:
            leads, tool_log, notes = investigator.explore_blind_spots(
                spots, context, self.settings.blind_spot_max_tool_calls, on_progress=self._activity(run_id, stage)
            )
        except Exception:
            logger.warning("blind-spot questions failed run_id=%s", run_id)
            leads, tool_log, notes = [], [], ["The blind-spot questions failed. Those items stay unresolved."]
        return leads, tool_log, [*limitations, *notes]

    def _activity(self, run_id: str, stage: str = "investigating"):
        def on_progress(message: str, kind: str = "status") -> None:
            self._progress(run_id, stage, message, [message], activity={"kind": kind, "text": message})

        return on_progress

    def _progress(
        self,
        run_id: str,
        status: str,
        detail: str,
        lines: list[str] | None = None,
        activity: dict | None = None,
    ) -> None:
        self.store.write_status(run_id, status, detail=detail, lines=lines, activity=activity)

    def _diffs(self, snapshot: Snapshot, packages: list[PackageRef], failures: dict):
        diffs = []
        for package in packages:
            for object_type in ("PACKAGE", "PACKAGE BODY"):
                test = snapshot.find("test", package.owner, package.name, object_type)
                prod = snapshot.find("production", package.owner, package.name, object_type)
                try:
                    diff = build_object_diff(
                        package.owner,
                        package.name,
                        object_type,
                        None if test is None else test.source,
                        None if prod is None else prod.source,
                        None if test is None else test.sha256,
                        None if prod is None else prod.sha256,
                    )
                except Exception:
                    logger.warning("source comparison failed object=%s.%s type=%s", package.owner, package.name, object_type)
                    failures[(package.owner, package.name, object_type)] = "The source comparison failed for this object."
                    continue
                diffs.append(diff)
        return diffs

    def _scans(self, snapshot: Snapshot, packages: list[PackageRef], failures: dict):
        scans = {}
        for package in packages:
            scanned = {}
            try:
                for environment in ("test", "production"):
                    source = snapshot.text(environment, package.owner, package.name, "PACKAGE BODY")
                    scanned[(environment, package.owner, package.name)] = scan_package(source)
            except Exception:
                logger.warning("PL/SQL scan failed object=%s.%s", package.owner, package.name)
                failures[(package.owner, package.name, "PACKAGE BODY")] = (
                    "The PL/SQL scanner failed, so body checks did not run for this object."
                )
                continue
            scans.update(scanned)
        return scans

    def _compilation(self, readers: _Readers, packages: list[PackageRef], prod_in, test_in) -> CompilationSummary:
        gateways = readers.gateways()
        callers = sorted(
            {
                (edge.owner, edge.name)
                for edge in list(prod_in.edges) + list(test_in.edges)
                if edge.depth == 1 and edge.direction == "referenced_by"
            }
        )
        try:
            return collect_compilation(
                gateways,
                [(package.owner, package.name) for package in packages],
                {environment: callers for environment in gateways},
            )
        except OracleAccessError:
            return CompilationSummary(evaluated=False, reason="Object validity was not evaluated because the dictionary could not be read.")

    def _environment_infos(self, snapshot: Snapshot) -> list[EnvironmentInfo]:
        labels = self.labels()
        roles = {"test": "source", "production": "baseline"}
        infos = []
        for environment in ("test", "production"):
            item = next((obj for obj in snapshot.objects if obj.environment == environment), None)
            if item is None:
                continue
            infos.append(
                EnvironmentInfo(
                    role=roles[environment],
                    label=environment_name(environment, labels),
                    database_id=item.database_id,
                    fixture=self.settings.fixture_mode,
                )
            )
        return infos

    def _record_versions(self, run_id: str, snapshot: Snapshot) -> None:
        if self.registry is None:
            return
        try:
            self.registry.record_versions(
                run_id,
                [
                    {
                        "environment": item.environment,
                        "owner": item.owner,
                        "name": item.name,
                        "object_type": item.object_type,
                        "sha256": item.sha256 if item.found else None,
                        "found": item.found,
                        "captured_at": item.retrieved_at or snapshot.captured_at,
                    }
                    for item in snapshot.objects
                    if not item.inaccessible and f"{item.owner}.{item.name}" in snapshot.packages
                ],
            )
        except OSError:
            logger.warning("object version history could not be written run_id=%s", run_id)

    def _load_table(self, readers: _Readers, environment: str, owner: str, table: str, cache: dict):
        gateway = readers.test if environment == "test" else readers.production
        metadata = gateway.fetch_table(owner, table)
        cache[f"{environment}:{owner}.{table}"] = metadata
        return metadata

    def _tables(self, readers: _Readers, test_graph, prod_graph) -> dict:
        cache = {}
        for edge in list(test_graph.edges) + list(prod_graph.edges):
            if edge.referenced_type != "TABLE":
                continue
            self._load_table(readers, "test", edge.referenced_owner, edge.referenced_name, cache)
            self._load_table(readers, "production", edge.referenced_owner, edge.referenced_name, cache)
        return cache

    def _tool_context(self, snapshot, prod_out, test_out, prod_in, tables, packages):
        edges = [
            edge.model_dump(mode="json")
            for edge in list(prod_out.edges) + list(test_out.edges) + list(prod_in.edges)
        ]
        table_payload = {
            f"{metadata.owner}.{metadata.name}": metadata.model_dump(mode="json")
            for key, metadata in tables.items()
            if key.startswith("production:")
        }
        arguments = {}
        for package in packages:
            for environment, gateway in (("test", self.test_gateway), ("production", self.production_gateway)):
                fetched = gateway.fetch_arguments(package.owner, package.name)
                arguments[f"{environment}:{package.qualified}"] = [
                    item.model_dump(mode="json") for item in fetched.arguments
                ]
        return ToolContext(
            snapshot=snapshot,
            graph_edges=edges,
            tables=table_payload,
            arguments=arguments,
            max_depth=self.settings.max_dependency_depth,
            readers={"test": self.test_gateway, "production": self.production_gateway},
        )

    def _report(
        self,
        run_id,
        snapshot,
        diffs,
        findings,
        edges,
        recommendation,
        reasons,
        actions,
        limitations,
        outcome: _Review,
        readers: _Readers,
    ) -> Report:
        from oracle_impact.infrastructure.reports.diagram import dependency_diagram

        counts = {level.value: 0 for level in Severity}
        for finding in findings:
            if finding.origin == "investigator":
                continue
            counts[finding.severity.value] = counts.get(finding.severity.value, 0) + 1
        added = sum(diff.added for diff in diffs)
        removed = sum(diff.removed for diff in diffs)
        dependency_rows = []
        seen = set()
        for edge in edges:
            if edge.direction == "referenced_by":
                key = ("caller", edge.owner, edge.name, edge.object_type)
                if key in seen:
                    continue
                seen.add(key)
                dependency_rows.append(
                    DependencyRow(
                        direction="caller",
                        object_type=edge.object_type,
                        object_name=edge.name,
                        owner=edge.owner,
                        usage=f"References {edge.referenced_owner}.{edge.referenced_name}",
                        impact=_impact_for(findings, f"{edge.owner}.{edge.name}"),
                        depth=edge.depth,
                        database=edge.database,
                    )
                )
            else:
                key = ("uses", edge.referenced_owner, edge.referenced_name, edge.referenced_type)
                if key in seen:
                    continue
                seen.add(key)
                dependency_rows.append(
                    DependencyRow(
                        direction="uses",
                        object_type=edge.referenced_type,
                        object_name=edge.referenced_name,
                        owner=edge.referenced_owner,
                        usage=f"Referenced by {edge.owner}.{edge.name}",
                        impact=_impact_for(findings, f"{edge.referenced_owner}.{edge.referenced_name}"),
                        depth=edge.depth,
                        database=edge.database,
                    )
                )
        changes = [
            ChangeRow(
                finding_id=finding.id,
                change_type=finding.change_category.replace("_", " "),
                description=finding.explanation,
                lines=_line_label(finding),
                severity=finding.severity,
                status=finding.status,
            )
            for finding in findings
        ]
        test_object = next((item for item in snapshot.objects if item.environment == "test"), None)
        prod_object = next((item for item in snapshot.objects if item.environment == "production"), None)
        analyzer = outcome.analyzer or (
            f"Deterministic engine and investigator ({outcome.model_name})"
            if outcome.ai_used
            else "Deterministic engine only. AI review did not run."
        )
        return Report(
            run_id=run_id,
            created_at=snapshot.captured_at,
            packages=snapshot.packages,
            test_environment=EnvironmentInfo(
                role="source",
                label=environment_name("test", self.labels()),
                database_id=test_object.database_id if test_object else readers.test.database_id,
                fixture=self.settings.fixture_mode,
            ),
            production_environment=EnvironmentInfo(
                role="baseline",
                label=environment_name("production", self.labels()),
                database_id=prod_object.database_id if prod_object else readers.production.database_id,
                fixture=self.settings.fixture_mode,
            ),
            analyzer=analyzer,
            model_name=outcome.model_name,
            ai_used=outcome.ai_used,
            status="completed",
            recommendation=recommendation,
            recommendation_label=label_for(recommendation),
            recommendation_reasons=reasons,
            required_actions=actions,
            statistics=ReportStatistics(
                lines_added=added,
                lines_removed=removed,
                lines_changed=added + removed,
                routines_changed=len({finding.changed_object for finding in findings if finding.change_category != "coverage"}),
                dependency_count=len(dependency_rows),
                finding_counts=counts,
                potential_impacts=sum(1 for finding in findings if finding.status.value == "potential"),
                high_findings=counts.get("critical", 0) + counts.get("high", 0),
                medium_findings=counts.get("medium", 0),
            ),
            diffs=diffs,
            changes=changes,
            dependencies=dependency_rows,
            findings=findings,
            limitations=limitations,
            diagram_svg=dependency_diagram(snapshot.packages, dependency_rows),
            tool_log=outcome.tool_log,
        )

    def _capture_callers(self, snapshot: Snapshot, prod_in) -> None:
        """Keep direct caller source available for the investigator and citation checks."""
        seen = {(item.environment, item.owner, item.name, item.object_type) for item in snapshot.objects}
        captured = 0
        for edge in prod_in.edges:
            if edge.depth != 1 or edge.object_type not in {"PACKAGE", "PACKAGE BODY"}:
                continue
            if captured >= 8:
                break
            for environment, gateway in (("test", self.test_gateway), ("production", self.production_gateway)):
                for object_type in ("PACKAGE", "PACKAGE BODY"):
                    key = (environment, edge.owner, edge.name, object_type)
                    if key in seen:
                        continue
                    record = gateway.fetch_source(edge.owner, edge.name, object_type)
                    seen.add(key)
                    if not record.found or record.inaccessible:
                        continue
                    snapshot.objects.append(
                        CapturedObject(
                            environment=environment,
                            database_id=gateway.database_id,
                            owner=edge.owner,
                            name=edge.name,
                            object_type=object_type,
                            found=True,
                            source=record.source,
                            sha256=record.sha256,
                            retrieved_at=record.retrieved_at,
                        )
                    )
            captured += 1


def _unread_limitation(remaining: list[RequiredRead]) -> str:
    if not remaining:
        return ""
    listed = "; ".join(item.describe() for item in remaining[:_UNREAD_LISTED])
    more = f"; and {len(remaining) - _UNREAD_LISTED} more" if len(remaining) > _UNREAD_LISTED else ""
    return (
        f"The investigator did not collect {len(remaining)} required read{'s' if len(remaining) != 1 else ''}: "
        f"{listed}{more}. Those items are treated as unreadable, so coverage is incomplete."
    )


def _relabel_unread_packages(snapshot: Snapshot, packages: list[PackageRef], reads: RecordedReads) -> None:
    """Say a package was not read, rather than that access was denied, when the investigator skipped it."""
    for package in packages:
        if any(("test", package.owner, package.name, kind) in reads.sources for kind in ("PACKAGE", "PACKAGE BODY")):
            continue
        denied = f"{package.qualified} could not be read in Test. Metadata or source access was denied."
        snapshot.limitations = [
            f"{package.qualified} was not read in Test by the investigator." if line == denied else line
            for line in snapshot.limitations
        ]


def _attach_read_sources(snapshot: Snapshot, reads: RecordedReads, gateways: dict) -> None:
    """Add caller source the investigator read, as the dashboard run does before its review."""
    present = {(item.environment, item.owner, item.name, item.object_type) for item in snapshot.objects}
    for key, record in reads.sources.items():
        environment, owner, name, object_type = key
        if key in present or environment not in gateways or not record.found or record.inaccessible:
            continue
        snapshot.objects.append(
            CapturedObject(
                environment=environment,
                database_id=gateways[environment].database_id,
                owner=owner,
                name=name,
                object_type=object_type,
                found=True,
                source=record.source,
                sha256=record.sha256,
                retrieved_at=record.retrieved_at,
            )
        )


def _changed_test_lines(diffs) -> dict[tuple[str, str], set[int]]:
    changed: dict[tuple[str, str], set[int]] = {}
    for diff in diffs:
        if diff.object_type != "PACKAGE BODY":
            continue
        lines = changed.setdefault((diff.owner, diff.name), set())
        lines.update(row.test_line_no for row in diff.rows if row.test_kind == "added" and row.test_line_no)
    return changed


def _change_type(test, prod, diff) -> str:
    if (test is not None and test.inaccessible) or (prod is not None and prod.inaccessible):
        return "unknown"
    test_found = test is not None and test.found
    prod_found = prod is not None and prod.found
    if test_found and not prod_found:
        return "created"
    if prod_found and not test_found:
        return "deleted"
    if not test_found and not prod_found:
        return "absent"
    if diff is not None and diff.unchanged:
        return "unchanged"
    if diff is not None and diff.formatting_only:
        return "formatting"
    if diff is None and test.sha256 == prod.sha256:
        return "unchanged"
    return "modified"


def _object_states(snapshot: Snapshot, packages, diffs, compilation: CompilationSummary, labels: dict[str, str]) -> list[ObjectState]:
    status = {
        (item.environment, item.owner, item.name, item.object_type): item
        for item in compilation.objects
        if item.role == "in_scope"
    }
    states = []
    for owner, name in packages:
        for object_type in ("PACKAGE", "PACKAGE BODY"):
            environments = []
            for environment in ("test", "production"):
                item = snapshot.find(environment, owner, name, object_type)
                if item is None:
                    continue
                compiled = status.get((environment, owner, name, object_type))
                environments.append(
                    EnvironmentObject(
                        environment=environment,
                        label=environment_name(environment, labels),
                        found=item.found,
                        inaccessible=item.inaccessible,
                        sha256=item.sha256 if item.found else None,
                        captured_at=item.retrieved_at,
                        capture_drift=item.drift,
                        compile_status=compiled.status if compiled else None,
                        last_ddl_time=compiled.last_ddl_time if compiled else None,
                    )
                )
            diff = next(
                (item for item in diffs if item.owner == owner and item.name == name and item.object_type == object_type),
                None,
            )
            states.append(
                ObjectState(
                    owner=owner,
                    name=name,
                    object_type=object_type,
                    change_type=_change_type(
                        snapshot.find("test", owner, name, object_type),
                        snapshot.find("production", owner, name, object_type),
                        diff,
                    ),
                    environments=environments,
                )
            )
    return states


def _snapshot_lines(snapshot: Snapshot) -> list[str]:
    lines = []
    for item in snapshot.objects:
        if item.inaccessible:
            state = "metadata or source access was denied"
        elif not item.found:
            state = "not found"
        else:
            state = "captured"
        if item.drift:
            state += "; the object changed during capture"
        digest = (item.sha256 or "")[:12]
        suffix = f", checksum {digest}" if digest else ""
        lines.append(f"{item.environment} {item.owner}.{item.name} {item.object_type}: {state}{suffix}.")
    lines.extend(snapshot.limitations)
    return lines


def _diff_lines(diffs) -> list[str]:
    lines = []
    for diff in diffs:
        if diff.unchanged:
            change = "unchanged"
        elif diff.formatting_only:
            change = f"formatting only, {diff.added} added, {diff.removed} removed"
        else:
            change = f"{diff.added} added, {diff.removed} removed"
        lines.append(f"{diff.owner}.{diff.name} {diff.object_type}: {change}.")
    return lines


def _dependency_lines(prod_out, test_out, prod_in, signature_changes, findings, gaps) -> list[str]:
    lines = []
    for edge in list(prod_in.edges) + list(test_out.edges) + list(prod_out.edges):
        if edge.direction == "referenced_by":
            lines.append(
                f"Caller {edge.owner}.{edge.name} ({edge.object_type}) references "
                f"{edge.referenced_owner}.{edge.referenced_name}."
            )
        else:
            lines.append(
                f"{edge.owner}.{edge.name} uses {edge.referenced_owner}.{edge.referenced_name} "
                f"({edge.referenced_type}), depth {edge.depth}, {edge.database}."
            )
    if not lines:
        lines.append("No dependency edges were returned.")
    for gap in gaps:
        lines.append(gap)
    for change in signature_changes:
        lines.append(f"{change.package} {change.routine}: {change.detail}")
    for finding in findings:
        lines.append(f"{finding.id} {finding.severity.value} {finding.change_category}: {finding.explanation}")
    return lines


def _impact_for(findings, qualified: str) -> str:
    related = [
        finding
        for finding in findings
        if qualified in (finding.affected_object or "")
        or qualified in finding.changed_object
        or any(qualified in step for step in finding.dependency_path)
    ]
    if any(finding.status.value == "confirmed" and finding.attributes.get("breaking") for finding in related):
        return "Affected"
    if any(finding.status.value == "unknown" for finding in related):
        return "Unknown"
    if any(finding.status.value == "potential" for finding in related):
        return "Potentially affected"
    return "No incompatible change detected"


def _line_label(finding) -> str:
    test = ",".join(str(line) for line in finding.changed_lines_test[:6]) or "-"
    prod = ",".join(str(line) for line in finding.corresponding_lines_prod[:6]) or "-"
    return f"Test {test} / Prod {prod}"
