"""Application settings.

Settings are read from environment variables and an optional local ``.env``
file (see ``.env.example`` for the expected variable names). No settings in
this module perform filesystem writes or network/API calls at import or
instantiation time, and no credential value is ever printed or logged.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Historical ORB data lives outside this repository and is read-only by
# project policy (see CLAUDE.md / AGENTS.md / PROJECT_STATE.md). Nothing in
# this project may write to, move, delete, or transform files under this
# path.
DEFAULT_EXTERNAL_HISTORICAL_DATA_PATH = Path(r"C:\ORB_Project\data")


class Settings(BaseSettings):
    """Runtime configuration.

    All fields are optional so that ``Settings()`` can be instantiated
    without any environment variables or credentials present.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    external_historical_data_path: Path = DEFAULT_EXTERNAL_HISTORICAL_DATA_PATH

    alpaca_api_key: SecretStr | None = None
    alpaca_api_secret: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    def provider_status(self) -> dict[str, bool]:
        """Report whether each provider has credentials configured.

        Returns only booleans — never the credential values themselves.
        """
        return {
            "alpaca": self.alpaca_api_key is not None and self.alpaca_api_secret is not None,
            "fred": self.fred_api_key is not None,
            "openai": self.openai_api_key is not None,
            "anthropic": self.anthropic_api_key is not None,
        }
