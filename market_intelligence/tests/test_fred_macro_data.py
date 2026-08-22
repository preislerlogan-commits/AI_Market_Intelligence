"""Tests for market_intelligence.data_connectors.fred_macro_data.

These tests must never make live HTTP requests or read real credentials.
All HTTP interaction is stubbed via httpx.MockTransport, and settings are
built from monkeypatched environment variables pointed at a nonexistent
.env file, mirroring market_intelligence/tests/test_alpaca_market_data.py.
"""

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FRED_BASE_URL,
    MAX_OBSERVATION_PAGES,
    OBSERVATIONS_OUTPUT_TYPE,
    OBSERVATIONS_PATH,
    OBSERVATIONS_REALTIME_END,
    OBSERVATIONS_REALTIME_START,
    OBSERVATIONS_UNITS,
    FredCredentialsMissingError,
    FredInvalidObservationRequestError,
    FredInvalidSeriesIdError,
    FredMacroDataClient,
    FredMacroDataError,
    FredObservation,
    normalize_observation_date,
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


# =====================================================================
# get_observations() -- historical observations
# =====================================================================


def observation_json(
    date_: str,
    value: str = "5.33",
    realtime_start: str = "2026-08-20",
    realtime_end: str = "2026-08-20",
) -> dict:
    return {
        "realtime_start": realtime_start,
        "realtime_end": realtime_end,
        "date": date_,
        "value": value,
    }


def observations_page_payload(
    observations: list, *, count: int | None = None, offset: int = 0, limit: int = 1000
) -> dict:
    return {
        "realtime_start": "2026-08-20",
        "realtime_end": "2026-08-20",
        "observation_start": "1600-01-01",
        "observation_end": "9999-12-31",
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "asc",
        "count": count if count is not None else len(observations),
        "offset": offset,
        "limit": limit,
        "observations": observations,
    }


# --- normalize_observation_date ---------------------------------------------

VALID_OBSERVATION_DATES = ["2026-08-01", "2026-01-01", "2026-12-31", "2024-02-29"]

INVALID_OBSERVATION_DATES = [
    "",
    "   ",
    "2026-8-1",
    "2026/08/01",
    "08-01-2026",
    "2026-08-01T00:00:00Z",
    "2026-08-01T00:00:00",
    "2026-13-01",
    "2026-00-01",
    "2026-08-32",
    "2026-08-00",
    "2026-02-30",
    "2023-02-29",
    "not-a-date",
    True,
    False,
    12345,
    None,
]


@pytest.mark.parametrize("raw", VALID_OBSERVATION_DATES)
def test_normalize_observation_date_accepts_valid_input(raw):
    assert normalize_observation_date(raw, field_name="observation_start") == raw


@pytest.mark.parametrize("raw", INVALID_OBSERVATION_DATES)
def test_normalize_observation_date_rejects_invalid_input(raw):
    with pytest.raises(FredInvalidObservationRequestError) as exc_info:
        normalize_observation_date(raw, field_name="observation_start")
    if isinstance(raw, str) and raw.strip():
        assert raw not in str(exc_info.value)


# --- get_observations: success -----------------------------------------------


