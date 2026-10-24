"""Environment-backed settings. No secrets belong in code."""

from pathlib import Path

from pydantic import Field
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
    def llm_configured(self) -> bool:
        return bool(self.llm_egress_enabled and self.llm_base_url and self.llm_model)
