# Report redesign: implementation notes

The report is a deployment decision-support page. The analyzer recommendation and export formats are unchanged; readiness, classification, coverage, compilation, transaction impact, business impact, validation tracking, approval, and history were added. See `docs/architecture.md` for the rules behind each state.

## Where the data comes from

Every value on the page is read from `runs/<id>/report.json`, `status.json`, `review.json`, and the registry through `services/report_view.py` and `services/decision_view.py`. Nothing in the templates is hard-coded from the mockup.

| Component | Source |
| --- | --- |
| Header and metadata | `report.run_id`, `created_at`, `packages`, `environments`, `analyzer`, `report_version`, `policy.policy_version` |
| Readiness banner | `evaluate_readiness` over `report.policy`, review records, drift classification, coverage, newer runs, snapshot age |
| Four states | Analysis completed (`report.status`), Coverage complete (`report.coverage`), Deployment recommended (readiness), Human approval (latest approval record) |
| Primary metrics | Planned changes, unexpected drift, coverage, open blockers |
| Secondary metrics | Unclassified changes, open validations, invalid objects in Test, dynamic SQL to verify, snapshot age, baseline state |
| "Why deployment is on hold" | Unresolved policy items and drift, with finding, object, severity, evidence, status, and action |
| Findings explorer | `report.findings` with `rule_id`, `fingerprint`, `severity_rationale`, evidence class, policy item, validation and review state |
| Changed objects | `report.object_states`, classification, manifest, baselines, compilation, transaction profiles |
| Source comparison | `report.diffs` for Test to Production; `report.comparisons` for Test to AUTUMN and AUTUMN to Production |
| Dependencies | `report.dependency_edges` (provenance dictionary, source scan, unresolved), impact path tree, business mappings |
| Validation and approval | Policy items, review records, approval history, coverage, external checks, performance status |
| Audit trail | Phase and tool-call times, review and registry events, approval history, run history and comparison |

## Evidence classes

Deterministic verified, AI interpretation, unverified hypothesis, test-validated (a passed validation is recorded), and human-reviewed (a reviewer decision is recorded). Investigator output can only be AI interpretation or unverified hypothesis.

## Business impact

Confirmed (a mapped object has a confirmed finding), Potential, "No impact identified within analyzed scope", and Not analyzed. Objects without a mapping show "Business impact not established". Mappings are maintained at `/mappings` by approvers.

## URL contract

`/analyses/<id>/report` accepts `tab` (`overview`, `findings`, `objects`, `source`, `dependencies`, `validation`, `audit`), `finding`, `object` (`OWNER.NAME:OBJECT TYPE`), `line`, `side` (`t` or `p`), `mode` (`developer` or `support`), `cmp` (`test:autumn` or `autumn:production`), and `msg` (a fixed action message code). Unknown values fall back to defaults. Filter and sort state is kept in the URL by the script: findings `q`, `sev`, `type`, `cat`, `src`, `ev`, `ec` (evidence class), `val` (validation state), `blk` (blocking), `env`, `chg` (change type), `drivers`, `sort`; dependencies `dq`, `dir`, `impact`; edges `eq`, `edir`, `eprov`, `etype`. The fragments `#changes`, `#comparison`, `#dependencies`, `#ai`, `#risk`, `#limitations`, `#issue-<id>`, `#objects`, `#compilation`, `#transactions`, `#coverage`, `#approval`, `#regression-tests`, `#performance`, and `#history` resolve to their tab.

Export links (`?format=html|pdf|json`) are unchanged. The HTML and PDF exports render every panel and finding detail, without forms. The fpdf2 PDF fallback adds the readiness decision, the four states, the hold reasons, coverage, and compilation.

## New endpoints

| Method and path | Who | Purpose |
| --- | --- | --- |
| `GET /analyses/{id}/review` | Signed in | JSON: readiness, classification, review records, approval |
| `POST /analyses/{id}/validations` | Signed in | Record or update a validation result for a finding |
| `POST /analyses/{id}/reviews` | Signed in | Record a reviewer decision for a finding |
| `POST /analyses/{id}/approval` | Approver | Record Approved, Rejected, or On hold. Approved needs Deployment recommended |
| `POST /analyses/{id}/baseline` | Approver | Trust the captured Production or AUTUMN checksum of one or all objects |
| `POST /analyses/{id}/manifest` | Approver | Pin the captured Test checksum under a change reference |
| `POST /registry/manifest/{entry}/withdraw` | Approver | Withdraw a manifest entry with a reason |
| `POST /registry/baselines/{entry}/clear` | Approver | Clear a trusted baseline with a reason |
| `GET /mappings`, `POST /mappings`, `POST /mappings/{id}/retire` | View: signed in; change: approver | Business process mappings |

All POST routes require the CSRF token and redirect back with `?msg=<code>`.

## Screenshots

`screenshots/` holds captures from bundled fixture runs (`DEMO.PKG_BILLING`, `DEMO.PKG_STATUS`). They contain no data from a real schema. `decision-*.png` show the decision-support pages; the other files are from the earlier redesign.
