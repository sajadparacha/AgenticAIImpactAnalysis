# ImpactAnalysisAgentic — Cursor UI/UX Redesign and Implementation Plan

**Version:** 1.0  
**Prepared for:** Sajjad Paracha  
**Date:** 25 November 2026  
**Basis:** Read-only visual/usability audit of the running application, followed by research into comparable database and code-review interfaces.

## 1. Copy-ready master instruction for Cursor

```text
Act as a senior enterprise UI/UX designer and full-stack engineer.

Improve the existing ImpactAnalysisAgentic application using this plan. First
read AGENTS.md and other repository instructions, inspect the actual stack,
report models, routes, tests, and current git changes. Preserve existing work.

Visual direction:
- Bytebase-inspired database review workspace.
- Linear-inspired spacing, alignment, and restrained hierarchy.
- GitHub-inspired SQL comparison and contextual evidence panels.

Preserve the distinction between deterministic findings, AI interpretations,
policy readiness recommendations, and human deployment decisions.

Implement in reviewable phases. Use synthetic fixtures and existing tests.
Do not start live database analyses, execute SQL, compile database objects,
modify grants or schemas, record deployment approvals, send email, change
access/data-sharing settings, publish, merge, or deploy without explicit
user authorization for that action.

Do not change deterministic scoring, approval policy, or agent execution order
to resolve presentation inconsistencies. Investigate the existing domain model
and reuse shared definitions. Preserve URLs, exports, and authorization.

First provide a short discovery summary and proposed implementation slices.
Then begin the first safe slice. Continue routine reversible source work
without repeatedly asking for confirmation. Validate rendered behavior and
relevant tests before marking each phase complete. If a domain decision is
unresolved, identify it precisely and continue independent work.
```

This is an implementation brief, not a statement that changes have already been made. The application was not modified during the audit.

## 2. Goal, users and architectural boundaries

The target is a compact, calm, trustworthy database release-review workspace. A reviewer should quickly understand what changed, what blocks deployment, what requires validation, where the evidence is, what to do next, whether the snapshot remains valid, and whether a human decision exists.

| User | Primary need | Required experience |
|---|---|---|
| Developer | Understand changes and fixes | Finding → source evidence → remediation → validation |
| DBA | Assess Production prerequisites | Environment presence, grants, validity, dependency paths |
| Release reviewer | Understand readiness and unresolved work | Recommendation, blockers, freshness and human decision |
| Support analyst | Understand operational consequences | Affected workflows, validation and concise handoff |

Keep FastAPI/Jinja if confirmed. Prefer reusable macros, templates, CSS components and focused JavaScript improvements. Do not introduce React, a SPA, a large component framework, or a graph library solely to modernize appearance.

Preserve package specification/body comparisons, filtering, sorting, evidence links, drawer navigation, dependencies and provenance, remediation scripts, email previews, HTML/PDF/JSON exports and existing role-based approval behavior.

The user described deterministic analysis followed by agent enrichment. The inspected installation also exposed an agent-selected read-order mode. Verify actual supported modes and display their real sequence. Do not reorganize the analysis architecture as part of this redesign.

Never change policy values to make counts or labels agree. Presentation fixes should explain existing semantics. Isolate any necessary domain correction and document its effect. Do not weaken authorization or infer that an AI interpretation clears a blocker.

## 3. Audit baseline and priorities

Ratings are expert judgments of the inspected interface, not controlled user-study results. Reproduce findings against the current repository before treating them as current defects.

| Surface | Audit rating / 10 | Main reason |
|---|---:|---|
| Overall UI/UX | 6.2 | Good workflow foundation; inconsistent presentation and layout defects |
| Visual appearance alone | 6.5 | Credible palette and typography; weak proportions and hierarchy |
| Sign-in | 6 | Clear but limited orientation/help |
| New analysis | 6.5 | Excessive spacing and environment ambiguity |
| History | 4.5 | Opaque identity, unbounded expansion, freshness mismatch |
| Failed-run/progress record | 4.5 | Inaccurate terminal title, duplicate errors, unclear stages |
| Report Summary | 6.5 | Clear verdict but repetitive content and unexplained counts |
| Findings | 6 | Useful dimensions; expanded-filter defect and dense rows |
| Finding drawer | 7.5 | Good context/evidence links; content ordering can improve |
| Full deterministic finding | 6 | ID-focused title and inconsistent action emphasis |
| AI lead | 5.5 | Clear AI identity; repeated text and missing caveats |
| SQL comparison | 7.5 | Strongest technical workspace; code space can improve |
| Dependencies | 4.5 | Scope/count confusion and misleading impact language |
| Details | 5.5 | Good audit depth; weak grouping and scope communication |
| Fix plan | 7 | Useful phases; action duplication and prerequisite ambiguity |
| Email previews | 6 | Useful audiences; incomplete handoff and local links |
| Browser print preview | 3.5 | Clipped remediation column and incomplete presentation |

