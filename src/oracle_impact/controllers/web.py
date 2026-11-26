"""HTTP controllers. They validate requests and render templates."""

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from oracle_impact.analysis.identifiers import IdentifierError, parse_package_list
from oracle_impact.analysis.action_scripts import apply_action_scripts
from oracle_impact.analysis.test_snippets import apply_test_snippets
from oracle_impact.infrastructure.llm.plain_text import (
    ai_sources,
    investigator_notes,
    issue_list,
    plain_reasons,
    review_lines,
    severity_label,
)
from oracle_impact.models.domain import Snapshot
from oracle_impact.models.report import Report
from oracle_impact.security import (
    ANALYST,
    APPROVER,
    csrf_token,
    csrf_valid,
    issue_session,
    session_role,
    session_valid,
    token_matches,
)
from oracle_impact.services.analysis_service import AGENTIC, DETERMINISTIC, AgenticUnavailable, UnknownModel
from oracle_impact.services.decision_service import ACTION_MESSAGES, ERROR_CODES, ActionError, actor
from oracle_impact.services.email_view import AUDIENCES, DEVELOPER, build_email, build_eml, recipients
from oracle_impact.services.fix_plan import ENVIRONMENTS, build_fix_plan, combined_script
from oracle_impact.services.report_view import build_report_view, initial_state

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_ROOT / "views" / "templates"))
TEMPLATES.env.globals["review_lines"] = review_lines
TEMPLATES.env.globals["plain_reasons"] = plain_reasons
TEMPLATES.env.globals["investigator_notes"] = investigator_notes
TEMPLATES.env.globals["severity_label"] = severity_label
TEMPLATES.env.globals["issue_list"] = issue_list
TEMPLATES.env.globals["ai_sources"] = ai_sources
TEMPLATES.env.globals["build_report_view"] = build_report_view
TEMPLATES.env.globals["initial_state"] = initial_state
SESSION_COOKIE = "oia_session"
_TABS_AFTER = {"overview", "findings", "objects", "dependencies", "details"}

# The captured recommendation maps to a coarse readiness bucket for the runs list.
# "Readiness" here is the stored analyzer outcome, not a re-evaluation: a run that
# finished ("Completed") is distinct from one whose outcome was "Deployment recommended".
_RECOMMENDATION_READINESS = {
    "eligible_for_review": ("recommended", "Deployment recommended"),
    "not_recommended": ("on_hold", "Deployment on hold"),
    "requires_investigation": ("investigate", "Requires investigation"),
}
_RUN_LIFECYCLES = ("completed", "failed", "running", "queued")
_RUN_MODES = ("deterministic", "agentic")
_RUN_READINESS = ("recommended", "on_hold", "investigate")
_RUN_AGES = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}


