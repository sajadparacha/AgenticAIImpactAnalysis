"""Environment-backed settings. No secrets belong in code."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix="OIA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    secret_key: str = "dev-only-secret-change-me"
    app_access_token: str = "dev-only-change-me"
    host: str = "127.0.0.1"
    port: int = 8000
    runs_dir: Path = Path("runs")
    cookie_secure: bool = False
    fixture_mode: bool = True

    test_oracle_label: str = "TEST"
    test_oracle_dsn: str = ""
    test_oracle_user: str = ""
    test_oracle_password: str = ""
    test_oracle_edition: str = ""

    prod_oracle_label: str = "PRODUCTION"
    prod_oracle_dsn: str = ""
    prod_oracle_user: str = ""
    prod_oracle_password: str = ""
    prod_oracle_edition: str = ""

    autumn_oracle_label: str = "AUTUMN"
    autumn_oracle_dsn: str = ""
    autumn_oracle_user: str = ""
    autumn_oracle_password: str = ""
    autumn_oracle_edition: str = ""
    fixture_autumn: bool = True

    approver_access_token: str = ""
    state_dir: Path | None = None
    policy_file: Path | None = None
    baseline_max_age_days: int = 30
    snapshot_stale_hours: int = 24
    performance_plan_source: str = "none"

    llm_egress_enabled: bool = False
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    max_tool_calls_per_package: int = 15
    max_tool_calls_per_run: int = 40
    max_dependency_depth: int = 2
    analysis_timeout_seconds: int = 120
    llm_token_budget: int = 8000

    using_default_token: bool = Field(default=False, exclude=True)

    @field_validator("state_dir", "policy_file", mode="before")
    @classmethod
    def _blank_path_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    def model_post_init(self, __context: object) -> None:
        object.__setattr__(
            self,
            "using_default_token",
            self.app_access_token == "dev-only-change-me",
        )

    @property
    def test_oracle_configured(self) -> bool:
        return bool(self.test_oracle_dsn and self.test_oracle_user)

    @property
    def production_oracle_configured(self) -> bool:
        return bool(self.prod_oracle_dsn and self.prod_oracle_user)

    @property
    def autumn_oracle_configured(self) -> bool:
        return bool(self.autumn_oracle_dsn and self.autumn_oracle_user)

    @property
    def approvals_enabled(self) -> bool:
        return bool(self.approver_access_token) and self.approver_access_token != self.app_access_token

    @property
    def registry_dir(self) -> Path:
        return self.state_dir if self.state_dir is not None else self.runs_dir / "_registry"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_egress_enabled and self.llm_base_url and self.llm_model)
