import re

from oracle_impact.infrastructure.oracle.queries import QUERIES, SESSION_SQL

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|ALTER|DROP|CREATE|TRUNCATE|GRANT|REVOKE|EXECUTE|CALL|BEGIN)\b",
    re.IGNORECASE,
)


def test_dba_rewrite_stays_read_only():
    from oracle_impact.infrastructure.oracle.queries import apply_dictionary_views

    rewritten = apply_dictionary_views(QUERIES["source"], "dba")
    assert "dba_source" in rewritten
    assert "all_source" not in rewritten
    assert rewritten.strip().upper().startswith("SELECT")
    assert apply_dictionary_views(QUERIES["source"], "all") == QUERIES["source"]


def test_catalog_is_select_only():
    assert SESSION_SQL == "SET TRANSACTION READ ONLY"
    assert QUERIES
    for name, sql in QUERIES.items():
        compact = " ".join(sql.split())
        assert compact.upper().startswith("SELECT"), name
        assert ";" not in sql
        assert _FORBIDDEN.search(sql) is None, name


def test_synonym_read_uses_the_fixed_catalog_and_the_dba_rewrite():
    from oracle_impact.infrastructure.oracle.queries import apply_dictionary_views

    sql = QUERIES["synonym_target"]
    assert ":owner" in sql and ":name" in sql
    assert "dba_synonyms" in apply_dictionary_views(sql, "dba")


def test_live_gateway_does_not_interpolate_sql():
    source = Path_text()
    assert 'f"SELECT' not in source
    assert "f'SELECT" not in source
    assert ".format(" not in source
    assert "QUERIES[" in source


def Path_text() -> str:
    from pathlib import Path

    return Path("src/oracle_impact/infrastructure/oracle/oracledb_gateway.py").read_text(encoding="utf-8")
