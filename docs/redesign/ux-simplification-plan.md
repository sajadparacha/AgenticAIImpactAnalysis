# UX simplification plan

A complete brief for redesigning the web interface so it is less cluttered, responsive, attractive, and usable without training. It is written to be handed to a fresh agent session. Work on branch `feature/ux-simplification`.

## 0. How to use this document

1. Read the workspace rules in `.cursor/rules/` (`security.mdc`, `architecture.mdc`, `testing.mdc`) before changing anything. They override this plan if they ever conflict.
2. Read sections 1 to 8 once, then work phase by phase from section 9. Do not start a phase before the previous one is green.
3. After each phase: run `./run_tests.sh`, complete the section 11 build checklist, commit with a descriptive message, and add a short entry to `docs/progress.md`. The comprehension check runs once, at the end of Phase 6, and is done by someone other than the implementer.
4. Start the app for manual checks with fixture data (no database needed):

   ```bash
   source .venv/bin/activate
   oracle-impact            # http://127.0.0.1:8000, token: dev-only-change-me
   ```

   Demo packages: `DEMO.PKG_STATUS` (readiness positive), `DEMO.PKG_BILLING` (blockers, compile failure), `DEMO.PKG_DISPATCH` (dynamic SQL, synonyms, AI leads when the investigator is enabled). Use all three when reviewing screens.

## 1. Goal and design principles

Every report answers one question first: **can I deploy this, and if not, what do I do next?** A release manager, a developer, and a support engineer all start there. The page must answer it within five seconds; everything else is supporting evidence reached on demand.

Principles, in priority order:

1. **Three levels of depth.** Decide (Summary tab) → Work (Findings, Changes, Dependencies) → Prove (Details tab). Never put level-3 material on the first screen.
2. **One verdict, one number per concept.** No concept is stated in more than one place on screen.
3. **Obvious navigation.** The same anchors on every page, one primary action per screen, clickable things look clickable. No tour or manual should be needed.
4. **Visual language next to the claim.** Trust is taught by badges whose words are precise: Confirmed, Possible, Not checkable, and AI lead · not verified. A help popover explains them once. It does not replace the words on the badge. Uncertainty that can affect a human decision stays on the first screen as a count, even when it cannot change the verdict.
5. **Hide emptiness on screen, keep completeness in exports.** Empty sections collapse on screen; print, PDF, and HTML exports still show every section.
6. **Responsive by design.** Each breakpoint gets a layout made for it, not a squeezed desktop.
7. **Calm, restrained visuals.** Colour carries meaning only for severity and verdict tone.

## 2. Guardrails (non-negotiable)

- **Content-security policy** in `src/oracle_impact/controllers/web.py` (`security_headers`) is `default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'`. Therefore:
  - No CDNs, no Google Fonts, no external icon fonts. Everything ships in `src/oracle_impact/views/static/`.
  - No inline `style="..."` attributes and no inline `<script>`. All styling in `app.css`, all behaviour in `app.js`.
  - Do not loosen the CSP.
- **Architecture rules:** controllers only validate input, start runs, and render templates. View shaping lives in `services/report_view.py`, `services/decision_view.py`, `services/email_view.py`. Domain models stay free of FastAPI and Jinja. Do not hardcode findings, recommendations, object names, or counts in templates.
- **Deterministic logic is untouched.** Do not change analysis, policy, readiness computation, evidence rules, or stored report schemas. This is information architecture, wording, and visual design only.
- **No content loss.** Every piece of data currently rendered must remain reachable within two clicks on screen, and must appear in print/PDF/HTML export. Section 6 maps every existing card to its new home.
- **Stored labels stay.** Policy and analyzer labels in `services/readiness.py` (`Deployment recommended`, `Deployment on hold`, `Indeterminate`) and `models/enums.py` are used by exports, emails, JSON, and tests. Friendly on-screen wording (section 4.6) is added in the view layer, not by renaming stored values.
- **Tests:** pytest must stay green without Oracle or an API key. Update assertions that test old labels or tab names; never delete a test to make it pass. A positive recommendation in a test must still come from the policy.
- **Accessibility:** WCAG 2.1 AA contrast, full keyboard use, visible focus, semantic landmarks, `prefers-reduced-motion` respected.
- **Security:** never log credentials or package source; keep `runs/` out of git; run identifiers in new URLs must be validated exactly like existing ones.

## 3. Current state inventory

Templates in `src/oracle_impact/views/templates/`, styles in `views/static/app.css` (640 lines), behaviour in `views/static/app.js` (696 lines).

| Page / partial | Route | Main problems |
|---|---|---|
| `base.html` | all | Nav has two analysis entries (Hybrid, Agent-guided) plus Business mappings; no mobile menu; no icons. |
| `dashboard.html` | `GET /` | Four environment stat cards before the form; long hints; recent-runs table duplicated on `agentic.html`. |
| `agentic.html` | `GET /agentic` | Near-copy of the dashboard form with a long technical paragraph. |
| `analysis.html` | `GET /analyses/{id}` | Stepper plus per-step detail lists always expanded. |
| `report_body.html` | `GET /analyses/{id}/report` | Breadcrumb, 4-field meta strip, up to 4 notices, decision block (readiness + state chips + analyzer recommendation + top-4 reasons + a `decision-states` list repeating recommendation and human decision), primary metrics, secondary metrics, collapsible change statistics, counting footnote, then 7 tabs. |
| `_report_overview.html` | tab `overview` | 11 cards. Three overlapping action lists (Hold table actions, Next actions, Required actions + Suggested checks). Two overlapping object lists (Changed objects, Affected objects). Plain-language summary restates the verdict. Coverage and Unverified duplicate Tests & approval. |
| `_report_findings.html` | tab `findings` | 10 filter dropdowns + 1 checkbox always visible; Source and Evidence in separate columns; a Details button column. |
| `_report_objects.html` | tab `objects` | Long explanatory paragraph; baseline and manifest forms inline; compilation and transaction cards. |
| `_report_source.html` | tab `source` | Object list + diff; already supports "Changes only" / "Full source" via `data-diff-mode`. |
| `_report_dependencies.html` | tab `dependencies` | Reasonable; keep. |
| `_report_validation.html` | tab `validation` | Repeats readiness/recommendation/human-decision as three cards (third time on the page); approval form; coverage; performance; required actions (duplicate); tests; AI review; AI leads; limitations. |
| `_report_audit.html` | tab `audit` | Metadata, reviewer actions, phases, history, object versions, tool calls, events. |
| `_finding_detail.html` | drawer + print | Six numbered blocks; evidence key-value list with fingerprint/hash/run ID up front; a caveat in almost every block. |
| `email.html` | `GET /analyses/{id}/email` | Two separate warnings; four equal-weight buttons. |
| `mappings.html`, `finding.html`, `login.html`, `missing.html`, `print.html` | various | Need the new design system and nav; no structural change required. |

