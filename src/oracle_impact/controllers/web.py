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
        return TEMPLATES.TemplateResponse(
            request,
            "finding.html",
            {"report": report, "finding": finding},
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
        "runs": _runs_for_display(request.app.state.store.list_runs()),
        "csrf_token": csrf_token(settings.secret_key, session),
        "error": error,
        "fixture_packages": ["DEMO.PKG_STATUS", "DEMO.PKG_BILLING", "DEMO.PKG_DISPATCH"] if settings.fixture_mode else [],
        "submitted_packages": packages,
        "models": models,
        "selected_model": model if model in models else settings.llm_model,
        "agentic_available": request.app.state.service.agentic_available(),
        "investigate": investigate,
    }


def _runs_for_display(runs: list[dict]) -> list[dict]:
    """Add a relative/absolute timestamp and short id for the recent-runs table."""
    from datetime import datetime, timezone

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
            when, title = "—", ""
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
        item = dict(run)
        item["when"] = when
        item["when_title"] = title
        item["short_id"] = run["run_id"][:8]
        result.append(item)
    return result


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
    return {"status": status, "report": report, "run_id": run_id, "mode": mode, "packages": packages}


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