def install_routes(app: FastAPI) -> None:
    static_dir = PACKAGE_ROOT / "views" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        path = request.url.path
        settings = request.app.state.settings
        public = path == "/health" or path == "/login" or path.startswith("/static")
        session = request.cookies.get(SESSION_COOKIE)
        if not public and not session_valid(settings.secret_key, session):
            return RedirectResponse("/login", status_code=303)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    @app.get("/health")
    def health() -> JSONResponse:
        settings = app.state.settings
        return JSONResponse(
            {
                "status": "ok",
                "fixture_mode": settings.fixture_mode,
                "test_oracle_configured": settings.test_oracle_configured,
                "production_oracle_configured": settings.production_oracle_configured,
                "llm_egress_enabled": settings.llm_egress_enabled,
                "ai_review": "enabled" if settings.llm_egress_enabled else "disabled",
            }
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": ""})

    @app.post("/login")
    def login(request: Request, access_token: str = Form("")):
        settings = request.app.state.settings
        if settings.approvals_enabled and token_matches(settings.approver_access_token, access_token):
            role = APPROVER
        elif token_matches(settings.app_access_token, access_token):
            role = ANALYST
        else:
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {"error": "Access token was not accepted."},
                status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            issue_session(settings.secret_key, role),
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, error: str = "", investigate: str = "", packages: str = ""):
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            _dashboard_context(request, error, packages, investigate=bool(investigate)),
        )

    @app.get("/preview", response_class=HTMLResponse)
    def preview(request: Request):
        # Isolated branding/icon showcase. No analysis, approval, or email side effects.
        return TEMPLATES.TemplateResponse(request, "preview.html", {})

    @app.get("/runs", response_class=HTMLResponse)
    def runs(
        request: Request,
        q: str = "",
        status: str = "",
        mode: str = "",
        readiness: str = "",
        age: str = "",
        page: int = 1,
    ):
        settings = request.app.state.settings
        rows = _runs_for_display(request.app.state.store.list_runs(), settings.snapshot_stale_hours)
        query = q.strip().lower()
        if query:
            rows = [row for row in rows if query in row["search"]]
        if status in _RUN_LIFECYCLES:
            rows = [row for row in rows if row["lifecycle"] == status]
        if mode in _RUN_MODES:
            rows = [row for row in rows if row["mode"] == mode]
        if readiness in _RUN_READINESS:
            rows = [row for row in rows if row["readiness"] == readiness]
        if age in _RUN_AGES:
            rows = _within_age(rows, age)
        total = len(rows)
        per_page = 25
        pages = ((total - 1) // per_page) + 1 if total else 1
        page = max(1, min(page, pages))
        start = (page - 1) * per_page
        page_rows = rows[start : start + per_page]
        from urllib.parse import urlencode

        page_params = {}
        if q:
            page_params["q"] = q
        if status:
            page_params["status"] = status
        if mode:
            page_params["mode"] = mode
        if readiness:
            page_params["readiness"] = readiness
        if age:
            page_params["age"] = age
        return TEMPLATES.TemplateResponse(
            request,
            "runs.html",
            {
                "rows": page_rows,
                "total": total,
                "shown": len(page_rows),
                "page": page,
                "pages": pages,
                "per_page": per_page,
                "qs": urlencode(page_params),
                "q": q,
                "status": status,
                "mode": mode,
                "readiness": readiness,
                "age": age,
                "statuses": [("", "All statuses"), ("completed", "Completed"), ("failed", "Failed"), ("running", "Running"), ("queued", "Queued")],
                "modes": [("", "All modes"), ("deterministic", "Checks only"), ("agentic", "AI investigated")],
                "readiness_options": [("", "All outcomes"), ("recommended", "Deployment recommended"), ("on_hold", "Deployment on hold"), ("investigate", "Requires investigation")],
                "ages": [("", "Any time"), ("24h", "Last 24 hours"), ("7d", "Last 7 days"), ("30d", "Last 30 days")],
            },
        )

    @app.post("/analyses")
    def start_analysis(
        request: Request,
        background_tasks: BackgroundTasks,
        packages: str = Form(""),
        model: str = Form(""),
        csrf_token_value: str = Form(""),
        investigate: str = Form(""),
    ):
        settings = request.app.state.settings
        session = request.cookies.get(SESSION_COOKIE)
        if not csrf_valid(settings.secret_key, session, csrf_token_value):
            return RedirectResponse("/?error=The+form+expired.+Submit+it+again.", status_code=303)
        model = model.strip()
        mode = AGENTIC if investigate else DETERMINISTIC
        try:
            parsed = parse_package_list(packages)
            run_id = request.app.state.service.create_run(parsed, model, mode=mode)
        except (IdentifierError, UnknownModel, AgenticUnavailable) as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "dashboard.html",
                _dashboard_context(request, str(exc), packages, model, investigate=bool(investigate)),
                status_code=400,
            )
        background_tasks.add_task(request.app.state.background, run_id)
        return RedirectResponse(f"/analyses/{run_id}", status_code=303)

    @app.get("/agentic")
    def agentic_page():
        return RedirectResponse("/?investigate=1", status_code=303)

    @app.post("/agentic/analyses")
    def start_agentic_analysis(
        request: Request,
        background_tasks: BackgroundTasks,
        packages: str = Form(""),
        model: str = Form(""),
        csrf_token_value: str = Form(""),
    ):
        settings = request.app.state.settings
        session = request.cookies.get(SESSION_COOKIE)
        if not csrf_valid(settings.secret_key, session, csrf_token_value):
            return RedirectResponse("/agentic?error=The+form+expired.+Submit+it+again.", status_code=303)
        model = model.strip()
        try:
            parsed = parse_package_list(packages)
            run_id = request.app.state.service.create_run(parsed, model, mode=AGENTIC)
        except (IdentifierError, UnknownModel, AgenticUnavailable) as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "dashboard.html",
                _dashboard_context(request, str(exc), packages, model, investigate=True),
                status_code=400,
            )
        background_tasks.add_task(request.app.state.background, run_id)
        return RedirectResponse(f"/analyses/{run_id}", status_code=303)

    @app.get("/analyses/{run_id}", response_class=HTMLResponse)
    def analysis_status(request: Request, run_id: str):
        payload = _load_status(request, run_id)
        if payload is None:
            return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
        if payload["status"].get("status") == "completed" and payload["report"] is not None:
            return RedirectResponse(f"/analyses/{run_id}/report", status_code=303)
        return TEMPLATES.TemplateResponse(request, "analysis.html", payload)

    @app.get("/analyses/{run_id}/status")
    def analysis_status_json(request: Request, run_id: str):
        status = _read(request, run_id, "status.json")
        if status is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(status)

    @app.get("/analyses/{run_id}/report", response_class=HTMLResponse)
    def analysis_report(request: Request, run_id: str):
        report = _with_snippets(request, run_id)
        if report is None:
            return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
        context = _report_context(request, run_id, report, interactive=True)
        context["state"] = initial_state(context["view"], request.query_params)
        code = request.query_params.get("msg", "")
        if code in ACTION_MESSAGES:
            context["notice"] = {"text": ACTION_MESSAGES[code], "error": code in ERROR_CODES}
        return TEMPLATES.TemplateResponse(request, "report.html", context)

    @app.get("/analyses/{run_id}/review")
    def review_json(request: Request, run_id: str):
        report = request.app.state.decisions.load(run_id)
        if report is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        context = request.app.state.decisions.context(report)
        readiness = context["readiness"]
        return JSONResponse(
            {
                "run_id": run_id,
                "readiness": {
                    key: value
                    for key, value in readiness.items()
                    if key not in {"blockers", "validations"}
                }
                | {
                    "blockers": [{**entry, "item": entry["item"].model_dump()} for entry in readiness["blockers"]],
                    "validations": [{**entry, "item": entry["item"].model_dump()} for entry in readiness["validations"]],
                },
                "classification": {key: value for key, value in context["classification"].items() if key != "objects"}
                | {"objects": [{key: value for key, value in item.items() if key != "environments"} for item in context["classification"]["objects"]]},
                "review": context["review"],
                "approval": context["approval"],
            }
        )

    @app.post("/analyses/{run_id}/approval")
    async def record_approval(request: Request, run_id: str):
        return await _act(request, run_id, "overview", "approval_saved", lambda svc, report, form, who: svc.record_approval(report, form, who))

    @app.get("/analyses/{run_id}/findings/{finding_id}", response_class=HTMLResponse)
    def finding_detail(request: Request, run_id: str, finding_id: str):
        report = _with_snippets(request, run_id)
        if report is None:
            return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
        finding = next((item for item in report.findings if item.id == finding_id), None)
        if finding is None:
            return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
        context = _report_context(request, run_id, report)
        row = next((item for item in context["view"]["rows"] if item["id"] == finding_id), None)
        return TEMPLATES.TemplateResponse(
            request,
            "finding.html",
            {"report": report, "finding": finding, "view": context["view"], "row": row},
        )

    @app.get("/analyses/{run_id}/email", response_class=HTMLResponse)
    def email_preview(request: Request, run_id: str, audience: str = DEVELOPER):
        email = _email(request, run_id, audience)
        if email is None:
            return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
        return TEMPLATES.TemplateResponse(
            request,
            "email.html",
            {
                "email": email,
                "run_id": run_id,
                "mode": (_read(request, run_id, "request.json") or {}).get("mode") or "deterministic",
                "audiences": AUDIENCES,
                "public_url_set": bool(request.app.state.settings.public_base_url),
            },
        )

    @app.get("/analyses/{run_id}/email.eml")
    def email_draft(request: Request, run_id: str, audience: str = DEVELOPER):
        email = _email(request, run_id, audience)
        if email is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(
            build_eml(email),
            media_type="message/rfc822",
            headers={"Content-Disposition": f'attachment; filename="impact-{run_id}-{email["audience"]}.eml"'},
        )

    @app.get("/analyses/{run_id}/fix-plan", response_class=HTMLResponse)
    def fix_plan_page(request: Request, run_id: str):
        plan = _fix_plan(request, run_id)
        if plan is None:
            return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
        return TEMPLATES.TemplateResponse(request, "fix_plan.html", {"plan": plan, "run_id": run_id, "environments": ENVIRONMENTS})

    @app.get("/analyses/{run_id}/fix-plan.sql")
    def fix_plan_script(request: Request, run_id: str, environment: str = "test"):
        if environment not in ENVIRONMENTS:
            return JSONResponse({"error": "environment must be test or production"}, status_code=400)
        plan = _fix_plan(request, run_id)
        if plan is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(
            combined_script(plan, environment),
            media_type="application/sql",
            headers={"Content-Disposition": f'attachment; filename="fix-plan-{plan["run_id"]}-{environment}.sql"'},
        )

    @app.get("/analyses/{run_id}/export")
    def export_report(request: Request, run_id: str, format: str = "json"):
        report = _with_snippets(request, run_id)
        if report is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if format == "json":
            return Response(report.model_dump_json(indent=2), media_type="application/json")
        if format == "html":
            markup = TEMPLATES.get_template("print.html").render(_report_context(request, run_id, report))
            return Response(markup, media_type="text/html")
        if format == "pdf":
            from oracle_impact.infrastructure.reports.pdf import render_pdf

            context = _report_context(request, run_id, report)
            markup = TEMPLATES.get_template("print.html").render(context)
            try:
                payload = render_pdf(report, markup, context["view"])
            except Exception:
                return JSONResponse(
                    {"error": "PDF export is unavailable in this environment."},
                    status_code=503,
                )
            return Response(
                payload,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{run_id}.pdf"'},
            )
        return JSONResponse({"error": "format must be html, pdf, or json"}, status_code=400)