**P1:** trust, task completion, accessibility, or output correctness.  
**P2:** efficiency and visual composition.  
**P3:** optional polish after core acceptance criteria pass.

| ID | Observed issue | Priority |
|---|---|---|
| A01 | Large masthead/form spacing pushes Start analysis low | P1 |
| A02 | Demo placeholder conflicts with live configuration/missing-demo failures | P1 |
| A03 | History uses opaque IDs, lacks age and expands all 139 runs | P1 |
| A04 | Historical eligibility differs from current expired report | P1 |
| A05 | Failed run says Analysing, repeats error, indistinct stages | P1 |
| A06 | Agent activity includes planning-style narration | P1 |
| A07 | Blocker/validation/action counts have unexplained overlap | P1 |
| A08 | Summary repeats findings; Go links lack purpose | P2 |
| A09 | More filters creates tall blank area and bottom-aligned controls | P1 |
| A10 | Paragraph-heavy rows repeat deployment labels | P2 |
| A11 | Drawer What changed can say only Modified; evidence appears far down | P2 |
| A12 | Full finding uses ID as title and differs from drawer | P1 |
| A13 | AI lead repeats explanation and omits validation/caveat content | P1 |
| A14 | Diff wraps heavily; hashes and badges compete with code | P2 |
| A15 | Dependency counts 3/7/9/13 have different unexplained scopes | P1 |
| A16 | Overlapping dependency tables and self-looking relationships | P1 |
| A17 | Missing-object critical finding beside No incompatible change detected | P1 |
| A18 | Green graph nodes/Verified provenance can imply safety | P1 |
| A19 | Dense accordions, joined titles/counts, duplicate config notices | P2 |
| A20 | 2/2 fully analyzed appears comprehensive despite runtime blind spots | P1 |
| A21 | AI investigated hides 0/9 serious findings explained plus one lead | P1 |
| A22 | Overlapping fix steps, indistinct titles, create step only verifies | P1 |
| A23 | Production preparation precedes later approval instruction | P1 |
| A24 | Localhost email link, subject-only mail action, flattened action wording | P1 |
| A25 | Portrait print clips How it clears, splits row, retains 13 more | P1 |
| A26 | Copy-run-ID control looked blank despite accessible name | P1 |
| A27 | Faint labels, ID-heavy links, wide legend label/count separation | P2 |

Keep the successful patterns: prominent readiness, separate human decision, useful drawer, deep links, explicit AI uncertainty, syntax highlighting, aligned SQL lines and environment-labeled scripts.

**Not verified in the audit:** live progress updates, active approval dialog, server-generated PDF, downloaded HTML, full focus-trap behavior, screen-reader operation, mobile layout, measured contrast, and 200% zoom. These are verification tasks, not confirmed failures.

## 4. Visual system

Use a white workspace, pale slate navigation, compact navy identity and blue primary actions. Reserve semantic colors for meaning.

| Token | Proposed value | Purpose |
|---|---|---|
| Canvas | #FFFFFF | Main workspace |
| Navigation/sunken | #F8FAFC | Sidebar and secondary areas |
| Ink | #172B4D | Main text and identity |
| Muted | #526174 | Secondary text |
| Border | #DFE5EC | Dividers/control boundaries |
| Primary | #1D4ED8 | Primary actions, links, selection |
| Focus | #0B6BCB | Visible keyboard focus |
| Danger ink/surface | #9F1239 / #FFF1F2 | Blocking issues/errors |
| Warning ink/surface | #92400E / #FFFBEB | Required validation |
| Success ink/surface | #166534 / #F0FDF4 | Passed/completed check only |
| AI ink/surface | #6B21A8 / #FAF5FF | Unverified AI interpretation |

These are starting values; verify actual contrast combinations.