Tab state: `TABS` and `initial_state()` in `src/oracle_impact/services/report_view.py` (line ~47 and ~217). Panels are rendered with `ui.panel_attrs(state, '<key>', print_mode)` from `_report_macros.html`, which sets `data-panel`. Action redirects use `_back(run_id, tab, code)` in `web.py` with tab keys `validation` (approval) and `objects` (baseline, manifest).

Existing severity glyphs in CSS (`.sev-critical::before` ◆, high ▲, medium ●, low ▼, informational ○) already avoid colour-only meaning; keep that idea, optionally swapping glyphs for sprite icons.

## 4. Key decisions

### 4.1 One verdict

Deployment readiness is the single verdict on screen. The banner title is a friendly label. The sentence under it is the readiness `summary` from `evaluate_readiness`, shown in full, not a shortened slogan.

| Decision | Banner title | Sentence under it |
|---|---|---|
| `recommended` | Recommended, pending approval | The stored summary, which ends with "Human approval is still required." |
| `on_hold` | On hold | The stored summary ("Unresolved: N blocking findings, M required validations, K unexpected drift items."). |
| `indeterminate` | Can't decide yet | The stored summary, with the indeterminate reasons listed under it. |

"Ready to deploy" is forbidden on screen. A positive readiness is a recommendation, and `docs/decisions.md` says it never implies approval.

The analyzer recommendation becomes the "basis" shown in the Details tab (and in print). The human decision becomes a small status chip next to the verdict ("Approved by …", "On hold by …", "No decision recorded"). Remove the `decision-states` list, the three state cards at the top of the old validation tab, and the plain-language summary card. The formal readiness label stays once, in small text under the banner ("Policy result: Deployment recommended, policy {version}").

### 4.2 One analysis entry point

Merge Hybrid and Agent-guided into one "New analysis" form on `/`.

- A single checkbox: **"Let the AI investigate"** with helper text "Slower. While it chooses reads it sees short summaries, not source. Afterwards it may send up to 60 source lines per read to ask about dynamic SQL and synonyms. Those answers are AI leads and do not change the verdict." Off by default. The sentence must mention both phases. The first phase does not send source. The blind-spot questions do.
- When agentic mode is unavailable (`agentic_available` false: LLM egress disabled), the checkbox is disabled with one line: "AI investigation is off on this server."
- The model selector appears only when more than one model is offered, inside a "More options" disclosure.
- `POST /analyses` accepts an `investigate` form field and calls `create_run(parsed, model, mode=AGENTIC)` when it is set (reuse the same validation and `AgenticUnavailable` handling currently in `start_agentic_analysis`).
- Keep `POST /agentic/analyses` working for backward compatibility. `GET /agentic` returns a 303 redirect to `/?investigate=1`, which pre-ticks the checkbox.
- Delete `agentic.html` once nothing renders it.
- Reports and progress pages look the same for both modes; the run mode is shown only in Details ("Run mode: With AI investigation").

### 4.3 Admin tools out of the reading flow

- **Approval:** approvers see a "Record decision" button in the verdict banner that opens a native `<dialog>` containing the existing approval form (`POST /analyses/{id}/approval`, unchanged). Non-approvers see only the decision chip. If approvals are disabled (no `OIA_APPROVER_ACCESS_TOKEN`), show nothing beyond the chip. No-JS fallback: the button is a link to `?tab=details#approval`, where the form also renders for approvers.
- **Creating baselines and approved change records** stays contextual because both pin checksums captured in this run. In the Changes tab, approvers get one collapsed "Approver tools" section containing the baseline form and per-object "Record approved change" actions. Non-approvers do not see the section at all.
- **Listing, clearing, and withdrawing** baselines and change records moves to a new page **Trust records** at `GET /registry`, linked in the top nav next to Business mappings. Existing POST endpoints `/registry/baselines/{id}/clear` and `/registry/manifest/{id}/withdraw` stay; they currently redirect via `_registry_act` — make them return to `/registry` when called from that page (hidden `return_to=registry` field, validated against an allowlist). The Changes tab shows a contextual line such as "3 objects not yet approved. Manage trust records →".

### 4.4 Report tabs: 7 → 5

Keep URL keys stable where possible to limit churn. New tab keys and labels:

| Key | Label | Replaces |
|---|---|---|
| `overview` | Summary | Overview |
| `findings` | Findings | Findings |
| `objects` | Changes | Changed objects + Source comparison |
| `dependencies` | Dependencies | Dependencies |
| `details` | Details | Tests & approval + Audit trail |

