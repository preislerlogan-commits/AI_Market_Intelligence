"""Tests for market_intelligence.data_connectors.fred_macro_data.

These tests must never make live HTTP requests or read real credentials.
All HTTP interaction is stubbed via httpx.MockTransport, and settings are
built from monkeypatched environment variables pointed at a nonexistent
.env file, mirroring market_intelligence/tests/test_alpaca_market_data.py.
"""

from pathlib import Path

import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FRED_BASE_URL,
    OBSERVATIONS_PATH,
    FredCredentialsMissingError,
    FredInvalidSeriesIdError,
    FredMacroDataClient,
    FredMacroDataError,
    normalize_series_id,
)

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]

FAKE_FRED_KEY = "unit-test-fred-key"


@pytest.fixture(autouse=True)
def clear_credential_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def configured_settings(monkeypatch, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("FRED_API_KEY", FAKE_FRED_KEY)
    return Settings(_env_file=isolated_env_file)


def unconfigured_settings(isolated_env_file: Path) -> Settings:
    return Settings(_env_file=isolated_env_file)


def mock_client(handler) -> httpx.Client:
    return httpx.Client(base_url=FRED_BASE_URL, transport=httpx.MockTransport(handler))


def observations_payload(date: str = "2026-08-01", value: str = "5.33") -> dict:
    return {
        "realtime_start": "2026-08-20",
        "realtime_end": "2026-08-20",
        "observation_start": "1600-01-01",
        "observation_end": "9999-12-31",
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "desc",
        "count": 1,
        "offset": 0,
        "limit": 1,
        "observations": [
            {
                "realtime_start": "2026-08-20",
                "realtime_end": "2026-08-20",
                "date": date,
                "value": value,
            }
        ],
    }


# --- is_configured / credentials ----------------------------------------


def test_is_configured_false_without_credentials(isolated_env_file):
    client = FredMacroDataClient(settings=unconfigured_settings(isolated_env_file))
    assert client.is_configured() is False


def test_is_configured_true_with_credentials(monkeypatch, isolated_env_file):
    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    assert client.is_configured() is True


def test_get_latest_observation_raises_without_credentials(isolated_env_file):
    client = FredMacroDataClient(settings=unconfigured_settings(isolated_env_file))
    with pytest.raises(FredCredentialsMissingError):
        client.get_latest_observation("FEDFUNDS")


# --- get_latest_observation: success and failure modes -------------------


def test_get_latest_observation_returns_payload_on_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == OBSERVATIONS_PATH
        assert request.url.params["series_id"] == "FEDFUNDS"
        assert request.url.params["api_key"] == FAKE_FRED_KEY
        return httpx.Response(200, json=observations_payload())

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        payload = client.get_latest_observation("FEDFUNDS", client=http_client)

    assert payload["observations"][0]["date"] == "2026-08-01"


def test_get_latest_observation_sanitized_error_on_http_status_error(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    message = str(exc_info.value)
    assert FAKE_FRED_KEY not in message
    assert "500" in message


def test_get_latest_observation_sanitized_error_on_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    message = str(exc_info.value)
    assert FAKE_FRED_KEY not in message


def test_get_latest_observation_sanitized_error_on_malformed_json(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid json")

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    message = str(exc_info.value)
    assert "{not valid json" not in message
    assert FAKE_FRED_KEY not in message


def test_get_latest_observation_sanitized_error_on_non_object_json(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["FEDFUNDS", "CPIAUCSL"])

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    message = str(exc_info.value)
    assert "FEDFUNDS" not in message
    assert FAKE_FRED_KEY not in message


def test_get_latest_observation_sanitized_error_on_fred_error_payload(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error_code": 400,
                "error_message": (
                    f"Bad Request. api_key {FAKE_FRED_KEY} is not a valid API key."
                ),
            },
        )

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    message = str(exc_info.value)
    assert FAKE_FRED_KEY not in message
    assert "error" in message.lower()


def test_get_latest_observation_sanitized_error_on_missing_observations(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_payload()
        payload["observations"] = []
        payload["count"] = 0
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    assert FAKE_FRED_KEY not in str(exc_info.value)


def test_get_latest_observation_sanitized_error_on_malformed_observation_entry(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_payload()
        payload["observations"] = ["not-a-dict"]
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    assert FAKE_FRED_KEY not in str(exc_info.value)


def test_get_latest_observation_sanitized_error_on_missing_date_field(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_payload()
        del payload["observations"][0]["date"]
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_latest_observation("FEDFUNDS", client=http_client)

    assert FAKE_FRED_KEY not in str(exc_info.value)


# --- check_connection: success and failure modes --------------------------


def test_check_connection_not_configured(isolated_env_file):
    client = FredMacroDataClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("FEDFUNDS")

    assert status.configured is False
    assert status.success is False
    assert status.status_category == "not_configured"
    assert status.series_id == "FEDFUNDS"
    assert status.latest_observation_date is None


def test_check_connection_success_extracts_date(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=observations_payload(date="2026-08-01"))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is True
    assert status.status_category == "2xx"
    assert status.series_id == "FEDFUNDS"
    assert status.latest_observation_date == "2026-08-01"


def test_check_connection_http_error_reports_status_category(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "5xx"
    assert status.latest_observation_date is None


def test_check_connection_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "network_error"
    assert status.latest_observation_date is None


def test_check_connection_invalid_response_on_malformed_json(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid json")

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "invalid_response"
    assert status.latest_observation_date is None


def test_check_connection_invalid_response_on_non_object_json(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["FEDFUNDS", "CPIAUCSL"])

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "invalid_response"
    assert status.latest_observation_date is None


def test_check_connection_fred_error_payload(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error_code": 400,
                "error_message": (
                    f"Bad Request. api_key {FAKE_FRED_KEY} is not a valid API key."
                ),
            },
        )

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "fred_error"
    assert status.latest_observation_date is None


def test_check_connection_missing_observation_on_empty_list(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_payload()
        payload["observations"] = []
        payload["count"] = 0
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "missing_observation"
    assert status.latest_observation_date is None


def test_check_connection_missing_observation_on_malformed_entry(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_payload()
        payload["observations"] = ["not-a-dict"]
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "missing_observation"
    assert status.latest_observation_date is None


def test_check_connection_missing_observation_on_missing_date_field(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_payload()
        del payload["observations"][0]["date"]
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "missing_observation"
    assert status.latest_observation_date is None


def test_check_connection_status_never_exposes_observation_value(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=observations_payload(date="2026-08-01", value="5.33"))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert not hasattr(status, "value")
    assert not hasattr(status, "latest_observation_value")
    assert "5.33" not in repr(status)


# --- Series ID normalization/validation -----------------------------------

VALID_SERIES_IDS = [
    ("fedfunds", "FEDFUNDS"),
    ("  fedfunds  ", "FEDFUNDS"),
    ("CPIAUCSL", "CPIAUCSL"),
    ("DGS10", "DGS10"),
    ("t10y2y", "T10Y2Y"),
    ("GDPC1", "GDPC1"),
]

INVALID_SERIES_IDS = [
    "",
    "   ",
    "../../etc/passwd",
    "FEDFUNDS/FEDFUNDS",
    "FEDFUNDS?api_key=x",
    "http://evil.com",
    "https://evil.com/FEDFUNDS",
    "FED FUNDS",
    "FED\tFUNDS",
    "FED\nFUNDS",
    "FED\x00FUNDS",
    "FED\x1bFUNDS",
    "_FEDFUNDS",
    "-FEDFUNDS",
    "A" * 65,  # over MAX_SERIES_ID_LENGTH
]


@pytest.mark.parametrize(("raw", "expected"), VALID_SERIES_IDS)
def test_normalize_series_id_accepts_valid_input(raw, expected):
    assert normalize_series_id(raw) == expected


@pytest.mark.parametrize("raw", INVALID_SERIES_IDS)
def test_normalize_series_id_rejects_invalid_input(raw):
    with pytest.raises(FredInvalidSeriesIdError) as exc_info:
        normalize_series_id(raw)
    message = str(exc_info.value)
    if raw.strip():
        assert raw not in message


def test_get_latest_observation_normalizes_lowercase_series_id(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["series_id"] == "FEDFUNDS"
        return httpx.Response(200, json=observations_payload())

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        payload = client.get_latest_observation("  fedfunds  ", client=http_client)

    assert payload["observations"][0]["date"] == "2026-08-01"


@pytest.mark.parametrize("raw", INVALID_SERIES_IDS)
def test_get_latest_observation_invalid_series_id_makes_zero_requests(
    monkeypatch, isolated_env_file, raw
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_payload())

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredInvalidSeriesIdError) as exc_info:
        client.get_latest_observation(raw, client=http_client)

    assert calls == []
    if raw.strip():
        assert raw not in str(exc_info.value)


@pytest.mark.parametrize("raw", INVALID_SERIES_IDS)
def test_check_connection_invalid_series_id_makes_zero_requests(
    monkeypatch, isolated_env_file, raw
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_payload())

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection(raw, client=http_client)

    assert calls == []
    assert status.success is False
    assert status.status_category == "invalid_series_id"
    assert status.series_id == ""
    assert status.latest_observation_date is None


# --- API key never leaks into any sanitized output -------------------------


def test_api_key_never_in_check_connection_status_repr(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=observations_payload())

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("FEDFUNDS", client=http_client)

    assert FAKE_FRED_KEY not in repr(status)


def test_api_key_sent_as_query_param_not_header(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert FAKE_FRED_KEY not in dict(request.headers).values()
        assert request.url.params["api_key"] == FAKE_FRED_KEY
        return httpx.Response(200, json=observations_payload())

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.get_latest_observation("FEDFUNDS", client=http_client)
