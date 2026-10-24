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