- Retain system sans-serif. Use monospace for SQL, objects, hashes and identifiers.
- Suggested sizes: page title 24–28px; section title 18–20px; body 15–16px; tables 14–15px; metadata 13–14px; code 13–14px. Use rem equivalents.
- Use 4/8/12/16/24/32px spacing; gutters approximately 24px desktop and 16px narrow.
- Sidebar approximately 224–240px; top bar 52–60px.
- Controls approximately 36–40px desktop with adequate target spacing; touch layouts at least 44px. Verify applicable WCAG target-size criteria.
- Use 6–8px corners; pills only for concise states. Replace unnecessary nested cards with dividers.
- Use shadows mainly for floating panels/dialogs.
- One dominant action per task. Export, Email, Help and Copy are secondary.
- Use consistent icons; verify sprite loading and visible fallbacks. Accessible names are required for icon-only controls.
- Respect reduced motion; avoid decorative progress/metric animation.

Create reusable components: app shell, breadcrumb, page header, environment pair, freshness indicator, readiness strip, human-decision summary, compact metrics, filter toolbar, advanced grid, finding row, evidence/provenance label, detail panel, SQL toolbar, code block, activity timeline, empty/error state and print issue block.

## 5. Navigation and workspace

| Region | Content |
|---|---|
| Left navigation | Product identity, New analysis, Runs, small bounded Recent reports list |
| Top bar | Breadcrumb, current context, account/menu |
| Report header | Package/change set, source/target, capture age; quiet Export/Email/Help |
| Readiness strip | Policy result, unresolved work, freshness; separate human decision |
| Local tabs | Summary, Findings, Changes, Dependencies, Details |
| Main workspace | Content suitable for the selected task |

Add no empty navigation destinations. Implement a read-only Runs route if needed, preserving authentication. Do not load all runs into every page. On narrow screens collapse global navigation into an accessible menu.

Preserve report/finding/object/line URLs and filter/sort query state. Test reload and Back/Forward. Apply coordinated sticky offsets and scroll margins so anchors/focus are visible. No GET route or navigation control may launch an analysis.

## 6. Screen implementation requirements

### 6.1 Sign-in

Order: identity, Sign in, one-sentence purpose, labeled token, Continue, access-help explanation. Keep development-access context secondary but readable. Use an existing support contact only if configured; do not invent one.

Preserve secure input, session and server-validation behavior. Do not add SSO during this work. Associate errors with inputs and announce them without exposing tokens.

**Accept:** keyboard access, labeled errors, narrow/zoom fit, no tokens in URLs/logs.

### 6.2 New analysis

Use a main form with an adjacent environment panel on wide screens; stack on narrow screens. Reduce intro whitespace and keep Start analysis readily reachable. At 1440×900 it should fit initially when content allows; never overlap content to force it above the fold.

Form: packages, short helper, validation, Include AI investigation, More options, Start analysis. Context: Test/proposed, Production/baseline, Live/Demo, AI availability/model.

- Use safe configured environment labels; LOCAL alone cannot distinguish endpoints. Never expose credentials/connection strings.
- Use environment-appropriate examples. Demo chips appear only in demo/fixture mode. Do not imply a package was verified live.
- Replace long AI instructions with a short benefit statement and expandable data-handling details. Preserve the current source-sharing disclosure accurately.
- Explain that model choice affects AI contribution, not deterministic scoring.
- Explain disabled AI beside the control; retain checks-only mode where supported.
- Validate malformed lines before submission and server-side. Preserve entered values. Address duplicates/whitespace consistently. Do not silently change quoted identifiers or introduce uppercase normalization without checking Oracle rules and existing contracts.

**Accept:** distinguishable environments, honest examples, helpful errors, no live analysis during form verification.

### 6.3 Runs/history

Create a dedicated read-only view with Package/change set first, followed by capture time/age, execution status, current validity/readiness, mode and optional model. Keep short ID secondary/copyable.

Search package/ID; filter appropriate status, mode, readiness and date; use bounded pagination. Preserve state in URL. Distinguish Completed from Ready. Use explicit timezones; local time plus UTC details is useful when timezone is known.

Preserve captured outcomes. When freshness expires show both Captured recommendation and Current validity: expired. Use shared policy/freshness logic rather than duplicated JavaScript rules. Do not rewrite stored historical reports merely for display.