def test_get_observations_returns_normalized_observations(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["series_id"] == "FEDFUNDS"
        assert request.url.params["observation_start"] == "2026-08-01"
        assert request.url.params["observation_end"] == "2026-08-02"
        assert request.url.params["sort_order"] == "asc"
        assert request.url.params["realtime_start"] == OBSERVATIONS_REALTIME_START
        assert request.url.params["realtime_end"] == OBSERVATIONS_REALTIME_END
        assert request.url.params["output_type"] == str(OBSERVATIONS_OUTPUT_TYPE)
        assert request.url.params["units"] == OBSERVATIONS_UNITS
        payload = observations_page_payload(
            [observation_json("2026-08-01", "5.33"), observation_json("2026-08-02", "5.34")]
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-02", client=http_client
        )

    assert len(observations) == 2
    assert isinstance(observations[0], FredObservation)
    assert observations[0].provider == "fred"
    assert observations[0].series_id == "FEDFUNDS"
    assert observations[0].observation_date == "2026-08-01"
    assert observations[0].value == Decimal("5.33")
    assert observations[0].is_missing is False
    assert observations[0].realtime_start == "2026-08-20"
    assert observations[0].realtime_end == "2026-08-20"
    assert observations[0].retrieved_at.endswith("Z")


def test_get_observations_empty_result_is_valid(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=observations_page_payload([]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-02", client=http_client
        )

    assert observations == []


def test_get_observations_missing_dot_value_marks_is_missing(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01", ".")])
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client
        )

    assert len(observations) == 1
    assert observations[0].value is None
    assert observations[0].is_missing is True


def test_get_observations_start_equal_end_is_allowed(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=observations_page_payload([observation_json("2026-08-01")])
        )

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client
        )

    assert len(observations) == 1


def test_get_observations_preserves_realtime_vintage(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [
                observation_json(
                    "2026-08-01",
                    "5.30",
                    realtime_start="2026-08-05",
                    realtime_end="2026-08-05",
                )
            ]
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client
        )

    assert observations[0].realtime_start == "2026-08-05"
    assert observations[0].realtime_end == "2026-08-05"
    assert observations[0].value == Decimal("5.30")


def test_get_observations_deterministic_chronological_order(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [
                observation_json("2026-08-03"),
                observation_json("2026-08-01"),
                observation_json("2026-08-02"),
            ]
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-03", client=http_client
        )

    assert [o.observation_date for o in observations] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]


def test_observations_fixed_request_constants_are_the_documented_values():
    """realtime_start=1776-07-04, realtime_end=9999-12-31, output_type=1, units=lin."""
    assert OBSERVATIONS_REALTIME_START == "1776-07-04"
    assert OBSERVATIONS_REALTIME_END == "9999-12-31"
    assert OBSERVATIONS_OUTPUT_TYPE == 1
    assert OBSERVATIONS_UNITS == "lin"


def test_get_observations_sends_fixed_realtime_params_on_every_page(
    monkeypatch, isolated_env_file
):
    """The fixed realtime/units provenance must be present on every paginated page."""
    seen_params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        offset = int(request.url.params["offset"])
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01"), observation_json("2026-08-02")],
                count=3,
                offset=0,
                limit=2,
            )
        else:
            payload = observations_page_payload(
                [observation_json("2026-08-03")], count=3, offset=2, limit=2
            )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-03", limit=2, client=http_client)

    assert len(seen_params) == 2
    for params in seen_params:
        assert params["realtime_start"] == OBSERVATIONS_REALTIME_START
        assert params["realtime_end"] == OBSERVATIONS_REALTIME_END
        assert params["output_type"] == str(OBSERVATIONS_OUTPUT_TYPE)
        assert params["units"] == OBSERVATIONS_UNITS


# --- get_observations: deduplication and conflicts ----------------------------


def test_get_observations_exact_duplicate_deduplicated(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01", "5.33"), observation_json("2026-08-01", "5.33")]
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client
        )

    assert len(observations) == 1


def test_get_observations_conflicting_duplicate_raises(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01", "5.33"), observation_json("2026-08-01", "9.99")]
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)

    assert "conflicting" in str(exc_info.value).lower()


# --- get_observations: malformed response handling -----------------------------


def test_get_observations_malformed_observation_fails_whole_fetch(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01"), {"date": "not-a-date", "value": "5.33"}]
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-02", client=http_client)


def test_get_observations_malformed_value_fails_whole_fetch(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01", "not-a-number")]
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity"])
def test_get_observations_non_finite_value_rejected(monkeypatch, isolated_env_file, bad_value):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01", bad_value)])
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_non_object_observation_entry_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(["not-a-dict"])
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_missing_observations_list_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([])
        del payload["observations"]
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_non_object_json_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["FEDFUNDS"])

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_fred_error_payload_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error_code": 400,
                "error_message": f"Bad Request. api_key {FAKE_FRED_KEY} is not a valid API key.",
            },
        )

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)

    assert FAKE_FRED_KEY not in str(exc_info.value)


