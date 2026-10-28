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

AUTUMN was later removed from the project. Reports compare Test with Production only; older saved reports still load, and any AUTUMN data in them is ignored. Result: **251 passed**.

The run progress page and the report now act as two stages of one run. The progress page opens the report as soon as the run completes, and `/analyses/{id}` redirects to the report for completed runs. Failed runs stay on the progress page with their error. The report breadcrumb links to the run log in the Audit tab (`?tab=audit#phases`), and the progress page has its own breadcrumb with the short run ID. Result: **252 passed**.

The Audit tab's "Analysis phases" section now has its own full-width card. Its detail lines no longer wrap after every word. Finding IDs, severity, category, and environment are shown as tags, and long lists scroll inside their box. Result: **253 passed**.

Screenshots `screenshots/decision-*.png` were captured at 1440 px from a local fixture server with seeded records (a baseline, a manifest entry, two mappings, one validation, one approval). Pages were fetched with a session cookie and rendered by headless Chrome without the script, so they show the server-rendered state.

The UX simplification began on branch `feature/ux-simplification` (spec in `docs/redesign/ux-simplification-plan.md`). Phase 1 Foundation is done: design tokens in `app.css`, a local Lucide icon sprite, a `_ui.html` breadcrumb macro, and rebuilt top navigation with a skip link and mobile menu. Result: **254 passed**.

Phase 2 (Dashboard and single analysis entry) is done: Hybrid and Agent-guided are merged into one "New analysis" form on `/` with demo-package chips, a "Let the AI investigate" checkbox (with the two-phase helper text), and a "More options" model selector shown only when more than one model is served. The environment status is a single line (Demo data / Live, Test → Production, AI on/off). `POST /analyses` accepts `investigate`; `GET /agentic` returns a 303 to `/?investigate=1`; `POST /agentic/analyses` still works. `agentic.html` was deleted and the progress-page breadcrumb now starts at "New analysis". Recent runs shows the latest 10 with a "Show all" toggle. Result: **254 passed**.

Phase 3 (Report header, verdict banner, and measure tiles) is done. The report opens with a decision-first hero: a tone-based verdict banner titled "Recommended, pending approval" (or "On hold" / "Can't decide yet") with the stored readiness summary ("Human approval is still required."), an unresolved-checks line that renders only when the count is non-zero, the human-decision chip, and the approver `<dialog>`. A meta line shows timestamps, the Test → Production comparison, a copyable run ID, a demo-data badge, and the run-mode badge ("Checks only" / "AI investigated"). Four measure tiles show Findings (with a severity stack bar), Changed objects (+/− line counts), Affected dependencies, and Coverage as a count ("5 of 6 objects"). Breadcrumbs run "New analysis › Run › Impact report"; the "How to read this report" help popover explains badges. Screenshots: `screenshots/ux-3-report-header-*.png` at 1440/768/375 px. Result: **254 passed**.

Phase 4 (Tabs 7 → 5 and Trust records page) is done. The report now has five tabs — Summary (`overview`), Findings (`findings`), Changes (`objects`), Dependencies (`dependencies`), Details (`details`). The old Validation and Audit tabs and the Source comparison were folded into a new Details tab (`_report_details.html`, 13 collapsible sections) and a two-pane Changes tab (`_report_objects.html`, object list + side-by-side diff plus per-object compilation and transaction cards). The Summary tab now shows the "What's holding deployment" hold table, a de-duplicated "What to do next" list, top findings, and business impact. A new `/registry` page (`registry.html`) lists trusted baselines, approved change records, and recent trust actions, with clear/withdraw forms for approvers (`return_to=registry`). Legacy URLs still work: `?tab=source` → objects, `?tab=validation` and `?tab=audit` → details. The Trust records link is now present in the top navigation. Obsolete templates `_report_source.html`, `_report_validation.html`, and `_report_audit.html` were deleted. Screenshots: `screenshots/ux-4-summary-*`, `ux-4-findings-*`, `ux-4-changes-*`, `ux-4-details-*`, `ux-4-registry-*` at 1440/768/375 px. Result: **254 passed**.

