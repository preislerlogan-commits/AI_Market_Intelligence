"""Tests for market_intelligence.data_connectors.alpaca_bars.

These tests must never make live HTTP requests or read real credentials,
and must never write to any database. All HTTP interaction is stubbed via
httpx.MockTransport, and settings are built from monkeypatched environment
variables pointed at a nonexistent .env file, mirroring
market_intelligence/tests/test_alpaca_news.py.
"""

import math
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import (
    BARS_BASE_URL,
    DATA_ADJUSTMENT,
    DATA_CURRENCY,
    DATA_FEED,
    MAX_LIMIT,
    MAX_PAGES,
    AlpacaBarsClient,
    AlpacaBarsCredentialsMissingError,
    AlpacaBarsError,
    AlpacaBarsInvalidInputError,
    normalize_limit,
    normalize_max_pages,
    normalize_timeframe,
    normalize_timestamp,
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
    return httpx.Client(base_url=BARS_BASE_URL, transport=httpx.MockTransport(handler))


def sample_bar(
    t: str = "2026-08-19T09:30:00Z",
    o: float = 100.0,
    h: float = 101.0,
    lo: float = 99.5,
    c: float = 100.5,
    v: int = 1000,
    n: int | None = 50,
    vw: float | None = 100.2,
) -> dict:
    return {"t": t, "o": o, "h": h, "l": lo, "c": c, "v": v, "n": n, "vw": vw}


def bars_payload(bars: list, next_page_token: str | None = None) -> dict:
    return {"bars": bars, "symbol": "SPY", "next_page_token": next_page_token}


DEFAULT_START = "2026-08-01T00:00:00Z"
DEFAULT_END = "2026-08-20T00:00:00Z"


def get_bars_default(client, http_client, **kwargs):
    return client.get_bars(
        kwargs.pop("symbol", "SPY"),
        kwargs.pop("timeframe", "1Day"),
        kwargs.pop("start", DEFAULT_START),
        kwargs.pop("end", DEFAULT_END),
        client=http_client,
        **kwargs,
    )


# --- credentials -------------------------------------------------------------


def test_is_configured_false_without_credentials(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    assert client.is_configured() is False


def test_is_configured_true_with_credentials(monkeypatch, isolated_env_file):
    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    assert client.is_configured() is True


def test_get_bars_raises_without_credentials(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    with pytest.raises(AlpacaBarsCredentialsMissingError):
        get_bars_default(client, None)


def test_get_bars_without_credentials_makes_zero_requests(isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsCredentialsMissingError):
        get_bars_default(client, http_client)

    assert calls == []


# --- symbol normalization -----------------------------------------------------


def test_get_bars_normalizes_lowercase_symbol(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/stocks/SPY/bars"
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client, symbol="  spy ")


INVALID_SYMBOLS = [
    "",
    "   ",
    "../../etc/passwd",
    "AAPL/AAPL",
    "http://evil.com",
    "AA PL",
    "AA\nPL",
    "AA\x00PL",
    "SPY,QQQ",
    [],
    123,
    None,
]


@pytest.mark.parametrize("raw", INVALID_SYMBOLS)
def test_get_bars_invalid_symbol_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, symbol=raw)

    assert calls == []


# --- timeframe normalization ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1Min", "1Min"),
        ("1min", "1Min"),
        ("1MIN", "1Min"),
        (" 1 Min ", "1Min"),
        ("5Min", "5Min"),
        ("5min", "5Min"),
        ("5 MIN", "5Min"),
        ("1Day", "1Day"),
        ("1day", "1Day"),
        ("1 DAY", "1Day"),
    ],
)
def test_normalize_timeframe_accepts_case_and_whitespace_variants(raw, expected):
    assert normalize_timeframe(raw) == expected


INVALID_TIMEFRAMES = [
    "1Hour",
    "1Week",
    "2Min",
    "10Min",
    "daily",
    "",
    "   ",
    None,
    True,
    False,
    123,
    [],
]


@pytest.mark.parametrize("raw", INVALID_TIMEFRAMES)
def test_normalize_timeframe_rejects_invalid_input(raw):
    with pytest.raises(AlpacaBarsInvalidInputError):
        normalize_timeframe(raw)