def test_get_observations_http_status_error_sanitized(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)

    assert "500" in str(exc_info.value)
    assert FAKE_FRED_KEY not in str(exc_info.value)


def test_get_observations_network_error_sanitized(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)

    assert FAKE_FRED_KEY not in str(exc_info.value)


# --- get_observations: pagination ---------------------------------------------


def test_get_observations_follows_multiple_pages(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        assert limit == 2
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01"), observation_json("2026-08-02")],
                count=3,
                offset=0,
                limit=2,
            )
        elif offset == 2:
            payload = observations_page_payload(
                [observation_json("2026-08-03")], count=3, offset=2, limit=2
            )
        else:
            raise AssertionError(f"unexpected offset {offset}")
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-03", limit=2, client=http_client
        )

    assert [o.observation_date for o in observations] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]


def test_get_observations_pagination_exceeds_max_pages_raises(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        # Always returns a full page and a huge count, so the client keeps
        # paginating until the max_pages bound is hit.
        payload = observations_page_payload(
            [observation_json("2026-08-01")], count=10_000, offset=offset, limit=1
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", limit=1, max_pages=3, client=http_client
        )

    assert "maximum page count" in str(exc_info.value).lower()


def test_get_observations_repeated_offset_detected(monkeypatch, isolated_env_file):
    """A misbehaving provider that ignores the offset and repeats a page must be caught."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Always echoes offset 0 and a full page, regardless of what was
        # requested -- simulates a provider that never actually advances.
        payload = observations_page_payload(
            [observation_json("2026-08-01")], count=10_000, offset=0, limit=1
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", limit=1, max_pages=5, client=http_client
        )

    assert "offset" in str(exc_info.value).lower()


# --- get_observations: hardened pagination metadata -----------------------------


def test_get_observations_exact_final_page_succeeds(monkeypatch, isolated_env_file):
    """count exactly divides limit's final short page: offset+returned == count."""

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01"), observation_json("2026-08-02")],
                count=3,
                offset=0,
                limit=2,
            )
        else:
            payload = observations_page_payload(
                [observation_json("2026-08-03")], count=3, offset=2, limit=2
            )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-03", limit=2, client=http_client
        )

    assert len(observations) == 3


def test_get_observations_exact_multiple_of_limit_succeeds(monkeypatch, isolated_env_file):
    """count is an exact multiple of limit: the final page is a full page, not a short one."""

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01"), observation_json("2026-08-02")],
                count=4,
                offset=0,
                limit=2,
            )
        elif offset == 2:
            payload = observations_page_payload(
                [observation_json("2026-08-03"), observation_json("2026-08-04")],
                count=4,
                offset=2,
                limit=2,
            )
        else:
            raise AssertionError(f"unexpected offset {offset}")
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-04", limit=2, client=http_client
        )

    assert len(observations) == 4


def test_get_observations_invalid_offset_type_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        payload["offset"] = "0"  # string, not a plain int
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)

    assert "offset" in str(exc_info.value).lower()


def test_get_observations_boolean_offset_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        payload["offset"] = False
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_negative_offset_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        payload["offset"] = -1
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_missing_offset_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        del payload["offset"]
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_offset_mismatch_on_later_page_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01")], count=3, offset=0, limit=1
            )
        else:
            # Echoes the wrong offset on the second page.
            payload = observations_page_payload(
                [observation_json("2026-08-02")], count=3, offset=99, limit=1
            )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-03", limit=1, client=http_client
        )

    assert "offset" in str(exc_info.value).lower()