**Accept:** consistent history/report expiry, combined search/filter/page behavior, usable hundreds of runs, authorized counts/results only.

### 6.4 Progress and failure

Derive stage order from actual mode/events. Support pending/running/completed/failed/skipped and other states only where implemented. Show status text and icons, not color alone.

Show package/environment, elapsed duration, last update, current activity, real work counts and partial results when supported. Use indeterminate progress if total work is unknown; no invented ETA/percentage. Distinguish connection loss from analysis failure; reconnect must not relaunch.

Failure: Analysis failed title, one main error, failed stage, preserved packages and precise Edit packages/Review environment action. Returning to editing must not retry automatically.

Use structured public activities rather than planning narration. Trace the source of existing text: do not assume planning-style logs are private reasoning. Do not request/display private model reasoning; retain authorized audit evidence without changing retention policy.

**Accept:** synthetic event streams, no terminal-state regression, no duplicate job, one missing-package error, polite/throttled announcements.

### 6.5 Report Summary

Use compact header → readiness → decision/freshness → blockers/validations → next actions, with compact metrics.

| Main area | Secondary area |
|---|---|
| Concise blockers and required validations | Next action, fix plan, blind spots, scoped AI contribution |

- Preserve readiness vs human approval. Recommended must retain Pending human approval where appropriate; never AI approved or Deploy safely.
- Choose one primary evidence/planning CTA; it must not execute SQL or deploy.
- Shorten UUID display; preserve full copy value and fix blank icon.
- Replace four large cards with inline statistics.
- Avoid reproducing full Findings content. Use short title, object, consequence and next action.
- Explain overlapping populations. The audit had 5 blockers + 4 validations but 4 fix/reanalysis + 5 test/review actions. If correct, label them as different dimensions; do not discard findings to make numbers match.
- Replace Go links with exact filtered findings, fix phase or coverage destination.
- Show expiry/incomplete capture separately from historical evidence. Do not say all historical evidence is untrustworthy solely because readiness expired.
- For multiple packages use readable change-set title and expandable list.

**Accept:** reviewer can identify readiness, why, unresolved actions, validity and next step within one screen at a representative desktop size.

### 6.6 Findings and filters

Basic toolbar: search, severity, deployment effect, Contributes to readiness, More filters, Reset. Put advanced filters in a separate full-width grid below it. Do not stretch/bottom-align the basic row when a disclosure opens.

Retain type/category/evidence/environment/change-type filters and URL state. Show/clear active filters. Explain scope of verdict contribution.

Table: Severity | Finding/consequence | Object/location | Evidence | Deployment effect | View evidence. Use short titles and secondary IDs. Full descriptions remain accessible in drawer/expansion. No essential content available only on hover. Keep title/action visibly clickable and full-page access available without right-click.

Preserve separate severity, certainty, deployment effect and workflow resolution. Remove duplicate labels only when truly redundant. Keep legend labels/counts together and state whether distribution is report-wide or filtered.

**Accept:** expanded-grid defect fixed; filters/sort/reset/reload work together; zero-match recovery; accessible sort state; 0/1/11/200+ fixtures and long names fit.

### 6.7 Finding drawer

Preserve contextual drawer. Order: descriptive title/status; deployment consequence; concrete change/evidence; remediation/tests; optional AI interpretation; technical details. Replace Modified/see below with actual change summary or Evidence unavailable.

Move Open full finding to header. Keep Previous/Next and count in stable footer based on active sorted/filtered collection. Make environment/object/line links explicit. Keep script environment/completeness beside Copy. Show absent AI review as small note. Use full-screen detail with Back on narrow screens.

**Accept:** accessible dialog/name, initial focus, focus containment/restoration, Escape, inert background, independent scrolling and coherent Back/Forward/source navigation.

### 6.8 Full deterministic finding

Use descriptive problem title as H1; ID/provenance secondary. Reuse drawer model/sections, including deployment effect, exact evidence, source links, ordered actions and caveats. Update incompatible callers before compile/validation. Prefer Deterministic check over Python-specific copy; retain technical rules in details.

**Accept:** drawer/full-page parity and independent direct URLs, explicit missing evidence, correct environment/line links.

### 6.9 AI lead page

