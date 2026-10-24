"""Fixed dictionary queries.

Every statement here must be a single read. The Oracle gateway may also
issue the session statement in SESSION_SQL. Nothing in this catalog may
be built from model output or user-supplied SQL.
"""

SESSION_SQL = "SET TRANSACTION READ ONLY"

DICTIONARY_VIEWS = (
    "all_objects",
    "all_source",
    "all_dependencies",
    "all_arguments",
    "all_tab_columns",
    "all_constraints",
    "all_cons_columns",
    "all_triggers",
)


def apply_dictionary_views(sql: str, prefix: str) -> str:
    """Use DBA dictionary views when that grant exists. Otherwise keep ALL views."""
    if prefix == "all":
        return sql
    if prefix != "dba":
        raise ValueError("Dictionary prefix must be all or dba.")
    rewritten = sql
    for name in DICTIONARY_VIEWS:
        rewritten = rewritten.replace(name, "dba_" + name[4:])
    return rewritten

QUERIES: dict[str, str] = {
    "object_status": """
SELECT object_name, object_type, status
FROM all_objects
WHERE owner = :owner
  AND object_name = :name
  AND object_type = :object_type
""",
    "source": """
SELECT line, text
FROM all_source
WHERE owner = :owner
  AND name = :name
  AND type = :object_type
ORDER BY line
""",
    "outgoing_dependencies": """
SELECT referenced_owner, referenced_name, referenced_type, referenced_link_name
FROM all_dependencies
WHERE owner = :owner
  AND name = :name
  AND type = :object_type
""",
    "incoming_dependencies": """
SELECT owner, name, type, referenced_owner, referenced_name, referenced_type
FROM all_dependencies
WHERE referenced_owner = :owner
  AND referenced_name = :name
""",
    "arguments": """
SELECT object_name, overload, argument_name, position, data_type, in_out, defaulted
FROM all_arguments
WHERE owner = :owner
  AND package_name = :package_name
ORDER BY object_name, overload, position
""",
    "columns": """
SELECT column_name, data_type, nullable, column_id
FROM all_tab_columns
WHERE owner = :owner
  AND table_name = :table_name
ORDER BY column_id
""",
    "constraints": """
SELECT c.constraint_name, c.constraint_type, c.r_constraint_name, c.status,
       cc.column_name, cc.position
FROM all_constraints c
JOIN all_cons_columns cc
  ON cc.owner = c.owner
 AND cc.constraint_name = c.constraint_name
WHERE c.owner = :owner
  AND c.table_name = :table_name
ORDER BY c.constraint_name, cc.position
""",
    "referenced_constraint": """
SELECT table_name
FROM all_constraints
WHERE owner = :owner
  AND constraint_name = :constraint_name
""",
    "triggers": """
SELECT trigger_name, trigger_type, triggering_event, status
FROM all_triggers
WHERE table_owner = :owner
  AND table_name = :table_name
ORDER BY trigger_name
""",
    # ALL_TAB_PRIVS and DBA_TAB_PRIVS name the owner column differently, so the
    # prefix rewrite cannot be used. The gateway picks one of these two.
    "object_grants": """
SELECT grantee, privilege
FROM all_tab_privs
WHERE table_schema = :owner
  AND table_name = :name
ORDER BY grantee, privilege
""",
    "object_grants_dba": """
SELECT grantee, privilege
FROM dba_tab_privs
WHERE owner = :owner
  AND table_name = :name
ORDER BY grantee, privilege
""",
}
