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
- Table triggers are listed from dictionary metadata. The report does not prove that a given statement fires them.
- No row data is read, so volume, data-quality, and financial-result claims stay potential.
- Investigator hypotheses are not confirmed. A citation that misses the snapshot cannot change the recommendation.
- Fixture mode results are computed from the bundled catalog. They are not evidence about a real schema.
- PDF layout uses fpdf2 when WeasyPrint’s native libraries are absent. The content matches the report model; the pagination is plain.
- One process and JSON files. Restarts lose in-flight work that has not been written. Multiple workers would race on run directories.
- Development authentication is a shared token. It must not be exposed on a network.
