"""Tests for market_intelligence.config.settings.

These tests must never read real credentials from the developer's actual
environment or a real .env file, so every credential-related environment
variable is explicitly cleared via monkeypatch before each test, and the
settings file lookup is pointed at a path that does not exist.
"""

from pathlib import Path

import pytest

from market_intelligence.config.settings import REPO_ROOT, Settings

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]


@pytest.fixture
def isolated_settings_env(monkeypatch, tmp_path):
    """Clear credential env vars and point at a nonexistent .env file."""
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("PROJECT_DATA_PATH", raising=False)
    return tmp_path / "does-not-exist.env"


def make_settings(env_file: Path) -> Settings:
    return Settings(_env_file=env_file)


def test_settings_instantiate_without_credentials(isolated_settings_env):
    settings = make_settings(isolated_settings_env)

    assert settings.alpaca_api_key is None
    assert settings.alpaca_api_secret is None
    assert settings.fred_api_key is None
    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None


def test_default_project_data_path(isolated_settings_env):
    settings = make_settings(isolated_settings_env)

    assert settings.project_data_path == REPO_ROOT / "data"


def test_credential_repr_does_not_reveal_value(monkeypatch, isolated_settings_env):
    secret_value = "unit-test-secret-value-should-not-appear"
    monkeypatch.setenv("ALPACA_API_KEY", secret_value)

    settings = make_settings(isolated_settings_env)

    rendered = repr(settings)
    assert secret_value not in rendered
    assert secret_value not in str(settings.alpaca_api_key)
    assert secret_value not in repr(settings.alpaca_api_key)


def test_provider_status_reports_only_booleans(monkeypatch, isolated_settings_env):
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "unit-test-alpaca-secret")

    settings = make_settings(isolated_settings_env)
    status = settings.provider_status()

    assert status == {
        "alpaca": True,
        "fred": False,
        "openai": False,
        "anthropic": False,
    }
    for value in status.values():
        assert isinstance(value, bool)


def test_provider_status_all_false_without_credentials(isolated_settings_env):
    settings = make_settings(isolated_settings_env)

    status = settings.provider_status()

    assert status == {
        "alpaca": False,
        "fred": False,
        "openai": False,
        "anthropic": False,
    }


def test_provider_status_treats_blank_credential_as_not_configured(
    monkeypatch, isolated_settings_env
):
    monkeypatch.setenv("FRED_API_KEY", "")
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "")

    settings = make_settings(isolated_settings_env)
    status = settings.provider_status()

    assert status == {
        "alpaca": False,
        "fred": False,
        "openai": False,
        "anthropic": False,
    }


def test_provider_status_treats_whitespace_only_credential_as_not_configured(
    monkeypatch, isolated_settings_env
):
    monkeypatch.setenv("FRED_API_KEY", "   ")
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "\t\n")

    settings = make_settings(isolated_settings_env)
    status = settings.provider_status()

    assert status == {
        "alpaca": False,
        "fred": False,
        "openai": False,
        "anthropic": False,
    }


def test_env_example_has_placeholders_for_expected_credentials():
    env_example_path = Path(__file__).resolve().parents[2] / ".env.example"
    content = env_example_path.read_text(encoding="utf-8")

    for var in CREDENTIAL_ENV_VARS:
        assert var in content, f"{var} missing from .env.example"

    for line in content.splitlines():
        if not line.strip() or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in CREDENTIAL_ENV_VARS:
            assert value.strip() == "", f"{key} should be a blank placeholder in .env.example"