def test_get_observations_invalid_count_type_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        payload["count"] = "1"  # string, not a plain int
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)

    assert "count" in str(exc_info.value).lower()


def test_get_observations_boolean_count_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        payload["count"] = True
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_negative_count_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        payload["count"] = -1
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_missing_count_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([observation_json("2026-08-01")])
        del payload["count"]
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_inconsistent_count_across_pages_fails(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01")], count=3, offset=0, limit=1
            )
        else:
            # Reports a different count on the second page.
            payload = observations_page_payload(
                [observation_json("2026-08-02")], count=5, offset=1, limit=1
            )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-03", limit=1, client=http_client
        )

    assert "count" in str(exc_info.value).lower()


def test_get_observations_short_page_with_records_outstanding_fails(
    monkeypatch, isolated_env_file
):
    """count says 3 remain but the page returns fewer than limit and stops -- must fail."""

    def handler(request: httpx.Request) -> httpx.Response:
        # A single short page (1 observation) that claims count=3 -- an
        # incomplete/misbehaving response that must not be silently
        # accepted as a successful, complete result.
        payload = observations_page_payload(
            [observation_json("2026-08-01")], count=3, offset=0, limit=2
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", limit=2, client=http_client
        )

    assert "outstanding" in str(exc_info.value).lower()


def test_get_observations_empty_page_with_records_outstanding_fails(
    monkeypatch, isolated_env_file
):
    """An empty page must not be treated as successful completion when count says otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([], count=3, offset=0, limit=2)
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", limit=2, client=http_client
        )


def test_get_observations_metadata_failure_returns_no_partial_observations(
    monkeypatch, isolated_env_file
):
    """A second-page metadata failure must not leak first-page observations anywhere."""

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01")], count=3, offset=0, limit=1
            )
        else:
            payload = observations_page_payload(
                [observation_json("2026-08-02")], count=3, offset=99, limit=1
            )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError):
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-03", limit=1, client=http_client
        )


def test_get_observations_count_zero_with_observations_fails(monkeypatch, isolated_env_file):
    """count=0 must never coexist with a non-empty observations list."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01")], count=0, offset=0, limit=2
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", limit=2, client=http_client
        )

    message = str(exc_info.value)
    assert FAKE_FRED_KEY not in message
    assert "2026-08-01" not in message
    assert FRED_BASE_URL not in message


def test_get_observations_page_exceeds_requested_limit_fails(monkeypatch, isolated_env_file):
    """A page returning more observations than the requested limit must fail."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01"), observation_json("2026-08-02")],
            count=2,
            offset=0,
            limit=1,
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-02", limit=1, client=http_client
        )

    message = str(exc_info.value)
    assert FAKE_FRED_KEY not in message
    assert "2026-08-01" not in message
    assert FRED_BASE_URL not in message


def test_get_observations_first_page_exceeds_count_fails(monkeypatch, isolated_env_file):
    """The first page reporting more returned records than its own declared count must fail."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01"), observation_json("2026-08-02")],
            count=1,
            offset=0,
            limit=5,
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-02", limit=5, client=http_client
        )

    message = str(exc_info.value)
    assert FAKE_FRED_KEY not in message
    assert "2026-08-01" not in message
    assert FRED_BASE_URL not in message


def test_get_observations_later_page_overshoots_count_fails(monkeypatch, isolated_env_file):
    """A later page whose offset + returned exceeds the previously-reported count must fail,
    and must not merge that page's observations into the result."""

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            payload = observations_page_payload(
                [observation_json("2026-08-01"), observation_json("2026-08-02")],
                count=3,
                offset=0,
                limit=2,
            )
        else:
            # offset(2) + returned(2) = 4, exceeding the previously-reported count of 3.
            payload = observations_page_payload(
                [observation_json("2026-08-03"), observation_json("2026-08-04")],
                count=3,
                offset=2,
                limit=2,
            )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredMacroDataError) as exc_info:
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-04", limit=2, client=http_client
        )

    message = str(exc_info.value)
    assert FAKE_FRED_KEY not in message
    assert "2026-08" not in message
    assert FRED_BASE_URL not in message


