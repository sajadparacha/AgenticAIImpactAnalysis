"""Print-oriented PDF built from the report model.

WeasyPrint is used when its native libraries import successfully. Otherwise
fpdf2 renders the same sections. Neither path screenshots the dashboard.
"""

from oracle_impact.infrastructure.llm.plain_text import issue_list, plain_reasons, review_lines
from oracle_impact.models.enums import Recommendation
from oracle_impact.models.report import Report


def render_pdf(report: Report, html: str | None = None) -> bytes:
    try:
        from weasyprint import HTML
    except Exception:
        return _render_fpdf(report)
    try:
        if html:
            return HTML(string=html).write_pdf()
    except Exception:
        return _render_fpdf(report)
    return _render_fpdf(report)


def _render_fpdf(report: Report) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    _write(pdf, "Oracle package impact analysis report", 16, "B", 9)
    _write(pdf, f"Run {report.run_id}")
    _write(pdf, f"Created {report.created_at}")
    _write(pdf, "Packages: " + ", ".join(report.packages))
    _write(
        pdf,
        f"Source {report.test_environment.label} ({report.test_environment.database_id}); "
        f"baseline {report.production_environment.label} ({report.production_environment.database_id})",
    )
    _write(pdf, f"Analyzer: {report.analyzer}")
    pdf.ln(2)
    _write(pdf, "Deployment recommendation", 13, "B", 8)
    _write(pdf, report.recommendation_label, 12, "B", 7)
    severities = {finding.id: finding.severity.value for finding in report.findings}
    reasons = plain_reasons(report)
    linked = [f"{item['id']} ({severities.get(item['id'], 'n/a')})" for item in reasons if item["id"]]
    if linked:
        _write(pdf, "Because of: " + ", ".join(linked) + ". Details are in the Issues section.", 10, "", 5)
    for item in reasons:
        if not item["id"]:
            _write(pdf, item["text"], 10, "", 5)
    _heading(pdf, "Analysis statistics")
    stats = report.statistics
    _write(
        pdf,
        f"Lines changed {stats.lines_changed} ({stats.lines_added} added, {stats.lines_removed} removed). "
        f"Dependencies {stats.dependency_count}. "
        f"Critical {stats.finding_counts.get('critical', 0)}, "
        f"high {stats.finding_counts.get('high', 0)}, "
        f"medium {stats.finding_counts.get('medium', 0)}, "
        f"low {stats.finding_counts.get('low', 0)}, "
        f"informational {stats.finding_counts.get('informational', 0)}.",
        10,
        "",
        5,
    )
    issues = issue_list(report)
    _heading(pdf, "Issues")
    for row in issues["rows"]:
        finding = row["finding"]
        _write(pdf, f"{row['level'].upper()}  {finding.id}  {row['group_label']}  {finding.status.value}", 11, "B", 6)
        _write(pdf, "What changed: " + row["headline"], 10, "", 5)
        if row["explanation"]:
            _write(pdf, f"AI explanation ({row['note'].id}): " + row["explanation"], 10, "I", 5)
        if row["todo"]:
            _write(pdf, "Test to run: " + row["todo"], 10, "", 5)
        if row["where"]:
            _write(pdf, "Where: " + row["where"], 10, "", 5)
        _write(pdf, f"Evidence: {finding.evidence}", 10, "", 5)
        if finding.dependency_path:
            _write(pdf, "Path: " + " | ".join(finding.dependency_path), 10, "", 5)
        for snippet in finding.attributes.get("test_snippets") or []:
            _write(pdf, snippet.get("title", "Test script"), 10, "B", 4)
            _write(pdf, snippet.get("sql", ""), 9, "", 4)
        pdf.ln(1)
    if issues["extra"]:
        _heading(pdf, "Additional notes from the AI")
        for finding in issues["extra"]:
            lines = review_lines(finding, report)
            _write(pdf, f"{finding.id}  AI investigator  {finding.status.value}", 11, "B", 6)
            if lines["change"]:
                _write(pdf, "What changes: " + lines["change"], 10, "", 5)
            if lines["todo"]:
                _write(pdf, "What to do: " + lines["todo"], 10, "", 5)
            pdf.ln(1)
    _heading(pdf, "Risk assessment")
    _write(pdf, "Deterministic decision. The AI investigator cannot change this recommendation.", 10, "", 5)
    _write(pdf, report.recommendation_label, 10, "", 5)
    if report.recommendation == Recommendation.ELIGIBLE_FOR_REVIEW:
        _write(
            pdf,
            "Eligible only after testing and human approval. This is not a certification that production is safe.",
            10,
            "",
            5,
        )
    _heading(pdf, "Required actions")
    for action in report.required_actions:
        _write(pdf, f"- {action}", 10, "", 5)
    _heading(pdf, "Known limitations")
    for item in report.limitations:
        _write(pdf, f"- {item}", 10, "", 5)
    for finding in report.findings:
        if finding.change_category == "coverage" and finding.origin != "investigator":
            _write(pdf, f"- {finding.id}: {finding.explanation}", 10, "", 5)
    _heading(pdf, "Source comparison")
    for diff in report.diffs:
        if diff.unchanged:
            continue
        _write(pdf, f"{diff.owner}.{diff.name} {diff.object_type}", 11, "B", 6)
        for row in diff.rows:
            if row.test_kind == "context" and row.prod_kind == "context":
                continue
            left = f"{row.test_line_no or '':>4} {row.test_text}" if row.test_kind != "empty" else ""
            right = f"{row.prod_line_no or '':>4} {row.prod_text}" if row.prod_kind != "empty" else ""
            _write(pdf, f"T {left}", 8, "", 4, "Courier")
            _write(pdf, f"P {right}", 8, "", 4, "Courier")
        pdf.ln(1)
    output = pdf.output()
    return bytes(output)


def _heading(pdf, text: str) -> None:
    pdf.ln(2)
    _write(pdf, text, 13, "B", 8)


def _write(pdf, text: str, size: int = 10, style: str = "", height: int = 6, family: str = "Helvetica") -> None:
    pdf.set_font(family, style, size)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, height, _ascii(text), new_x="LMARGIN", new_y="NEXT")


def _ascii(value: str) -> str:
    return value.encode("latin-1", errors="replace").decode("latin-1")
