"""HTTP controllers. They validate requests and render templates."""

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from oracle_impact.analysis.identifiers import IdentifierError, parse_package_list
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
from oracle_impact.security import csrf_token, csrf_valid, issue_session, session_valid, token_matches
from oracle_impact.services.analysis_service import UnknownModel
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
        if not token_matches(settings.app_access_token, access_token):
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {"error": "Access token was not accepted."},
                status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            issue_session(settings.secret_key),
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
    def dashboard(request: Request, error: str = ""):
        return TEMPLATES.TemplateResponse(request, "dashboard.html", _dashboard_context(request, error, ""))

    @app.post("/analyses")
    def start_analysis(
        request: Request,
        background_tasks: BackgroundTasks,
        packages: str = Form(""),
        model: str = Form(""),
        csrf_token_value: str = Form(""),
    ):
        settings = request.app.state.settings
        session = request.cookies.get(SESSION_COOKIE)
        if not csrf_valid(settings.secret_key, session, csrf_token_value):
            return RedirectResponse("/?error=The+form+expired.+Submit+it+again.", status_code=303)
        model = model.strip()
        try:
            parsed = parse_package_list(packages)
            run_id = request.app.state.service.create_run(parsed, model)
        except (IdentifierError, UnknownModel) as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "dashboard.html",
                _dashboard_context(request, str(exc), packages, model),
                status_code=400,
            )
        background_tasks.add_task(request.app.state.background, run_id)
        return RedirectResponse(f"/analyses/{run_id}", status_code=303)

    @app.get("/analyses/{run_id}", response_class=HTMLResponse)
    def analysis_status(request: Request, run_id: str):
        payload = _load_status(request, run_id)
        if payload is None:
            return TEMPLATES.TemplateResponse(request, "missing.html", {"run_id": run_id}, status_code=404)
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
        context = _report_context(request, run_id, report)
        context["state"] = initial_state(context["view"], request.query_params)
        return TEMPLATES.TemplateResponse(request, "report.html", context)

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

            markup = TEMPLATES.get_template("print.html").render(_report_context(request, run_id, report))
            try:
                payload = render_pdf(report, markup)
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


def _dashboard_context(request: Request, error: str, packages: str, model: str = "") -> dict:
    settings = request.app.state.settings
    session = request.cookies.get(SESSION_COOKIE, "")
    models = request.app.state.service.available_models()
    return {
        "settings": settings,
        "runs": request.app.state.store.list_runs(),
        "csrf_token": csrf_token(settings.secret_key, session),
        "error": error,
        "fixture_packages": ["DEMO.PKG_STATUS", "DEMO.PKG_BILLING"] if settings.fixture_mode else [],
        "submitted_packages": packages,
        "models": models,
        "selected_model": model if model in models else settings.llm_model,
    }


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
    return {"status": status, "report": report, "run_id": run_id}


def _with_snippets(request: Request, run_id: str) -> Report | None:
    report = _report(request, run_id)
    if report is None:
        return None
    payload = _read(request, run_id, "snapshot.json")
    if payload is not None:
        apply_test_snippets(report.findings, Snapshot.model_validate(payload))
    return report


def _report(request: Request, run_id: str) -> Report | None:
    payload = _read(request, run_id, "report.json")
    if payload is None:
        return None
    return Report.model_validate(payload)


def _report_context(request: Request, run_id: str, report: Report) -> dict:
    view = build_report_view(report, _read(request, run_id, "status.json"))
    return {"report": report, "sections": _sections(report), "view": view, "state": initial_state(view, {})}


def _sections(report: Report) -> dict:
    return {
        "business": [item for item in report.findings if item.change_category == "business_logic"],
        "data": [item for item in report.findings if item.change_category == "data"],
        "transaction": [item for item in report.findings if item.change_category in {"transaction", "exception"}],
        "interface": [item for item in report.findings if item.change_category == "interface"],
        "coverage": [item for item in report.findings if item.change_category == "coverage"],
    }
