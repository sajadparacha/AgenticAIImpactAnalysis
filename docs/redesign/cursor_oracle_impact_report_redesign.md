# Cursor implementation prompt: Oracle Package Impact Analysis report redesign

You are acting as a senior enterprise UI/UX engineer, full-stack architect, and Oracle/PLSQL impact-analysis domain specialist. Redesign and implement the **existing Oracle Package Impact Analysis report** for two audiences: development engineers and production support engineers.

I will attach two references: (1) a screenshot of the current report, and (2) a proposed dashboard mockup (`oracle_impact_dashboard_mockup.png`). The mockup is a **visual/interaction reference only**. Its labels, object names, risk counts, code examples, statuses, timestamps, and suggested actions are illustrative; never copy them into real report data.

## Mission
Transform the current long, evidence-heavy page into a **decision-first, evidence-driven, progressively disclosed** report without losing existing analysis detail or changing existing deterministic/AI analysis behavior. A user must be able to answer: What is the deployment recommendation? Why? What requires action? Which Oracle objects and business processes may be affected? Where is the supporting code/evidence? What remains unverified?

## Non-negotiable guardrails
1. **Inspect the repository before coding.** Identify the existing framework, MVC conventions, routing, templates/components, APIs, report schema, authentication/authorization, analysis pipeline, current risk logic, export functionality, and tests. Preserve the established stack; do not assume React, Next.js, Tailwind, or any specific framework.
2. **Do not rewrite analysis engines or modify risk/recommendation algorithms.** The task is a UX/report presentation refactor. Make only the minimal compatible backend/read-model changes necessary to expose existing data safely. Explain any unavoidable modifications.
3. **Preserve every existing fact and capability:** environment identifiers (including TEST, AUTUMN/staging, and PRODUCTION if present), comparison direction, package/procedure/function/trigger details, source diffs, dependencies, deterministic findings, AI assessments, severity and totals, export links, and navigation. Maintain backward compatibility with existing report URLs and APIs.
4. **Never invent real analysis data.** All production UI counts, titles, findings, impact statements, dependency edges, timestamps, and statuses must be derived from actual persisted output/API data. Use clearly marked sample fixtures only in development/test/demo mode. Missing values should appear as `Not available` or `Not analyzed`, never fabricated.
5. **Do not imply that deployment is formally blocked or approved** merely because an analyzer recommends against deployment. Distinguish clearly: `Analyzer recommendation` (e.g., Not recommended) vs. `Human deployment decision` (Approved / Rejected / Pending, only if backed by an actual workflow). Use “Deployment on hold” only if an authoritative persisted state supports it.
6. **Keep Oracle production access read-only** for this reporting flow. Do not add deployment execution, DDL/DML execution, or approval writes unless compatible authorized backend workflows already exist.
7. **Avoid misleading AI confidence numbers.** Only display quantified confidence when a properly defined existing field supports it; otherwise show evidence/verification status, not an invented percentage.

## Target information architecture
Build one responsive report with these views. Prefer tabs within the report, with global application navigation unchanged when possible:

### A. Overview — default landing view
- Compact breadcrumb/header: report ID, analysis time, environment comparison direction, scope, report/export actions, and analysis version/hash when available.
- Strong but restrained recommendation banner showing the **actual analyzer recommendation** and the **specific recorded reasons** behind it. Include a distinct human decision state only if supported.
- Summary metrics derived from real report data: analyzed/changed Oracle objects by type, impacted dependencies, findings by severity, and counts of unverified/validated findings when available. Define clearly whether metrics count unique objects or occurrences; totals must reconcile.
- Immediately visible, compact **Top Blocking/High-Priority Findings** table, sorted by severity and actionable status. Do not fill it with lower-priority findings by default.
- **Required Actions Before Deployment** area. Derive steps from supported risk/remediation/test data. If these are AI suggestions, label them as suggestions requiring review; never imply they are completed tasks.
- Condensed risk distribution and affected-object summary, with charts secondary to the actual findings.
- A clearly labeled link to see the complete evidence and full findings.

### B. Findings — primary triage workspace
- Searchable, sortable, filterable table with severity, finding title/type, affected object, technical/operational impact, source (`deterministic`, `AI assessment`, `combined`), verification/review state, and details action.
- Filters for severity, object type, evidence source, review state (if available); visible total/result counts, reset filters, pagination or virtualization as appropriate.
- Selecting a finding opens a detail drawer/panel with four distinct blocks:
  1. **Observed evidence (deterministic):** actual source/environment comparison facts, object names, diff hunk/line references, dependency paths, and stable evidence IDs.
  2. **AI interpretation:** inferred consequences clearly labeled as AI-generated, with relevant citations back to observed evidence. Do not present hypotheses as tested behavior.
  3. **Verification status:** `Not verified`, `Needs review`, `Validated`, etc., only where supported by the data and workflow; otherwise use `Verification not recorded`.
  4. **Recommended investigation:** concrete review/test steps with attribution, plus a link to the exact source diff and dependency entry.
- Deep links (query params or fragment IDs) must let users open a particular finding/object/line and return without losing filters.

