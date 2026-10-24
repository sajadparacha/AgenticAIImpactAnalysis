"""Side-by-side textual diff. Test is the proposed version; Production is the baseline."""

import difflib

from oracle_impact.analysis.normalize import normalize_plsql
from oracle_impact.models.report import DiffRow, ObjectDiff


def build_object_diff(
    owner: str,
    name: str,
    object_type: str,
    test_source: str | None,
    prod_source: str | None,
    test_hash: str | None,
    prod_hash: str | None,
) -> ObjectDiff:
    test_text = test_source or ""
    prod_text = prod_source or ""
    test_lines = test_text.splitlines()
    prod_lines = prod_text.splitlines()
    matcher = difflib.SequenceMatcher(a=prod_lines, b=test_lines, autojunk=False)
    rows: list[DiffRow] = []
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                rows.append(
                    DiffRow(
                        test_line_no=j1 + offset + 1,
                        test_text=test_lines[j1 + offset],
                        test_kind="context",
                        prod_line_no=i1 + offset + 1,
                        prod_text=prod_lines[i1 + offset],
                        prod_kind="context",
                    )
                )
        elif tag == "insert":
            for offset, line in enumerate(test_lines[j1:j2]):
                added += 1
                rows.append(
                    DiffRow(
                        test_line_no=j1 + offset + 1,
                        test_text=line,
                        test_kind="added",
                        prod_kind="empty",
                    )
                )
        elif tag == "delete":
            for offset, line in enumerate(prod_lines[i1:i2]):
                removed += 1
                rows.append(
                    DiffRow(
                        prod_line_no=i1 + offset + 1,
                        prod_text=line,
                        prod_kind="removed",
                        test_kind="empty",
                    )
                )
        else:
            prod_chunk = prod_lines[i1:i2]
            test_chunk = test_lines[j1:j2]
            width = max(len(prod_chunk), len(test_chunk))
            for offset in range(width):
                row = DiffRow()
                if offset < len(test_chunk):
                    added += 1
                    row.test_line_no = j1 + offset + 1
                    row.test_text = test_chunk[offset]
                    row.test_kind = "added"
                if offset < len(prod_chunk):
                    removed += 1
                    row.prod_line_no = i1 + offset + 1
                    row.prod_text = prod_chunk[offset]
                    row.prod_kind = "removed"
                rows.append(row)
    raw_same = test_text.replace("\r\n", "\n") == prod_text.replace("\r\n", "\n")
    normalized_same = normalize_plsql(test_text) == normalize_plsql(prod_text)
    return ObjectDiff(
        owner=owner,
        name=name,
        object_type=object_type,
        test_hash=test_hash,
        prod_hash=prod_hash,
        added=added,
        removed=removed,
        formatting_only=normalized_same and not raw_same,
        unchanged=raw_same,
        rows=rows,
    )
