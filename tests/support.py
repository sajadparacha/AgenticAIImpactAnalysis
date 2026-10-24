"""Shared builders for engine and regression tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from oracle_impact.config import Settings
from oracle_impact.infrastructure.oracle.gateway import Catalog, FixtureGateway
from oracle_impact.infrastructure.storage.run_store import RunStore
from oracle_impact.main import create_app
from oracle_impact.models.domain import Argument, Column, Constraint, PackageRef, Trigger
from oracle_impact.models.report import Report
from oracle_impact.services.analysis_service import AnalysisService


def settings(tmp_path: Path, **overrides) -> Settings:
    payload = {
        "app_env": "development",
        "secret_key": "test-secret",
        "app_access_token": "test-token",
        "host": "127.0.0.1",
        "runs_dir": tmp_path,
        "fixture_mode": True,
        "llm_egress_enabled": False,
        "test_oracle_password": "should-not-leak-test",
        "prod_oracle_password": "should-not-leak-prod",
        "llm_api_key": "should-not-leak-key",
    }
    payload.update(overrides)
    return Settings(**payload)


def argument(routine: str, name: str | None, position: int, data_type: str, in_out: str = "IN", defaulted: bool = False, overload: str = "") -> Argument:
    return Argument(
        object_name=routine,
        overload=overload,
        argument_name=name,
        position=position,
        data_type=data_type,
        in_out=in_out,
        defaulted=defaulted,
    )


def client_for(tmp_path: Path, **overrides) -> TestClient:
    application = create_app(settings(tmp_path, **overrides))
    return TestClient(application)


def login(client: TestClient) -> None:
    response = client.post("/login", data={"access_token": "test-token"}, follow_redirects=False)
    assert response.status_code == 303


def run_catalogs(tmp_path: Path, packages: list[str], test_catalog: Catalog, prod_catalog: Catalog, **overrides) -> Report:
    investigator = overrides.pop("investigator", None)
    configured = settings(tmp_path, **overrides)
    service = AnalysisService(
        configured,
        RunStore(tmp_path),
        FixtureGateway("test", "fixture-test", "TEST", test_catalog),
        FixtureGateway("production", "fixture-production", "PRODUCTION", prod_catalog),
        investigator=investigator,
    )
    parsed = [PackageRef(owner=item.split(".", 1)[0], name=item.split(".", 1)[1]) for item in packages]
    run_id = service.create_run(parsed)
    service.execute(run_id)
    payload = service.store.read_json(run_id, "report.json")
    assert payload is not None, service.store.read_json(run_id, "status.json")
    return Report.model_validate(payload)


def package(catalog: Catalog, owner: str, name: str, spec: str, body: str, arguments: list[Argument]) -> None:
    catalog.add_source(owner, name, "PACKAGE", spec)
    catalog.add_source(owner, name, "PACKAGE BODY", body)
    catalog.add_arguments(owner, name, arguments)


def table(catalog: Catalog, owner: str, name: str, trigger: str | None = None, constraint: str = "PK_ROW") -> None:
    catalog.add_table(
        owner,
        name,
        columns=[Column(name="ID", data_type="NUMBER", nullable=False), Column(name="AMOUNT", data_type="NUMBER")],
        constraints=[Constraint(name=constraint, constraint_type="P", columns=["ID"])],
        triggers=[Trigger(name=trigger, trigger_type="AFTER EACH ROW", event="UPDATE")] if trigger else [],
    )
