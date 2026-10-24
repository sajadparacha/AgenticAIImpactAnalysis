# Implementation checklist

All five phases were implemented in this repository after the architecture plan was approved for execution. Later production use still depends on the gates in `docs/readiness.md`.

## Phase 1 — Foundation

- [x] FastAPI application factory and settings from `OIA_` environment variables
- [x] Dashboard, login, and health routes
- [x] Jinja layout and static assets
- [x] MVC package layout
- [x] Smoke tests that start without Oracle or an API key
- [x] Architecture, decision, test, limitation, and progress documents
- [x] Cursor rules for boundaries, security, and tests

## Phase 2 — Deterministic engine

- [x] Read-only query catalog and live gateway
- [x] Fixture gateway and demo catalog
- [x] Snapshots, checksums, and drift detection
- [x] Diff, signature, dependency, and impact findings
- [x] Recommendation policy
- [x] Run status and results page
- [x] Tests that the catalog is `SELECT`-only

## Phase 3 — Investigator

- [x] Tool schemas and argument allowlist
- [x] Budget, timeout, and per-package call limits
- [x] Evidence validator
- [x] Scripted-model tests
- [x] OpenAI-compatible adapter that refuses calls when egress is disabled
- [x] Tool log without source text

## Phase 4 — Reports

- [x] Package form, progress polling, report, and finding pages
- [x] Side-by-side diff and inline SVG dependency diagram
- [x] HTML, PDF, and JSON export
- [x] Low-risk and high-risk fixture scenarios with different recommendations

## Phase 5 — Hardening

- [x] Regression suite of 22 PL/SQL scenarios
- [x] Security checklist and readiness memo
- [x] Failure handling for missing packages, denied metadata, investigator outages, and budgets
- [ ] Authorized nonproduction Oracle execution (not available in this workspace)
- [ ] Enterprise SSO (required before any shared deployment)
