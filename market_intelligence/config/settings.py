"""Application settings.

Settings are read from environment variables and an optional local ``.env``
file (see ``.env.example`` for the expected variable names). No settings in
this module perform filesystem writes or network/API calls at import or
instantiation time, and no credential value is ever printed or logged.
"""

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


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
