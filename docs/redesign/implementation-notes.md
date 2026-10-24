# Report redesign: implementation notes

The redesign changes how a saved report is presented. The analysis pipeline, recommendation policy, report model, and export formats are unchanged.

## Where the data comes from

Every value on the page is read from `runs/<id>/report.json` and `status.json` through `services/report_view.py`. Nothing in the templates is hard-coded from the mockup.

| Component | Source |
| --- | --- |
| Header and metadata | `report.run_id`, `created_at`, `packages`, `test_environment`, `production_environment`, `analyzer`, `report_version` |
| Analyzer recommendation | `report.recommendation`, `recommendation_label`, `recommendation_reasons` |
| Human deployment decision | Not persisted. Always "Not recorded" |
| Metric cards | Deterministic findings, `report.diffs`, `report.dependencies`, `statistics.lines_added/removed` |
| Findings table and drawer | `report.findings` via `issue_list`; investigator notes attached through `attributes.review_of` |
| Source comparison | `report.diffs` rows and hashes; finding line links from `changed_lines_test` / `corresponding_lines_prod` |
| Dependencies | `report.dependencies`; linked findings by whole-name match on `affected_object`, `dependency_path`, or `changed_object` |
| Validation and approval | `required_actions`, finding `proposed_tests`, investigator test suggestions, `limitations` |
| Audit trail | `status.json` steps and events (investigator thoughts removed), `report.tool_log`, diff hashes |

## Labels

- Evidence: "Confirmed by source comparison", "Potential, not confirmed", "Could not be checked". These map one-to-one to `EvidenceStatus`.
- Source: "Deterministic", "Deterministic + AI note", "AI assessment only". AI-only items are "Not rated" and never drive the recommendation.
- Kinds: Deterministic, AI-generated, Test-verified (nothing is test-verified today), Recommendation policy.
- Confidence is shown as the stored qualitative value. No percentages are computed.

## URL contract

`/analyses/<id>/report` accepts `tab` (`overview`, `findings`, `source`, `dependencies`, `validation`, `audit`), `finding`, `object` (`OWNER.NAME:OBJECT TYPE`), `line`, `side` (`t` or `p`), and `mode` (`developer` or `support`). Unknown values fall back to defaults. Filter and sort state (`q`, `sev`, `type`, `cat`, `src`, `ev`, `drivers`, `dq`, `dir`, `impact`, `sort`) is kept in the URL by the script. The fragments `#changes`, `#comparison`, `#dependencies`, `#ai`, `#risk`, `#limitations`, and `#issue-<id>` still resolve.

Export links (`?format=html|pdf|json`) are unchanged. The HTML and PDF exports render every panel and every finding detail.

## Blocked by missing backend data

| Requirement | Shown today |
| --- | --- |
| Human approval, rejection, or hold | "Not recorded" |
| Test execution results and verified findings | "Verification not recorded" |
| Business process and application mapping | "Not mapped" |
| Monitoring, rollback, escalation contacts | "Not recorded by the analyzer" |
| Reviewer actions and comments | "None recorded" |
| Per-phase and per-tool-call timestamps | Order only |
| Environments beyond Test and Production | Not shown |

## Screenshots

`screenshots/` holds captures from bundled fixture runs (`DEMO.PKG_BILLING` with a scripted mock investigator, `DEMO.PKG_STATUS`). They contain no data from a real schema.
