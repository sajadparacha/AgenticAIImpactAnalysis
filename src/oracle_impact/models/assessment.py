"""Report sections added in schema 1.1. Every field has a default so 1.0 reports still load."""

from pydantic import BaseModel, Field

from oracle_impact.models.report_parts import ObjectDiff


class EnvironmentObject(BaseModel):
    """One object as captured in one environment."""

    environment: str
    label: str
    found: bool = False
    inaccessible: bool = False
    sha256: str | None = None
    captured_at: str = ""
    capture_drift: bool = False
    compile_status: str | None = None
    last_ddl_time: str | None = None


class ObjectState(BaseModel):
    """An in-scope object across every captured environment. change_type compares Test with Production."""

    owner: str
    name: str
    object_type: str
    change_type: str
    environments: list[EnvironmentObject] = Field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.owner}.{self.name}"

    def in_environment(self, environment: str) -> EnvironmentObject | None:
        return next((item for item in self.environments if item.environment == environment), None)


class Comparison(BaseModel):
    """A source-to-target diff set. The Test to Production pair lives in Report.diffs."""

    key: str
    source_environment: str
    target_environment: str
    source_label: str
    target_label: str
    diffs: list[ObjectDiff] = Field(default_factory=list)


class CompiledObject(BaseModel):
    environment: str
    owner: str
    name: str
    object_type: str
    status: str
    last_ddl_time: str | None = None
    role: str = "in_scope"


class CompileError(BaseModel):
    environment: str
    owner: str
    name: str
    object_type: str
    line: int
    position: int = 0
    text: str
    attribute: str = "ERROR"


class CompilationSummary(BaseModel):
    evaluated: bool = False
    reason: str = ""
    environments: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    objects: list[CompiledObject] = Field(default_factory=list)
    errors: list[CompileError] = Field(default_factory=list)


class CoverageItem(BaseModel):
    owner: str
    name: str
    object_type: str
    state: str
    reasons: list[str] = Field(default_factory=list)


class DynamicSqlRef(BaseModel):
    owner: str
    name: str
    routine: str
    line: int
    changed: bool = False


class DependencyCoverage(BaseModel):
    edges: int = 0
    verified_edges: int = 0
    inferred_edges: int = 0
    gaps: int = 0
    unresolved: int = 0
    skipped_system_objects: int = 0
    max_depth: int = 0


class CoverageSummary(BaseModel):
    """Object coverage. The denominator is in_scope: every specification and body of each selected package."""

    in_scope: int = 0
    analyzed: int = 0
    partial: int = 0
    skipped: int = 0
    inaccessible: int = 0
    failed: int = 0
    items: list[CoverageItem] = Field(default_factory=list)
    dynamic_sql: list[DynamicSqlRef] = Field(default_factory=list)
    dependency: DependencyCoverage = Field(default_factory=DependencyCoverage)
    environments: list[str] = Field(default_factory=list)
    completed_at: str = ""


class RoutineFacts(BaseModel):
    start_line: int = 0
    end_line: int = 0
    reads: list[str] = Field(default_factory=list)
    writes: dict[str, list[str]] = Field(default_factory=dict)
    commits: list[int] = Field(default_factory=list)
    rollbacks: list[int] = Field(default_factory=list)
    savepoints: list[int] = Field(default_factory=list)
    autonomous: bool = False
    dynamic_sql: list[int] = Field(default_factory=list)
    handlers: list[int] = Field(default_factory=list)
    swallowed: list[int] = Field(default_factory=list)
    sequence: list[str] = Field(default_factory=list)


class TransactionProfile(BaseModel):
    """Observed statement shapes of one changed routine. Predicted effects come from linked findings."""

    owner: str
    name: str
    routine: str
    change: str
    test: RoutineFacts | None = None
    production: RoutineFacts | None = None
    differences: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)


class ModifiedStatement(BaseModel):
    owner: str
    name: str
    routine: str
    operation: str
    table: str
    change: str
    test_line: int | None = None
    prod_line: int | None = None
    statement: str = ""


class PerformanceAssessment(BaseModel):
    evaluated: bool = False
    reason: str = ""
    plan_source: str = "none"
    statements: list[ModifiedStatement] = Field(default_factory=list)


class ExternalCheck(BaseModel):
    """An optional integration such as application-source search. evaluated stays False until one is configured."""

    name: str
    evaluated: bool = False
    reason: str = ""
    results: list[dict] = Field(default_factory=list)


class DependencyEdgeRecord(BaseModel):
    direction: str
    owner: str
    name: str
    object_type: str
    referenced_owner: str
    referenced_name: str
    referenced_type: str
    depth: int = 1
    database: str = ""
    provenance: str = "dictionary"
    note: str = ""


class PolicyItem(BaseModel):
    rule_id: str
    title: str
    finding_id: str
    resolution: str
    explanation: str
    required_action: str


class PolicyEvaluation(BaseModel):
    """Blocking rules applied to deterministic findings when the run finished."""

    policy_version: str = ""
    blockers: list[PolicyItem] = Field(default_factory=list)
    validations: list[PolicyItem] = Field(default_factory=list)
    rules: list[dict] = Field(default_factory=list)