Phase 5 (Finding drawer and finding page) is done. The finding drawer now opens with the four numbered evidence blocks in order — 1 What changed, 2 Does it block deployment, 3 What to test, 4 Evidence (lines, diff link, dependencies) — followed by a separate, unnumbered "AI interpretation, not evidence" block (with the "AI lead · not verified" badge only when the AI contributed), an "Evidence details" disclosure (Rule, Fingerprint, Version hash, Confidence, Evidence ID, Run), and one caveat at the bottom. The evidence-status/source/evidence-class chips in the head now collapse to the single combined evidence badge. The drawer footer adds Previous/Next with an "n of m" count that follows the active filtered order, Esc still closes, and below 720px the sheet goes full-screen with a Back button (the × is hidden). The standalone finding page gained the breadcrumb (New analysis › Run › Findings › ID), the same caveat, and the extra evidence-detail fields. Screenshots: `screenshots/ux-5-finding-drawer-*.png` at 1440/768/375 px. Result: **254 passed**.

Phase 6 (Progress, email, mappings, polish) is done. The progress page now shows a horizontal stepper (vertical below 720px) with done/current/pending icons, one current-status line, the agent activity feed (AI runs only), and a single "Show run log" disclosure holding every per-stage detail line; errors render as a danger notice with a "Start again" button that returns to `/` with the packages pre-filled (via `packages` from `request.json`). The email page got a shorter lede, one combined setup notice (recipients + public base URL), a "New analysis › Run › Impact report › Prepare email" breadcrumb, and "Copy email" as the primary action. The mappings page was restyled with a panel head, an iconed empty state, and iconed notices. A disclaimer sweep kept "test results are not recorded" only in Details › Recommended tests and "Consequences are predicted from static evidence…" only in the drawer/finding caveat; empty states now carry icons (`circle-check` on "Nothing is holding this deployment", `building-2` on unmapped objects, `history` on empty recent runs). Orphaned CSS from the removed old UI (`.decision-states`, `.state-chips`, `.metrics-primary/-secondary`, `.change-stats`, `.three-up`) was deleted. Screenshots: `screenshots/ux-6-progress-*`, `ux-6-email-*`, `ux-6-mappings-*` at 1440/768/375 px. Result: **254 passed**. The section 11 comprehension check is **outstanding**: it must be run by someone other than the implementer against the fixture server (six tasks on `DEMO.PKG_STATUS`, `DEMO.PKG_BILLING`, and `DEMO.PKG_DISPATCH`, plus a developer-email blocker list for `DEMO.PKG_BILLING`).

Phase 7 (Documentation) is done. `README.md` now describes the single analysis form with the "Let the AI investigate" checkbox (Checks only / AI investigated), the one-verdict banner and screen-vs-stored labels, the five report tabs, and the New analysis / Business mappings / Trust records navigation. `docs/redesign/implementation-notes.md` records the new URL contract (`overview`, `findings`, `objects`, `dependencies`, `details` with `source`/`validation`/`audit` legacy mapping, the `investigate` form field, and `/registry`). `docs/decisions.md` gained "One verdict on screen, formal labels in exports" and "Admin tools sit out of the reading flow". Final result: **254 passed**. The section 11 comprehension check remains outstanding for someone other than the implementer.

## Remaining defects

- WeasyPrint was not installed, so the PDF path in this environment is the fpdf2 fallback. The exported PDF contains the recommendation, finding ids, and the limitations section; it is a plain print layout.
- Real database behavior is unverified.
- Development authentication remains a shared token. Approval records carry self-declared names.
- `ALL_ERRORS` reads and the registry have only been exercised against fixtures.

## Phase status

| Phase | State |
| --- | --- |
| 1 Foundation | Implemented and covered by smoke tests |
| 2 Deterministic engine | Implemented and covered by regression tests |
| 3 Investigator | Implemented with egress off and scripted-model tests |
| 4 Reports | Implemented for HTML, PDF, and JSON; report page redesigned |
| 5 Hardening | Regression suite and readiness memo done; live Oracle and SSO remain open |
