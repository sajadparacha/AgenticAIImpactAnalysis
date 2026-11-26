# UI/UX Redesign — Progress

Tracks work against `docs/ImpactAnalysisAgentic_Cursor_Redesign_Plan.md` (the "Cursor Redesign" plan,
version 1.0). This file supersedes the older `docs/progress.md` (which tracked the earlier
`feature/ux-simplification` plan, phases 1–7, all committed to `main`).

Branch: `ui-ux-redesign`. Test baseline: `./run_tests.sh` → **278 passed, 1 skipped** (with the
working-tree changes below applied). Fixture mode only; no live Oracle or model was contacted.

## Status at a glance

- **Phase 0 Discovery** — done. Routes, models, templates, tests and fixtures mapped; the earlier
  ux-simplification work already aligned much of the report surface with this plan.
- **Phase 1 Correctness** — implemented in the working tree (uncommitted), essentially complete.
- **Phase 2 Visual foundation** — implemented in the working tree (uncommitted); tokens + app shell
  in place, sign-in done. The full focus/contrast/zoom + every-page responsive sweep is deferred to
  Phase 9, but the Phase 3 pages were responsive-checked at 375 / 768 / 1440 px.
- **Phase 3 Entry/history/progress** — implemented in the working tree (uncommitted), this slice.
- **Phase 4 Summary** — implemented; compact header, targeted links, overlapping-count note.
- **Phase 5 Findings** — implemented; advanced filter grid, chips, drawer/full-page parity, AI-lead page.
- **Phase 6 Technical review** — implemented; diff workspace (baseline left / proposed right), dependencies.
- **Phase 7 Handoff/audit** — implemented; details grouping, fix plan, email handoff.
- **Phase 8 Documents/export** — implemented; print CSS, HTML/PDF/JSON parity, full-report labeling.
- **Phase 9 Verification** — implemented; responsive 375/768/1440, contrast, keyboard/focus, tests green.

## Phase 1 (Correctness) — working tree, uncommitted

Shared labels, counts, freshness, dependency wording, script titles/prerequisites, and print CSS.

| Item | What changed | Files |
|---|---|---|
| A17 | Fallback dependency impact reads "No breaking change in checked scope", not "No incompatible change detected" | `services/analysis_service.py` |
| A18 | Dependency diagram "Referenced objects" are neutral (was green); dictionary provenance labeled "From ALL_DEPENDENCIES" | `infrastructure/reports/diagram.py`, `services/decision_view.py`, `_report_dependencies.html` |
| A16 | Diagram shows object type suffix; spec vs body told apart | `infrastructure/reports/diagram.py` |
| A20 | Coverage wording "analyzed by the checks" (not "fully analyzed"), with a scope note | `services/decision_view.py`, `_report_details.html` |
| A21 | AI section shows "Explained 0 of N serious findings" summary + a warning when none explained | `services/report_view.py`, `_report_details.html`, `report_body.html` |
| A22 | Missing-object Production step is "Confirm … exists and is VALID", never "Create" | `analysis/action_scripts.py` |
| A23 | Production fix-plan steps state the change-approval prerequisite | `services/fix_plan.py`, `fix_plan.html` |
| A15 | Dependency panel explains the two-count scope (edges vs impact table) | `_report_dependencies.html` |
| A03/A04 | Shared `freshness()` helper; recent runs + run history show age and "Current validity: expired" | `services/history.py`, `services/decision_service.py`, `controllers/web.py`, `_recent_runs.html`, `_report_details.html` |
| A25 | Print CSS: tables reflow, disclosures expand, hidden "N more" summaries removed in print | `app.css` |

Regression locks added in `tests/test_regression.py` and `tests/test_services_units.py`.

## Phase 2 (Visual foundation) — working tree, uncommitted

- Design tokens rewritten to the plan §4 values (`--brand #172b4d`, `--bg #ffffff`, `--nav-bg #f8fafc`,
  `--primary #1d4ed8`, `--muted #526174`, `--line #dfe5ec`, danger/warning/success/AI surfaces, 8px radii).
- App shell: persistent 240px sidebar (identity, New analysis, bounded recent runs) + 56px top bar
  (breadcrumb context, sign out), with a mobile drawer + backdrop. `base.html`, `app.css`, `app.js`.