Explicit Unverified AI lead label. Show hypothesis, related finding, captured references, unknowns, validation suggestion and effect on readiness. Remove repeated paragraphs. Preserve runtime/synonym caveats and validation instructions from Details. Label inherited severity Related finding: High, not the lead's own severity. Do not treat confidence as objective probability.

**Accept:** caveats survive standalone/email/export; factual existence claims retain environment; generated text is safely escaped.

### 6.10 SQL Changes workspace

Use object list/tree approximately 240–280px, spacious code area, optional contextual finding panel. Collapse auxiliary panels when space is limited.

- Production baseline left, Test proposed right. Preserve t/p link/data semantics and correct addition/removal meaning when reordering visually.
- Offer split/unified, wrap, changes-with-context/full source, prev/next and position, object search.
- Keep object identity/key controls visible while reviewing.
- Put hashes/metadata in disclosure. Use contextual finding count/titles instead of long ID chains.
- Optional compact/comfortable code density only if inexpensive.
- Improve intraline emphasis only when existing diff engine supports it reliably; do not replace analysis engine for aesthetics.
- Put compilation/transaction summaries in collapsible context or separate below-code areas.
- Unified fallback on narrow screens; intentional keyboard-reachable code scroll.

**Accept:** side/line deep links remain exact; addition/deletion counts match; long SQL and object names fit; no execution controls.

### 6.11 Dependencies

Highest-priority semantic redesign. Build one canonical presentation model used by Tree/Table/Diagram. Inspect apparent duplicates before merging: environment, collection direction, object type and observations may differ. Retain raw provenance in details. Distinguish package specification/body to avoid misleading self-loops.

Table: Source/type | Relationship | Target/type | Test presence | Production presence | Provenance | Deployment impact | Findings; depth when useful. Expand finding counts into descriptive titles rather than opaque ID chains.

| Dimension | Examples |
|---|---|
| Provenance | Dictionary observation, source-inferred, unresolved |
| Presence | Present, not visible to analysis account, not checked |
| Compatibility | Compatible within checked scope, incompatible, not evaluated |
| Deployment impact | Blocking missing dependency, validation required, no issue within scope |

Do not claim global absence when account visibility limits the dictionary. Do not show unqualified No incompatible change detected beside missing-object blockers.

Define affected objects, unique relationships, environment observations, unresolved references, omitted Oracle references and depth. Trace observed 3/7/9/13 counts instead of hardcoding replacements.

Diagram: neutral nodes, type labels/direction, independent risk and environment indicators, dashed inferred/unresolved edges with text, legend and disclosed truncation. Equivalent accessible table required. Avoid heavy libraries unless needed.

**Accept:** no false reassurance; spec/body distinguished; multi-environment/cycles correctly represented; all count scopes explained; filters work consistently across active views.

### 6.12 Details, coverage, AI audit and decisions

Group Readiness basis, Coverage/limitations, AI investigation, Technical audit. Add small section index if useful. Separate accordion titles/counts and remove duplicate config notices.

Coverage: selected-object check completion, failed/skipped checks, runtime gaps, traversal/visibility limits, freshness and performance evaluation. 2/2 checked is not safety/confidence.

AI: distinguish not requested/disabled/running/partial/failed/completed, reads/objects, findings interpreted, leads and unavailable/blocked reads. Mode name alone does not prove review completeness. Audit example 0/9 serious findings explained + one lead must be clear near header and consistent in details.

Use structured activity and optional tool audit. Explain Not allowed consequences without secrets. Expand long narrative notes explicitly rather than silently cutting mid-word.

One human-decision/capability summary. Disabled recording: explain external change-process tracking. Put configuration keys in authorized admin diagnostics. Preserve role-based controls/permissions; do not add approval capability.

**Accept:** scope/limits understood; partial AI not mistaken for complete review; disabled approvals have no active submit path. Active dialog tested only with isolated authorized fixtures.

### 6.13 Fix plan and scripts

Keep phased numbered steps and add phase index. Phase order/naming follows actual release policy. Every step has unique title, linked findings, object, environment, prerequisites, manual/generated nature, expected result and tracking location.

- Consolidate genuine duplicate compilation steps without losing links/prerequisites.
- Distinguish privilege-model review from grant verification.
- Separate manual DDL application from SELECT verification; title must match displayed SQL.
- Label complete executable action, verification query, editable template, or extractor.
- Make approved-change prerequisites explicit before Production DDL/grant actions, not only before package deployment.
- Keep environment warnings next to Copy and in bundled SQL comments. No Run SQL/Deploy buttons.
- If validations are externally tracked, say so. Do not add local completion controls that clear blockers.
- Code-related blockers clear only through the existing reanalysis/policy process, not review notes.

