"""Tests for market_intelligence.data_connectors.alpaca_market_data.

These tests must never make live HTTP requests or read real credentials.
All HTTP interaction is stubbed via httpx.MockTransport, and settings are
built from monkeypatched environment variables pointed at a nonexistent
.env file, mirroring market_intelligence/tests/test_settings.py.
"""

from pathlib import Path

import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_market_data import (
    MARKET_DATA_BASE_URL,
    AlpacaCredentialsMissingError,
    AlpacaMarketDataClient,
    AlpacaMarketDataError,
)

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]

FAKE_ALPACA_KEY = "unit-test-alpaca-key"
FAKE_ALPACA_SECRET = "unit-test-alpaca-secret"


@pytest.fixture(autouse=True)
def clear_credential_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def configured_settings(monkeypatch, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("ALPACA_API_KEY", FAKE_ALPACA_KEY)
    monkeypatch.setenv("ALPACA_API_SECRET", FAKE_ALPACA_SECRET)
    return Settings(_env_file=isolated_env_file)


def unconfigured_settings(isolated_env_file: Path) -> Settings:
    return Settings(_env_file=isolated_env_file)


def mock_client(handler) -> httpx.Client:
    return httpx.Client(base_url=MARKET_DATA_BASE_URL, transport=httpx.MockTransport(handler))


def test_is_configured_false_without_credentials(isolated_env_file):
    client = AlpacaMarketDataClient(settings=unconfigured_settings(isolated_env_file))
    assert client.is_configured() is False


def test_is_configured_true_with_credentials(monkeypatch, isolated_env_file):
    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    assert client.is_configured() is True


def test_get_snapshot_raises_without_credentials(isolated_env_file):
    client = AlpacaMarketDataClient(settings=unconfigured_settings(isolated_env_file))
    with pytest.raises(AlpacaCredentialsMissingError):
        client.get_snapshot("SPY")


def test_get_snapshot_returns_json_on_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/stocks/SPY/snapshot"
        assert request.headers["APCA-API-KEY-ID"] == FAKE_ALPACA_KEY
        assert request.headers["APCA-API-SECRET-KEY"] == FAKE_ALPACA_SECRET
        return httpx.Response(
            200,
            json={"symbol": "SPY", "latestTrade": {"t": "2026-08-20T12:00:00Z", "p": 500.0}},
        )

    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        payload = client.get_snapshot("SPY", client=http_client)

    assert payload["symbol"] == "SPY"


def test_get_snapshot_sanitized_error_on_http_status_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaMarketDataError) as exc_info:
        client.get_snapshot("SPY", client=http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message
    assert "403" in message


def test_get_snapshot_sanitized_error_on_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaMarketDataError) as exc_info:
        client.get_snapshot("SPY", client=http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message


def test_check_connection_not_configured(isolated_env_file):
    client = AlpacaMarketDataClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("SPY")

    assert status.configured is False
    assert status.success is False
    assert status.status_category == "not_configured"
    assert status.symbol == "SPY"
    assert status.timestamp is None


def test_check_connection_success_extracts_timestamp(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"symbol": "SPY", "latestTrade": {"t": "2026-08-20T12:00:00Z", "p": 500.0}},
        )

    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.configured is True
    assert status.success is True
    assert status.status_category == "2xx"
    assert status.symbol == "SPY"
    assert status.timestamp == "2026-08-20T12:00:00Z"


def test_check_connection_success_without_timestamp_field(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbol": "SPY"})

    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is True
    assert status.timestamp is None


def test_check_connection_http_error_reports_status_category(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "4xx"
    assert status.timestamp is None


def test_check_connection_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = AlpacaMarketDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "network_error"
    assert status.timestamp is None