def _dashboard_context(request: Request, error: str, packages: str, model: str = "", investigate: bool = False) -> dict:
    settings = request.app.state.settings
    session = request.cookies.get(SESSION_COOKIE, "")
    models = request.app.state.service.available_models()
    return {
        "settings": settings,
        "runs": _runs_for_display(request.app.state.store.list_runs(), settings.snapshot_stale_hours),
        "csrf_token": csrf_token(settings.secret_key, session),
        "error": error,
        "fixture_packages": ["DEMO.PKG_STATUS", "DEMO.PKG_BILLING", "DEMO.PKG_DISPATCH", "DEMO.PKG_ORDER", "DEMO.PKG_LOOKUP"] if settings.fixture_mode else [],
        "submitted_packages": packages,
        "models": models,
        "selected_model": model if model in models else settings.llm_model,
        "agentic_available": request.app.state.service.agentic_available(),
        "investigate": investigate,
    }


def _runs_for_display(runs: list[dict], stale_hours: int = 0) -> list[dict]:
    """Add a relative/absolute timestamp, short id, freshness, lifecycle, and readiness
    for the recent-runs table and the read-only runs view. Shared by both so a captured
    recommendation is always shown beside its current validity."""
    from datetime import datetime, timezone

    from oracle_impact.services.history import freshness

    now = datetime.now(timezone.utc)
    result = []
    for run in runs:
        created_at = run.get("created_at") or ""
        requested_ns = run.get("requested_ns") or 0
        dt = None
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
            except ValueError:
                dt = None
        if dt is None and requested_ns:
            dt = datetime.fromtimestamp(requested_ns / 1e9, tz=timezone.utc)
        if dt is None:
            when, title, utc, local = "—", "", "", ""
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            seconds = (now - dt).total_seconds()
            if seconds < 60:
                when = "just now"
            elif seconds < 3600:
                when = f"{int(seconds // 60)}m ago"
            elif seconds < 86400:
                when = f"{int(seconds // 3600)}h ago"
            else:
                when = f"{int(seconds // 86400)}d ago"
            title = created_at or dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            utc = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            local_dt = dt.astimezone()
            local = local_dt.strftime("%Y-%m-%d %H:%M") + " " + local_dt.strftime("%z")
        status = run.get("status") or "queued"
        if status == "completed":
            lifecycle, status_label = "completed", "Completed"
        elif status == "failed":
            lifecycle, status_label = "failed", "Failed"
        elif status in {"", "queued", "unknown"}:
            lifecycle, status_label = "queued", "Queued"
        else:
            lifecycle, status_label = "running", "Running"
        readiness, readiness_label = _RECOMMENDATION_READINESS.get(run.get("recommendation_value") or "", ("", ""))
        packages = run.get("packages") or []
        item = dict(run)
        item["when"] = when
        item["when_title"] = title
        item["utc"] = utc
        item["local"] = local
        item["short_id"] = run["run_id"][:8]
        item["freshness"] = freshness(created_at, stale_hours, now)
        item["search"] = " ".join([run["run_id"], item["short_id"], *packages]).lower()
        item["lifecycle"] = lifecycle
        item["status_label"] = status_label
        item["mode_label"] = "AI investigated" if run.get("mode") == "agentic" else "Checks only"
        item["readiness"] = readiness
        item["readiness_label"] = readiness_label
        item["status_tone"] = {"completed": "neutral", "failed": "danger", "running": "warning", "queued": "neutral"}[lifecycle]
        result.append(item)
    return result


