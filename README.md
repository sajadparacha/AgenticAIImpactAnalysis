# Oracle package impact analysis

Web application that compares selected Oracle PL/SQL packages in Test with Production, then writes an evidence-backed impact report.

The analysis is hybrid. Python always performs the snapshot, diff, signature, dependency, and impact checks. One optional investigator can request more read-only evidence and propose hypotheses. It cannot confirm a finding or approve a deployment.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
oracle-impact
```

Open http://127.0.0.1:8000 and sign in. The default development token is `dev-only-change-me` until you set `OIA_APP_ACCESS_TOKEN`. The server binds to `127.0.0.1`.

Fixture mode is the default. These packages can be analyzed without a database:

- `DEMO.PKG_STATUS` — optional parameter, eligible for review after testing; readiness Deployment recommended
- `DEMO.PKG_BILLING` — breaking interface, missing table, a commit before a later insert, and a dependent (`DEMO.PKG_LEGACY`) that no longer compiles in Test; not recommended, deployment on hold

Enter one uppercase `OWNER.PACKAGE` name per line.

## Live Oracle

Set `OIA_FIXTURE_MODE=false` and provide separate read-only Test and Production credentials, and optionally AUTUMN. The accounts need `SELECT` on the dictionary views listed in `docs/architecture.md`. The application never executes PL/SQL packages and never runs statements other than the fixed `SELECT` catalog plus `SET TRANSACTION READ ONLY`.

The driver package on PyPI is `oracledb` (the python-oracledb driver). Thin mode is the default, so Instant Client is not required for a normal connection.

## AI review

`OIA_LLM_EGRESS_ENABLED` defaults to false. No package source is sent anywhere until you set it and provide an approved OpenAI-compatible base URL, model, and API key. Organizational approval is required before that flag is turned on.

## Deployment decision support

Each report shows a readiness decision computed from the configurable blocking policy: **Deployment recommended**, **Deployment on hold**, or **Indeterminate**. It also shows four separate states (analysis completed, coverage complete, deployment recommended, human approval), the reasons deployment is on hold, and the next actions. A recommendation is never an approval.

- Analysts record regression test results and reviewer decisions against findings.
- Approvers sign in with `OIA_APPROVER_ACCESS_TOKEN` (it must differ from the app token). They record the deployment decision, trusted Production and AUTUMN baselines, approved changes (a manifest that pins the Test checksum), and business process mappings at `/mappings`.
- Changes are classified as planned, unexpected drift, or unclassified against those records.
- Compilation readiness reads `ALL_OBJECTS` and `ALL_ERRORS`. Nothing is compiled.
- AUTUMN (staging) is an optional third read-only connection (`OIA_AUTUMN_ORACLE_*`). Fixture mode includes an AUTUMN catalog.

`docs/architecture.md` describes the rules. `docs/known-limitations.md` lists what they cannot see.

## Reports

Each run has a UUID. The browser shows progress, a summary, and the full report. Export links download HTML, PDF, and JSON. PDF is a print layout of the report model. WeasyPrint is used when its native libraries import; otherwise fpdf2 writes the same sections.

## Tests

`pytest` uses fixture catalogs and a scripted model. It does not connect to Oracle and does not call an external model.

`./run_tests.sh` runs the whole suite from `.venv` and ends with a per-file results table and a list of failed tests with their first error line. Add `--setup` once to create the virtual environment and install the dev dependencies. Any other arguments go to pytest, for example `./run_tests.sh tests/test_integration.py -v`.

## Documents

- `docs/architecture.md`
- `docs/decisions.md`
- `docs/implementation-plan.md`
- `docs/test-strategy.md`
- `docs/known-limitations.md`
- `docs/security-checklist.md`
- `docs/readiness.md`
- `docs/progress.md`
