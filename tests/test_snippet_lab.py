"""Run the generated Test scripts against a real lab database.

Skipped unless OIA_LAB_TEST_DSN, OIA_LAB_TEST_USER, and OIA_LAB_TEST_PASSWORD are
set, so the suite still passes without a database. Point it at the Test lab that
`lab/demo_schema` built. Every script must behave the way its banner says: it
runs, or it fails with the error the banner predicts.
"""

import os
import re
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OIA_LAB_TEST_DSN"),
    reason="Set OIA_LAB_TEST_DSN, OIA_LAB_TEST_USER, and OIA_LAB_TEST_PASSWORD to run the lab check.",
)

_DEMO_PACKAGES = ("DEMO.PKG_DISPATCH", "DEMO.PKG_BILLING", "DEMO.PKG_STATUS", "DEMO.PKG_ORDER", "DEMO.PKG_LOOKUP")


def _split(sql: str) -> list[str]:
    """Split a SQL*Plus script the way a client runs it: one command per slash."""
    chunks: list[str] = []
    buffer: list[str] = []
    for line in sql.splitlines():
        if line.strip() == "/":
            body = "\n".join(buffer).strip()
            if body:
                chunks.append(body)
            buffer = []
        elif line.startswith("--"):
            continue
        else:
            buffer.append(line)
    body = "\n".join(buffer).strip()
    if body:
        chunks.append(body)
    return [
        re.sub(r"^SET SERVEROUTPUT ON\s*", "", chunk, flags=re.IGNORECASE).strip()
        for chunk in chunks
        if chunk.strip()
    ]


def _collected() -> list[tuple[str, str, str]]:
    """(finding id, title, sql) for every generated script in the demo packages."""
    from tests.support import run_demo

    collected: list[tuple[str, str, str]] = []
    with tempfile.TemporaryDirectory() as directory:
        for package in _DEMO_PACKAGES:
            report = run_demo(Path(directory) / package.replace(".", "_"), [package])
            for finding in report.findings:
                for snippet in finding.attributes.get("test_snippets") or []:
                    collected.append((finding.id, snippet["title"], snippet["sql"]))
    return collected


def test_generated_scripts_run_or_fail_as_their_banner_says():
    import oracledb

    connection = oracledb.connect(
        user=os.environ.get("OIA_LAB_TEST_USER", "DEMO"),
        password=os.environ["OIA_LAB_TEST_PASSWORD"],
        dsn=os.environ["OIA_LAB_TEST_DSN"],
    )
    cursor = connection.cursor()
    checked = 0
    try:
        for finding_id, title, sql in _collected():
            header = " ".join(line[3:] for line in sql.splitlines() if line.startswith("-- "))
            expects_failure = "does not compile" in header
            for statement in _split(sql):
                try:
                    cursor.execute(statement)
                    if cursor.description:
                        cursor.fetchall()
                except oracledb.DatabaseError as error:
                    (info,) = error.args
                    assert expects_failure, (
                        f"{finding_id} {title} raised {info.code} "
                        f"{info.message.splitlines()[0]} but its banner predicts success:\n{sql}"
                    )
                    assert info.code == 6550, info.message
                    connection.rollback()
                checked += 1
    finally:
        cursor.close()
        connection.close()
    assert checked > 10, "the lab check did not collect the generated scripts"
