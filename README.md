# Oracle package impact analysis

Web application that compares selected Oracle PL/SQL packages in Test with Production, then writes an evidence-backed impact report.

The analysis is hybrid. Python always performs the snapshot, diff, signature, dependency, and impact checks. One optional investigator can request more read-only evidence and propose hypotheses. It cannot confirm a finding or approve a deployment.

## Run locally

```bash
./start.sh --setup
```

`--setup` creates `.venv`, installs the dependencies, and copies `.env.example` to `.env` when there is no `.env` yet. After that, `./start.sh` starts the server again, `./start.sh --reload` restarts it when code changes, and `--host`/`--port` override `OIA_HOST` and `OIA_PORT`. Ctrl+C stops it. The same steps by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
oracle-impact
```

Open http://127.0.0.1:8000 and sign in. The default development token is `dev-only-change-me` until you set `OIA_APP_ACCESS_TOKEN`. The server binds to `127.0.0.1`.

Fixture mode is the default. `src/oracle_impact/fixtures/demo_catalog.py` mirrors the lab schema in `lab/demo_schema/`, so these packages can be analyzed without a database:

- `DEMO.PKG_STATUS` — optional parameter, eligible for review after testing; readiness Deployment recommended
- `DEMO.PKG_BILLING` — breaking interface, a missing Test-only object (`DEMO.BILLING_HISTORY`), a commit before a later insert, and a dependent (`DEMO.PKG_LEGACY`) that no longer compiles in Test; not recommended, deployment on hold
- `DEMO.PKG_DISPATCH` — dynamic SQL, a synonym that resolves to a different local table in each environment, two functions (`DEMO.NEXT_BATCH`, `DEMO.NEXT_DISPATCH_ID`) that exist only in Test, an `AUTHID` change, a changed error contract, and a grant Production does not have. The caller `DEMO.PKG_MAILER` still compiles. Not recommended, deployment on hold. With the investigator enabled, the dynamic SQL and the synonym become AI leads.
- `DEMO.PKG_ORDER` — a mandatory parameter, an `AUTHID` change, a public exception, a Test-only function (`DEMO.ORDER_FEE`) and view (`DEMO.ORDER_AUDIT`), a commit before a later insert, dynamic SQL, and a grant Production does not have. The caller `DEMO.PKG_ORDER_CALLER` no longer compiles in Test. Not recommended, deployment on hold.
- `DEMO.PKG_LOOKUP` — identical in Test and Production, so no blocker or required validation; readiness Deployment recommended.

Enter one uppercase `OWNER.PACKAGE` name per line.

## Live Oracle

Set `OIA_FIXTURE_MODE=false` and provide separate read-only Test and Production credentials. The accounts need `SELECT` on the dictionary views listed in `docs/architecture.md`. The application never executes PL/SQL packages and never runs statements other than the fixed `SELECT` catalog plus `SET TRANSACTION READ ONLY`.

The driver package on PyPI is `oracledb` (the python-oracledb driver). Thin mode is the default, so Instant Client is not required for a normal connection.

### Demo schema

`lab/demo_schema/` creates the same objects in a real Oracle database. It is a lab, not something to deploy.

```bash
python lab/demo_schema/apply.py --docker-container <oracle-container-name>
python lab/demo_schema/apply.py --docker-container <oracle-container-name> --clean
```

One database can hold both environments through Oracle editions: Production is `ORA$BASE` and Test is a child edition (`OIA_TEST` by default). Point the application at a read-only account that owns `SELECT ANY DICTIONARY` and `USE` on the Test edition. Quote the base edition name in `.env`, because a shell would expand `$BASE`:

```bash
OIA_FIXTURE_MODE=false
OIA_TEST_ORACLE_EDITION=OIA_TEST
OIA_PROD_ORACLE_EDITION='ORA$BASE'
```

Pass `--dsn` and `--test-dsn` instead to create the objects in two separate databases; leave both edition settings empty then. Each database gets its own objects and its own copy of the synthetic data. Both must accept the same DBA password, and `OIA_READER_PASSWORD` creates the read-only `OIA_READER` account where it is missing. For example, a second container for Test:

```bash
docker run -d --name oracle-impact-test -p 1522:1521 -e ORACLE_PASSWORD=<same as Production> gvenzl/oracle-free:23-slim-faststart
OIA_READER_PASSWORD=<reader password> python lab/demo_schema/apply.py --docker-container <production-container> \
  --dsn 127.0.0.1:1521/FREEPDB1 --test-dsn 127.0.0.1:1522/FREEPDB1 --clean
