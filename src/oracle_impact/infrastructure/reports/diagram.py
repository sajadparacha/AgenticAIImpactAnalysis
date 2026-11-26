"""Small dependency diagram. Text is escaped before it is placed in the SVG."""

import html

from oracle_impact.models.report import DependencyRow

_SHORT_TYPE = {
    "PACKAGE": "spec",
    "PACKAGE BODY": "body",
    "PROCEDURE": "procedure",
    "FUNCTION": "function",
    "TABLE": "table",
    "VIEW": "view",
    "SYNONYM": "synonym",
    "TRIGGER": "trigger",
    "SEQUENCE": "sequence",
    "TYPE": "type",
}


def _type_suffix(object_type: str) -> str:
    key = (object_type or "").upper()
    return _SHORT_TYPE.get(key, (object_type or "").lower())


def dependency_diagram(packages: list[str], rows: list[DependencyRow]) -> str:
    callers = [row for row in rows if row.direction == "caller"][:6]
    uses = [row for row in rows if row.direction == "uses"][:6]
    width = 760
    height = 80 + max(len(packages), len(callers), len(uses), 1) * 42
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
        f"<title>Dependency diagram for {html.escape(', '.join(packages))}</title>",
        f'<rect width="{width}" height="{height}" rx="16" fill="#f8fbfe"/>',
    ]
    parts.extend(_links(len(callers), len(packages), len(uses)))
    parts.extend(_column(24, "Callers", callers, "#1d4e89", show_type=True))
    parts.extend(_column(268, "Selected packages", _package_rows(packages), "#12365c"))
    parts.extend(_column(512, "Referenced objects", uses, "#475569", show_type=True))
    parts.append("</svg>")
    return "".join(parts)


def _package_rows(packages: list[str]) -> list[DependencyRow]:
    rows = []
    for package in packages:
        owner, _, name = package.partition(".")
        rows.append(
            DependencyRow(
                direction="selected",
                object_type="PACKAGE",
                object_name=name,
                owner=owner,
                usage="",
                impact="",
                depth=0,
                database="",
            )
        )
    return rows


def _links(callers: int, packages: int, uses: int) -> list[str]:
    lines = []
    pairs = max(callers, packages, 1)
    for index in range(min(pairs, 6)):
        y = 56 + index * 42
        lines.append(f'<path d="M234 {y} C250 {y}, 252 {56 if packages == 1 else y}, 268 {56 if packages == 1 else y}" fill="none" stroke="#b7c9dc" stroke-width="2"/>')
    for index in range(min(max(uses, 1), 6)):
        y = 56 + index * 42
        lines.append(f'<path d="M478 {56 if packages == 1 else y} C494 {56 if packages == 1 else y}, 496 {y}, 512 {y}" fill="none" stroke="#b7c9dc" stroke-width="2"/>')
    return lines


def _column(x: int, title: str, rows: list[DependencyRow], color: str, show_type: bool = False) -> list[str]:
    items = [
        f'<text x="{x}" y="22" fill="#526173" font-size="12" font-family="Segoe UI, sans-serif">{html.escape(title)}</text>'
    ]
    if not rows:
        items.append(
            f'<text x="{x}" y="58" fill="#526173" font-size="12" font-family="Segoe UI, sans-serif">None recorded</text>'
        )
        return items
    for index, row in enumerate(rows):
        y = 40 + index * 42
        label = html.escape(f"{row.owner}.{row.object_name}")
        if show_type:
            suffix = _type_suffix(row.object_type)
            if suffix:
                label += f" ({html.escape(suffix)})"
        items.append(
            f'<rect x="{x}" y="{y}" width="210" height="32" rx="8" fill="{color}"/>'
            f'<text x="{x + 12}" y="{y + 21}" fill="#ffffff" font-size="12" font-family="Segoe UI, sans-serif">{label}</text>'
        )
    return items
