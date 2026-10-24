# Architecture

The application is one Python process. FastAPI controllers accept a package list, create a run, and render Jinja templates. `AnalysisService` owns the pipeline.

```
browser → controllers → AnalysisService
                      → SnapshotService → read-only gateways (Test, Production, optional AUTUMN)
                      → diff, scanner, signatures, dependencies, impacts
                      → compilation status, transaction profiles, coverage, edge provenance
                      → fingerprints and rule ids (annotate)
                      → recommendation policy and blocking rules
                      → optional investigator → allowlisted tools
                      → evidence validator
                      → RunStore and HTML / PDF / JSON renderers
        controllers → DecisionService → readiness, change classification, history, business impact
                                      → ReviewStore (runs/<id>/review.json), Registry (_registry/*.json)
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
- The human deployment decision is the latest approval record for the run, or "Not recorded".
- `initial_state` validates the `tab`, `finding`, `object`, `line`, `side`, `mode`, and `cmp` query parameters against the report, so deep links render the right panel without JavaScript.

`services/decision_view.py` adds the decision-support layer to that dictionary from a context built by `DecisionService.context()`. Standalone rendering (tests, old callers) uses `offline_context()`, which has no records and no registry.

`report_body.html` composes seven panels (`_report_overview.html`, `_report_findings.html`, `_report_objects.html`, `_report_source.html`, `_report_dependencies.html`, `_report_validation.html`, `_report_audit.html`) and a finding drawer (`_finding_detail.html`). The print and PDF-HTML exports reuse the same partials with every panel and finding detail expanded and without forms. `app.js` adds tab switching, filters, sorting, the drawer, and URL state as progressive enhancement under a CSP that forbids inline script and style.

## Decision support

### Report schema 1.1

New `Report` sections are additive and default to empty, so 1.0 reports still load: `environments`, `object_states` (per-environment checksum of each in-scope object), `comparisons` (Test to AUTUMN and AUTUMN to Production diffs), `compilation`, `coverage`, `transaction_profiles`, `performance`, `dependency_edges`, `policy`, and `external_checks`. Findings gain `rule_id` (for example `SIG-ADDED-PARAMETER`, `CMP-DEPENDENT`, `TXN-PARTIAL-COMMIT`), `fingerprint` (`FP-` plus 16 hex characters of a SHA-256 over owner, name, type, category, rule id, and evidence, so it is stable across runs), and `severity_rationale`.

### Blocking rules and readiness

`services/readiness.py` holds the rules (`BLK-*` blockers, `VAL-*` validations, policy version `2026.10-1`). They run once on deterministic findings when the analysis finishes and the result is saved as `report.policy`. Investigator findings are skipped. `OIA_POLICY_FILE` may disable a rule or change its resolution; it cannot add rules.

Each rule has a resolution:

- `fix_and_reanalyze`: only a new analysis clears it (interface breaks, missing objects, compile failures, snapshot drift).
- `validation_and_review`: a passed validation and a reviewer decision of Accepted (partial-commit hazards).
- `validation`: a passed validation, or a reviewer decision of Not an issue with a reason.

Readiness is evaluated each time the report is viewed:

1. **Indeterminate** when the run did not complete, coverage is missing (1.0 reports) or has inaccessible or failed objects, a newer run of the same packages exists, or the snapshot is older than `OIA_SNAPSHOT_STALE_HOURS`.
2. **Deployment on hold** when any blocker, required validation, or unexpected drift is unresolved.
3. **Deployment recommended** otherwise. Human approval is still required, and is recorded separately.

The investigator cannot change any input to this evaluation.

### Planned change, unexpected drift, unclassified

`services/change_classification.py` compares each object's checksums with the registry:

- **Planned**: the Test checksum equals the checksum pinned by an approved manifest entry.
- **Unexpected drift**: Production or AUTUMN no longer matches its active trusted baseline, or Test no longer matches the checksum pinned when the change was approved. Drift in Production that Test does not share is flagged production-only.
- **Unclassified**: any other difference. Being in Test does not make a change planned.

Baseline states are available, partial, outdated (older than `OIA_BASELINE_MAX_AGE_DAYS`), unavailable, and not recorded (1.0 reports). Drift holds deployment; unclassified changes do not (see `docs/decisions.md`).

### Compilation readiness

`ALL_OBJECTS.STATUS` and `ALL_ERRORS` are read for the in-scope packages and their direct dependents in Test and Production. Nothing is compiled. An in-scope object that is invalid in Test is a critical confirmed finding; a dependent that is invalid in Test but valid in Production is high. Both are `BLK-COMPILE-FAILURE` blockers. A dependent that is invalid in both environments, or an in-scope object invalid only in Production, is a medium finding. It becomes a required validation, which a recorded result or reviewer decision can clear without a new analysis. If the views cannot be read, compilation is "not evaluated" and a limitation is added.

### Coverage

The denominator is every specification and body of each selected package. Object coverage (analyzed, partial, skipped, inaccessible, failed) is reported separately from dependency coverage (dictionary edges, source-scan edges, unresolved references, and external callers that cannot be seen).

### Records and roles

- `runs/<id>/review.json` (`ReviewStore`): validation results, reviewer decisions, approval decisions, and an append-only event list.
- `<OIA_STATE_DIR or runs/_registry>/` (`Registry`): `baselines.json`, `manifest.json`, `mappings.json`, `versions.json` (object checksum history per run), and `events.json`.

Analysts (application token) record validations and reviewer decisions. Approvers (`OIA_APPROVER_ACCESS_TOKEN`) also record approval decisions, trusted baselines, manifest entries, and business mappings. Approval as "approved" is rejected unless readiness is Deployment recommended. Every record carries the role and a self-declared name, because development sign-in does not identify a person.

### History

Runs of the same package set are ordered by snapshot time, then request time. The previous run is compared by fingerprint (new, resolved, unchanged, severity changed), by blocker, and by object checksum.

## Pipeline

1. Validate uppercase `OWNER.PACKAGE` names.
2. Read package specifications and bodies from Test and Production. Hash them, read them again, and record drift.
3. Build a side-by-side diff and a normalized comparison.
4. Compare `ALL_ARGUMENTS` shapes, including overloads, defaults, modes, and types.
5. Walk dependencies to a depth of 2 in both directions, skipping Oracle-supplied schemas.
6. Classify SQL, transaction, exception, dynamic SQL, and business-rule changes. Compare the error contract (`RAISE_APPLICATION_ERROR` codes per routine, public exceptions, `PRAGMA EXCEPTION_INIT` codes), the `AUTHID` clause, and object grants on each package.
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
- `ALL_TAB_PRIVS`, or `DBA_TAB_PRIVS` when the account can read it
- `ALL_ERRORS` (compilation readiness)

`DBA_*` views are not required. Each live call starts with `SET TRANSACTION READ ONLY`. AUTUMN uses the same catalog through a third read-only connection.

## Storage

`runs/<uuid>/` holds status, the request, the snapshot, findings, the tool log, the report, and `review.json`. The tool log records tool names, sizes, and times, not source text. The registry lives in `runs/_registry/` unless `OIA_STATE_DIR` is set. The run store skips directories that start with `_` or `.`, and run identifiers containing path separators are rejected. `runs/` is gitignored.

## Authentication

Development access is an `httpOnly` cookie `role.nonce.signature` issued after an access token matches. Cookies issued before roles existed (`nonce.signature`) are accepted as analyst sessions. Every form uses an HMAC CSRF token tied to that cookie, and every write redirects back with a fixed message code. This is not enterprise SSO. A shared deployment needs a reverse proxy or OIDC integration before it is exposed beyond loopback.