**Accept:** honest script titles/completeness, prerequisites visible, environment preserved, deduplication retains provenance; copying/downloading records no execution or approval.

### 6.14 Email handoff

Keep Developer/Support versions. Lead with readiness, exact request, fix/validation breakdown, report context, then details. Avoid lengthy social introductions.

Reuse classifications from report/fix plan. Do not call every blocker a code fix. Prefer Download complete email draft for long content; label subject-only action Open subject in mail app and explain before clicking.

Detect local/loopback report URLs. Explain recipients cannot use them to reach this server; offer complete attachments/drafts without inventing a public URL or exposing the application. If recipients absent, say Add recipients in your mail app; place config keys in admin help.

Retain uncertainty, freshness, environment and AI labels. Replies do not automatically clear deterministic blockers.

**Accept:** complete EML/encoding; accurate audience-specific requests; explicit mailto fallback; no sending/opening external mail in automated tests.

### 6.15 Print and exports

Create a document presentation sharing the report model, not a printout of whichever tab/disclosure is visible. Verify browser print and server PDF code paths separately.

Include concise executive summary, capture/freshness, readiness/decision, complete issues/actions, evidence references, coverage/AI caveats and scoped appendices.

Use issue/object/action blocks in portrait rather than squeezing desktop tables. Fix clipped How it clears. Expand required content; never leave 13 more instead of hidden items. Label Summary-only vs Full report exports explicitly.

Hide interactive controls/blank icons. Use readable references/links. Avoid clipping, sticky offsets, tiny scaling, stranded headings and splitting short issue blocks. Long blocks may continue with context. Page numbering where supported; readable title rather than huge UUID. Test monochrome/background-disabled output.

Keep JSON schema and raw machine-readable values backward compatible; labels must not replace enums. Additive metadata needs explicit contract/version consideration.

**Accept:** A4/Letter portrait readable and complete; long findings/scripts paginate; browser print AND actual server PDF inspected; HTML content usable as intended; exported meaning understandable without localhost access.

## 7. Shared semantics and presentation contracts

Map actual models/enums first. Reuse existing typed helpers rather than adding a competing data layer. Conceptual contract:

| Concept | Required distinctions |
|---|---|
| Execution | Job lifecycle, not readiness |
| Readiness | Existing deterministic recommendation policy |
| Human decision | Separate outcome with authorized actor/time/scope |
| Freshness | Capture time, evaluation instant, expiry, current validity |
| Severity | Existing deterministic values |
| Evidence | Confirmed/possible/not checkable/unverified AI lead |
| Provenance | Deterministic/AI; dictionary/source/inferred origin |
| Action | Fix+reanalysis, validation/review, prerequisites, external tracking |
| Coverage | Objects/checks, dependency relationships/observations and gaps |
| AI contribution | Requested mode vs actual outcome/read counts/interpretations/leads |

Share presentation helpers across history, report, drawer, full finding, fix plan, email and documents. Do not calculate readiness in Jinja/JavaScript. Evaluate display freshness consistently through existing policy interfaces without rewriting stored outcomes.

Every count needs a unit, scope/denominator, overlap rule and linked destination. Affected objects differ from edges. Read completion differs from findings interpreted. A possible finding may legitimately block under policy; evidence certainty does not automatically determine deployment effect.

## 8. Accessibility, responsive behavior and states

Target WCAG 2.2 AA and document verification. Tokens alone do not establish compliance.

- Normal text contrast ≥4.5:1; qualifying large text ≥3:1; applicable controls/focus meet non-text contrast.
- Logical headings/landmarks, explicit labels, meaningful links, table captions/headers and sort state.
- Native buttons for actions and links for navigation; no click-only divs/context-menu-only functions.
- Preserve tab arrows/Home/End; keyboard access to filters, disclosures, code and graph alternatives.
- Drawer/dialog names, focus, Escape, inert background and restoration verified.
- Polite result/progress live regions with throttling; important failures announced appropriately.
- Test 1440×900, 1280×800, 1024×768, 768×1024, 390×844 CSS pixels and 200% zoom/narrow reflow.
- Narrow form stack, full-screen finding, unified SQL; ordinary content reflows. Data/code tables may scroll in labeled reachable regions.
- Sticky bars never cover focused controls/source anchors; coordinate scroll padding/margins.
- Adequate touch targets; avoid tiny icon-only mobile toolbars.
- Reduced motion, visible focus, disabled-state reasons and copy success/failure feedback.
- Distinguish zero from unknown in empty/error states: no runs/matches/changes/dependencies, AI absent, unavailable evidence, expired capture, partial analysis and export failure.

