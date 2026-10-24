# Progress

The five-phase build is on `main`. The report redesign is on `feature/enhance-impact-report`.

## Verification

Command: `.venv/bin/pytest -q`

Result after the error-contract, `AUTHID`, and grant checks and the scanner fixes for statement boundaries and overload pairing: **83 passed, 1 warning**.

The warning is Starlette’s deprecation notice that `httpx` test client support is moving to `httpx2`. The tests still run.

No live Oracle connection was attempted. No external model was called. PDF export was exercised through fpdf2 because WeasyPrint is not installed.

## What a reviewer can run

```bash
source .venv/bin/activate
pytest
oracle-impact
```

Sign in with the configured token (`dev-only-change-me` if `.env` was not changed). Analyze `DEMO.PKG_STATUS` and `DEMO.PKG_BILLING`.

Browser check against the local server: sign-in, `DEMO.PKG_STATUS` (eligible for review after testing, deterministic-only), and `DEMO.PKG_BILLING` (not recommended: breaking signature, missing `DEMO.BILLING_HISTORY`, commit before a later insert). The finding page for the signature break names `DEMO.PKG_LEGACY`. JSON and PDF exports for that run returned the same recommendation.

## Report redesign

The report page is decision-first: recommendation and human-decision state, metrics, then Overview, Findings, Source comparison, Dependencies, Validation and approval, and Audit trail tabs. See `docs/redesign/implementation-notes.md` for the data contract and the requirements blocked by missing backend data.

Manual browser checks on fixture runs: tab arrow keys, drawer focus trap, Escape and focus return, Back/Forward, Developer/Support toggle, filters and sorting, deep links, 900 px tablet layout. A synthetic report with 319 findings and a 6,000-line package body loaded in about 0.6 s; filtering took under 10 ms.

## Decision support

Readiness, change classification, coverage, compilation readiness, transaction impact, business mappings, regression test tracking, approval, run history, and AUTUMN comparison were added. See `docs/architecture.md` (Decision support) and `docs/redesign/implementation-notes.md`.

Command: `.venv/bin/pytest -q`. Result: **109 passed, 1 warning** (the same Starlette deprecation notice). New tests are in `tests/test_regression.py` (compile blocker, drift and planned classification, baseline states, coverage and stale or superseded runs, readiness reached through policy plus a recorded validation, fingerprints and run comparison, investigator failure), `tests/test_signatures.py` (reorder, rename, default), and `tests/test_decision_support.py` (roles, CSRF, approval gating, validations, manifest and baselines, drift across runs, mappings, legacy cookie, tabs and filters, AUTUMN, exports, unsafe run ids, blank optional paths).

Unit and integration coverage was then extended to **225 passed**:

- `tests/test_storage.py`: run store id and artifact checks, ordering, status bounds; registry versions, baselines, manifest, mappings, events; review store updates and history.
- `tests/test_analysis_units.py`: scanner, hashing, rule ids and fingerprints, transaction differences, coverage states, edge provenance, compilation scopes and caps, diagram escaping.
- `tests/test_services_units.py`: decision service input rules, approval staleness, business impact levels, history ordering and freshness, policy file overrides and item states, deep-link state, sessions and CSRF, snapshot capture, Oracle gateway binds and the read-only query catalog.
- `tests/test_integration.py`: unauthenticated access, login roles and logout, security headers, 404 and 400 routes, fixed action messages, persistence across an app restart, and a positive recommendation reached through a policy file plus recorded validations.

Screenshots `screenshots/decision-*.png` were captured at 1440 px from a local fixture server with seeded records (a baseline, a manifest entry, two mappings, one validation, one approval). Pages were fetched with a session cookie and rendered by headless Chrome without the script, so they show the server-rendered state.

## Remaining defects

- WeasyPrint was not installed, so the PDF path in this environment is the fpdf2 fallback. The exported PDF contains the recommendation, finding ids, and the limitations section; it is a plain print layout.
- Real database behavior is unverified.
- Development authentication remains a shared token. Approval records carry self-declared names.
- `ALL_ERRORS` reads, AUTUMN connections, and the registry have only been exercised against fixtures.

## Phase status

| Phase | State |
| --- | --- |
| 1 Foundation | Implemented and covered by smoke tests |
| 2 Deterministic engine | Implemented and covered by regression tests |
| 3 Investigator | Implemented with egress off and scripted-model tests |
| 4 Reports | Implemented for HTML, PDF, and JSON; report page redesigned |
| 5 Hardening | Regression suite and readiness memo done; live Oracle and SSO remain open |
