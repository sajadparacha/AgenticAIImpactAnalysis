# Decisions

## MVC web application, not a CLI product

Sections 1–15 of the design document describe a Typer CLI and Markdown reports. Sections 16–21 and the implementation prompt replace that with FastAPI, Jinja, and HTML, PDF, and JSON. A diagnostic CLI was not added. `oracle-impact` only starts the web server.

## Fixture mode is the default

`OIA_FIXTURE_MODE=true` avoids accidental connections to Production while the app is being demonstrated. Live gateways are built only when fixture mode is off and a DSN and user are both set. Startup never opens a connection.

## File storage instead of an application database

Version 1 is a single process. JSON run directories are enough for local review and avoid an extra service. They will not support horizontal scaling.

## Qualitative risk only

Confidence is high, medium, or low. The UI does not show percentages. Recommendation labels are the three conditional outcomes in `docs/architecture.md`, not “proceed with deployment” and not “production-safe”.

## Confirmed means deterministic

Business-behavior claims stay potential even when the cited lines exist. A failed investigator citation is kept as an informational unknown and cannot move the recommendation.

## Change-set caller rule

Signature compatibility uses dictionary arguments. An added defaulted suffix is compatible. Other signature breaks are confirmed when a visible caller sits outside the change set, when caller source cannot be read, or when no caller was updated. If every visible database caller is in the change set and the new parameter names appear in its Test source, the finding is not marked breaking. Unknown non-database callers still block an eligible result.

## Scanner, not a PL/SQL parser

`difflib` produces the review diff. A line scanner finds routines, SQL shapes, transaction order, swallowed handlers, and dynamic SQL. It will miss q-quote syntax, nested string tricks, and SQL hidden inside dynamic strings. Those gaps are reported as unknown rather than safe.

## Dependency depth and system schemas

Walks stop at depth 2 and skip Oracle-maintained schemas so dictionary noise does not exhaust the budget. Synonyms and database links are coverage gaps.

## One OpenAI-compatible investigator

The live adapter speaks the chat-completions tool-calling API so an approved gateway or hosted model can be configured with a base URL. Egress is off by default. Tests use a scripted provider. No second agent framework is present.

## PDF engine

WeasyPrint is attempted first because the design document names it. Its native Cairo and Pango libraries are not assumed. fpdf2 writes a sectioned print document from the same report model when WeasyPrint cannot import or render. The PDF is not a screenshot of the dashboard.

## Driver distribution name

The PyPI distribution is `oracledb` (python-oracledb 26.x in this environment). The import name remains `oracledb`. Thin mode is used.

## Development authentication

Loopback binding plus a shared access token is the Version 1 control. Enterprise SSO is a deployment prerequisite, not an identity-provider implementation in this codebase.

## Unquoted identifiers

Lowercase and quoted identifiers are rejected so names can be validated before they are bound. This avoids a class of injection and case-folding surprises.

## Readiness is separate from the analyzer recommendation

The three analyzer labels (Not recommended, Requires further investigation, Eligible for review after testing) are kept unchanged for existing exports and tests. Readiness (Deployment recommended, Deployment on hold, Indeterminate) is a second, policy-driven state that also considers required tests, coverage, and freshness. It does not store validation results or reviewer decisions. A positive readiness never implies approval.

## One verdict on screen, formal labels in exports

The screen shows one deployment verdict, not a set of state chips. A friendly banner title — "Recommended, pending approval", "On hold", or "Can't decide yet" — sits above the stored readiness summary in full. The formal policy labels (`Deployment recommended`, `Deployment on hold`, `Indeterminate`) appear once in small text under the banner and stay the labels used by HTML/PDF/JSON exports, email, and tests. "Ready to deploy" and "Not yet approved" are forbidden on screen: a positive readiness is only ever a recommendation. Screen-only wording lives in the view layer (`decision_view.py` / `report_view.py`); the stored analyzer and readiness labels are never renamed.

## Admin actions sit out of the reading flow

Recording the deployment decision changes what other people see, so it is approver-only and kept out of the primary reading path. The approval form opens from a "Record decision" button in the verdict banner (a native `<dialog>`), with a no-JS fallback link to the Details tab. Non-approvers see only the decision chip and the read-only history.

## Differences do not hold deployment

A source difference between Test and Production is a fact about the captured code, not a verdict. There is no record of approved changes to compare it with, so differences are counted and shown in the Changes tab and the developer reviews them. Only a blocking finding or a required validation holds deployment.

## Fix-and-reanalyze blockers cannot be cleared by people

Interface breaks, missing Production objects, compile failures, and snapshot drift are facts about the captured code. Only a new analysis can clear them. Transaction hazards and other required tests stay open on the report. People track the test or the review outside this application, usually by replying to the email.

## Approval needs a second token

Approval records change what other people see. They need `OIA_APPROVER_ACCESS_TOKEN`, which must differ from the application token. When it is not set, approval recording is disabled rather than open to every user. Validation results and reviewer decisions are not recorded.

## Performance is not evaluated

`EXPLAIN PLAN` writes `PLAN_TABLE`, which conflicts with the read-only rule. Reading cursor caches needs privileges that are not assumed. The report states "not evaluated" with that reason.

## Readiness is recomputed on view, the policy is not

Blocking rules run once when the analysis finishes, so a rule change cannot silently rewrite an old report. Readiness depends on records that change after the run and on the age of the snapshot, so it is evaluated each time the report is shown.