`TABS = ("overview", "findings", "objects", "dependencies", "details")`. In `initial_state()`, map legacy keys before validation: `source → objects`, `validation → details`, `audit → details`. A request with an `object` parameter and no tab now opens `objects`. Update `_back()` callers: approval returns to `overview` (the verdict), baseline/manifest return to `objects`.

Keep every legacy anchor ID currently asserted by tests: `changes` (findings panel), `comparison` (diff area, now inside Changes), `dependencies`, `ai` and `limitations` (now inside Details), `risk` (verdict banner), `phases` and `history` (inside Details), `approval`, `coverage`, `performance`, `compilation`, `transactions`, `policy-items`, `ai-leads`.

### 4.5 Finding drawer

Numbered evidence blocks: 1 What changed → 2 Does it block deployment → 3 What to test (with action scripts) → 4 Evidence (lines, diff link, dependencies). The AI interpretation is not step 5 of that sequence. Render it after the evidence as a separate block labeled "AI interpretation, not evidence", with the badge "AI lead · not verified". Move Rule, Fingerprint, Version hash, Confidence, Evidence ID, and Run into an "Evidence details" disclosure. Show the caveat ("Consequences are predicted from static evidence…") once at the bottom. Add Previous / Next buttons ("3 of 12", following the current filtered order), Esc to close, focus trap (already present), and a full-screen sheet with a Back button below 720px.

### 4.6 Plain-language wording (screen only)

Add display labels in the view layer (`decision_view.py` / `report_view.py`), leave stored values unchanged.

| Stored / current | On screen |
|---|---|
| Deployment recommended | Recommended, pending approval |
| Deployment on hold | On hold |
| Indeterminate | Can't decide yet |
| Fixture mode | Demo data |
| Hybrid analysis / Agent-guided | Checks only / AI investigated |
| Unclassified (change) | Unclassified |
| Evidence status + Source + Evidence class | One badge: **Confirmed** (deterministic, confirmed), **Possible** (deterministic, potential), **Not checkable**, **AI lead · not verified** (investigator) |

"Confirmed" means a deterministic check saw the change in source or in the dictionary. It does not mean the business effect was verified. "Unclassified" keeps its policy meaning: the object differs, and no trusted baseline or approved checksum contradicts it. It does not hold deployment, so the screen must not call it "Not yet approved".

Print, PDF, email, and JSON keep the formal labels. On screen the formal readiness label appears once as small text under the verdict ("Policy result: Deployment on hold, policy {version}").

## 5. Design system

Rewrite `app.css` around tokens. Delete rules that no template uses after the redesign.

### 5.1 Tokens

```css
:root {
  /* colour */
  --brand: #0b1f33;        /* navy accent, nav, primary buttons */
  --brand-hover: #16324f;
  --ink: #1c2b39;          /* body text */
  --muted: #5b6b7f;        /* secondary text, must stay >= 4.5:1 on --bg */
  --bg: #f6f8fb;           /* page */
  --surface: #ffffff;      /* cards */
  --surface-sunken: #eef2f7;
  --line: #e2e8f0;         /* use sparingly */
  --focus: #0b6bcb;
  --success: #0f7a4a;  --success-bg: #ecf8f1;
  --warning: #b45309;  --warning-bg: #fff6e8;
  --danger:  #be123c;  --danger-bg:  #fff1f3;
  --info:    #1d4e89;  --info-bg:    #ebf3fc;
  --ai:      #6b21a8;  --ai-bg:      #f8f3ff;
  /* severity keeps the existing hues in app.css (.sev-*) */

  /* space: 4px base */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px; --s6: 32px; --s7: 48px;

  /* type */
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --t-xs: 0.75rem; --t-sm: 0.875rem; --t-md: 1rem; --t-lg: 1.125rem;
  --t-xl: 1.375rem; --t-2xl: 1.75rem; --t-3xl: 2.25rem;

  /* shape */
  --radius: 12px; --radius-sm: 8px; --radius-pill: 999px;
  --shadow-1: 0 1px 2px rgb(15 23 42 / 6%), 0 1px 3px rgb(15 23 42 / 8%);
  --shadow-2: 0 8px 24px rgb(15 23 42 / 12%);

  /* motion */
  --ease: cubic-bezier(.2, .8, .2, 1); --fast: 120ms; --med: 220ms;
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; animation: none !important; } }
```

Body text 15–16px (`--t-md`), line-height 1.55. Headings weight 650. Cards: `--surface`, `--radius`, `--shadow-1`, no border by default; separate sections with spacing, not nested boxes.

### 5.2 Icons

