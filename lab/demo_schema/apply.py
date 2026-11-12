#!/usr/bin/env python3
"""Create the DEMO lab schema in Oracle for both Test and Production.

On this machine Test and Production are two editions of one PDB: Production is
ORA$BASE and Test is the OIA_TEST child edition. The DEMO user is edition
enabled, so the same DEMO.PKG_* names can hold a Production version and a Test
version at the same time, and the application reads the right one through
OIA_TEST_ORACLE_EDITION / OIA_PROD_ORACLE_EDITION.

The SQL files are plain Oracle SQL and can also be run directly:

    Production database:  01_shared.sql, 02_production.sql, 04_seed_data.sql
    Test database:        01_shared.sql, 03_test.sql, 04_seed_data.sql

Usage:
    .venv/bin/python lab/demo_schema/apply.py --docker-container codex-oracle-impact-demo
    .venv/bin/python lab/demo_schema/apply.py --dba-password "$ORACLE_PASSWORD"
    .venv/bin/python lab/demo_schema/apply.py --docker-container codex-oracle-impact-demo --clean
    .venv/bin/python lab/demo_schema/apply.py --seed-only

Separate Test and Production databases (for example two containers):
    OIA_READER_PASSWORD=... .venv/bin/python lab/demo_schema/apply.py \
        --docker-container codex-oracle-impact-demo \
        --dsn 127.0.0.1:1521/FREEPDB1 --test-dsn 127.0.0.1:1522/FREEPDB1 --clean
    Both databases must accept the same DBA password.

The DBA account is used only for bootstrap (edition, roles, user, grants). The
scripts themselves run as DEMO. The DBA password is read from the environment
or a container and is never printed or written to disk. The DEMO password is a
local demo default; override it with OIA_DEMO_PASSWORD or --demo-password.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

import oracledb

HERE = Path(__file__).resolve().parent
DEMO_USER = "DEMO"
DEMO_APP_ROLE = "DEMO_APP"
DEMO_DISPATCHER_ROLE = "DEMO_DISPATCHER"
DEMO_ORDER_SVC_ROLE = "DEMO_ORDER_SVC"
DEFAULT_EDITION = "OIA_TEST"
DEFAULT_DSN = "127.0.0.1:1521/FREEPDB1"
DEFAULT_DEMO_PASSWORD = "Demo_Local_1"

# ORA-00955 name is already used, ORA-01920 user exists, ORA-01921 role exists,
# ORA-01918 user does not exist, ORA-38812 editions already enabled.
IGNORED_EXISTS = (955, 1920, 1921, 1918, 38812)


class SetupError(RuntimeError):
    pass


def split_statements(text: str) -> list[str]:
    """Split on lines that contain only a slash. Everything else belongs to one
    statement, including the semicolons inside a PL/SQL block."""
    chunks: list[str] = []
    buffer: list[str] = []
    for line in text.splitlines():
        if line.strip() == "/":
            statement = "\n".join(buffer).strip()
            if statement:
                chunks.append(statement)
            buffer = []
        else:
            buffer.append(line)
    tail = "\n".join(buffer).strip()
    if tail:
        chunks.append(tail)
    return chunks


def execute(cur, sql: str, ignore: tuple[int, ...] = ()) -> bool:
    """Run one statement. Return False when it was ignored because it already exists."""
    try:
        cur.execute(sql)
        return True
    except oracledb.DatabaseError as exc:
        code = exc.args[0].code if exc.args else None
        if code in ignore:
            return False
        raise


def docker_secret(container: str) -> str:
    try:
        output = subprocess.check_output(
            ["docker", "inspect", container, "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SetupError(f"Could not read the environment of container {container}.") from exc
    for line in output.splitlines():
        if line.startswith("ORACLE_PASSWORD="):
            return line.split("=", 1)[1]
    raise SetupError(f"Container {container} has no ORACLE_PASSWORD.")


def dba_password(args) -> str:
    if args.dba_password:
        return args.dba_password
    for name in ("OIA_DBA_PASSWORD", "ORACLE_PASSWORD"):
        value = os.environ.get(name)
        if value:
            return value
    if args.docker_container:
        return docker_secret(args.docker_container)
    return getpass.getpass(f"Password for {args.dba_user}: ")


def connect(user: str, password: str, dsn: str, edition: str | None):
    try:
        return oracledb.connect(user=user, password=password, dsn=dsn, edition=edition)
    except oracledb.DatabaseError as exc:
        detail = exc.args[0].message.strip() if exc.args else str(exc)
        target = f"{user}@{dsn}" + (f" edition {edition}" if edition else "")
        raise SetupError(f"Could not connect as {target}: {detail}") from exc


def bootstrap(args, password: str, dsn: str, edition: str | None, clean: bool) -> None:
    """Create the edition (local mode), roles, DEMO user, and edition grants."""
    with connect(args.dba_user, password, dsn, None) as conn:
        cur = conn.cursor()
        if clean:
            execute(cur, f"DROP USER {DEMO_USER} CASCADE", ignore=(1918,))
            for role in (DEMO_APP_ROLE, DEMO_DISPATCHER_ROLE, DEMO_ORDER_SVC_ROLE):
                execute(cur, f"DROP ROLE {role}", ignore=(1919,))
            print(f"Removed the previous {DEMO_USER} user and demo roles.")
        if edition:
            execute(cur, f"CREATE EDITION {edition}", ignore=(955,))
        for role in (DEMO_APP_ROLE, DEMO_DISPATCHER_ROLE, DEMO_ORDER_SVC_ROLE):
            execute(cur, f"CREATE ROLE {role}", ignore=(1921,))
        created = execute(
            cur,
            f'CREATE USER {DEMO_USER} IDENTIFIED BY "{args.demo_password}" '
            f"DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS",
            ignore=(1920,),
        )
        if not created:
            execute(cur, f'ALTER USER {DEMO_USER} IDENTIFIED BY "{args.demo_password}"')
        execute(cur, f"ALTER USER {DEMO_USER} ENABLE EDITIONS", ignore=(38812,))
        execute(
            cur,
            f"GRANT CREATE SESSION, CREATE TABLE, CREATE PROCEDURE, CREATE SEQUENCE, "
            f"CREATE VIEW, CREATE SYNONYM, CREATE TRIGGER, CREATE TYPE TO {DEMO_USER}",
        )
        execute(cur, f"GRANT UNLIMITED TABLESPACE TO {DEMO_USER}")
        if args.reader_password:
            # Created only when missing, so an existing reader keeps its password.
            created_reader = execute(
                cur,
                f'CREATE USER {args.reader_user} IDENTIFIED BY "{args.reader_password}"',
                ignore=(1920,),
            )
            execute(cur, f"GRANT CREATE SESSION, SELECT ANY DICTIONARY TO {args.reader_user}")
            if created_reader:
                print(f"Created read-only account {args.reader_user} on {dsn}.")
        if edition:
            execute(cur, f"GRANT USE ON EDITION {edition} TO {DEMO_USER}", ignore=(1933, 955))
            # The application signs in as OIA_READER. Grant the edition when that
            # account already exists, but do not create it here.
            execute(
                cur,
                f"GRANT USE ON EDITION {edition} TO {args.reader_user}",
                ignore=(1917, 1918, 1920, 1933, 955, 6550),
            )
    print(f"Bootstrap ready on {dsn}: user {DEMO_USER}, edition {edition or 'none'}, roles {DEMO_APP_ROLE}/{DEMO_DISPATCHER_ROLE}/{DEMO_ORDER_SVC_ROLE}.")


def run_file(conn, filename: str, allow_compile_errors: bool = False) -> None:
    text = (HERE / filename).read_text(encoding="utf-8")
    statements = split_statements(text)
    with conn.cursor() as cur:
        for statement in statements:
            try:
                cur.execute(statement)
            except oracledb.DatabaseError as exc:
                code = exc.args[0].code if exc.args else None
                if allow_compile_errors and code in (24344, 6550):
                    print(f"  {filename}: expected compile error ({code}); object left INVALID.")
                    continue
                first_line = statement.splitlines()[0][:90]
                message = exc.args[0].message.strip() if exc.args else str(exc)
                raise SetupError(f"{filename}: {first_line}\n  {message}") from exc
    print(f"  ran {filename} ({len(statements)} statements)")


def rows(cur, sql: str, binds: dict | None = None):
    if binds:
        cur.execute(sql, binds)
    else:
        cur.execute(sql)
    return cur.fetchall()


SEED_TABLES = (
    "employee_status",
    "customer_bills",
    "billing_history_data",
    "outbox",
    "quote_book_prod",
    "quote_book_local",
    "orders",
    "order_audit_data",
    "customers",
)


def seed_data(conn, label: str) -> None:
    """Reset the tables to the synthetic rows in 04_seed_data.sql. Not used by the checks."""
    run_file(conn, "04_seed_data.sql")
    with conn.cursor() as cur:
        counts = ", ".join(f"{table} {rows(cur, f'SELECT COUNT(*) FROM demo.{table}')[0][0]}" for table in SEED_TABLES)
    print(f"  {label} rows: {counts}")


def verify(prod_conn, test_conn, edition: str) -> list[str]:
    """Check the dictionary shape the application depends on. Return problems."""
    problems: list[str] = []
    with prod_conn.cursor() as cur:
        prod_objects = rows(
            cur,
            "SELECT object_name, object_type, status FROM all_objects "
            "WHERE owner = 'DEMO' ORDER BY object_name, object_type",
        )
        prod_args = rows(
            cur,
            "SELECT object_name, argument_name, position, data_type FROM all_arguments "
            "WHERE owner = 'DEMO' AND package_name = 'PKG_STATUS' ORDER BY position",
        )
        prod_billing_args = rows(
            cur,
            "SELECT argument_name FROM all_arguments "
            "WHERE owner = 'DEMO' AND package_name = 'PKG_BILLING' AND argument_name IS NOT NULL ORDER BY position",
        )
        prod_grants = rows(
            cur,
            "SELECT grantee FROM all_tab_privs WHERE table_schema = 'DEMO' AND table_name = 'PKG_DISPATCH' ORDER BY grantee",
        )
        prod_order_grants = rows(
            cur,
            "SELECT grantee FROM all_tab_privs WHERE table_schema = 'DEMO' AND table_name = 'PKG_ORDER' ORDER BY grantee",
        )
        prod_synonym = rows(
            cur,
            "SELECT table_owner, table_name FROM all_synonyms WHERE owner = 'DEMO' AND synonym_name = 'QUOTE_BOOK'",
        )
    with test_conn.cursor() as cur:
        test_objects = rows(
            cur,
            "SELECT object_name, object_type, status FROM all_objects "
            "WHERE owner = 'DEMO' ORDER BY object_name, object_type",
        )
        test_args = rows(
            cur,
            "SELECT object_name, argument_name, position, data_type FROM all_arguments "
            "WHERE owner = 'DEMO' AND package_name = 'PKG_STATUS' ORDER BY position",
        )
        test_billing_args = rows(
            cur,
            "SELECT argument_name FROM all_arguments "
            "WHERE owner = 'DEMO' AND package_name = 'PKG_BILLING' AND argument_name IS NOT NULL ORDER BY position",
        )
        test_grants = rows(
            cur,
            "SELECT grantee FROM all_tab_privs WHERE table_schema = 'DEMO' AND table_name = 'PKG_DISPATCH' ORDER BY grantee",
        )
        test_order_grants = rows(
            cur,
            "SELECT grantee FROM all_tab_privs WHERE table_schema = 'DEMO' AND table_name = 'PKG_ORDER' ORDER BY grantee",
        )
        test_synonym = rows(
            cur,
            "SELECT table_owner, table_name FROM all_synonyms WHERE owner = 'DEMO' AND synonym_name = 'QUOTE_BOOK'",
        )
    prod_names = {(name, kind): status for name, kind, status in prod_objects}
    test_names = {(name, kind): status for name, kind, status in test_objects}

    def check(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    check(("PKG_LEGACY", "PACKAGE BODY") in test_names and test_names[("PKG_LEGACY", "PACKAGE BODY")] == "INVALID", "Test PKG_LEGACY body is not INVALID.")
    check(prod_names.get(("PKG_LEGACY", "PACKAGE BODY")) == "VALID", "Production PKG_LEGACY body is not VALID.")
    check(("PKG_ORDER_CALLER", "PACKAGE BODY") in test_names and test_names[("PKG_ORDER_CALLER", "PACKAGE BODY")] == "INVALID", "Test PKG_ORDER_CALLER body is not INVALID.")
    check(prod_names.get(("PKG_ORDER_CALLER", "PACKAGE BODY")) == "VALID", "Production PKG_ORDER_CALLER body is not VALID.")
    check(("BILLING_HISTORY", "VIEW") in test_names, "Test BILLING_HISTORY view is missing.")
    check(("BILLING_HISTORY", "VIEW") not in prod_names and ("BILLING_HISTORY", "TABLE") not in prod_names, "Production unexpectedly has BILLING_HISTORY.")
    check(("ORDER_AUDIT", "VIEW") in test_names, "Test ORDER_AUDIT view is missing.")
    check(("ORDER_AUDIT", "VIEW") not in prod_names and ("ORDER_AUDIT", "TABLE") not in prod_names, "Production unexpectedly has ORDER_AUDIT.")
    for name in ("NEXT_BATCH", "NEXT_DISPATCH_ID", "ORDER_FEE"):
        check((name, "FUNCTION") in test_names, f"Test function {name} is missing.")
        check((name, "FUNCTION") not in prod_names, f"Production unexpectedly has {name}.")
    check(len(prod_billing_args) == 2, f"Production PKG_BILLING has {len(prod_billing_args)} arguments, expected 2.")
    check(len(test_billing_args) == 4, f"Test PKG_BILLING has {len(test_billing_args)} arguments, expected 4.")
    check(len(prod_args) == 2, f"Production PKG_STATUS has {len(prod_args)} argument rows, expected 2.")
    check(len(test_args) == 3, f"Test PKG_STATUS has {len(test_args)} argument rows, expected 3.")
    prod_grant_names = {name for (name,) in prod_grants}
    test_grant_names = {name for (name,) in test_grants}
    check(prod_grant_names == {DEMO_APP_ROLE}, f"Production PKG_DISPATCH grants are {sorted(prod_grant_names)}, expected ['{DEMO_APP_ROLE}'].")
    check(test_grant_names == {DEMO_APP_ROLE, DEMO_DISPATCHER_ROLE}, f"Test PKG_DISPATCH grants are {sorted(test_grant_names)}, expected both roles.")
    prod_order_grant_names = {name for (name,) in prod_order_grants}
    test_order_grant_names = {name for (name,) in test_order_grants}
    check(prod_order_grant_names == {DEMO_APP_ROLE}, f"Production PKG_ORDER grants are {sorted(prod_order_grant_names)}, expected ['{DEMO_APP_ROLE}'].")
    check(test_order_grant_names == {DEMO_APP_ROLE, DEMO_ORDER_SVC_ROLE}, f"Test PKG_ORDER grants are {sorted(test_order_grant_names)}, expected both roles.")
    check(prod_synonym == [("DEMO", "QUOTE_BOOK_PROD")], f"Production QUOTE_BOOK target is {prod_synonym}.")
    check(test_synonym == [("DEMO", "QUOTE_BOOK_LOCAL")], f"Test QUOTE_BOOK target is {test_synonym}.")

    print()
    print(f"  {'object':22} {'Production':14} Test (edition {edition or 'none'})")
    for name, kind in sorted({(n, k) for n, k, _ in prod_objects} | {(n, k) for n, k, _ in test_objects}):
        if kind not in ("PACKAGE", "PACKAGE BODY", "VIEW", "FUNCTION", "SYNONYM", "SEQUENCE", "TABLE"):
            continue
        print(f"  {name + ' ' + kind:22} {prod_names.get((name, kind), '-'):14} {test_names.get((name, kind), '-')}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the DEMO demo schema for Test and Production.")
    parser.add_argument("--dsn", default=os.environ.get("OIA_DEMO_DSN", DEFAULT_DSN), help="Production DSN (or the single DSN when both are editions).")
    parser.add_argument("--test-dsn", default="", help="Test DSN when Test is a separate database.")
    parser.add_argument("--edition", default=DEFAULT_EDITION, help="Test edition name when both environments share one database.")
    parser.add_argument("--dba-user", default="SYSTEM")
    parser.add_argument("--dba-password", default="")
    parser.add_argument("--docker-container", default="", help="Read the DBA password from this container's ORACLE_PASSWORD.")
    parser.add_argument("--demo-password", default=os.environ.get("OIA_DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD))
    parser.add_argument("--reader-user", default="OIA_READER", help="Application account that needs USE on the Test edition.")
    parser.add_argument(
        "--reader-password",
        default=os.environ.get("OIA_READER_PASSWORD", ""),
        help="Create the read-only application account where it is missing (CREATE SESSION, SELECT ANY DICTIONARY).",
    )
    parser.add_argument("--clean", action="store_true", help="Drop the existing DEMO user and roles first.")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only reset the tables to the synthetic data. Connects as DEMO; no DBA password needed.",
    )
    args = parser.parse_args()
    args.demo_password = args.demo_password or DEFAULT_DEMO_PASSWORD
    test_dsn = args.test_dsn.strip() or args.dsn
    split = test_dsn != args.dsn
    edition = None if split else (args.edition.strip().upper() or None)
    if not split and not edition:
        raise SetupError("Name the Test edition with --edition, or give --test-dsn for a separate Test database.")

    if args.seed_only:
        with connect(DEMO_USER, args.demo_password, args.dsn, None if split else "ORA$BASE") as prod:
            seed_data(prod, "Production" if split else "Shared")
        if split:
            with connect(DEMO_USER, args.demo_password, test_dsn, None) as test:
                seed_data(test, "Test")
        return 0

    password = dba_password(args)
    mode = f"separate databases ({args.dsn} -> {test_dsn})" if split else f"one database, Test edition {edition}"
    print(f"Mode: {mode}")
    bootstrap(args, password, args.dsn, edition, args.clean)
    if split:
        bootstrap(args, password, test_dsn, None, args.clean)

    print("Production scripts:")
    prod = connect(DEMO_USER, args.demo_password, args.dsn, "ORA$BASE" if not split else None)
    test = connect(DEMO_USER, args.demo_password, test_dsn, edition)
    try:
        run_file(prod, "01_shared.sql")
        run_file(prod, "02_production.sql")
        if split:
            run_file(test, "01_shared.sql")
        print("Test scripts:")
        run_file(test, "03_test.sql", allow_compile_errors=True)
        print("Synthetic data:")
        seed_data(prod, "Production" if split else "Shared")
        if split:
            seed_data(test, "Test")
        if not args.skip_verify:
            problems = verify(prod, test, edition)
    finally:
        prod.close()
        test.close()

    if not args.skip_verify and problems:
        print()
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        return 1
    if not args.skip_verify:
        print()
        print("DEMO schema is ready.")
        print("Analyze DEMO.PKG_STATUS, DEMO.PKG_BILLING, DEMO.PKG_DISPATCH, DEMO.PKG_ORDER, and DEMO.PKG_LOOKUP with OIA_FIXTURE_MODE=false.")
        if split:
            print(f"Keep OIA_TEST_ORACLE_DSN={test_dsn} and OIA_PROD_ORACLE_DSN={args.dsn}, and leave both edition settings empty.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
