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

- `DEMO.PKG_STATUS` — optional parameter, eligible for review after testing
- `DEMO.PKG_BILLING` — breaking interface, missing table, and a commit before a later insert; not recommended for deployment

Enter one uppercase `OWNER.PACKAGE` name per line.

## Live Oracle

Set `OIA_FIXTURE_MODE=false` and provide separate read-only Test and Production credentials. The accounts need `SELECT` on the dictionary views listed in `docs/architecture.md`. The application never executes PL/SQL packages and never runs statements other than the fixed `SELECT` catalog plus `SET TRANSACTION READ ONLY`.

The driver package on PyPI is `oracledb` (the python-oracledb driver). Thin mode is the default, so Instant Client is not required for a normal connection.

## AI review

`OIA_LLM_EGRESS_ENABLED` defaults to false. No package source is sent anywhere until you set it and provide an approved OpenAI-compatible base URL, model, and API key. Organizational approval is required before that flag is turned on.

## Reports

Each run has a UUID. The browser shows progress, a summary, and the full report. Export links download HTML, PDF, and JSON. PDF is a print layout of the report model. WeasyPrint is used when its native libraries import; otherwise fpdf2 writes the same sections.

## Tests

`pytest` uses fixture catalogs and a scripted model. It does not connect to Oracle and does not call an external model.

## Documents

- `docs/architecture.md`
- `docs/decisions.md`
- `docs/implementation-plan.md`
- `docs/test-strategy.md`
- `docs/known-limitations.md`
- `docs/security-checklist.md`
- `docs/readiness.md`
- `docs/progress.md`