## 9. Repository map — verify before editing

The earlier brief described FastAPI/Jinja, one main CSS/JS file and an SVG sprite. These are candidate paths, not verified source locations from this audit.

| Area | Candidate path/file |
|---|---|
| Styles | src/oracle_impact/views/static/app.css |
| Interactions | src/oracle_impact/views/static/app.js |
| Icons | src/oracle_impact/views/static/icons.svg |
| Shell | templates/base.html |
| Entry | login.html, dashboard.html |
| Progress | analysis.html |
| Report | report.html, report_body.html |
| Report views | _report_overview/findings/objects/dependencies/details.html |
| Findings | finding.html, _finding_detail.html |
| Handoff | fix_plan.html, email.html |
| Shared | _ui.html, _icons.html, _approval_form.html |
| Backend | Discover routes/models, policy/freshness/export and run-list helpers |
| Tests | Discover existing unit/integration/browser/export infrastructure |

Find actual root, instructions and test commands. Preserve existing user changes. Avoid unrelated refactors and a parallel frontend.

## 10. Fixtures and validation

Use synthetic fixtures with fixed capture times and controllable evaluation clock. Do not hardcode real audit run IDs. Use existing infrastructure; add meaningful behavioral regressions rather than tests mirroring CSS details.

| Fixture | Purpose |
|---|---|
| Fresh blocked | 5 blockers/4 validations vs 4 fix/5 test groups; explain overlap |
| Fresh eligible | Recommendation pending human approval |
| Expired eligible | Captured outcome + current expiry across surfaces |
| Failed/partial | Missing packages, failed checks, unavailable evidence |
| AI absent | Disabled/not requested accurately rendered |
| AI partial | 0 serious interpretations, one lead, blocked synonym read |
| Dependency complexity | Spec/body, cycles, environments, invisible/missing/runtime targets |
| Dense | Multiple packages, long names/SQL, 200+ findings, large graph |
| Sparse | No findings/changes/runs or search matches |
| Approval capabilities | Off/on/unauthorized in isolated fixtures; no real decision persistence |

Required verification:

1. Unit/integration: classification/count scopes, freshness, role rendering, routes, safe serialization.
2. Interaction: filters/sort/page/reset, URL reload/Back/Forward, drawer/focus, source links, split/unified order, copy failure.
3. Visual: desktop/narrow/zoom, expanded filters, long names, dialogs, empty/error states. Snapshots do not replace semantic tests.
4. Export: completeness/parity plus rendered A4/Letter inspection. Text extraction detects missing content; visual review detects clipping.
5. Accessibility: automated checks when available plus keyboard and representative screen-reader tests.
6. Side-effect boundaries: navigation/filter/GET/preview must not analyze, approve, execute SQL, send or deploy. Mock/assert these boundaries.
7. Performance: compare payload and interaction before/after. Avoid loading all history, graphs and hidden source panels into every initial page. No decorative heavyweight dependencies.

Run relevant existing lint/type/test commands per slice. Broaden checks for shared shell/model changes. Fix introduced failures; document unrelated pre-existing failures reproducibly.

## 11. Ordered implementation phases

