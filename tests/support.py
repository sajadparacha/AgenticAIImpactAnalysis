"""Shared builders for engine and regression tests."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from oracle_impact.config import Settings
from oracle_impact.infrastructure.llm.investigator import Investigator
from oracle_impact.infrastructure.llm.provider import ModelResponse, ToolCall
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
    mode = overrides.pop("mode", "deterministic")
    if mode == "agentic":
        overrides.setdefault("llm_egress_enabled", True)
        overrides.setdefault("llm_model", "mock")
    configured = settings(tmp_path, **overrides)
    service = AnalysisService(
        configured,
        RunStore(tmp_path),
        FixtureGateway("test", "fixture-test", "TEST", test_catalog),
        FixtureGateway("production", "fixture-production", "PRODUCTION", prod_catalog),
        investigator=investigator,
    )
    parsed = [PackageRef(owner=item.split(".", 1)[0], name=item.split(".", 1)[1]) for item in packages]
    run_id = service.create_run(parsed, mode=mode)
    service.execute(run_id)
    payload = service.store.read_json(run_id, "report.json")
    assert payload is not None, service.store.read_json(run_id, "status.json")
    return Report.model_validate(payload)


class ChecklistProvider:
    """Scripted model for the agentic loop. It reads the checklist the loop sends back and requests
    those reads, `batch` at a time, then sends `final`. `early_finishes` answers are sent first.
    Checklist items for which `skip(item)` is true are never requested."""

    def __init__(self, final: str = "I read every required item.", batch: int = 6, early_finishes: int = 0, skip=None) -> None:
        self.final = final
        self.batch = batch
        self.early_finishes = early_finishes
        self.skip = skip or (lambda item: False)
        self.calls = 0
        self.tools_seen: list[str] = []

    def complete(self, messages, tools) -> ModelResponse:
        self.calls += 1
        self.tools_seen = [tool["function"]["name"] for tool in tools]
        if self.early_finishes:
            self.early_finishes -= 1
            return ModelResponse(content="Done.", prompt_tokens=1, completion_tokens=1)
        wanted = [item for item in _latest_checklist(messages)["next"] if not self.skip(item)]
        if wanted:
            calls = [ToolCall(item["tool"], item["arguments"], f"c{self.calls}-{index}") for index, item in enumerate(wanted[: self.batch])]
            return ModelResponse(content="Reading the next required items.", tool_calls=calls, prompt_tokens=1, completion_tokens=1)
        return ModelResponse(content=self.final, prompt_tokens=1, completion_tokens=1)


def _latest_checklist(messages: list[dict]) -> dict:
    for message in reversed(messages):
        content = message.get("content") or ""
        if message["role"] == "tool":
            payload = json.loads(content)
            if "checklist" in payload:
                return payload["checklist"]
        elif message["role"] == "user" and "{" in content:
            return json.loads(content[content.index("{"):])
    raise AssertionError("no checklist was sent to the model")


def agentic_investigator(provider, max_calls_per_package: int = 500, max_calls_per_run: int = 500) -> Investigator:
    return Investigator(
        provider,
        max_calls_per_package=max_calls_per_package,
        max_calls_per_run=max_calls_per_run,
        timeout_seconds=30,
        token_budget=1_000_000,
        max_depth=2,
    )


def run_demo(tmp_path: Path, packages: list[str], mode: str = "deterministic", investigator=None) -> Report:
    """Run the bundled demo catalogs through AnalysisService in either mode."""
    from oracle_impact.fixtures.demo_catalog import build_demo_catalogs

    test_catalog, prod_catalog = build_demo_catalogs()
    service = AnalysisService(
        settings(tmp_path, llm_egress_enabled=investigator is not None, llm_model="mock" if investigator else ""),
        RunStore(tmp_path),
        FixtureGateway("test", "fixture-test", "TEST", test_catalog),
        FixtureGateway("production", "fixture-production", "PRODUCTION", prod_catalog),
        investigator=investigator,
    )
    parsed = [PackageRef(owner=item.split(".", 1)[0], name=item.split(".", 1)[1]) for item in packages]
    run_id = service.create_run(parsed, mode=mode)
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