@pytest.mark.parametrize("raw", ["1Hour", "2Min", None, True, 123])
def test_get_bars_unsupported_timeframe_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, timeframe=raw)

    assert calls == []


def test_get_bars_sends_normalized_timeframe(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["timeframe"] == "5Min"
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client, timeframe="5 min")


# --- start/end validation ------------------------------------------------------

INVALID_TIMESTAMPS = [
    "",
    "not-a-date",
    "2026-13-40T00:00:00Z",
    "2026-08-20T00:00:00Z; DROP TABLE bars;",
    "A" * 41,
    123,
    None,
    True,
    False,
    "2026-08-20",  # date-only
    "2026-08-20T12:00:00",  # naive
    "2026-08-20T12:00:00+0000",  # malformed offset
    "2026-08-20T12:00:00+25:00",  # out-of-range offset hour
    "2026-08-20T12:00:00-04",  # incomplete offset
    "2026-02-30T00:00:00Z",  # invalid calendar date
    "2026-08-20 12:00:00Z",  # space instead of "T"
]


@pytest.mark.parametrize("raw", INVALID_TIMESTAMPS)
def test_normalize_timestamp_rejects_invalid_input(raw):
    with pytest.raises(AlpacaBarsInvalidInputError):
        normalize_timestamp(raw, field_name="start")


def test_normalize_timestamp_accepts_valid_rfc3339():
    assert normalize_timestamp("2026-08-20T12:00:00Z", field_name="start") == "2026-08-20T12:00:00Z"


def test_normalize_timestamp_normalizes_offset_to_utc():
    assert (
        normalize_timestamp("2026-08-20T08:00:00-04:00", field_name="start")
        == "2026-08-20T12:00:00Z"
    )


@pytest.mark.parametrize("raw", INVALID_TIMESTAMPS)
def test_get_bars_invalid_start_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, start=raw)

    assert calls == []


@pytest.mark.parametrize("raw", INVALID_TIMESTAMPS)
def test_get_bars_invalid_end_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, end=raw)

    assert calls == []


def test_get_bars_rejects_start_equal_end_with_zero_requests(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, start=DEFAULT_START, end=DEFAULT_START)

    assert calls == []


def test_get_bars_rejects_start_after_end_with_zero_requests(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, start=DEFAULT_END, end=DEFAULT_START)

    assert calls == []


# --- limit validation ----------------------------------------------------------

INVALID_LIMITS = [0, -1, 1001, 100000, "10", True, False, None, 3.5]


@pytest.mark.parametrize("raw", INVALID_LIMITS)
def test_normalize_limit_rejects_invalid_input(raw):
    with pytest.raises(AlpacaBarsInvalidInputError):
        normalize_limit(raw)


def test_normalize_limit_accepts_boundaries():
    assert normalize_limit(1) == 1
    assert normalize_limit(MAX_LIMIT) == MAX_LIMIT


@pytest.mark.parametrize("raw", INVALID_LIMITS)
def test_get_bars_invalid_limit_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, limit=raw)

    assert calls == []


# --- max_pages validation -------------------------------------------------------

INVALID_MAX_PAGES = [0, -1, 1.5, True, False, "2", None, MAX_PAGES + 1, MAX_PAGES + 100]


@pytest.mark.parametrize("raw", INVALID_MAX_PAGES)
def test_normalize_max_pages_rejects_invalid_input(raw):
    with pytest.raises(AlpacaBarsInvalidInputError):
        normalize_max_pages(raw)


def test_normalize_max_pages_accepts_boundaries():
    assert normalize_max_pages(1) == 1
    assert normalize_max_pages(MAX_PAGES) == MAX_PAGES


def test_normalize_max_pages_error_never_echoes_input():
    with pytest.raises(AlpacaBarsInvalidInputError) as exc_info:
        normalize_max_pages(MAX_PAGES + 12345)

    assert str(MAX_PAGES + 12345) not in str(exc_info.value)


@pytest.mark.parametrize("raw", INVALID_MAX_PAGES)
def test_get_bars_invalid_max_pages_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsInvalidInputError):
        get_bars_default(client, http_client, max_pages=raw)

    assert calls == []