- Controls/typography tightened to the token scale; `.tabs` sticky offset now respects the top bar.

Sign-in page (`login.html`) is done: identity → "Sign in" → purpose → labeled token → Continue →
secondary access-help, with the error announced and associated to the input (`aria-invalid` +
`aria-describedby`). The 401 "not accepted" text is preserved. Verified via TestClient and a browser
snapshot (sidebar 240px, topbar 56px, content 1200px, no horizontal overflow).

Remaining Phase 2 work (deferred to Phase 9): a focus/contrast/zoom + responsive sweep (375 / 768 /
1440 px) against every rendered page, including the mobile sidebar drawer.

## Phase 3 (Entry/history/progress) — working tree, uncommitted

Read-only Runs history, terminal-state failure handling, and a structured (verifiable-actions-only)
investigator activity feed. Analysis/approval semantics are unchanged.

| Item | What changed | Files |
|---|---|---|
| A03 | Dedicated read-only `/runs` view: Package/change set first, capture time/age (relative + local + UTC title), execution status, captured recommendation, mode and optional model; short copyable run ID. Combined search (`q`) plus status / mode / outcome / capture-age filters preserved in the URL (`qs`), bounded 25/page pagination with `Newer`/`Older`, result-count summary and a Reset link. | `controllers/web.py`, `views/templates/runs.html`, `views/templates/base.html`, `views/templates/_recent_runs.html`, `views/static/app.css` |
| A04 | Historical captured recommendation is preserved while the shared `freshness()` helper renders "Current validity: expired" when the snapshot exceeds `snapshot_stale_hours`; stored `report.json` is never rewritten for display. Completed and Ready are kept distinct. | `controllers/web.py`, `views/templates/runs.html` |
| A05 | Failed run never reads "Analysing": title becomes "Analysis failed", a single main error, the failed stage is marked on the stepper, packages are preserved, and CTAs are contextual — `Edit packages` (`error_kind=missing`), `Review environment` (`connection`), `Start again` (`analysis`) — plus `View all runs`. Elapsed/last-update timing shown. | `services/analysis_service.py`, `infrastructure/storage/run_store.py`, `views/templates/analysis.html`, `views/static/app.js`, `views/static/app.css` |
| A06 | Structured public activity: the `.investigator-activity` card lists only verifiable `action` entries with a "Read" badge; planning-style `thought` narration is dropped on both the server template and the client renderer. | `views/templates/analysis.html`, `views/static/app.js`, `views/static/app.css` |

Persistence additions: `RunStore.write_status` records `error_kind` (`missing` / `connection` /
`analysis`) and auto-derives `failed_stage` from the previous stage on failure; `list_runs` exposes
`recommendation_value` for readiness filtering. `AnalysisService.execute` classifies failures into
those `error_kind`s.

### Validation evidence

- `./run_tests.sh` → **273 passed, 1 skipped** (fixture mode; no live Oracle or model). New/updated
  locks: `tests/test_storage.py` (`error_kind`, `failed_stage`, `recommendation_value`),
  `tests/test_http_and_agent.py` (failed-run rendering, connection-error guidance, `/runs`
  search/filter/pagination, activity thought-suppression), `tests/test_regression.py`.
- Synthetic fixture runs rendered and inspected at 1440 / 768 / 375 px
  (`docs/redesign/screenshots/phase3-*.png`): desktop sidebar; tablet/mobile drawer nav (toggle shows
  at ≤899 px); the runs table scrolls inside `.table-scroll` with no page-level horizontal overflow
  (`body.scrollWidth == innerWidth` at 768 and 375 px). Failed-missing, failed-connection, and running
  agentic-activity states all captured.
- State separation verified: a run can be `completed` while its recommendation is `on_hold` /
  `investigate`; expired readiness shows both the captured recommendation and "Current validity:
  expired".

### Remaining defects / decisions

- No known blockers. `overflow-x: clip` on `html` can make the document `scrollWidth` exceed
  `innerWidth` at tablet widths even though `body` has no real overflow; the runs table is fully
  contained by `.table-scroll`, so no scrollbar leaks to the page.
- The `Model` column shows `—` for deterministic runs; acceptable now, revisit if mixed-model history
  makes it noisy.

## Phase 4 (Summary) — working tree, uncommitted