def _within_age(rows: list[dict], age: str) -> list[dict]:
    """Keep only runs captured within the selected window. Rows without a readable
    capture time are excluded when an age filter is active."""
    from datetime import datetime, timedelta, timezone

    hours = _RUN_AGES.get(age)
    if not hours:
        return rows
    now = datetime.now(timezone.utc)
    kept = []
    for row in rows:
        created_at = row.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(created_at)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if (now - dt) <= timedelta(hours=hours):
            kept.append(row)
    return kept


def _read(request: Request, run_id: str, name: str):
    try:
        return request.app.state.store.read_json(run_id, name)
    except ValueError:
        return None


def _load_status(request: Request, run_id: str) -> dict | None:
    status = _read(request, run_id, "status.json")
    if status is None:
        return None
    report = _report(request, run_id)
    request_json = _read(request, run_id, "request.json") or {}
    mode = request_json.get("mode") or "deterministic"
    packages = request_json.get("packages") or []
    settings = request.app.state.settings
    return {
        "status": status,
        "report": report,
        "run_id": run_id,
        "mode": mode,
        "packages": packages,
        "test_label": settings.test_oracle_label,
        "prod_label": settings.prod_oracle_label,
        "error_kind": status.get("error_kind", ""),
        "failed_stage": status.get("failed_stage", ""),
        "timing": _status_timing(status),
    }