def test_get_bars_accepts_max_pages_equal_to_max_pages_cap(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar()], next_page_token=None))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client, max_pages=MAX_PAGES)

    assert len(bars) == 1


# --- successful single-page normalization --------------------------------------


def test_get_bars_returns_normalized_bar_on_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/stocks/SPY/bars"
        assert request.headers["APCA-API-KEY-ID"] == FAKE_ALPACA_KEY
        assert request.headers["APCA-API-SECRET-KEY"] == FAKE_ALPACA_SECRET
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert len(bars) == 1
    bar = bars[0]
    assert bar.provider == "alpaca"
    assert bar.symbol == "SPY"
    assert bar.timeframe == "1Day"
    assert bar.feed == DATA_FEED
    assert bar.timestamp == "2026-08-19T09:30:00Z"
    assert bar.open == Decimal("100.0")
    assert bar.high == Decimal("101.0")
    assert bar.low == Decimal("99.5")
    assert bar.close == Decimal("100.5")
    assert bar.volume == 1000
    assert bar.trade_count == 50
    assert bar.vwap == Decimal("100.2")
    assert bar.retrieved_at is not None
    assert bar.retrieved_at != bar.timestamp


def test_get_bars_uses_decimal_for_price_fields(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=bars_payload([sample_bar(o=100.1, h=100.9, lo=99.9, c=100.5)])
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    bar = bars[0]
    assert isinstance(bar.open, Decimal)
    assert isinstance(bar.high, Decimal)
    assert isinstance(bar.low, Decimal)
    assert isinstance(bar.close, Decimal)
    assert isinstance(bar.vwap, Decimal)
    # Decimal(str(0.1)) must equal exactly Decimal("0.1"), not the binary
    # float artifact that Decimal(0.1) would produce.
    assert bar.open == Decimal("100.1")


def test_get_bars_sends_query_params(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == DEFAULT_START
        assert request.url.params["end"] == DEFAULT_END
        assert request.url.params["limit"] == "1000"
        assert "page_token" not in request.url.params
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)


# --- UTC normalization -----------------------------------------------------


def test_get_bars_normalizes_start_end_offsets_to_utc(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == "2026-08-01T04:00:00Z"
        assert request.url.params["end"] == "2026-08-20T04:00:00Z"
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(
            client,
            http_client,
            start="2026-08-01T00:00:00-04:00",
            end="2026-08-20T00:00:00-04:00",
        )


def test_get_bars_bar_timestamp_normalized_to_utc(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=bars_payload([sample_bar(t="2026-08-19T05:30:00-04:00")])
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert bars[0].timestamp == "2026-08-19T09:30:00Z"


# --- numeric field validation ---------------------------------------------


MALFORMED_NUMERIC_BARS = [
    sample_bar(o=True),
    sample_bar(h="101.0"),
    sample_bar(lo=float("nan")),
    sample_bar(c=float("inf")),
    sample_bar(v=float("-inf")),
]


@pytest.mark.parametrize("bad_bar", MALFORMED_NUMERIC_BARS)
def test_get_bars_rejects_malformed_numeric_fields(monkeypatch, isolated_env_file, bad_bar):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([bad_bar]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_rejects_nan_open(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar(o=math.nan)]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_rejects_boolean_volume(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar(v=True)]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_rejects_negative_volume(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar(v=-1)]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_rejects_negative_trade_count(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar(n=-5)]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_rejects_missing_timestamp(monkeypatch, isolated_env_file):
    bad_bar = sample_bar()
    del bad_bar["t"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([bad_bar]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


# --- candle consistency -----------------------------------------------------


CANDLE_INCONSISTENT_BARS = [
    sample_bar(h=99.0, lo=100.0),  # high < low
    sample_bar(h=100.0, o=101.0, lo=99.0, c=99.5),  # open > high
    sample_bar(h=100.0, o=99.5, lo=99.0, c=101.0),  # close > high
    sample_bar(h=100.0, o=98.0, lo=99.0, c=99.5),  # open < low
    sample_bar(h=100.0, o=99.5, lo=99.0, c=98.0),  # close < low
]


@pytest.mark.parametrize("bad_bar", CANDLE_INCONSISTENT_BARS)
def test_get_bars_rejects_candle_inconsistent_bars(monkeypatch, isolated_env_file, bad_bar):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([bad_bar]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


# --- nullable trade_count/vwap ------------------------------------------------


def test_get_bars_accepts_null_trade_count_and_vwap(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar(n=None, vw=None)]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert bars[0].trade_count is None
    assert bars[0].vwap is None


def test_get_bars_accepts_missing_trade_count_and_vwap_keys(monkeypatch, isolated_env_file):
    raw_bar = {
        "t": "2026-08-19T09:30:00Z",
        "o": 100.0,
        "h": 101.0,
        "l": 99.5,
        "c": 100.5,
        "v": 1000,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([raw_bar]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert bars[0].trade_count is None
    assert bars[0].vwap is None


def test_get_bars_rejects_malformed_vwap(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar(vw="not-a-number")]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_rejects_malformed_trade_count(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar(n="fifty")]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


# --- empty bars success --------------------------------------------------------


def test_get_bars_empty_list_succeeds(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert bars == []


# --- malformed response shapes --------------------------------------------------


def test_get_bars_sanitized_error_on_non_object_json(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_sanitized_error_on_missing_bars_key(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"next_page_token": None})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_sanitized_error_on_non_list_bars_field(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bars": "not-a-list"})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_sanitized_error_on_malformed_json(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid json")

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError) as exc_info:
        get_bars_default(client, http_client)

    assert "{not valid json" not in str(exc_info.value)


# --- non-empty malformed / mixed bars -------------------------------------------


def test_get_bars_raises_when_all_bars_malformed(monkeypatch, isolated_env_file):
    bad_bars = [{"t": "2026-08-19T09:30:00Z"}, "not-a-dict"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bad_bars))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_raises_on_mixed_valid_and_malformed_bars(monkeypatch, isolated_env_file):
    bars = [sample_bar(t="2026-08-19T09:30:00Z"), {"t": "2026-08-19T09:31:00Z"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


# --- deterministic duplicate removal --------------------------------------------


def test_get_bars_deduplicates_exact_duplicate_bars(monkeypatch, isolated_env_file):
    bar = sample_bar(t="2026-08-19T09:30:00Z")
    bars = [bar, dict(bar), sample_bar(t="2026-08-19T09:31:00Z")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = get_bars_default(client, http_client)

    assert len(result) == 2
    assert [b.timestamp for b in result] == ["2026-08-19T09:30:00Z", "2026-08-19T09:31:00Z"]


def test_get_bars_raises_on_conflicting_duplicate_timestamp(monkeypatch, isolated_env_file):
    bars = [
        sample_bar(t="2026-08-19T09:30:00Z", c=100.5),
        sample_bar(t="2026-08-19T09:30:00Z", c=200.0),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


# --- pagination ------------------------------------------------------------


def test_get_bars_follows_pagination_across_pages(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page_token = request.url.params.get("page_token")
        if page_token is None:
            return httpx.Response(
                200,
                json=bars_payload(
                    [sample_bar(t="2026-08-19T09:30:00Z")], next_page_token="TOK1"
                ),
            )
        elif page_token == "TOK1":
            return httpx.Response(
                200, json=bars_payload([sample_bar(t="2026-08-19T09:31:00Z")], next_page_token=None)
            )
        raise AssertionError("unexpected page token")

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert len(calls) == 2
    assert [b.timestamp for b in bars] == ["2026-08-19T09:30:00Z", "2026-08-19T09:31:00Z"]


def test_get_bars_never_sends_page_token_in_first_request(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "page_token" not in request.url.params
        return httpx.Response(200, json=bars_payload([sample_bar()], next_page_token=None))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)


def test_get_bars_repeated_page_token_raises(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=bars_payload([sample_bar(t="2026-08-19T09:30:00Z")], next_page_token="TOK1")
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)

    # First request gets TOK1; second request (using TOK1) gets TOK1 again,
    # which must be detected as a repeat without a third request being made.
    assert len(calls) == 2


def test_get_bars_invalid_page_token_type_raises(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar()], next_page_token=12345))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)


def test_get_bars_bounded_pagination_limit(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        token = f"TOK{len(calls)}"
        bar_timestamp = f"2026-08-19T09:{30 + len(calls):02d}:00Z"
        return httpx.Response(
            200, json=bars_payload([sample_bar(t=bar_timestamp)], next_page_token=token)
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client, max_pages=3)

    assert len(calls) == 3


def test_get_bars_later_page_failure_returns_no_partial_result(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page_token = request.url.params.get("page_token")
        if page_token is None:
            return httpx.Response(
                200,
                json=bars_payload(
                    [sample_bar(t="2026-08-19T09:30:00Z")], next_page_token="TOK1"
                ),
            )
        return httpx.Response(500, json={"message": "server error"})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError):
        get_bars_default(client, http_client)

    assert len(calls) == 2


# --- chronological ordering ------------------------------------------------


def test_get_bars_returns_chronological_order(monkeypatch, isolated_env_file):
    bars_out_of_order = [
        sample_bar(t="2026-08-19T09:32:00Z"),
        sample_bar(t="2026-08-19T09:30:00Z"),
        sample_bar(t="2026-08-19T09:31:00Z"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars_out_of_order))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = get_bars_default(client, http_client)

    assert [b.timestamp for b in result] == [
        "2026-08-19T09:30:00Z",
        "2026-08-19T09:31:00Z",
        "2026-08-19T09:32:00Z",
    ]


# --- HTTP/network errors -----------------------------------------------------


def test_get_bars_sanitized_error_on_http_status_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError) as exc_info:
        get_bars_default(client, http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message
    assert "403" in message


def test_get_bars_sanitized_error_on_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError) as exc_info:
        get_bars_default(client, http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message


# --- secrets never appear in exceptions or URLs/params ------------------------


def test_secrets_sent_as_headers_not_query_params(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert FAKE_ALPACA_KEY not in str(request.url)
        assert FAKE_ALPACA_SECRET not in str(request.url)
        assert request.headers["APCA-API-KEY-ID"] == FAKE_ALPACA_KEY
        assert request.headers["APCA-API-SECRET-KEY"] == FAKE_ALPACA_SECRET
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)


def test_secrets_never_in_bar_repr(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert FAKE_ALPACA_KEY not in repr(bars[0])
    assert FAKE_ALPACA_SECRET not in repr(bars[0])


def test_secrets_never_in_get_bars_exception_message(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaBarsError) as exc_info:
        get_bars_default(client, http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message


def test_normalize_timestamp_error_never_echoes_untrusted_input():
    secret_marker = "SUPER-SECRET-INPUT-VALUE"
    with pytest.raises(AlpacaBarsInvalidInputError) as exc_info:
        normalize_timestamp(f"not-a-date-{secret_marker}", field_name="start")

    assert secret_marker not in str(exc_info.value)


# --- check_connection ---------------------------------------------------------


def test_check_connection_not_configured(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("SPY")

    assert status.configured is False
    assert status.success is False
    assert status.status_category == "not_configured"
    assert status.symbol == "SPY"
    assert status.timeframe == "5Min"
    assert status.feed == DATA_FEED
    assert status.bar_count == 0
    assert status.oldest_bar_timestamp is None
    assert status.newest_bar_timestamp is None


def test_check_connection_invalid_symbol(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("")

    assert status.success is False
    assert status.status_category == "invalid_symbol"
    assert status.symbol == ""


def test_check_connection_invalid_timeframe(monkeypatch, isolated_env_file):
    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    status = client.check_connection("SPY", timeframe="1Hour")

    assert status.success is False
    assert status.status_category == "invalid_input"


def test_check_connection_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/stocks/SPY/bars"
        return httpx.Response(
            200,
            json=bars_payload(
                [
                    sample_bar(t="2026-08-18T09:30:00Z"),
                    sample_bar(t="2026-08-19T09:30:00Z"),
                ]
            ),
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.configured is True
    assert status.success is True
    assert status.status_category == "2xx"
    assert status.symbol == "SPY"
    assert status.timeframe == "5Min"
    assert status.feed == DATA_FEED
    assert status.bar_count == 2
    assert status.oldest_bar_timestamp == "2026-08-18T09:30:00Z"
    assert status.newest_bar_timestamp == "2026-08-19T09:30:00Z"


def test_check_connection_deduplicates_exact_duplicate_bars(monkeypatch, isolated_env_file):
    bar = sample_bar(t="2026-08-19T09:30:00Z")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=bars_payload(
                [bar, dict(bar), sample_bar(t="2026-08-19T09:31:00Z")]
            ),
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is True
    assert status.status_category == "2xx"
    assert status.bar_count == 2
    assert status.oldest_bar_timestamp == "2026-08-19T09:30:00Z"
    assert status.newest_bar_timestamp == "2026-08-19T09:31:00Z"


def test_check_connection_conflicting_duplicate_timestamp_fails(monkeypatch, isolated_env_file):
    bars = [
        sample_bar(t="2026-08-19T09:30:00Z", c=100.5),
        sample_bar(t="2026-08-19T09:30:00Z", c=200.0),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.status_category == "invalid_response"
    assert status.bar_count == 0
    assert status.oldest_bar_timestamp is None
    assert status.newest_bar_timestamp is None


def test_check_connection_deduplicated_chronological_order(monkeypatch, isolated_env_file):
    bars_out_of_order = [
        sample_bar(t="2026-08-19T09:32:00Z"),
        sample_bar(t="2026-08-19T09:30:00Z"),
        dict(sample_bar(t="2026-08-19T09:30:00Z")),
        sample_bar(t="2026-08-19T09:31:00Z"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars_out_of_order))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is True
    assert status.bar_count == 3
    assert status.oldest_bar_timestamp == "2026-08-19T09:30:00Z"
    assert status.newest_bar_timestamp == "2026-08-19T09:32:00Z"


def test_check_connection_conflicting_duplicate_status_repr_never_leaks_data(
    monkeypatch, isolated_env_file
):
    bars = [
        sample_bar(t="2026-08-19T09:30:00Z", c=100.5, o=999.25),
        sample_bar(t="2026-08-19T09:30:00Z", c=200.0, o=999.25),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    status_repr = repr(status)
    assert FAKE_ALPACA_KEY not in status_repr
    assert FAKE_ALPACA_SECRET not in status_repr
    assert "999.25" not in status_repr
    assert "100.5" not in status_repr
    assert "200.0" not in status_repr


def test_check_connection_empty_bars_is_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is True
    assert status.bar_count == 0
    assert status.oldest_bar_timestamp is None
    assert status.newest_bar_timestamp is None


def test_check_connection_http_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "4xx"


def test_check_connection_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.status_category == "network_error"


def test_check_connection_invalid_response_non_object(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.status_category == "invalid_response"


def test_check_connection_invalid_response_malformed_bar(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([{"t": "2026-08-19T09:30:00Z"}]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.status_category == "invalid_response"


def test_check_connection_never_makes_more_than_one_request(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=bars_payload([sample_bar()], next_page_token="TOK1")
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.check_connection("SPY", client=http_client)

    assert len(calls) == 1


def test_check_connection_status_repr_never_leaks_secrets(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    status_repr = repr(status)
    assert FAKE_ALPACA_KEY not in status_repr
    assert FAKE_ALPACA_SECRET not in status_repr


# --- IEX feed hardening -------------------------------------------------------


def test_data_feed_constant_is_iex():
    assert DATA_FEED == "iex"


def test_get_bars_sends_feed_iex_param(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["feed"] == "iex"
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)


def test_get_bars_sends_feed_iex_on_every_paginated_request(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["feed"] == "iex"
        page_token = request.url.params.get("page_token")
        if page_token is None:
            return httpx.Response(
                200,
                json=bars_payload(
                    [sample_bar(t="2026-08-19T09:30:00Z")], next_page_token="TOK1"
                ),
            )
        elif page_token == "TOK1":
            return httpx.Response(
                200, json=bars_payload([sample_bar(t="2026-08-19T09:31:00Z")], next_page_token=None)
            )
        raise AssertionError("unexpected page token")

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)

    assert len(calls) == 2
    assert all(call.url.params["feed"] == "iex" for call in calls)


def test_check_connection_sends_feed_iex_param(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["feed"] == "iex"
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.check_connection("SPY", client=http_client)


def test_get_bars_normalized_bars_always_have_feed_iex(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=bars_payload(
                [sample_bar(t="2026-08-19T09:30:00Z"), sample_bar(t="2026-08-19T09:31:00Z")]
            ),
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert len(bars) == 2
    assert all(bar.feed == "iex" for bar in bars)


def test_check_connection_status_always_has_feed_iex_including_on_failure(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.feed == "iex"


def test_check_connection_not_configured_status_has_feed_iex(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("SPY")

    assert status.feed == "iex"


def test_check_connection_invalid_symbol_status_has_feed_iex(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("")

    assert status.feed == "iex"


def test_get_bars_does_not_accept_a_feed_keyword_argument(monkeypatch, isolated_env_file):
    """No caller-controlled feed can be injected: get_bars has no ``feed`` parameter."""
    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with pytest.raises(TypeError):
        client.get_bars(
            "SPY",
            "1Day",
            DEFAULT_START,
            DEFAULT_END,
            feed="sip",  # type: ignore[call-arg]
        )


def test_check_connection_does_not_accept_a_feed_keyword_argument(monkeypatch, isolated_env_file):
    """No caller-controlled feed can be injected: check_connection has no ``feed`` parameter."""
    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with pytest.raises(TypeError):
        client.check_connection("SPY", feed="sip")  # type: ignore[call-arg]


def test_get_bars_feed_param_cannot_be_overridden_via_response_payload(
    monkeypatch, isolated_env_file
):
    """A provider response cannot smuggle a different feed onto the normalized Bar."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = bars_payload([sample_bar()])
        payload["feed"] = "sip"
        return httpx.Response(200, json=payload)

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert bars[0].feed == "iex"


def test_dedup_identity_accounts_for_feed_with_all_same_feed_still_deduplicates(
    monkeypatch, isolated_env_file
):
    """All bars from this connector share DATA_FEED, so identical (feed, timestamp)
    duplicates must still be deduplicated exactly as before feed was added."""
    bar = sample_bar(t="2026-08-19T09:30:00Z")
    bars = [bar, dict(bar)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bars_payload(bars))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = get_bars_default(client, http_client)

    assert len(result) == 1
    assert result[0].feed == "iex"


def test_get_bars_credentials_absent_from_url_params_and_feed_still_present(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["feed"] == "iex"
        assert FAKE_ALPACA_KEY not in str(request.url)
        assert FAKE_ALPACA_SECRET not in str(request.url)
        for value in request.url.params.values():
            assert value not in (FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET)
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)


# --- adjustment/currency provenance hardening ----------------------------------


def test_data_adjustment_constant_is_raw():
    assert DATA_ADJUSTMENT == "raw"


def test_data_currency_constant_is_usd():
    assert DATA_CURRENCY == "USD"


def test_get_bars_sends_adjustment_and_currency_params(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["adjustment"] == "raw"
        assert request.url.params["currency"] == "USD"
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)


def test_get_bars_sends_adjustment_and_currency_on_every_paginated_request(
    monkeypatch, isolated_env_file
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["adjustment"] == "raw"
        assert request.url.params["currency"] == "USD"
        page_token = request.url.params.get("page_token")
        if page_token is None:
            return httpx.Response(
                200,
                json=bars_payload(
                    [sample_bar(t="2026-08-19T09:30:00Z")], next_page_token="TOK1"
                ),
            )
        elif page_token == "TOK1":
            return httpx.Response(
                200, json=bars_payload([sample_bar(t="2026-08-19T09:31:00Z")], next_page_token=None)
            )
        raise AssertionError("unexpected page token")

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        get_bars_default(client, http_client)

    assert len(calls) == 2
    assert all(call.url.params["adjustment"] == "raw" for call in calls)
    assert all(call.url.params["currency"] == "USD" for call in calls)


def test_check_connection_sends_adjustment_and_currency_params(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["adjustment"] == "raw"
        assert request.url.params["currency"] == "USD"
        return httpx.Response(200, json=bars_payload([sample_bar()]))

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.check_connection("SPY", client=http_client)


def test_get_bars_normalized_bars_always_have_raw_usd_provenance(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=bars_payload(
                [sample_bar(t="2026-08-19T09:30:00Z"), sample_bar(t="2026-08-19T09:31:00Z")]
            ),
        )

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert len(bars) == 2
    assert all(bar.adjustment == "raw" for bar in bars)
    assert all(bar.currency == "USD" for bar in bars)


def test_check_connection_status_always_has_raw_usd_provenance_including_on_failure(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.adjustment == "raw"
    assert status.currency == "USD"


def test_check_connection_not_configured_status_has_raw_usd_provenance(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("SPY")

    assert status.adjustment == "raw"
    assert status.currency == "USD"


def test_check_connection_invalid_symbol_status_has_raw_usd_provenance(isolated_env_file):
    client = AlpacaBarsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("")

    assert status.adjustment == "raw"
    assert status.currency == "USD"


def test_get_bars_does_not_accept_adjustment_or_currency_keyword_arguments(
    monkeypatch, isolated_env_file
):
    """No caller-controlled adjustment/currency can be injected: get_bars has no such params."""
    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with pytest.raises(TypeError):
        client.get_bars(
            "SPY",
            "1Day",
            DEFAULT_START,
            DEFAULT_END,
            adjustment="split",  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        client.get_bars(
            "SPY",
            "1Day",
            DEFAULT_START,
            DEFAULT_END,
            currency="EUR",  # type: ignore[call-arg]
        )


def test_check_connection_does_not_accept_adjustment_or_currency_keyword_arguments(
    monkeypatch, isolated_env_file
):
    """No caller-controlled adjustment/currency can be injected via check_connection."""
    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with pytest.raises(TypeError):
        client.check_connection("SPY", adjustment="split")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.check_connection("SPY", currency="EUR")  # type: ignore[call-arg]


def test_get_bars_adjustment_and_currency_cannot_be_overridden_via_response_payload(
    monkeypatch, isolated_env_file
):
    """A provider response cannot smuggle a different adjustment/currency onto the Bar."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = bars_payload([sample_bar()])
        payload["adjustment"] = "split"
        payload["currency"] = "EUR"
        return httpx.Response(200, json=payload)

    client = AlpacaBarsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        bars = get_bars_default(client, http_client)

    assert bars[0].adjustment == "raw"
    assert bars[0].currency == "USD"


# --- scripts/check_alpaca_bars.py sanitized output -----------------------------


def _load_check_alpaca_bars_module():
    import importlib.util
    from pathlib import Path

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_alpaca_bars.py"
    spec = importlib.util.spec_from_file_location("check_alpaca_bars", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_check_alpaca_bars_script_prints_sanitized_feed(monkeypatch, isolated_env_file, capsys):
    from market_intelligence.data_connectors.alpaca_bars import BarsConnectionStatus

    def blocked_send(self, request, **kwargs):
        raise AssertionError("Unexpected live HTTP request from check_alpaca_bars script test")

    monkeypatch.setattr(httpx.Client, "send", blocked_send)

    module = _load_check_alpaca_bars_module()
    monkeypatch.setattr(
        module, "Settings", lambda: configured_settings(monkeypatch, isolated_env_file)
    )

    fake_status = BarsConnectionStatus(
        configured=True,
        success=True,
        status_category="2xx",
        symbol="SPY",
        timeframe="5Min",
        feed=DATA_FEED,
        adjustment=DATA_ADJUSTMENT,
        currency=DATA_CURRENCY,
        bar_count=2,
        oldest_bar_timestamp="2026-08-18T09:30:00Z",
        newest_bar_timestamp="2026-08-19T09:30:00Z",
    )
    monkeypatch.setattr(
        AlpacaBarsClient, "check_connection", lambda self, *args, **kwargs: fake_status
    )

    exit_code = module.main()

    captured = capsys.readouterr()
    assert "feed: iex" in captured.out
    assert exit_code == 0
    assert FAKE_ALPACA_KEY not in captured.out
    assert FAKE_ALPACA_SECRET not in captured.out
