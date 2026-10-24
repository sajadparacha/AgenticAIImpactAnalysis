# Progress

The five-phase build is on `main`. The report redesign is on `feature/enhance-impact-report`.

## Verification

Command: `.venv/bin/pytest -q`

Result after the report redesign: **73 passed, 1 warning**.

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

## Remaining defects

- WeasyPrint was not installed, so the PDF path in this environment is the fpdf2 fallback. The exported PDF contains the recommendation, finding ids, and the limitations section; it is a plain print layout.
- Real database behavior is unverified.
- Development authentication remains a shared token.

## Phase status

| Phase | State |
| --- | --- |
| 1 Foundation | Implemented and covered by smoke tests |
| 2 Deterministic engine | Implemented and covered by regression tests |
| 3 Investigator | Implemented with egress off and scripted-model tests |
| 4 Reports | Implemented for HTML, PDF, and JSON; report page redesigned |
| 5 Hardening | Regression suite and readiness memo done; live Oracle and SSO remain open |