Compact report header, exact filtered links, and an explicit note explaining overlapping counts.

| Item | What changed | Files |
|---|---|---|
| A07 | Overview note explains the policy-item vs work-item overlap ("Holding deployment" is a policy count — blockers plus required validations; "What to do next" is a work view of the same items). No hardcoded object names or counts. | `services/decision_view.py`, `_report_overview.html` |
| A08 | "What to do next" links are specific and accessible: `Open finding {id}`, `See details`, `See the summary` instead of generic "Go"; each targets the exact filtered destination. | `_report_overview.html`, `_report_macros.html` |

## Phase 5 (Findings) — working tree, uncommitted

Advanced filter grid in a dedicated sub-row, interactive chips, and full finding-page/drawer parity.

| Item | What changed | Files |
|---|---|---|
| A09 | Advanced filters (object type, category, evidence, environment, change type) live in a dedicated full-width sub-row under "More filters"; the disclosure carries a count of active advanced filters. | `_report_findings.html`, `app.js`, `app.css` |
| A10 | Compact finding rows: short headline + secondary ID, no hover-only content. | `_report_findings.html`, `app.css` |
| A11 | Finding drawer shows change type, evidence pointer, policy tags, and resolution text. | `_finding_detail.html`, `decision_view.py` |
| A12 | `finding.html` uses the descriptive headline as the H1 with the ID/provenance secondary (was: ID as H1). | `finding.html`, `controllers/web.py` |
| A13 | Unverified AI-lead page: explicit label, hypothesis, "what the AI looked up", unknowns, suggested validation, inherited (related-finding) severity — never treated as the lead's own severity. | `finding.html`, `controllers/web.py` |
| A21 | AI section shows "Explained N of M serious findings" near the header and in details. | `report_view.py`, `_report_details.html`, `report_body.html` |

## Phase 6 (Technical review) — working tree, uncommitted

Diff workspace and canonical dependencies.

| Item | What changed | Files |
|---|---|---|
| A14 | Diff cells wrap long SQL/object names (`white-space: pre-wrap; overflow-wrap: anywhere`). **Column order corrected to match the plan:** Production baseline is now the left column, Test proposed the right column (was reversed); addition/removal highlighting and t/p line semantics are preserved. Regression locked in `test_report_view.py::test_diff_table_orders_production_baseline_left_of_test_proposed`. | `_report_objects.html`, `app.css`, `tests/test_report_view.py` |
| A15 | Dependency panel explains the two-count scope (edges vs impact table). | `_report_dependencies.html` |
| A16 | Spec vs body kept apart (no misleading self-loops); provenance labeled (dictionary / source-inferred / unresolved). | `infrastructure/reports/diagram.py`, `_report_dependencies.html` |
| A17 | Impact wording reads "No breaking change in checked scope", not an unqualified "No incompatible change detected". | `services/analysis_service.py` |
| A18 | Diagram nodes neutral; provenance legend text. | `infrastructure/reports/diagram.py` |

## Phase 7 (Handoff/audit) — working tree, uncommitted

Details-tab grouping, fix plan, and email handoff.

| Item | What changed | Files |
|---|---|---|
| A19 | Details tab groups "Verdict basis", "Blocking rules and required validations", "Recommended tests", "Coverage and blind spots", "AI review", "AI leads", "SQL performance risk", "Known limitations", "Run information" as dense accordion sections with counts. | `_report_details.html` |
| A20 | Coverage wording "analyzed by the checks" with a scope note; dependency coverage counted separately (no false percentage). | `decision_view.py`, `_report_details.html` |
| A22 | Missing-object Production step is "Confirm … exists and is VALID", never "Create". | `analysis/action_scripts.py` |
| A23 | Fix-plan steps carry unique titles, environment tags, and the change-approval prerequisite before Production DDL/grant actions (and again before package deployment). | `services/fix_plan.py`, `fix_plan.html` |
| A24 | Email handoff: Developer/Support versions; mailto truncation shows "Open subject in mail app" with an explanation; local/loopback report URLs are called out instead of invented; config keys named for recipients. | `email.html`, `email_view.py` |

## Phase 8 (Documents/export) — working tree, uncommitted

