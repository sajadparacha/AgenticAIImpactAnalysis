#!/usr/bin/env python3
"""Create the case packages in the local Test and Production lab.

The SQL is plain Oracle SQL and can also be run by hand:

    Production (or the base edition):  01_production_cases.sql
    Test (or the Test edition):        02_test_cases.sql

This script connects as the DEMO schema owner, because it creates packages; the
read-only application account cannot run it. Build the lab first with
lab/demo_schema/apply.py. The DSNs default to the application's .env
(OIA_PROD_ORACLE_DSN, OIA_TEST_ORACLE_DSN) when the project is installed.

Usage:
    .venv/bin/python lab/cases/apply_cases.py
    .venv/bin/python lab/cases/apply_cases.py --demo-password "<DEMO password>"
    .venv/bin/python lab/cases/apply_cases.py  \
        --dsn 127.0.0.1:1521/FREEPDB1 --test-dsn 127.0.0.1:1522/FREEPDB1
    .venv/bin/python lab/cases/apply_cases.py --dsn 127.0.0.1:1521/FREEPDB1 --edition OIA_TEST
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

import oracledb

HERE = Path(__file__).resolve().parent
HELPERS = HERE.parent / "demo_schema" / "apply.py"

CASE_PACKAGES = ("PKG_PRICE", "PKG_SETTLE", "PKG_REFUND", "PKG_NOTIFY")
CASE_OBJECTS = (*CASE_PACKAGES, "REFUND_LEDGER", "REFUND_LEDGER_DATA")
REQUIRED_TABLES = ("CUSTOMER_BILLS", "OUTBOX")


def load_helpers():
    """Reuse the demo schema script's connection, splitter, and password defaults."""
    spec = importlib.util.spec_from_file_location("oia_demo_apply", HELPERS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {HELPERS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helpers()


def app_settings():
    """The application's .env when the project is installed. Never fatal."""
    try:
        from oracle_impact.config import Settings

        return Settings()
    except Exception:
        return None


def require_prerequisites(conn, label: str) -> None:
    """The case packages insert into the tables lab/demo_schema/01_shared.sql creates."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT table_name FROM user_tables")
        found = {name for (name,) in cursor.fetchall()}
    missing = [name for name in REQUIRED_TABLES if name not in found]
    if missing:
        raise HELPER.SetupError(
            f"The {label} {HELPER.DEMO_USER} schema is missing {', '.join(missing)}. "
            "Build the lab first with lab/demo_schema/apply.py, then run this again."
        )


def run_file(conn, path: Path) -> None:
    statements = HELPER.split_statements(path.read_text(encoding="utf-8"))
    with conn.cursor() as cursor:
        for statement in statements:
            try:
                cursor.execute(statement)
            except oracledb.DatabaseError as exc:
                message = exc.args[0].message.strip() if exc.args else str(exc)
                first_line = statement.splitlines()[0][:90]
                raise HELPER.SetupError(f"{path.name}: {first_line}\n  {message}") from exc
    print(f"  ran {path.name} ({len(statements)} statements)")


def objects(conn) -> dict[tuple[str, str], str]:
    placeholders = ", ".join(f"'{name}'" for name in CASE_OBJECTS)
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT object_name, object_type, status FROM all_objects "
            f"WHERE owner = 'DEMO' AND object_name IN ({placeholders})"
        )
        return {(name, kind): status for name, kind, status in cursor.fetchall()}


def verify(prod, test) -> list[str]:
    """Check the dictionary shape the four cases depend on."""
    prod_objects = objects(prod)
    test_objects = objects(test)
    problems: list[str] = []
    for name in CASE_PACKAGES:
        for kind in ("PACKAGE", "PACKAGE BODY"):
            if prod_objects.get((name, kind)) != "VALID":
                problems.append(f"Production {name} {kind} is {prod_objects.get((name, kind), 'missing')}.")
            if test_objects.get((name, kind)) != "VALID":
                problems.append(f"Test {name} {kind} is {test_objects.get((name, kind), 'missing')}.")
    if ("REFUND_LEDGER", "VIEW") not in test_objects:
        problems.append("Test REFUND_LEDGER view is missing.")
    if ("REFUND_LEDGER", "VIEW") in prod_objects or ("REFUND_LEDGER", "TABLE") in prod_objects:
        problems.append("Production unexpectedly has REFUND_LEDGER.")

    print()
    print(f"  {'object':28} {'Production':14} Test")
    for name, kind in sorted(set(prod_objects) | set(test_objects)):
        print(
            f"  {name + ' ' + kind:28} "
            f"{prod_objects.get((name, kind), '-'):14} "
            f"{test_objects.get((name, kind), '-')}"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the DEMO case packages in Test and Production.")
    parser.add_argument("--dsn", default="", help="Production DSN. Defaults to OIA_PROD_ORACLE_DSN.")
    parser.add_argument("--test-dsn", default="", help="Test DSN. Defaults to OIA_TEST_ORACLE_DSN, then --dsn.")
    parser.add_argument("--edition", default="", help="Test edition name when both environments share one database.")
    parser.add_argument("--demo-password", default="", help="Password of the DEMO schema owner.")
    parser.add_argument("--skip-verify", action="store_true")
    args = parser.parse_args()

    configured = app_settings()
    prod_dsn = (
        args.dsn
        or (configured.prod_oracle_dsn if configured else "")
        or os.environ.get("OIA_PROD_ORACLE_DSN", "")
        or HELPER.DEFAULT_DSN
    )
    test_dsn = (
        args.test_dsn
        or (configured.test_oracle_dsn if configured else "")
        or os.environ.get("OIA_TEST_ORACLE_DSN", "")
        or prod_dsn
    )
    split = test_dsn != prod_dsn
    edition = None if split else (
        args.edition
        or (configured.test_oracle_edition if configured else "")
        or HELPER.DEFAULT_EDITION
    )
    password = args.demo_password or os.environ.get("OIA_DEMO_PASSWORD") or HELPER.DEFAULT_DEMO_PASSWORD

    mode = f"separate databases ({prod_dsn} -> {test_dsn})" if split else f"one database, Test edition {edition}"
    print(f"Mode: {mode}")

    prod = HELPER.connect(HELPER.DEMO_USER, password, prod_dsn, None if split else "ORA$BASE")
    test = HELPER.connect(HELPER.DEMO_USER, password, test_dsn, edition)
    try:
        require_prerequisites(prod, "Production")
        require_prerequisites(test, "Test")
        print("Production cases:")
        run_file(prod, HERE / "01_production_cases.sql")
        print("Test cases:")
        run_file(test, HERE / "02_test_cases.sql")
        problems = [] if args.skip_verify else verify(prod, test)
    finally:
        prod.close()
        test.close()

    if problems:
        print()
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        return 1
    if not args.skip_verify:
        print()
        print("Case packages are ready. Analyze these with OIA_FIXTURE_MODE=false, one per line:")
        for name in CASE_PACKAGES:
            print(f"  DEMO.{name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HELPER.SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