| Phase | Work | Dependencies | Exit criteria |
|---|---|---|---|
| 0 Discovery | Verify repo, reproduce audit, map semantics/routes/tests, fixture baseline | None | Confirmed vs unconfirmed inventory |
| 1 Correctness | Shared labels/counts/freshness, dependencies, script titles/prerequisites, urgent print clipping | 0 | P1 semantics/output fixtures pass; policy unchanged |
| 2 Visual foundation | Tokens, shell/sidebar, controls, typography/icons, responsive primitives | 0 and shared contracts from 1 | Representative screens consistent; focus/contrast/zoom checked |
| 3 Entry/history/progress | Compact form, Runs search/pagination, real terminal/activity states | 1–2 | Bounded history, clear captured/current validity, safe recovery |
| 4 Summary | Header/readiness/decision, action groups, compact metrics | 1–2 | Main reviewer questions answered; exact links |
| 5 Findings | Filter grid, compact rows, drawer/full-page parity, AI leads | 1–2, 4 | URL/focus/uncertainty and expanded-grid checks pass |
| 6 Technical review | Diff workspace, canonical dependency views | 1–2, 5 | Exact side/line links, defined relationship scopes |
| 7 Handoff/audit | Details, scoped AI, approval capability, fix plan/email | 1–2, 5–6 | Consistent actions and prerequisites |
| 8 Documents | Full/summary print/PDF/HTML; JSON compatibility | 4–7; urgent fix in 1 | No clipped/missing content |
| 9 Verification | Responsive/a11y/performance/regression sweep and documentation | All | Acceptance evidence and limitations recorded |

Use small reviewable commits: presentation correctness; shell/tokens; entry/history/progress; summary; findings; code/dependencies; remediation/handoff; documents/accessibility. Follow repository contribution policy. Do not publish/merge/deploy automatically.

For each slice report: problem, resulting behavior, relevant files, validation, residual limitations and next slice. Source edits alone are not completion.

## 12. Definition of done

- [ ] All reproduced P1 issues implemented and verified, or precise domain blocker documented.
- [ ] Execution, readiness, decision, freshness, certainty and provenance remain distinct.
- [ ] Count units/scopes/overlap explicit across all surfaces.
- [ ] Missing/invisible Production targets have no unqualified safe label.
- [ ] History preserves captured outcome while showing current expiry.
- [ ] AI contribution reflects actual work; leads retain caveats/validation.
- [ ] Expanded filters aligned; URL, sort, reset and pagination preserved.
- [ ] Drawer/full finding content and actions agree.
- [ ] SQL visual order and source deep links correct.
- [ ] Remediation titles reflect scripts; Production prerequisites explicit.
- [ ] Email/document handoff preserves semantics and limitations.
- [ ] Browser print and actual server PDF both inspected; no clipped actions/hidden content.
- [ ] JSON consumers, existing routes and permissions preserved.
- [ ] Responsive, keyboard, screen-reader, zoom, contrast and reduced-motion scope documented.
- [ ] No unauthorized live analysis, SQL execution, database changes, approval, messages or deployment.
- [ ] Relevant tests pass; unrelated failures and limitations documented.
- [ ] Repository documentation records components, before/after behavior and validation.

Success means less interpretation and fewer interactions to reach readiness, blockers, evidence and the next action. Visual appeal should support that outcome.

## 13. Deferred work and non-goals

P3 candidates: dark mode after full semantic/code contrast work; density preference if supported by user testing; command palette if navigation scale warrants it; restrained brand mark; richer graph interactions if datasets require them.

Not included: SPA rewrite, new identity platform, approval-policy changes, autonomous deployment, SQL execution UI, new AI privileges/source transmission, automatic finding clearance, or persistent remediation tracking without separate domain design.

## 14. Research references

These official references informed design patterns; screenshots are version-specific. Create original assets and components rather than copying branding.

- Bytebase SQL review: https://www.bytebase.com/sql-review/
- Bytebase schema comparison screenshots: https://www.bytebase.com/blog/top-mysql-schema-compare-tools/
- Linear hierarchy/navigation redesign: https://linear.app/now/how-we-redesigned-the-linear-ui
- GitHub code/context panels: https://github.blog/changelog/2026-03-19-view-code-and-comments-side-by-side-in-pull-request-files-changed-page/
- SonarQube issue investigation: https://docs.sonarsource.com/sonarqube-server/user-guide/issues/reviewing
- SonarQube issue filtering/navigation: https://docs.sonarsource.com/sonarqube-server/user-guide/issues/retrieving
- Redgate SQL differences: https://documentation.red-gate.com/sc/reviewing-the-comparison-results/viewing-the-sql-differences

## 15. Suggested first message after attaching this file in Cursor

```text
Read this plan and the repository instructions. Start with Phase 0, then
implement Phase 1 and the visual foundation in reviewable slices. Use fixtures
only. Preserve the analysis engine, agent workflow, readiness policy, approval
semantics, permissions and existing user changes. Keep a completed/pending
acceptance checklist and verify each slice before moving on.
```