def _status_timing(status: dict) -> dict:
    """Earliest and latest recorded activity for the progress page. A failed run carries
    no report, so timing comes from the recorded steps and events only."""
    from datetime import datetime, timezone

    started = None
    updated = None
    stamps: list[datetime] = []
    for event in status.get("events") or []:
        at = event.get("at") or ""
        try:
            stamps.append(datetime.fromisoformat(at))
        except ValueError:
            continue
    for step in (status.get("steps") or {}).values():
        for key in ("started_at", "updated_at"):
            at = step.get(key) or ""
            try:
                stamps.append(datetime.fromisoformat(at))
            except ValueError:
                continue
    if stamps:
        started = min(stamps)
        updated = max(stamps)
    now = datetime.now(timezone.utc)
    started_iso = started.isoformat() if started else ""
    updated_iso = updated.isoformat() if updated else ""
    if started is None:
        elapsed = ""
        elapsed_epoch = 0
    else:
        end = updated if status.get("status") in {"completed", "failed"} else now
        elapsed = _elapsed_text(max(0, (end - started).total_seconds()))
        elapsed_epoch = started.timestamp()
    return {
        "started_at": started_iso,
        "started_epoch": elapsed_epoch,
        "last_update": updated_iso,
        "last_update_label": _relative_text(updated, now) if updated else "—",
        "elapsed": elapsed,
    }


