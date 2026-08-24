"""Application settings.

Settings are read from environment variables and an optional local ``.env``
file (see ``.env.example`` for the expected variable names). No settings in
this module perform filesystem writes or network/API calls at import or
instantiation time, and no credential value is ever printed or logged.
"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OPENAI_MODEL = "gpt-5-mini"


class Settings(BaseSettings):
    """Runtime configuration.

    Only the provider credential fields are optional, so that ``Settings()``
    can be instantiated without any of them present.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_data_path: Path = REPO_ROOT / "data"

    @field_validator("project_data_path")
    @classmethod
    def _resolve_relative_to_repo_root(cls, value: Path) -> Path:
        """Anchor a relative configured path to ``REPO_ROOT``.

        An absolute path (explicitly supplied) is left untouched. This does
        not create or access the resulting directory.
        """
        if value.is_absolute():
            return value
        return REPO_ROOT / value

    alpaca_api_key: SecretStr | None = None
    alpaca_api_secret: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # Non-secret OpenAI request configuration. The API key is configured
    # exclusively via ``openai_api_key`` above (SecretStr) — never via these
    # fields, and never accepted per-request from a caller.
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    openai_max_output_tokens: int = Field(default=2048, ge=1, le=16000)

    @field_validator("openai_model")
    @classmethod
    def _validate_openai_model(cls, value: str) -> str:
        """Reject a blank/whitespace-only configured model name."""
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("openai_model must not be blank.")
        return trimmed

    @staticmethod
    def _is_configured(value: SecretStr | None) -> bool:
        """Return True only if ``value`` holds a non-blank secret."""
        return value is not None and value.get_secret_value().strip() != ""

    def provider_status(self) -> dict[str, bool]:
        """Report whether each provider has credentials configured.

        A credential that is missing, empty, or whitespace-only is treated
        as not configured. Returns only booleans — never the credential
        values themselves.
        """
        return {
            "alpaca": self._is_configured(self.alpaca_api_key)
            and self._is_configured(self.alpaca_api_secret),
            "fred": self._is_configured(self.fred_api_key),
            "openai": self._is_configured(self.openai_api_key),
            "anthropic": self._is_configured(self.anthropic_api_key),
        }