- Source: [Lucide](https://lucide.dev) (ISC licence). Copy the needed SVGs from the `lucide-static` package into a single sprite `src/oracle_impact/views/static/icons.svg` with one `<symbol id="name" viewBox="0 0 24 24">` per icon. Keep the ISC licence text in an XML comment at the top of the sprite.
- Usage via a Jinja macro in a new `_icons.html`:

  ```jinja
  {% macro icon(name, label=none, cls='') -%}
  <svg class="icon {{ cls }}"{% if label %} role="img" aria-label="{{ label }}"{% else %} aria-hidden="true" focusable="false"{% endif %}><use href="/static/icons.svg#{{ name }}"></use></svg>
  {%- endmacro %}
  ```

  CSS: `.icon { width: 1.125em; height: 1.125em; stroke: currentColor; fill: none; stroke-width: 2; flex: none; }`.
- Rule: an icon always sits next to a visible text label, except universally understood controls (close, copy, menu, previous, next), which get an `aria-label`.
- Required set (Lucide names):

| Use | Icon |
|---|---|
| New analysis (nav, button) | `play` / `plus` |
| Business mappings | `building-2` |
| Trust records | `shield-check` |
| Sign out | `log-out` |
| Mobile menu / close | `menu` / `x` |
| Tabs: Summary, Findings, Changes, Dependencies, Details | `layout-dashboard`, `list-checks`, `file-diff`, `network`, `file-search` |
| Verdict: recommended / on hold / can't decide | `circle-check` / `circle-pause` / `circle-help` |
| Evidence: Confirmed / AI lead, not verified / Not checkable | `badge-check` / `sparkles` / `circle-dashed` |
| Blocks deployment / required test | `octagon-x` / `flask-conical` |
| Export / email / copy / external | `download` / `mail` / `copy` / `external-link` |
| Navigation | `chevron-right`, `chevron-left`, `arrow-left` |
| Filter / search / more | `sliders-horizontal` / `search` / `chevron-down` |
| Notices: info / warning | `info` / `triangle-alert` |
| Environment, history, time | `database`, `history`, `clock` |
| Help popover | `circle-help` |

### 5.3 Components

Build these once in CSS (and macros in `_report_macros.html` or `_ui.html` where markup repeats):

- **Buttons:** primary (filled `--brand`), secondary (outlined), ghost (text). Min height 44px on touch, 36px on desktop pointer (`@media (pointer: fine)`). Exactly one primary per screen.
- **Badges:** severity (existing hues + glyph/icon), evidence (Confirmed / Possible / Not checkable / AI lead · not verified), status (blocks / required test / does not block). Pill shape, `--t-xs`, weight 600. The AI badge includes the words "not verified" in the badge itself.
- **Verdict banner (hero):** large status icon (40px), banner title `--t-2xl` from section 4.1, then the readiness `summary` in full. Primary button "See what's blocking" (scrolls/opens the hold list) when anything is on hold or indeterminate; "View findings" when the title is "Recommended, pending approval". Decision chip and approver-only "Record decision" button. Background uses the tone `-bg` token, left accent bar in the tone colour. Do not add a second counts slogan that drops the distinction between blocking findings, required validations, and unexpected drift.
- **Measure tiles:** four tiles, each a link to its tab: Findings (with a tiny severity stack bar), Changed objects (+/− lines), Affected dependencies, Coverage as a count ("5 of 6 objects"), not a percentage. A percentage beside the verdict reads as a confidence score, which this product does not give. Replace primary, secondary, and change-statistics metrics.
- **Card:** title row (icon optional, title, optional count, optional right-side link), body. Empty state = icon + one sentence, muted.
- **Disclosure:** `<details>` styled with a rotating chevron; used for "More filters", "Evidence details", Details-tab sections, "Show run log", "Approver tools".
- **Clickable row:** whole row is the hit target (`data-open-finding` on `<tr>`), hover background, chevron at the end, `cursor: pointer`, keyboard focusable (`tabindex="0"`, Enter opens). Keep a real `<a>` inside the title cell for no-JS and middle-click.
- **Notice:** one style per tone with an icon. Limit to blocking conditions (incomplete run, superseded run, configuration problems).
- **Help popover:** "How to read this report" button in the report header opening a small panel (native `<details>` or `<dialog>`) explaining the verdict, the evidence badges, and "required test". This replaces the "How to read this report" card and every repeated disclaimer.
- **Tabs:** icon + label + count, red dot when the tab holds a blocker; sticky under the header; horizontally scrollable on small screens with fade edges; arrow-key navigation (already in `app.js` `showTab`).
- **Breadcrumb:** `New analysis › Run 1a2b3c4d › Impact report` pattern on every run-scoped page (progress, report, finding page, email).

## 6. Information architecture

### 6.1 Global layout (`base.html`)

- Top bar: wordmark (links to `/`), nav links with icons: **New analysis**, **Business mappings**, **Trust records**; right side: **Sign out**. `aria-current="page"` on the active link (keep the existing logic, simplified now that there is one analysis entry).
- Below 900px: nav collapses behind a menu button (`aria-expanded`, toggled in `app.js`). Without JS the links render stacked (progressive enhancement: hide only when `html.js` class is present; set that class at the top of `app.js`).
- Skip link "Skip to content" as first focusable element.
- `<main>` max width 1280px, side padding `--s5` (desktop) / `--s4` (mobile).

### 6.2 Dashboard (`/`)

1. Page title "New analysis" and one-line lede: "Compare packages in Test with Production before promotion."
2. **Environment status line** (one row, not cards): `database` icon, "Demo data" or "Live", "Test: {label} → Production: {label}", "AI: on/off". Only unconfigured items are highlighted with a warning tone and a short fix hint.
3. **Form card:** Packages textarea; below it, demo-package chips ("Try: DEMO.PKG_STATUS · DEMO.PKG_BILLING · DEMO.PKG_DISPATCH") that append the name to the textarea on click (no-JS: plain text); "Let the AI investigate" checkbox with the helper text from section 4.2; "More options" disclosure with the model selector; primary button "Start analysis". Validation errors appear inline above the textarea.
4. **Recent runs:** table on desktop (Run short ID linking to the report or progress page, Packages, When (relative, title shows absolute), Mode icon if AI, Status/verdict badge), card list below 720px. Drop the Model column (show model in the row's `title`). Show the latest 10 with "Show all" if more.

### 6.3 Progress (`/analyses/{id}`)

Breadcrumb; title "Analysing {packages}"; horizontal stepper (vertical below 720px) with icons for done / current / pending; the agent activity feed stays for AI runs; one current-status line; all per-step detail lines inside a single "Show run log" disclosure; errors as a danger notice with a "Start again" secondary button back to `/` with the packages pre-filled. Keep the auto-redirect to the report.

### 6.4 Report header (`report_body.html`)

1. Breadcrumb.
2. Title row: package names as the H1 (wrap gracefully), right side: Export menu (`download`), Prepare email menu (`mail`), help popover (`circle-help`). On mobile these collapse into one "Actions" menu.
3. Meta line (single line, muted): captured time · Test → Production · run ID with a copy button · "Demo data" badge when fixture mode · run mode, always: **Checks only** or **AI investigated**. Analyzer version, schema version, and policy version move to Details.
4. Notices: only incomplete run and superseded run.
5. **Verdict banner** (section 5.3), `id="risk"`. Directly under the readiness summary, when the count is non-zero, one line: "{n} things the checks could not resolve" and, when AI leads exist, "including {m} AI leads". The line links to the coverage or AI-leads anchor. Omit the line when the count is zero. Count dynamic SQL the checks could not resolve, unresolved synonyms, coverage gaps, and AI leads. Do not count ordinary findings.
6. **Measure tiles** (four).
7. Sticky tab bar.

Remove: `decision-states` list, state chips row (fold its information into the readiness summary and Details), secondary metrics, Change statistics disclosure, counting footnote (move the explanation into the help popover).

### 6.5 Summary tab (`overview`)

Order:

1. **What's holding deployment** — the hold table (from `d.hold_rows`), as clickable rows opening the finding drawer. Columns: Issue (title + finding ID), Object, Severity, Status, How it clears. Empty state: `circle-check` "Nothing is holding this deployment." Indeterminate: list the reasons here.
2. **What to do next** — one merged, de-duplicated ordered list built in the view layer from `d.next_actions`, `view.required_actions`, and `view.suggested_checks` (keep the attribution badge: Confirmed, or AI lead · not verified). Show 5, rest in a disclosure. Each item links to where the work happens. Where a hold item is fix-and-reanalyze, the action says a new analysis is required. Where it is a required test, the action says the result is tracked outside this report.
3. **Top findings** — five most severe drivers as clickable rows, link "All {n} findings →".
4. **Business impact** — the mapped-process table when mappings exist; otherwise a single muted line "No business process is mapped to these objects. Add mappings →". Support teams appear as a column here (replaces the plain-language facts list).

Removed from Summary (moved, see 6.10): plain-language summary, risk distribution (now inside the Findings measure tile and Findings tab header), affected objects (duplicate of Changes), analysis coverage, what remains unverified, how to read this report, changed objects card.

### 6.6 Findings tab

- Header: count, a compact severity stack bar with legend, short line on what a finding is (one sentence, no disclaimers).
- Toolbar: Search, Severity, Blocks deployment, "Behind the verdict" toggle, "More filters" disclosure (Object type, Category, Evidence, Environment, Change type), Reset. Show an active-filter count on "More filters" and removable chips for active filters.
- Table columns: Severity, Finding (title, ID, category, AI note indicator), Object, Evidence (single combined badge), Deployment (blocks / required test / —), chevron. Whole row opens the drawer.
- Below 720px each row renders as a card (CSS grid on `tr` with `data-label` on cells; no duplicated markup).
- Keep sort, pager, result count, no-results state, and the URL filter contract in `app.js`.

### 6.7 Changes tab (`objects`)

- Header line with classification counts as badges: Planned, Unexpected drift, Unclassified, No change, Not determined, Production-only. Trusted-baseline state as one notice only when it is not "available". Contextual link to Trust records. Unclassified stays visually distinct from Unexpected drift, and the header does not describe unclassified objects as blocking.
- Two-pane layout on desktop: left list of objects (name, type, change, +/−, highest severity, classification badge, compile status icon), right the source diff for the selected object (existing `_report_source.html` content, keep `id="comparison"`, keep Changes only / Full source toggle). Selecting an object updates `?object=` as today.
- Under the diff, per selected object: compilation status and errors (from `view.compilation`, `id="compilation"`), and transaction and data impact for that object's routines (from `view.transactions`, `id="transactions"`). Unchanged objects are grouped at the bottom of the list, collapsed.
- Below 900px: the list is shown first; choosing an object shows the diff full-width with a Back to list button. Below 720px the diff switches to a single-column interleaved view (add a third `data-diff-mode="inline"` rendering or a CSS-only stacked layout with Test/Production line markers).
- "Approver tools" disclosure (approvers only): baseline form and per-object "Record approved change" forms (existing endpoints).
- Print mode: render the full object table, every diff, compilation, and transactions in sequence as today.

### 6.8 Dependencies tab

Keep the content. Apply the design system, make edge rows card-like on mobile, keep the diagram disclosure (`min-width` on the SVG allows horizontal scroll inside its box only).

### 6.9 Details tab (`details`, new partial `_report_details.html`)

Collapsible sections (all open in print), in this order, each with the existing anchor ID:

1. **Verdict basis** — formal readiness label, policy version (and "applied now" note), analyzer recommendation with its summary and reasons, human decision with history (from `view.approval.history`), approval form for approvers (`id="approval"`, no-JS fallback).
2. **Blocking rules and required validations** (`id="policy-items"`).
3. **Recommended tests** (from `view.tests`).
4. **Coverage and blind spots** (`id="coverage"`), including dynamic SQL, dependency coverage, and the "what remains unverified" counts.
5. **AI review** (`id="ai"`) and **AI leads** (`id="ai-leads"`). When the AI did not run, replace these with one line in Run information. When their counts are non-zero, render the sections open so the header link does not land on a collapsed disclosure. AI lead text on this page uses the same "AI lead · not verified" badge.
6. **SQL performance** (`id="performance"`).
7. **Known limitations** (`id="limitations"`).
8. **Run information** — report metadata, run mode, analyzer/schema/policy versions, environments.
9. **Analysis phases** (`id="phases"`).
10. **Run history** (`id="history"`) with the previous-run comparison.
11. **Object versions** (hashes).
12. **AI tool calls** and investigator closing note.
13. **Reviewer actions** and run events.

Section headers show a one-line summary when collapsed (for example "Coverage — 5 of 6 objects fully analysed").

### 6.10 Content mapping (old → new)

| Old location | New location |
|---|---|
| Header meta: Report ID, Source captured, Comparison | Header meta line |
| Header meta: Analyzer, schema, policy | Details › Run information |
| Fixture notice | "Demo data" badge in meta line |
| Incomplete / superseded notices | Unchanged (notices) |
| Decision: readiness label, summary, indeterminate reasons | Verdict banner; reasons in Summary › What's holding deployment |
| Decision: state chips | Readiness summary in the verdict banner + Details › Verdict basis |
| Decision: analyzer recommendation + reasons | Details › Verdict basis |
| Decision: `decision-states` list | Removed (duplicate) |
| Primary / secondary metrics, Change statistics, footnote | Four measure tiles; footnote text in help popover |
| Overview › Hold table | Summary › What's holding deployment |
| Overview › Next actions, Required actions, Suggested checks | Summary › What to do next (merged) |
| Overview › Entry-point links | Removed (tabs and tiles do this) |
| Overview › Plain-language risk summary | Removed on screen; facts move to verdict and Business impact. Email drafts already carry the plain-language version. |
| Overview › Business process impact | Summary › Business impact |
| Overview › Findings behind this recommendation | Summary › Top findings |
| Overview › Changed objects | Changes tab |
| Overview › Risk distribution | Findings measure tile + Findings tab header |
| Overview › Affected objects | Changes tab object list (findings count + highest severity per object) |
| Overview › Analysis coverage, What remains unverified | Details › Coverage and blind spots; Coverage measure tile |
| Overview › How to read this report | Help popover |
| Changed objects tab (table, legend, baseline notice) | Changes tab |
| Changed objects › baseline/manifest forms | Changes › Approver tools |
| Changed objects › baseline and change-record lists | Trust records page (`/registry`) |
| Changed objects › Compilation, Transactions | Changes tab, per selected object |
| Source comparison tab | Changes tab right pane |
| Tests & approval › three state cards | Removed (duplicate); content in Details › Verdict basis |
| Tests & approval › approval form | Verdict banner dialog + Details › Verdict basis |
| Tests & approval › other cards | Details sections 2–7 |
| Audit trail tab | Details sections 8–13 |
| Recent runs on two pages | Dashboard only |

## 7. Responsive specification

Breakpoints (mobile-first, `min-width` queries): base < 720px (phone), `>= 720px` (tablet), `>= 900px` (small laptop), `>= 1200px` (desktop).

| Element | Phone | Tablet | Desktop |
|---|---|---|---|
| Top nav | Menu button, full-width sheet | Inline links, icons + labels | Inline links |
| Report actions | One "Actions" menu | Export, Email, Help | Same |
| Verdict banner | Stacked; button full width | Stacked | Verdict left, chip + buttons right |
| Measure tiles | 2 × 2 | 4 across | 4 across |
| Tabs | Scrollable, icons + short labels | Scrollable if needed | Full |
| Findings | Cards | Table, fewer columns (hide Object type meta) | Full table |
| Changes | List → diff screen with Back | List → diff screen | Two panes |
| Diff | Single-column interleaved | Side by side, horizontal scroll inside the diff only | Side by side |
| Finding drawer | Full-screen sheet, Back button | Right panel 80% width | Right panel 560px |
| Details sections | Collapsed by default | Collapsed | Collapsed (except Verdict basis) |
| Recent runs | Cards | Table | Table |

Rules: no page-level horizontal scroll at 360px; tap targets ≥ 44px; text never below 14px on mobile; tables only scroll inside their own container.

## 8. Navigation and interaction specification

- Same three anchors on every page: top nav, breadcrumb (run-scoped pages), H1.
- One primary button per screen: Dashboard "Start analysis"; Progress (on failure) "Start again"; Report "See what's blocking" (or "View findings" when nothing blocks); Email "Copy email".
- Every clickable row: hover state, chevron, pointer cursor, focusable, Enter/Space opens.
- URL reflects state (tab, finding, object, line, filters) exactly as today; browser Back closes the drawer and returns to the previous tab.
- Drawer: Previous / Next across the current filtered list, Esc closes, focus returns to the row that opened it.
- Tab bar shows counts and a blocker dot.
- First run: demo chips on the dashboard; an empty Recent runs shows "No analyses yet. Try a demo package above."
- Keyboard: existing arrow-key tab navigation; `/` focuses the findings search when the Findings tab is active (optional, announce in help popover).
- Motion: drawer slide 220ms, disclosure height/opacity 120ms, tab content fade 120ms; all disabled under `prefers-reduced-motion`.

## 9. Phases

Each phase lists files, tasks, and acceptance criteria. Commit at the end of each phase.

### Phase 1 — Foundation (design system, icons, layout, nav)

Files: `views/static/app.css`, `views/static/app.js`, new `views/static/icons.svg`, new `views/templates/_icons.html`, `base.html`, `login.html`, `missing.html`.

- Introduce the tokens (5.1) and base element styles; migrate existing components to tokens without changing page structure yet.
- Create the sprite (5.2) and the `icon` macro.
- Rebuild `base.html`: skip link, top bar, nav with icons (New analysis, Business mappings, Trust records — the last can point to `/registry` once Phase 4 adds it; until then hide it), responsive menu button, `html.js` class set by `app.js`.
- Breadcrumb component.

Acceptance: all pages render with the new top bar; nav collapses below 900px and works with keyboard; no CSP violations in the browser console; tests green.

### Phase 2 — Dashboard and single analysis entry

Files: `dashboard.html`, `_recent_runs.html`, `controllers/web.py` (`dashboard`, `start_analysis`, `agentic_page`, `_dashboard_context`, `_agentic_context`), remove `agentic.html`, `analysis.html` breadcrumb, `base.html` nav logic.

- Merged form (4.2) with demo chips, "Let the AI investigate", "More options".
- `POST /analyses` handles `investigate`; `GET /agentic` → 303 `/?investigate=1`; `POST /agentic/analyses` still works.
- Environment status line; recent runs table/cards.

Acceptance: both run modes start from `/`; disabled AI shows the reason; existing `/agentic` links still work; tests updated (section 10) and green.

### Phase 3 — Report header, verdict, measures

Files: `report_body.html`, `services/decision_view.py` and/or `services/report_view.py` (display labels, readiness summary, unresolved-checks line, measure tiles data), `app.css`, `app.js` (dialog open/close, help popover, copy run ID).

- Header, meta line including run mode, notices, verdict banner with the readiness summary, unresolved-checks line when the count is non-zero, decision chip, approver "Record decision" dialog, four measure tiles, help popover.
- Remove the duplicated blocks listed in 6.4.

Acceptance: above-the-fold on a 1440×900 screen shows title, verdict, readiness summary, and tiles with the tab bar visible; on 375×812 the verdict, the summary sentence, and the button are visible without scrolling. `DEMO.PKG_STATUS` shows "Recommended, pending approval" and "Human approval is still required." The string "Ready to deploy" appears nowhere. Approval works from the dialog for approvers and is invisible to others. Coverage tiles show a count, not a percentage.

### Phase 4 — Tabs 7 → 5 and Trust records page

Files: `services/report_view.py` (`TABS`, `initial_state` legacy mapping, merged next-actions list, per-object compilation/transactions grouping), `controllers/web.py` (`_back` tab keys, new `GET /registry`, `return_to` handling in `_registry_act`), `report_body.html` (tab list with icons and counts), `_report_overview.html` (Summary), `_report_findings.html`, `_report_objects.html` + `_report_source.html` (Changes), `_report_dependencies.html`, new `_report_details.html` replacing `_report_validation.html` and `_report_audit.html`, new `registry.html`, `app.js` (row clicks, filters disclosure, two-pane selection, mobile list/diff switching).

- Implement 6.5 to 6.9 and the content mapping 6.10. Preserve all anchor IDs in 4.4.
- Trust records page lists active and past baselines and change records with clear/withdraw for approvers, read-only for others.

Acceptance: every row in table 6.10 verified by hand on `DEMO.PKG_BILLING` and `DEMO.PKG_DISPATCH`; legacy URLs `?tab=source`, `?tab=validation`, `?tab=audit#phases` open the right place; print export still contains every section and every finding detail; tests green.

### Phase 5 — Finding drawer and finding page

Files: `_finding_detail.html`, `finding.html`, `_action_scripts.html`, `app.js`, `app.css`.

- Reorder blocks (4.5), Evidence details disclosure, single caveat, Previous/Next with "n of m", mobile full-screen sheet, Back button support.
- Standalone `finding.html` uses the same partial structure and breadcrumb.

Acceptance: drawer navigable entirely by keyboard; Previous/Next respects active filters; on 375px the sheet fills the screen and Back closes it.

### Phase 6 — Progress, email, mappings, polish

Files: `analysis.html`, `email.html`, `mappings.html`, `app.css`, `app.js`, all templates for a disclaimer sweep.

- Progress page per 6.3; email page: shorter lede, one combined setup notice, Copy as primary; mappings page restyled with the new components.
- Remove repeated disclaimers: search templates for "cannot change", "not recorded", "does not record", "interpretation, not proof" and keep each statement in the one place a reader sees the claim. "Not verified" stays on the AI badge. "Human approval is still required" stays in the recommended summary. The help popover explains the badges; it does not become the only place those limits are stated.
- Empty states with icons; motion; final contrast and focus pass; delete unused CSS.

Acceptance: section 11 build checklist passes at all three widths; the section 11 comprehension check is completed by someone other than the implementer and the answers match; Lighthouse accessibility score ≥ 95 on dashboard, report, and email pages (run in Chrome DevTools if available). Lighthouse does not replace the comprehension check.

### Phase 7 — Documentation

Update `README.md` (navigation names, single analysis form), `docs/redesign/implementation-notes.md` (URL contract: new tab keys and legacy mapping, `/registry`, `investigate` field), `docs/decisions.md` (one verdict on screen; admin tools placement; screen vs stored labels), `docs/progress.md` (final test count, screenshots).

## 10. Tests to update

Known assertions tied to the current UI (search again before each phase: `rg -n "Agent-guided|Hybrid analysis|Plain-language|tab=audit|tab=validation|tab=source|data-panel" tests`):

- `tests/test_http_and_agent.py:141` expects `href="/analyses/{run_id}/report?tab=audit#phases"` on the progress page → change to `?tab=details#phases` (legacy URL must still work; add a test for it).
- `tests/test_http_and_agent.py:151` and `:683` expect breadcrumb `href="/agentic">Agent-guided analysis` → breadcrumb now starts with `href="/">New analysis`.
- `tests/test_http_and_agent.py:682` expects "Agent-guided" on `GET /agentic` → assert the 303 redirect to `/?investigate=1` and that the checkbox is pre-ticked.
- `tests/test_report_view.py:197` iterates panels `overview, findings, source, dependencies, validation, audit` → `overview, findings, objects, dependencies, details`.
- `tests/test_report_view.py:222` deep link `tab=source` → keep as a legacy-mapping assertion that resolves to the `objects` panel; add a direct `tab=objects` case.
- `tests/test_report_view.py:239` expects "Plain-language risk summary" on the report page → remove that assertion and assert the verdict banner and "What's holding deployment" instead. Keep the anchor-ID loop (`changes`, `comparison`, `dependencies`, `ai`, `risk`, `limitations`).
- `tests/test_report_view.py:96-102` (`initial_state`) → add cases for `source`, `validation`, `audit` legacy keys.
- Any assertion on `Deployment recommended` / `Deployment on hold` / `Indeterminate` in rendered pages: the formal label still appears under the verdict and in Details, so these should keep passing. Also assert the banner title "Recommended, pending approval" and the sentence "Human approval is still required." Assert that "Ready to deploy" and "Not yet approved" do not appear.

New tests to add (in the most relevant existing file; regression scenarios go in `tests/test_regression.py`):

- `POST /analyses` with `investigate=1` starts an agentic run (scripted model); with egress disabled it returns 400 with the reason.
- Approval dialog markup present for approver sessions only; absent when approvals are disabled.
- `GET /registry` renders for analysts (read-only) and approvers (with actions); clear/withdraw from that page redirect back to `/registry`; `return_to` values outside the allowlist are ignored.
- Print export contains every Details section heading and every finding detail.
- `initial_state` legacy mapping.
- No template contains `style="` or inline `<script>` (simple file scan test).
- A completed checks-only report shows "Checks only" in the header and omits the unresolved line when the count is zero. An AI run of `DEMO.PKG_DISPATCH` shows "AI investigated" and a non-zero unresolved line that mentions AI leads. The AI badge text includes "not verified". The checkbox helper text mentions both summaries and source lines.

## 11. Verification

### Build checklist (every phase)

1. `./run_tests.sh` — all green.
2. Start the app; analyze `DEMO.PKG_STATUS`, `DEMO.PKG_BILLING`, `DEMO.PKG_DISPATCH`.
3. Screenshots with the browser tool at **375×812**, **768×1024**, **1440×900** of: dashboard, progress page, report Summary, Findings, Changes, Details, open finding drawer, email page. Save under `docs/redesign/screenshots/ux-<phase>-<page>-<width>.png`.
4. Browser console: no CSP or JavaScript errors.
5. Keyboard-only pass: skip link, nav, tabs (arrow keys), row open (Enter), drawer Previous/Next, Esc, dialog.
6. Check `?tab=source`, `?tab=validation`, `?tab=audit#phases` still land correctly.
7. Download HTML and PDF exports; confirm all sections present and that they keep the formal labels (`Deployment recommended`, not the banner title).
8. Contrast spot-check of muted text, badges, and verdict tones (AA).

Screenshots confirm layout. They do not confirm that a person understands the verdict. Lighthouse confirms accessibility mechanics. It does not confirm comprehension.

### Comprehension check (end of Phase 6, before Phase 7)

Someone other than the implementer runs these six tasks on the fixture server, without a walkthrough and without reading this plan. Record their answers. A task fails if the answer contradicts the expected result. Phase 6 is not done while any task fails. Expected results come from the policy and from `README.md`, not from the new banner wording.

| # | Setup | Task | Expected result |
|---|---|---|---|
| 1 | `DEMO.PKG_STATUS`, checks only, analyst session | Can this be deployed right now? | No. The banner says recommended, and it also says human approval is still required. A "yes" or "ready" fails the task. |
| 2 | Same report | What is the single next action? | An approver records the deployment decision. There is no code fix and no required test on this package. |
| 3 | `DEMO.PKG_BILLING` | Would you deploy this, and what is in the way? | No. Deployment is on hold. The way is blocked by deterministic findings: a breaking interface, a missing object, a compile failure, and a transaction hazard. |
| 4 | Same report | Which of those can you clear by writing a comment on this report? | None of the fix-and-reanalyze items. Only a new analysis clears those. A required test stays listed; its result is tracked outside this report, usually in an email reply. |
| 5 | `DEMO.PKG_DISPATCH` with "Let the AI investigate" ticked | What did the AI contribute, and did it change the verdict? | AI leads on the dynamic SQL and the synonym. They are marked not verified. They do not block or clear deployment. The hold comes from the deterministic findings (AUTHID, error contract, grant, objects that exist only in Test). |
| 6 | `DEMO.PKG_DISPATCH`, checks only, opened from the report link | Did AI take part, and is anything unresolved by the checks? | The header says "Checks only". The line under the verdict is present because dynamic SQL and the synonym remain unresolved, and it does not mention AI leads. |

Also have that person read the developer email draft for `DEMO.PKG_BILLING` and list the blockers. The list must match the hold table. A mismatch fails the check.

## 12. Out of scope

- Dark mode (tokens make it easy later; do not add now).
- New analysis features, policy rules, or report schema changes.
- A JavaScript framework, build step, or bundler. Plain CSS and vanilla JS only.
- Enterprise SSO or role model changes.
- Changing email draft content or the PDF layout beyond inheriting renamed headings.

## 13. Definition of done

- Report first screen shows: header (including run mode), verdict banner with the readiness summary, the unresolved line when its count is non-zero, four measures, and the tab bar.
- The banner never says "Ready to deploy". The recommended state says "Recommended, pending approval" and "Human approval is still required." Unclassified objects are labeled Unclassified. AI output is labeled "AI lead · not verified".
- Five tabs; no concept rendered twice on screen; every item in table 6.10 accounted for.
- One analysis entry point; the checkbox names both the summary phase and the later source-line reads; admin tools approver-only and out of the reading flow; Trust records page live.
- Responsive at 360–1920px with the behaviours in section 7; icons from the local sprite; CSP unchanged.
- All tests green, new tests added, docs updated, screenshots captured for the final state.
- The section 11 comprehension check passed, with answers recorded from someone other than the implementer.
