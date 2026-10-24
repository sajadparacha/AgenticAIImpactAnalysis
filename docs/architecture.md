# Architecture

The application is one Python process. FastAPI controllers accept a package list, create a run, and render Jinja templates. `AnalysisService` owns the pipeline.

```
browser → controllers → AnalysisService
                      → SnapshotService → read-only gateway
                      → diff, scanner, signatures, dependencies, impacts
                      → recommendation policy
                      → optional investigator → allowlisted tools
                      → evidence validator
                      → RunStore and HTML / PDF / JSON renderers
```

## Layers

| Layer | Location | Responsibility |
| --- | --- | --- |
| Model | `models/` | Packages, snapshots, findings, reports |
| View | `views/` | Dashboard, progress, report, finding, print layout |
| Controller | `controllers/web.py` | Auth, forms, status, downloads |
| Services | `services/` | Orchestration, snapshots, recommendation policy |
| Analysis | `analysis/` | Deterministic comparison |
| Infrastructure | `infrastructure/` | Oracle, storage, investigator, PDF |

Controllers do not contain SQL or prompts.

## Report presentation

`services/report_view.py` turns a saved `Report` and its `status.json` into the dictionary the report templates render. It groups, counts, links, and labels what the run recorded. It does not re-run the policy, change a severity, or fill gaps; missing data is returned as an explicit "not recorded" or "not mapped" state.

- Findings behind the recommendation come from the persisted `recommendation_reasons`, not from a second policy pass.
- Counts cover deterministic findings only. Investigator notes are attached to the finding they review; a note without a deterministic anchor is listed as "AI assessment only" and is never rated or counted.
- The human deployment decision is always "Not recorded" because no approval is persisted.
- `initial_state` validates the `tab`, `finding`, `object`, `line`, `side`, and `mode` query parameters against the report, so deep links render the right panel without JavaScript.

`report_body.html` composes six panels (`_report_overview.html`, `_report_findings.html`, `_report_source.html`, `_report_dependencies.html`, `_report_validation.html`, `_report_audit.html`) and a finding drawer (`_finding_detail.html`). The print and PDF-HTML exports reuse the same partials with every panel and finding detail expanded. `app.js` adds tab switching, filters, sorting, the drawer, and URL state as progressive enhancement under a CSP that forbids inline script and style.

## Pipeline

1. Validate uppercase `OWNER.PACKAGE` names.
2. Read package specifications and bodies from Test and Production. Hash them, read them again, and record drift.
3. Build a side-by-side diff and a normalized comparison.
4. Compare `ALL_ARGUMENTS` shapes, including overloads, defaults, modes, and types.
5. Walk dependencies to a depth of 2 in both directions, skipping Oracle-supplied schemas.
6. Classify SQL, transaction, exception, dynamic SQL, and business-rule changes.
7. Apply the recommendation policy in code.
8. If egress is enabled, one investigator may call `get_package_source`, `get_dependencies`, `get_object_source`, `get_table_metadata`, and `get_procedure_arguments`.
9. Validate citations. Investigator output cannot remain confirmed.

## Recommendation labels

- Not recommended for deployment
- Requires further investigation
- Eligible for review after testing

Eligible still requires human approval and functional tests. The policy does not certify production safety.

Confirmed breaking signatures, confirmed missing Production objects, and scanner-flagged partial-commit hazards are not recommended. High or medium potential findings, dynamic SQL, inaccessible metadata, unresolved synonyms, and snapshot drift require investigation.

A new optional parameter appended with a default is not a break. A mandatory parameter whose visible database callers are all in the submitted change set and already pass the new argument is not reported as an unavoidable break. Callers outside that set still are. Callers outside the database remain a coverage gap.

## Oracle reads

Fixed queries in `infrastructure/oracle/queries.py` use bind variables against:

- `ALL_OBJECTS`
- `ALL_SOURCE`
- `ALL_DEPENDENCIES`
- `ALL_ARGUMENTS`
- `ALL_TAB_COLUMNS`
- `ALL_CONSTRAINTS`
- `ALL_CONS_COLUMNS`
- `ALL_TRIGGERS`

`DBA_*` views are not required. Each live call starts with `SET TRANSACTION READ ONLY`.

## Storage

`runs/<uuid>/` holds status, the request, the snapshot, findings, the tool log, and the report. The tool log records tool names and sizes, not source text. The directory is gitignored.

## Authentication

Development access is an `httpOnly` cookie issued after the configured access token matches. The analysis form uses an HMAC CSRF token tied to that cookie. This is not enterprise SSO. A shared deployment needs a reverse proxy or OIDC integration before it is exposed beyond loopback.
