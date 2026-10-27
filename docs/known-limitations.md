# Known limitations

Static inspection does not prove runtime behavior. Reports say so.

- Dynamic SQL, `DBMS_SQL`, and `OPEN ... FOR` targets are unknown unless the statement text is literal and was not executed dynamically.
- Oracle q-quote literals, conditional compilation, and SQL hidden in strings can be missed or misread by the scanner.
- `ALL_DEPENDENCIES` is object-level. It does not provide column lineage or callers outside the database, including Java, external procedures, and ad hoc scripts.
- Synonyms and database links are reported as unresolved. They are not followed to a guessed target.
- A cross-database snapshot is not atomic. Drift is detected only when a second read of the same object returns a different checksum.
- Unquoted uppercase identifiers only. Quoted and lowercase names are rejected.
- Dependency walks stop at depth 2 and omit Oracle-supplied schemas.
- Signature compatibility for coordinated edits is a source heuristic: a selected caller is treated as updated when the new parameter name appears in its Test source. That can be fooled by a comment or an unused mention, and it cannot prove a type change was updated at the call site.
- An optional appended parameter is treated as backward compatible for positional and named PL/SQL calls. Dynamic callers that build argument lists are not inspected.
- Error-code comparison reads `RAISE_APPLICATION_ERROR` calls per routine name, so overloads with the same name are merged. A code passed through a constant is compared by the constant's name, not its value. `RAISE` of a named exception is not tracked per routine.
- `AUTHID` is read from the specification header. Without the clause Oracle's default, `DEFINER`, is assumed.
- Grant comparison is complete only when the account can read `DBA_TAB_PRIVS`. With `ALL_TAB_PRIVS` it sees only grants that involve the analysis account; such differences are marked potential and the report says so. Grants the package owner needs on objects in other schemas are not checked.
- Table triggers are listed from dictionary metadata. The report does not prove that a given statement fires them.
- No row data is read, so volume, data-quality, and financial-result claims stay potential.
- Investigator hypotheses are not confirmed. A citation that misses the snapshot cannot change the recommendation.
- AI leads on dynamic SQL and synonyms are guesses from source text and a few dictionary lookups. They do not follow database links, do not see statement text assembled from table data or parameters, and only check names that appear in the cited source. Synonyms used by callers and synonyms that point to a selected package are not asked about.
- Fixture mode results are computed from the bundled catalog. They are not evidence about a real schema.
- PDF layout uses fpdf2 when WeasyPrint’s native libraries are absent. The content matches the report model; the pagination is plain.
- One process and JSON files. Restarts lose in-flight work that has not been written. Multiple workers would race on run directories.
- Development authentication is a shared token. It must not be exposed on a network.

## Report page

- The investigator's private reasoning is filtered from the audit trail using the status activity log, which keeps the last 40 entries. A thought older than that could still appear in a phase line or event.
- Reports saved before the redesign counted AI notes in their stored severity totals. The page counts deterministic findings only and explains the difference when the two disagree.
- Monitoring plans, rollback plans, and escalation contacts are not recorded by the analyzer.
- Reports saved as schema 1.0 have no coverage, per-environment checksums, compilation, or saved policy. Their readiness is Indeterminate, their policy is computed when viewed, and classification shows "not recorded".

## Decision support

- Approval records carry a role and a self-declared name. Development sign-in does not identify a person, so these records are not an audit-grade signature. Enterprise SSO is required before relying on them.
- Anyone with the approver token is an approver. There is no separation of duties between the person who changes code and the person who approves it.
- Planned versus unexpected classification is only as good as the manifest and baselines approvers record. With no baseline, Production-only changes cannot be detected, and the page says so.
- Unclassified changes do not hold deployment. A team that wants every change pinned by the manifest must enforce that outside this application for now.
- Compilation readiness reads `ALL_OBJECTS` and `ALL_ERRORS` for in-scope packages and direct dependents only. Deeper dependents, and invalid states caused by grants or synonyms outside that set, are not checked.
- Dependents are found through `ALL_DEPENDENCIES` and the source scan. Callers outside the database remain a coverage gap.
- The Production existence check covers direct dictionary dependencies of the Test bodies. Objects named only inside dynamic SQL text, objects behind synonyms or database links, and objects that failed to resolve in Test (Oracle records no dependency row for them) are not looked up by this check. Tables in static SQL are still checked by the data impact rules. With `ALL_` views, an object the analysis account cannot see is reported as missing.
- SQL performance is not evaluated. `EXPLAIN PLAN` writes to `PLAN_TABLE`, which the read-only policy forbids. A future version could read `V$SQL_PLAN` or AWR with explicit approval.
- Transaction and data impact is derived from static statement order. It does not prove what happens at run time.
- Business impact comes only from mappings that approvers enter. Objects without one show "Business impact not established".
- The report lists tests to run. It does not run them, and it does not store results or reviewer decisions.
- Records are JSON files with an in-process lock. Multiple workers or hosts would race.
