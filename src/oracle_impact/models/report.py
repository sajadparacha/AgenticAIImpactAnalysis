"""Report and finding schema shared by HTML, PDF, and JSON renderers."""

from typing import Any

from pydantic import BaseModel, Field

from oracle_impact.models.enums import Confidence, EvidenceStatus, Recommendation, Severity


class Finding(BaseModel):
    id: str
    changed_object: str
    object_owner: str
    object_name: str
    object_type: str
    source_database: str
    version_hash: str | None = None
    changed_lines_test: list[int] = Field(default_factory=list)
    corresponding_lines_prod: list[int] = Field(default_factory=list)
    change_category: str
    affected_object: str | None = None
    dependency_path: list[str] = Field(default_factory=list)
    evidence: str
    severity: Severity
    confidence: Confidence
    status: EvidenceStatus
    explanation: str
    recommended_action: str
    proposed_tests: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    origin: str = "deterministic"


class DiffRow(BaseModel):
    test_line_no: int | None = None
    test_text: str = ""
    test_kind: str = "empty"
    prod_line_no: int | None = None
    prod_text: str = ""
    prod_kind: str = "empty"


class ObjectDiff(BaseModel):
    owner: str
    name: str
    object_type: str
    test_hash: str | None = None
    prod_hash: str | None = None
    added: int = 0
    removed: int = 0
    formatting_only: bool = False
    unchanged: bool = False
    rows: list[DiffRow] = Field(default_factory=list)


class ChangeRow(BaseModel):
    finding_id: str
    change_type: str
    description: str
    lines: str
    severity: Severity
    status: EvidenceStatus


class DependencyRow(BaseModel):
    direction: str
    object_type: str
    object_name: str
    owner: str
    usage: str
    impact: str
    depth: int
    database: str


class EnvironmentInfo(BaseModel):
    role: str
    label: str
    database_id: str
    fixture: bool


class ReportStatistics(BaseModel):
    lines_added: int = 0
    lines_removed: int = 0
    lines_changed: int = 0
    routines_changed: int = 0
    dependency_count: int = 0
    finding_counts: dict[str, int] = Field(default_factory=dict)
    potential_impacts: int = 0
    high_findings: int = 0
    medium_findings: int = 0


class Report(BaseModel):
    report_version: str = "1.0"
    run_id: str
    created_at: str
    packages: list[str]
    test_environment: EnvironmentInfo
    production_environment: EnvironmentInfo
    analyzer: str
    model_name: str | None = None
    ai_used: bool = False
    status: str
    recommendation: Recommendation
    recommendation_label: str
    recommendation_reasons: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    statistics: ReportStatistics
    diffs: list[ObjectDiff] = Field(default_factory=list)
    changes: list[ChangeRow] = Field(default_factory=list)
    dependencies: list[DependencyRow] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    diagram_svg: str = ""
    tool_log: list[dict] = Field(default_factory=list)