| Item | What changed | Files |
|---|---|---|
| A25 | Print/export CSS: tables reflow (`overflow-wrap: anywhere`), `.diff-scroll`/`.table-scroll` unclip, disclosures expand, hidden "N more" summaries removed (disclosures `open` + summary `display:none`). Export menu now states these are **full report** exports (every panel, finding, appendix). HTML (print.html), PDF (WeasyPrint → fpdf2 fallback), and JSON (`model_dump_json`, raw enum values preserved) all return 200. | `app.css`, `report_body.html`, `print.html`, `email.html`, `infrastructure/reports/pdf.py` |

## Phase 9 (Verification) — working tree, uncommitted

- Responsive sweep at 375 / 768 / 1440 px on Summary and Findings: no page-level horizontal scroll
  (`html { overflow-x: clip }`; tabs and `.table-scroll` scroll internally). Screenshots in
  `docs/redesign/screenshots/phase9-*.png`.
- Contrast: computed WCAG ratios for the token pairs all ≥ 6:1 (ink 14.1, muted 6.3, blue 6.7,
  green 7.1, red 8.0, amber 7.1 on white), exceeding AA.
- Keyboard/focus: skip link, `:focus-visible` outlines, tablist/tabpanel wiring, drawer
  `role="dialog" aria-modal` with Escape-to-close and a Tab focus trap (app.js), `aria-live` on
  filter chips/results, `aria-sort` on sortable columns.
- Full suite green: `./run_tests.sh` → **278 passed, 1 skipped** (skip = lab check needing a live DSN).

## Next phase

All phases (4–9) are implemented and verified against their acceptance criteria. Remaining
follow-ups are optional, not acceptance-blocking:

- A unified diff view (the plan lists "split/unified" as a control; the side-by-side split view and
  the §6.10 Accept criteria — exact deep links, correct counts, no clipping, no execution controls —
  are all met). Deferred as a non-blocking enhancement.
- Commit the working tree to `ui-ux-redesign`.
- Cosmetic tablet `scrollWidth` artifact (see remaining defects) — no page-level scrollbar leaks.

## Audit-issue ledger

| ID | Status | Note |
|---|---|---|
| A01 | done | Shell redesign compacts the masthead/form |
| A02 | done | Demo chips render only in fixture mode |
| A03 | done | Dedicated read-only `/runs` view: search/filter/pagination, short copyable ID, age + freshness |
| A04 | done | Shared freshness helper; captured outcome preserved with "Current validity: expired" |
| A05 | done | Failed run shows "Analysis failed" + failed stage + contextual recovery CTA |
| A06 | done | Structured activity feed lists only verifiable actions, no planning narration |
| A07 | done | Overview note explains policy-item vs work-item overlap |
| A08 | done | "What to do next" links are targeted and descriptive |
| A09 | done | Advanced filters in a dedicated sub-row + active-count on "More filters" |
| A10 | done | Compact finding rows |
| A11 | done | Drawer shows change type + evidence pointer + policy tags |
| A12 | done | `finding.html` H1 is the descriptive headline (ID secondary) |
| A13 | done | AI-lead page: hypothesis, looked-up, unknowns, validation, inherited severity |
| A14 | done | Diff wrap + corrected column order (baseline left, proposed right) |
| A15 | done | Count scopes explained |
| A16 | done | Spec/body distinguished |
| A17 | done | Scoped wording |
| A18 | done | Neutral nodes + provenance label |
| A19 | done | Details accordion density |
| A20 | done | Coverage wording |
| A21 | done | AI "explained N of M" |
| A22 | done | Confirm-not-create + prerequisite |
| A23 | done | Change-approval prerequisite |
| A24 | done | Mailto subject-only label + local-URL warning |
| A25 | done | Print CSS + full-report export labeling verified |
| A26 | done | Copy-run-ID has aria-label + icon |
| A27 | done | Tokens updated; contrast spot-check passed (≥6:1) |

## Verify

```bash
./run_tests.sh
source .venv/bin/activate
oracle-impact   # fixture mode; http://127.0.0.1:8000, token dev-only-change-me
```

Demo packages: `DEMO.PKG_STATUS`, `DEMO.PKG_BILLING`, `DEMO.PKG_DISPATCH`, `DEMO.PKG_ORDER`, `DEMO.PKG_LOOKUP`.