def test_get_observations_valid_empty_count_zero_response_succeeds(
    monkeypatch, isolated_env_file
):
    """count=0 with an empty observations list is a valid, successful empty result."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload([], count=0, offset=0, limit=2)
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-01", limit=2, client=http_client
        )

    assert observations == []


def test_get_observations_single_page_exact_completion_succeeds(
    monkeypatch, isolated_env_file
):
    """offset + returned == count on a single short page is a valid, complete result."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = observations_page_payload(
            [observation_json("2026-08-01"), observation_json("2026-08-02")],
            count=2,
            offset=0,
            limit=5,
        )
        return httpx.Response(200, json=payload)

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        observations = client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-02", limit=5, client=http_client
        )

    assert len(observations) == 2


# --- get_observations: input validation makes zero requests ---------------------


def test_get_observations_raises_without_credentials(isolated_env_file):
    client = FredMacroDataClient(settings=unconfigured_settings(isolated_env_file))
    with pytest.raises(FredCredentialsMissingError):
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-02")


@pytest.mark.parametrize("raw", INVALID_SERIES_IDS)
def test_get_observations_invalid_series_id_makes_zero_requests(
    monkeypatch, isolated_env_file, raw
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_page_payload([]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredInvalidSeriesIdError):
        client.get_observations(raw, "2026-08-01", "2026-08-02", client=http_client)

    assert calls == []


@pytest.mark.parametrize("raw", INVALID_OBSERVATION_DATES)
def test_get_observations_invalid_start_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_page_payload([]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredInvalidObservationRequestError):
        client.get_observations("FEDFUNDS", raw, "2026-08-02", client=http_client)

    assert calls == []


@pytest.mark.parametrize("raw", INVALID_OBSERVATION_DATES)
def test_get_observations_invalid_end_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_page_payload([]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredInvalidObservationRequestError):
        client.get_observations("FEDFUNDS", "2026-08-01", raw, client=http_client)

    assert calls == []


def test_get_observations_start_after_end_makes_zero_requests(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_page_payload([]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredInvalidObservationRequestError):
        client.get_observations("FEDFUNDS", "2026-08-02", "2026-08-01", client=http_client)

    assert calls == []


@pytest.mark.parametrize("bad_limit", [0, -1, True, False, "10", 1.5, None, 100_000])
def test_get_observations_invalid_limit_makes_zero_requests(
    monkeypatch, isolated_env_file, bad_limit
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_page_payload([]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredInvalidObservationRequestError):
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-02", limit=bad_limit, client=http_client
        )

    assert calls == []


@pytest.mark.parametrize(
    "bad_max_pages", [0, -1, True, False, "3", 1.5, None, MAX_OBSERVATION_PAGES + 1]
)
def test_get_observations_invalid_max_pages_makes_zero_requests(
    monkeypatch, isolated_env_file, bad_max_pages
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=observations_page_payload([]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(FredInvalidObservationRequestError):
        client.get_observations(
            "FEDFUNDS", "2026-08-01", "2026-08-02", max_pages=bad_max_pages, client=http_client
        )

    assert calls == []


# --- get_observations: secret leak prevention ------------------------------------


def test_get_observations_api_key_sent_as_query_param_not_header(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert FAKE_FRED_KEY not in dict(request.headers).values()
        assert request.url.params["api_key"] == FAKE_FRED_KEY
        return httpx.Response(200, json=observations_page_payload([observation_json("2026-08-01")]))

    client = FredMacroDataClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.get_observations("FEDFUNDS", "2026-08-01", "2026-08-01", client=http_client)


def test_get_observations_module_has_no_storage_dependency():
    import market_intelligence.data_connectors.fred_macro_data as module

    assert not hasattr(module, "duckdb")
