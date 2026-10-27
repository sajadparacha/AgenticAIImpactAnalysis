"""Object validity and compilation errors read from ALL_OBJECTS and ALL_ERRORS.

Nothing here compiles or alters an object. Validity is what the dictionary
reports at capture time; an object can be VALID and still fail at runtime.
"""

from oracle_impact.analysis.hashing import finding_id
from oracle_impact.infrastructure.oracle.gateway import OracleAccessError, OracleUnavailable
from oracle_impact.models.assessment import CompilationSummary, CompiledObject, CompileError
from oracle_impact.models.enums import Confidence, EvidenceStatus, Severity
from oracle_impact.models.report import Finding

_PACKAGE_TYPES = ("PACKAGE", "PACKAGE BODY")
_CODE_TYPES = {"PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "VIEW", "TYPE", "TYPE BODY"}
MAX_DEPENDENTS = 50


def collect_compilation(
    gateways: dict[str, object],
    packages: list[tuple[str, str]],
    dependents: dict[str, list[tuple[str, str]]],
) -> CompilationSummary:
    summary = CompilationSummary(evaluated=False)
    for environment, gateway in gateways.items():
        status_reader = getattr(gateway, "fetch_object_status", None)
        error_reader = getattr(gateway, "fetch_compile_errors", None)
        if status_reader is None or error_reader is None:
            summary.unavailable.append(f"{environment}: the gateway does not expose object status.")
            continue
        try:
            readable = _collect_environment(summary, environment, status_reader, error_reader, packages, dependents.get(environment, []))
        except OracleUnavailable:
            raise
        except OracleAccessError:
            readable = False
        if readable:
            summary.environments.append(environment)
        else:
            summary.unavailable.append(f"{environment}: ALL_OBJECTS or ALL_ERRORS could not be read.")
    summary.evaluated = bool(summary.environments)
    if not summary.evaluated:
        summary.reason = "Object validity was not evaluated. " + " ".join(summary.unavailable)
    elif summary.unavailable:
        summary.reason = "Object validity was evaluated for " + ", ".join(summary.environments) + " only. " + " ".join(summary.unavailable)
    return summary


def _collect_environment(summary, environment, status_reader, error_reader, packages, dependents) -> bool:
    for owner, name in packages:
        fetched = status_reader(owner, name)
        if fetched.inaccessible:
            return False
        for object_type, status, last_ddl in fetched.objects:
            if object_type in _PACKAGE_TYPES:
                summary.objects.append(
                    CompiledObject(environment=environment, owner=owner, name=name, object_type=object_type, status=status, last_ddl_time=last_ddl)
                )
        _record_errors(summary, environment, owner, name, error_reader(owner, name))
    selected = set(packages)
    for owner, name in dependents[:MAX_DEPENDENTS]:
        if (owner, name) in selected:
            continue
        fetched = status_reader(owner, name)
        if fetched.inaccessible:
            continue
        invalid = False
        for object_type, status, last_ddl in fetched.objects:
            if object_type in _CODE_TYPES:
                summary.objects.append(
                    CompiledObject(
                        environment=environment,
                        owner=owner,
                        name=name,
                        object_type=object_type,
                        status=status,
                        last_ddl_time=last_ddl,
                        role="dependent",
                    )
                )
                invalid = invalid or status != "VALID"
        if invalid:
            _record_errors(summary, environment, owner, name, error_reader(owner, name))
    return True


def _record_errors(summary, environment, owner, name, fetched) -> None:
    if fetched.inaccessible:
        summary.unread_errors[f"{environment}:{owner}.{name}"] = (
            "ALL_ERRORS was not read in this run" if getattr(fetched, "not_read", False) else "ALL_ERRORS could not be read"
        )
        return
    summary.errors.extend(_errors(environment, owner, name, fetched.errors))


def missing_errors_text(summary: CompilationSummary, environment: str, owner: str, name: str) -> str:
    """Why an invalid object shows no compile errors."""
    return summary.unread_errors.get(f"{environment}:{owner}.{name}", "no ALL_ERRORS rows were visible")


def _errors(environment, owner, name, rows) -> list[CompileError]:
    return [
        CompileError(
            environment=environment,
            owner=owner,
            name=name,
            object_type=object_type,
            line=line,
            position=position,
            text=text,
            attribute=attribute or "ERROR",
        )
        for object_type, line, position, text, attribute in rows
    ]


def compilation_findings(run_id: str, summary: CompilationSummary, labels: dict[str, str]) -> list[Finding]:
    if not summary.evaluated:
        return []
    findings: list[Finding] = []
    by_key = {(item.environment, item.owner, item.name, item.object_type): item for item in summary.objects}
    for item in summary.objects:
        if item.environment != "test" or item.status == "VALID":
            continue
        errors = [
            error
            for error in summary.errors
            if error.environment == "test" and error.owner == item.owner and error.name == item.name and error.object_type == item.object_type
            and error.attribute == "ERROR"
        ]
        prod = by_key.get(("production", item.owner, item.name, item.object_type))
        listed = "; ".join(f"line {error.line}: {error.text}" for error in errors[:5]) or missing_errors_text(
            summary, "test", item.owner, item.name
        )
        qualified = f"{item.owner}.{item.name}"
        if item.role == "in_scope":
            scope, severity = "in_scope", Severity.CRITICAL
            detail = f"{qualified} {item.object_type} is {item.status} in {labels.get('test', 'Test')}. Compilation errors: {listed}."
            action = "Fix the compilation errors in Test and re-run the analysis."
            rationale = "Critical: the proposed version does not compile in Test, so it cannot be deployed as captured."
        elif prod is not None and prod.status != "VALID":
            scope, severity = "dependent_preexisting", Severity.MEDIUM
            detail = (
                f"Dependent {qualified} {item.object_type} is {item.status} in both {labels.get('test', 'Test')} "
                f"and {labels.get('production', 'Production')}. Errors in Test: {listed}."
            )
            action = "Confirm whether the existing invalid state is expected. It was not introduced by comparing these environments."
            rationale = "Medium: the dependent is already invalid in Production, so the change set may not be the cause."
        else:
            scope, severity = "dependent", Severity.HIGH
            detail = (
                f"Dependent {qualified} {item.object_type} is {item.status} in {labels.get('test', 'Test')} "
                f"but {prod.status if prod else 'not recorded'} in {labels.get('production', 'Production')}. Errors in Test: {listed}."
            )
            action = f"Recompile or update {qualified} in Test and confirm it becomes VALID before promotion."
            rationale = "High: a caller of the changed code no longer compiles in Test."
        findings.append(
            Finding(
                id=finding_id(run_id, "compilation", "test", qualified, item.object_type),
                changed_object=qualified,
                object_owner=item.owner,
                object_name=item.name,
                object_type=item.object_type,
                source_database="test",
                changed_lines_test=[error.line for error in errors[:8]],
                change_category="compilation",
                affected_object=qualified,
                evidence=detail,
                severity=severity,
                confidence=Confidence.HIGH,
                status=EvidenceStatus.CONFIRMED,
                explanation=detail + " Validity is read from the data dictionary; this report never compiles objects.",
                recommended_action=action,
                proposed_tests=[f"After the fix, confirm ALL_OBJECTS shows {qualified} {item.object_type} as VALID in Test."],
                attributes={"compile_scope": scope, "compile_invalid": scope != "dependent_preexisting", "compile_status": item.status},
                severity_rationale=rationale,
            )
        )
    for item in summary.objects:
        if item.environment != "production" or item.role != "in_scope" or item.status == "VALID":
            continue
        qualified = f"{item.owner}.{item.name}"
        test = by_key.get(("test", item.owner, item.name, item.object_type))
        if test is not None and test.status != "VALID":
            continue
        detail = f"{qualified} {item.object_type} is {item.status} in {labels.get('production', 'Production')} today."
        findings.append(
            Finding(
                id=finding_id(run_id, "compilation", "production", qualified, item.object_type),
                changed_object=qualified,
                object_owner=item.owner,
                object_name=item.name,
                object_type=item.object_type,
                source_database="production",
                change_category="compilation",
                affected_object=qualified,
                evidence=detail,
                severity=Severity.MEDIUM,
                confidence=Confidence.HIGH,
                status=EvidenceStatus.CONFIRMED,
                explanation=detail + " Deploying the Test version would replace it; confirm why Production is invalid first.",
                recommended_action="Investigate the Production invalid state with the DBA before deployment.",
                proposed_tests=[],
                attributes={"compile_scope": "production", "compile_status": item.status},
                severity_rationale="Medium: an environment difference that predates this change set.",
            )
        )
    return findings
