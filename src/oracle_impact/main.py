"""Application factory."""

import logging
from collections.abc import Callable

from fastapi import FastAPI

from oracle_impact.config import Settings
from oracle_impact.controllers.web import install_routes
from oracle_impact.fixtures.demo_catalog import build_demo_catalogs
from oracle_impact.infrastructure.llm.investigator import Investigator
from oracle_impact.infrastructure.llm.openai_compatible import OpenAICompatibleProvider
from oracle_impact.infrastructure.oracle.gateway import FixtureGateway, UnconfiguredGateway
from oracle_impact.infrastructure.oracle.oracledb_gateway import OracledbGateway
from oracle_impact.infrastructure.storage.registry import Registry
from oracle_impact.infrastructure.storage.review_store import ReviewStore
from oracle_impact.infrastructure.storage.run_store import RunStore
from oracle_impact.services.analysis_service import AnalysisService
from oracle_impact.services.decision_service import DecisionService
from oracle_impact.services.readiness import load_rules

logger = logging.getLogger("oracle_impact")


def create_app(
    settings: Settings | None = None,
    test_gateway=None,
    production_gateway=None,
    investigator=None,
    background: Callable[[str], None] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if settings.using_default_token:
        logger.warning(
            "The default development access token is in use. Set OIA_APP_ACCESS_TOKEN before any shared deployment."
        )
    store = RunStore(settings.runs_dir)
    registry = Registry(settings.registry_dir)
    if test_gateway is None or production_gateway is None:
        test_gateway, production_gateway = build_gateways(settings)
    if investigator is None and settings.llm_egress_enabled:
        investigator = Investigator(
            provider=OpenAICompatibleProvider(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout=settings.analysis_timeout_seconds,
                egress_enabled=settings.llm_egress_enabled,
            ),
            max_calls_per_package=settings.max_tool_calls_per_package,
            max_calls_per_run=settings.max_tool_calls_per_run,
            timeout_seconds=settings.analysis_timeout_seconds,
            token_budget=settings.llm_token_budget,
            max_depth=settings.max_dependency_depth,
            gather_token_budget=settings.agentic_token_budget,
            gather_max_calls_per_run=settings.agentic_max_tool_calls_per_run,
        )
    service = AnalysisService(
        settings,
        store,
        test_gateway,
        production_gateway,
        investigator,
        registry=registry,
        rules=load_rules(settings.policy_file),
    )

    def enqueue(run_id: str) -> None:
        service.execute(run_id)

    app = FastAPI(title="Oracle AI Impact Analysis", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.registry = registry
    app.state.reviews = ReviewStore(store)
    app.state.decisions = DecisionService(settings, store, registry, app.state.reviews)
    app.state.service = service
    app.state.background = background or enqueue
    install_routes(app)
    return app


def build_gateways(settings: Settings):
    if settings.fixture_mode:
        test_catalog, prod_catalog = build_demo_catalogs()
        return (
            FixtureGateway("test", "fixture-test", settings.test_oracle_label, test_catalog),
            FixtureGateway("production", "fixture-production", settings.prod_oracle_label, prod_catalog),
        )
    test_gateway = _live_or_unconfigured(
        "test",
        settings.test_oracle_label,
        settings.test_oracle_dsn,
        settings.test_oracle_user,
        settings.test_oracle_password,
        settings.test_oracle_edition,
    )
    prod_gateway = _live_or_unconfigured(
        "production",
        settings.prod_oracle_label,
        settings.prod_oracle_dsn,
        settings.prod_oracle_user,
        settings.prod_oracle_password,
        settings.prod_oracle_edition,
    )
    return test_gateway, prod_gateway


def _live_or_unconfigured(environment: str, label: str, dsn: str, user: str, password: str, edition: str = ""):
    if not dsn or not user:
        return UnconfiguredGateway(environment, label)
    return OracledbGateway(environment, label, dsn, user, password, edition)


app = create_app()


def main() -> None:
    import uvicorn

    settings = app.state.settings
    uvicorn.run(app, host=settings.host, port=settings.port)
