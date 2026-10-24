"""Orchestrate one analysis run. Controllers call this and do not contain analysis rules."""

import logging
from uuid import uuid4

from oracle_impact.analysis.annotate import annotate
from oracle_impact.analysis.compilation import collect_compilation, compilation_findings
from oracle_impact.analysis.coverage import build_coverage, edge_records
from oracle_impact.analysis.dependencies import discover_dependencies
from oracle_impact.analysis.diff_engine import build_object_diff
from oracle_impact.analysis.grants import compare_grants
from oracle_impact.analysis.impacts import build_deterministic_findings
from oracle_impact.analysis.plsql_scanner import scan_package
from oracle_impact.analysis.signatures import compare_package_signatures
from oracle_impact.analysis.transactions import build_profiles, modified_statements, performance_assessment
from oracle_impact.config import Settings
from oracle_impact.infrastructure.llm.investigator import build_case_file
from oracle_impact.infrastructure.llm.tools import ToolContext
from oracle_impact.infrastructure.oracle.gateway import OracleAccessError, utc_now
from oracle_impact.infrastructure.storage.run_store import RunStore
from oracle_impact.models.assessment import (
    Comparison,
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


class UnknownModel(ValueError):
    """The requested model is not served by the configured endpoint."""


class AnalysisService:
    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        test_gateway,
        production_gateway,
        investigator=None,
        autumn_gateway=None,
        registry=None,
        rules=DEFAULT_RULES,
    ) -> None:
        self.settings = settings
        self.store = store
        self.test_gateway = test_gateway
        self.production_gateway = production_gateway
        self.autumn_gateway = autumn_gateway
        self.investigator = investigator
        self.registry = registry
        self.rules = rules
        self.snapshots = SnapshotService(test_gateway, production_gateway, autumn_gateway)

    def labels(self) -> dict[str, str]:
        labels = {"test": self.settings.test_oracle_label, "production": self.settings.prod_oracle_label}
        if self.autumn_gateway is not None:
            labels["autumn"] = getattr(self.autumn_gateway, "label", self.settings.autumn_oracle_label)
        return labels

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

    def create_run(self, packages: list[PackageRef], model: str = "") -> str:
        if model and model not in self.available_models():
            raise UnknownModel(f"Model {model} is not served by the configured endpoint.")
        run_id = str(uuid4())
        self.store.create(run_id, [package.qualified for package in packages], model or self.settings.llm_model)
        return run_id

    def execute(self, run_id: str) -> None:
        request = self.store.read_json(run_id, "request.json") or {}
        packages = [PackageRef(owner=item.split(".", 1)[0], name=item.split(".", 1)[1]) for item in request.get("packages", [])]
        chosen_model = request.get("model") or self.settings.llm_model
        try:
            names = ", ".join(package.qualified for package in packages)
            self._progress(
                run_id,
                "snapshotting",
                f"Reading Test and Production source for {names}.",
                [f"Selected packages: {names}."],
            )
            snapshot = self.snapshots.capture(packages)
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
            comparisons = self._comparisons(snapshot, packages)
            self._progress(run_id, "comparing", "Code comparison finished.", _diff_lines(diffs))

            self._progress(
                run_id,
                "dependencies",
                "Reading dependencies and procedure signatures.",
                ["Walking callers and referenced objects to depth "
                f"{self.settings.max_dependency_depth}."],
            )
            selected = {package.qualified for package in packages}
            prod_out = discover_dependencies(
                self.production_gateway,
                [(package.owner, package.name, "PACKAGE BODY") for package in packages],
                "referenced",
                self.settings.max_dependency_depth,
            )
            test_out = discover_dependencies(
                self.test_gateway,
                [(package.owner, package.name, "PACKAGE BODY") for package in packages],
                "referenced",
                self.settings.max_dependency_depth,
            )
            prod_in = discover_dependencies(
                self.production_gateway,
                [(package.owner, package.name, "PACKAGE") for package in packages],
                "referenced_by",
                self.settings.max_dependency_depth,
            )
            test_in = discover_dependencies(
                self.test_gateway,
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
                test_args = self.test_gateway.fetch_arguments(package.owner, package.name)
                prod_args = self.production_gateway.fetch_arguments(package.owner, package.name)
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
                    self.test_gateway.fetch_grants(package.owner, package.name),
                    self.production_gateway.fetch_grants(package.owner, package.name),
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
            tables = self._tables(test_out, prod_out)
            findings = build_deterministic_findings(
                run_id,
                snapshot,
                diffs,
                signature_changes,
                scans,
                lambda env, owner, table: tables.get(f"{env}:{owner}.{table}")
                or self._load_table(env, owner, table, tables),
                gaps,
                drift,
                grant_changes,
            )
            compilation = self._compilation(packages, prod_in, test_in)
            findings.extend(compilation_findings(run_id, compilation, self.labels()))
            annotate(findings)

            self._progress(
                run_id,
                "dependencies",
                f"Deterministic checks produced {len(findings)} findings.",
                _dependency_lines(prod_out, test_out, prod_in, signature_changes, findings, gaps),
            )
            limitations = list(snapshot.limitations) + grant_notes
            tool_log: list[dict] = []
            ai_used = False
            model_name = None
            if self.settings.llm_egress_enabled and self.investigator is not None:
                model_name = chosen_model or "configured-model"
                investigator = self.investigator.with_model(chosen_model)
                self._progress(run_id, "investigating", f"AI investigator is reviewing the change set with {model_name}.")
                self._capture_callers(snapshot, prod_in)
                context = self._tool_context(snapshot, prod_out, test_out, prod_in, tables, packages)
                case_file, requires_tools = build_case_file(findings)

                def on_progress(message: str, kind: str = "status") -> None:
                    self._progress(
                        run_id,
                        "investigating",
                        message,
                        [message],
                        activity={"kind": kind, "text": message},
                    )

                try:
                    extra, tool_log, ai_limitations = investigator.investigate(
                        snapshot,
                        context,
                        [finding.explanation for finding in findings],
                        on_progress=on_progress,
                        case_file=case_file,
                        requires_tools=requires_tools,
                        anchors=findings,
                    )
                except Exception:
                    logger.warning("investigator failed run_id=%s", run_id)
                    extra, ai_limitations = [], ["The investigator failed. Deterministic findings were kept."]
                findings.extend(extra)
                limitations.extend(ai_limitations)
                ai_used = True
                self._progress(run_id, "investigating", "AI investigator finished.")
            else:
                self._progress(
                    run_id,
                    "investigating",
                    "AI investigator is off. The report uses deterministic checks only.",
                    ["LLM egress is disabled. No package source was sent to a model."],
                )
                limitations.append(
                    "AI investigator did not run because LLM egress is disabled. This report is deterministic-only."
                )
            if prod_out.skipped_system_objects or test_out.skipped_system_objects:
                limitations.append("Oracle-supplied schemas were omitted from the dependency walk.")
            limitations.append(
                "Snapshots of Test and Production are not a single atomic point in time."
            )
            limitations.append(
                "Static inspection does not prove runtime behavior. Dynamic SQL, synonyms, and callers outside the database can remain unresolved."
            )

            from oracle_impact.analysis.test_snippets import apply_test_snippets

            apply_test_snippets(findings, snapshot)
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
                ai_used,
                model_name,
                tool_log,
            )
            selected_pairs = [(package.owner, package.name) for package in packages]
            all_edges = list(prod_in.edges) + list(test_in.edges) + list(test_out.edges) + list(prod_out.edges)
            edges = edge_records(all_edges, selected_pairs, scans)
            report.report_version = REPORT_VERSION
            report.environments = self._environment_infos(snapshot)
            report.object_states = _object_states(snapshot, selected_pairs, diffs, compilation, self.labels())
            report.comparisons = comparisons
            report.compilation = compilation
            report.dependency_edges = edges
            report.coverage = build_coverage(
                selected_pairs,
                snapshot,
                [env for env in self.snapshots.environments if any(item.environment == env for item in snapshot.objects)],
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
            self.store.write_json(run_id, "tool_log.json", tool_log)
            self._progress(run_id, "completed", "Report ready.", [label_for(recommendation)])
            logger.info(
                "analysis completed run_id=%s findings=%s recommendation=%s",
                run_id,
                len(findings),
                recommendation.value,
            )
        except LookupError as exc:
            self.store.write_status(run_id, "failed", str(exc), detail=str(exc))
        except OracleAccessError as exc:
            self.store.write_status(run_id, "failed", str(exc), detail=str(exc))
        except Exception:
            logger.exception("analysis failed run_id=%s", run_id)
            message = "The analysis failed before a report could be completed."
            self.store.write_status(run_id, "failed", message, detail=message)

    def _progress(
        self,
        run_id: str,
        status: str,
        detail: str,
        lines: list[str] | None = None,
        activity: dict | None = None,
    ) -> None:
        self.store.write_status(run_id, status, detail=detail, lines=lines, activity=activity)

    def _diffs(self, snapshot: Snapshot, packages: list[PackageRef], failures: dict, source_env: str = "test", target_env: str = "production"):
        diffs = []
        for package in packages:
            for object_type in ("PACKAGE", "PACKAGE BODY"):
                test = snapshot.find(source_env, package.owner, package.name, object_type)
                prod = snapshot.find(target_env, package.owner, package.name, object_type)
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

    def _comparisons(self, snapshot: Snapshot, packages: list[PackageRef]) -> list[Comparison]:
        if not any(item.environment == "autumn" for item in snapshot.objects):
            return []
        labels = self.labels()
        comparisons = []
        for source, target in (("test", "autumn"), ("autumn", "production")):
            comparisons.append(
                Comparison(
                    key=f"{source}:{target}",
                    source_environment=source,
                    target_environment=target,
                    source_label=labels[source],
                    target_label=labels[target],
                    diffs=self._diffs(snapshot, packages, {}, source, target),
                )
            )
        return comparisons

    def _compilation(self, packages: list[PackageRef], prod_in, test_in) -> CompilationSummary:
        gateways = {"test": self.test_gateway, "production": self.production_gateway}
        if self.autumn_gateway is not None:
            gateways["autumn"] = self.autumn_gateway
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
        roles = {"test": "source", "autumn": "staging", "production": "baseline"}
        infos = []
        for environment in ("test", "autumn", "production"):
            item = next((obj for obj in snapshot.objects if obj.environment == environment), None)
            if item is None:
                continue
            infos.append(
                EnvironmentInfo(
                    role=roles[environment],
                    label=labels.get(environment, environment.upper()),
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

    def _load_table(self, environment: str, owner: str, table: str, cache: dict):
        gateway = self.test_gateway if environment == "test" else self.production_gateway
        metadata = gateway.fetch_table(owner, table)
        cache[f"{environment}:{owner}.{table}"] = metadata
        return metadata

    def _tables(self, test_graph, prod_graph) -> dict:
        cache = {}
        for edge in list(test_graph.edges) + list(prod_graph.edges):
            if edge.referenced_type != "TABLE":
                continue
            self._load_table("test", edge.referenced_owner, edge.referenced_name, cache)
            self._load_table("production", edge.referenced_owner, edge.referenced_name, cache)
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
        ai_used,
        model_name,
        tool_log,
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
        analyzer = (
            f"Deterministic engine and investigator ({model_name})"
            if ai_used
            else "Deterministic engine only. AI review did not run."
        )
        return Report(
            run_id=run_id,
            created_at=snapshot.captured_at,
            packages=snapshot.packages,
            test_environment=EnvironmentInfo(
                role="source",
                label=self.settings.test_oracle_label,
                database_id=test_object.database_id if test_object else self.test_gateway.database_id,
                fixture=self.settings.fixture_mode,
            ),
            production_environment=EnvironmentInfo(
                role="baseline",
                label=self.settings.prod_oracle_label,
                database_id=prod_object.database_id if prod_object else self.production_gateway.database_id,
                fixture=self.settings.fixture_mode,
            ),
            analyzer=analyzer,
            model_name=model_name,
            ai_used=ai_used,
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
            tool_log=tool_log,
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
            for environment in ("test", "autumn", "production"):
                item = snapshot.find(environment, owner, name, object_type)
                if item is None:
                    continue
                compiled = status.get((environment, owner, name, object_type))
                environments.append(
                    EnvironmentObject(
                        environment=environment,
                        label=labels.get(environment, environment.upper()),
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