```

 Tables and sequences cannot be editioned, so in one database the Test-only `DEMO.BILLING_HISTORY` is a view over the shared `DEMO.BILLING_HISTORY_DATA` table, and `DEMO.NEXT_DISPATCH_ID` is a function where a separate database would have a sequence. `lab/demo_schema/99_cleanup.sql` drops the demo user and roles.

The script prints the Production and Test status of every object and fails if the drift the checks rely on is missing, such as `DEMO.PKG_LEGACY` compiling in Production and being `INVALID` in Test.

It also loads synthetic rows from `lab/demo_schema/04_seed_data.sql`, so the packages can be called by hand. The header of that file lists which inputs behave differently in each environment. `--seed-only` resets the tables to that data without rebuilding anything, connecting as `DEMO`. Tables are not editioned, so on one database both environments read the same rows; only the `QUOTE_BOOK` synonym target and the Test-only `BILLING_HISTORY` view differ.

## AI review

`OIA_LLM_EGRESS_ENABLED` defaults to false. No package source is sent anywhere until you set it and provide an approved OpenAI-compatible base URL, model, and API key. Organizational approval is required before that flag is turned on.

There is one analysis form on `/`. Python always performs the snapshot, diff, signature, dependency, and impact checks. A single **"Let the AI investigate"** checkbox turns on the optional investigator; the same form runs both modes:

- **Checks only** (checkbox off). Python reads Test and Production itself and scores the change. Nothing is sent to a model.
- **AI investigated** (checkbox on). The investigator chooses which allowlisted, read-only dictionary read comes next until a required-evidence checklist is empty. It sees short summaries of each read, not package source. The same deterministic checks then score what it collected and choose the recommendation. After scoring, it gets one narrow question about each item the checks could not resolve — SQL built while the program runs, and synonyms the package uses — which send up to 60 source lines per read. It can read the cited source lines, read where a synonym points (`ALL_SYNONYMS`), and check whether a named object exists. Its answers appear as "AI lead · not verified" items; they never block or clear deployment, and the original item stays open. Apart from those leads the investigator adds no findings, and its closing note is shown in Details only. Agentic runs have their own run budgets, `OIA_AGENTIC_MAX_TOOL_CALLS_PER_RUN` (default 80) and `OIA_AGENTIC_TOKEN_BUDGET` (default 60000), because they read every environment through the model. `OIA_MAX_TOOL_CALLS_PER_PACKAGE` and `OIA_ANALYSIS_TIMEOUT_SECONDS` still apply. Reads it does not reach within these budgets are reported as coverage gaps, and an invalid object whose ALL_ERRORS rows were not read says so instead of showing no errors. Unread package source makes readiness Indeterminate. The checkbox is disabled while egress is off.

## Deployment decision support

Each report opens with a single verdict computed from the configurable blocking policy. The banner shows a friendly on-screen label — **Recommended, pending approval**, **On hold**, or **Can't decide yet** — above the readiness summary ("Human approval is still required."). The formal policy labels (**Deployment recommended**, **Deployment on hold**, **Indeterminate**) appear once in small text under the banner and remain the labels used by exports, email, and JSON. A recommendation is never an approval. The human decision is a small status chip next to the verdict; the report also shows the reasons deployment is on hold and the next actions.

- The report and the email are read-only shares for developers and support. Test results are not recorded in the application; recommended tests are for the people who receive the report.
- Approvers sign in with `OIA_APPROVER_ACCESS_TOKEN` (it must differ from the app token). They record the deployment decision from the verdict banner.
- Change classification is a direct comparison of the captured Test and Production checksums. The application keeps no record of approved changes, so a difference is reported as a difference rather than as planned or drift.
- Compilation readiness reads `ALL_OBJECTS` and `ALL_ERRORS`. Nothing is compiled.
- Objects that only the Test version depends on (for example a new sequence or function) are looked up in Production's `ALL_OBJECTS`. A missing one blocks deployment.
- Under **Recommended action**, a finding can carry copy-paste scripts built from its recorded evidence: `GRANT` statements for a grant Production lacks, a `DBMS_METADATA` DDL extract from Test plus a Production existence check for a missing object, `ALTER ... COMPILE` with an `ALL_ERRORS` query for invalid objects and for the callers of a changed interface, and a read-only `ALL_OBJECTS` check when existence could not be read. Each script says whether it is for Test or Production. Review actions such as business rules, error contracts, `AUTHID`, and dynamic SQL get no script. The model does not write these scripts, and the application never runs them.
- **Step-by-step fix plan** appears in the verdict banner when blocking items or required tests are open. It puts every open item into numbered steps in release order: fix the code in Test, prepare Production, test or justify, run a new analysis, then approve and release (grants come after the package is deployed). Steps with a script show it inline, and **Test script** and **Production script** download every script for one environment as a single `.sql` file, in step order, with each section labelled with its step number. Review steps have no script and stay as checklist items; **Copy checklist** copies the whole plan as text for a ticket. Investigator leads never become steps. The application never runs the scripts.
- **Prepare email** on each report builds a friendly draft from the report's open blockers, validation items, and findings, in a developer version (code detail, suggested owners, tests) and a support version (runbooks, monitoring, jobs, and what developers are fixing). Copy it, open it in a mail app, or download an unsent `.eml` draft. The application never sends mail. Set `OIA_EMAIL_DEVELOPERS`, `OIA_EMAIL_SUPPORT`, and `OIA_PUBLIC_BASE_URL` so the recipients and report link are filled in.
`docs/architecture.md` describes the rules. `docs/known-limitations.md` lists what they cannot see.

## Reports

Each run has a UUID. The browser shows progress, a summary, and the full report. The report has five tabs — **Summary**, **Findings**, **Changes**, **Dependencies**, and **Details** — and every finding opens in a side drawer with numbered evidence blocks. The top navigation is the same on every page: **New analysis** and **Sign out**. Export links download HTML, PDF, and JSON. PDF is a print layout of the report model. WeasyPrint is used when its native libraries import; otherwise fpdf2 writes the same sections.

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