def _elapsed_text(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _relative_text(ts, now) -> str:
    if ts is None:
        return "—"
    seconds = int((now - ts).total_seconds())
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def _with_snippets(request: Request, run_id: str) -> Report | None:
    report = _report(request, run_id)
    if report is None:
        return None
    payload = _read(request, run_id, "snapshot.json")
    snapshot = Snapshot.model_validate(payload) if payload is not None else None
    if snapshot is not None:
        apply_test_snippets(report.findings, snapshot)
    apply_action_scripts(report.findings, report.dependency_edges, snapshot)
    return report


def _report(request: Request, run_id: str) -> Report | None:
    payload = _read(request, run_id, "report.json")
    if payload is None:
        return None
    return Report.model_validate(payload)


def _email(request: Request, run_id: str, audience: str) -> dict | None:
    report = _report(request, run_id)
    if report is None:
        return None
    settings = request.app.state.settings
    audience = audience if audience in AUDIENCES else DEVELOPER
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    return build_email(
        report,
        request.app.state.decisions.context(report)["readiness"],
        audience,
        to=recipients(settings.email_developers if audience == DEVELOPER else settings.email_support),
        report_url=f"{base}/analyses/{report.run_id}/report",
    )


def _fix_plan(request: Request, run_id: str) -> dict | None:
    report = _with_snippets(request, run_id)
    if report is None:
        return None
    readiness = request.app.state.decisions.context(report)["readiness"]
    return build_fix_plan(report, readiness, report_url=f"/analyses/{report.run_id}/report")


def _report_context(request: Request, run_id: str, report: Report, interactive: bool = False) -> dict:
    settings = request.app.state.settings
    cookie = request.cookies.get(SESSION_COOKIE, "")
    session = {
        "role": session_role(settings.secret_key, cookie),
        "csrf": csrf_token(settings.secret_key, cookie) if interactive else "",
        "approvals_enabled": settings.approvals_enabled,
        "interactive": interactive,
    }
    decision = request.app.state.decisions.context(report)
    view = build_report_view(report, _read(request, run_id, "status.json"), decision, session)
    return {"report": report, "sections": _sections(report), "view": view, "state": initial_state(view, {})}


async def _form(request: Request) -> tuple[dict, dict | None]:
    """Parsed form fields and the acting user, or None when the session or CSRF token is not valid."""
    settings = request.app.state.settings
    form = {key: value for key, value in (await request.form()).items() if isinstance(value, str)}
    cookie = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(settings.secret_key, cookie, form.get("csrf_token_value")):
        return form, None
    return form, actor(session_role(settings.secret_key, cookie) or ANALYST, form.get("actor_name", ""))


def _back(run_id: str, tab: str, code: str, finding: str = "") -> RedirectResponse:
    tab = tab if tab in _TABS_AFTER else "overview"
    target = f"/analyses/{run_id}/report?tab={tab}&msg={code}"
    if finding:
        target += f"&finding={finding}"
    return RedirectResponse(target, status_code=303)


async def _act(request: Request, run_id: str, tab: str, success: str, action) -> Response:
    service = request.app.state.decisions
    report = service.load(run_id)
    if report is None:
        return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
    form, who = await _form(request)
    finding = form.get("finding_id", "") if form.get("finding_id", "") in {item.id for item in report.findings} else ""
    back_tab = form.get("return_tab", tab)
    if who is None:
        return _back(run_id, back_tab, "form_expired", finding)
    try:
        action(service, report, form, who)
    except ActionError as exc:
        return _back(run_id, back_tab, exc.code, finding)
    return _back(run_id, back_tab, success, finding)


def _sections(report: Report) -> dict:
    return {
        "business": [item for item in report.findings if item.change_category == "business_logic"],
        "data": [item for item in report.findings if item.change_category == "data"],
        "transaction": [item for item in report.findings if item.change_category in {"transaction", "exception"}],
        "interface": [item for item in report.findings if item.change_category == "interface"],
        "coverage": [item for item in report.findings if item.change_category == "coverage"],
    }