### C. Source Comparison — developer investigation
- List/tree of changed Oracle objects on the left, searchable and filterable by object type and severity.
- Side-by-side real source diff for the chosen environments, with accurate labels, line numbers, syntax highlighting, additions/removals, linked changed-line context, and controls for `Changes only` vs. `Full source`.
- Collapse source details in the Overview; reveal them by deliberate navigation, not as hundreds of expanded lines on the landing page.
- Selecting a relevant finding jumps to its supporting object and line/diff hunk. Large source files must be lazy-rendered/virtualized if needed; do not block page load.
- Preserve exact source formatting and comparison logic. Do not alter PL/SQL text or swap before/after environment direction.

### D. Dependencies
- Display real callers, callees, referenced tables/views/packages/functions/triggers and impact paths. Use an accessible table/tree as the reliable baseline; a graph is optional enhancement.
- Every dependency link needs a traceable source. If business-process mappings are unavailable, show `Business impact not mapped` rather than asking AI to infer business processes from package names.

### E. Validation & Approval
- Present relevant recommended tests, evidence of executed tests (only when actual records exist), verification gaps, and actual review/approval status.
- Provide update/approval controls **only when the repository already has appropriate persistence endpoints and permissions**. Otherwise render an honest read-only view or clearly separated UI prototype with disabled/non-persistent controls; do not create fake approvals.
- Separate an analyst's recommendation from authorized deployment approval.

### F. Audit Trail
- Display real report generation metadata, analyzer/environment versions or object hashes where available, completed phases, AI model/tool provenance if captured, and recorded reviewer actions.
- If an agent tool loop exists, expose high-level executed tool names, statuses, timestamps, evidence IDs, and errors; do not expose private chain-of-thought or invent steps the agent never took.

## Two audience modes, one evidence base
- Provide a `Developer` / `Support` presentation toggle (view preference, not a security role). Keep the same underlying facts and stable finding IDs.
- **Developer mode:** code-level changes, exact objects, source line references, dependencies, and engineering test suggestions.
- **Support mode:** plain-language risk summary, affected applications/processes only if genuinely mapped, possible runtime failure modes as labeled hypotheses, monitoring/rollback considerations where supported, escalation and readiness information.
- If mode-specific evidence is missing, say so clearly; never generate unsupported business impact.

## Visual and interaction direction
Follow the attached sample screen as inspiration, not as a pixel-perfect spec: restrained navy left navigation, bright neutral content area, readable typography, clear 8px-based spacing, lightly bordered cards, a strong single recommendation banner, small severity chips, compact tables, and an evidence inspector. Avoid a wall of red cards. Convey status with text and icons as well as color. Support keyboard navigation, visible focus states, semantic headings/table headers, adequate contrast (target WCAG 2.2 AA), and responsive desktop/tablet layouts. Provide meaningful loading, empty, analysis-incomplete, and AI-unavailable states. Do not make unsupported UI controls look actionable.

## Implementation sequence
**Phase 1 — Audit and contract:** Inspect existing code, identify the data flow and report data schema, enumerate all current fields/links/actions, identify UI and backend touch points, and present a short implementation plan and a field-to-component mapping. Then proceed to implementation; do not stop at the plan.

**Phase 2 — Shared presentation model:** Add a backwards-compatible adapter/view model, only if useful, which separates observed deterministic facts, AI interpretation, evidence IDs, validation/review state, and computed display metrics. Unit-test metric reconciliation and recommendation-state labeling. Keep the persisted report format unchanged unless explicitly necessary.

**Phase 3 — Overview and Findings:** Implement the decision-first landing view and searchable/filterable findings workspace, including prioritized issue navigation and evidence drawers. Preserve existing URLs/exports.

**Phase 4 — Technical detail:** Implement improved source diffs, dependencies, and deep linking using existing source and dependency records. Handle large reports efficiently.

**Phase 5 — Role views and audit/readiness:** Add Developer/Support view preferences, honest validation/approval display, and report provenance as supported by real data.

**Phase 6 — QA and handoff:** Run the existing tests; add unit tests for counts, risk labels, missing data, and evidence associations; add integration/E2E coverage for navigation, filtering, deep linking, role switching, and exports when a harness exists. Check desktop/tablet responsiveness, keyboard usage, and performance on a long report. Document changed files, data contract, assumptions, unavailable features, and tests run. Include screenshots of the implementation when tooling supports them.

## Acceptance criteria
- The Overview immediately tells users the real analyzer recommendation, its supported reasons, highest-priority findings, and what must be verified next.
- Developers can reach the supporting environment-specific PL/SQL diff from any code-related finding in no more than a few interactions.
- Support users can interpret major risks without reading full PL/SQL; missing business mappings are explicitly indicated.
- Deterministic observations, AI-inferred risks, and test-verified results are visibly and structurally distinct.
- Displayed totals reconcile with the underlying report data; no hard-coded mock values leak into production.
- Every finding and dependency is traceable to a real identifier/evidence source, or clearly marked unsupported/not available.
- Existing report semantics, analyzer logic, exports, permissions, and navigation continue to work.
- The report remains usable on large packages and long findings lists, is keyboard accessible, and provides responsive layouts.

**Start now:** inspect the repository, briefly summarize the existing architecture and exact files you will touch, then implement these phases with tests. Prefer small reviewable changes. If a requested feature depends on missing backend data/persistence, implement the truthful read-only UI and document the blocker instead of manufacturing values or endpoints.
