"""Tests for market_intelligence.data_connectors.alpaca_options_chain.

These tests must never make live HTTP requests or read real credentials,
and must never write to any database. All HTTP interaction is stubbed via
httpx.MockTransport, and settings are built from monkeypatched environment
variables pointed at a nonexistent .env file, mirroring
market_intelligence/tests/test_alpaca_bars.py.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_options_chain import (
    MAX_EXPIRATION_RANGE_DAYS,
    MAX_LIMIT,
    MAX_PAGES,
    MAX_STRIKE_RANGE_WIDTH,
    MAX_TOTAL_CONTRACTS,
    OPTIONS_BASE_URL,
    AlpacaOptionsChainClient,
    AlpacaOptionsChainError,
    AlpacaOptionsChainInvalidInputError,
    AlpacaOptionsChainTruncatedError,
    AlpacaOptionsCredentialsMissingError,
    OptionChainRequest,
    OptionChainSnapshotBatch,
    OptionChainTruncationReason,
    normalize_option_chain_request,
    parse_occ_symbol,
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
    return httpx.Client(base_url=OPTIONS_BASE_URL, transport=httpx.MockTransport(handler))


def make_request(**overrides) -> OptionChainRequest:
    kwargs = dict(
        underlying="SPY",
        feed="opra",
        expiration_date_gte="2026-09-01",
        expiration_date_lte="2026-09-30",
        strike_price_gte=Decimal("400"),
        strike_price_lte=Decimal("600"),
        option_type=None,
        limit=1000,
        max_pages=3,
        max_total_contracts=1000,
    )
    kwargs.update(overrides)
    return normalize_option_chain_request(**kwargs)


def snapshot_json(
    *,
    quote: dict | None = None,
    trade: dict | None = None,
    greeks: dict | None = None,
    iv: float | None = 0.1543,
) -> dict:
    body: dict = {}
    if quote is not None:
        body["latestQuote"] = quote
    if trade is not None:
        body["latestTrade"] = trade
    if greeks is not None:
        body["greeks"] = greeks
    if iv is not None:
        body["impliedVolatility"] = iv
    return body


FULL_QUOTE = {"t": "2026-09-02T15:30:00Z", "bp": 5.25, "bs": 10, "ap": 5.35, "as": 12}
FULL_TRADE = {"t": "2026-09-02T15:29:55Z", "p": 5.30, "s": 3}
FULL_GREEKS = {"delta": -0.42, "gamma": 0.03, "theta": -0.06, "vega": 0.11, "rho": -0.02}

SPY_CALL = "SPY260918C00500000"
SPY_PUT = "SPY260918P00500000"


def chain_payload(snapshots: dict, next_page_token: str | None = None) -> dict:
    return {"snapshots": snapshots, "next_page_token": next_page_token}


# --- OCC symbol parsing -------------------------------------------------------


def test_parse_occ_symbol_call():
    parsed = parse_occ_symbol("SPY260918C00500000")
    assert parsed.underlying == "SPY"
    assert parsed.expiration_date == "2026-09-18"
    assert parsed.option_type == "call"
    assert parsed.strike_price == Decimal("500")


def test_parse_occ_symbol_put_with_fractional_strike():
    parsed = parse_occ_symbol("SPY260918P00512500")
    assert parsed.option_type == "put"
    assert parsed.strike_price == Decimal("512.5")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "SPY",
        "SPY260918X00500000",  # bad call/put char
        "SPY26091C00500000",  # short date
        "SPY260918C0050000",  # short strike
        "SPY2609 8C00500000",  # embedded space
        "SPY261332C00500000",  # impossible calendar date
        "TOOLONGROOT260918C00500000",  # root > 6 chars
        123,
        None,
    ],
)
def test_parse_occ_symbol_rejects_malformed(raw):
    from market_intelligence.data_connectors.alpaca_options_chain import _MalformedSnapshotError

    with pytest.raises(_MalformedSnapshotError):
        parse_occ_symbol(raw)


# --- request normalization ---------------------------------------------------


@pytest.mark.parametrize("bad_underlying", ["QQQ", "AAPL", "", "  ", "SPX", 123, None, True])
def test_request_rejects_non_spy_underlying(bad_underlying):
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(underlying=bad_underlying)


@pytest.mark.parametrize("bad_feed", ["sip", "iex", "", "opra indicative", "delayed", None, True])
def test_request_rejects_unknown_feed(bad_feed):
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(feed=bad_feed)


def test_request_accepts_opra_and_indicative():
    assert make_request(feed="opra").feed == "opra"
    assert make_request(feed="  Indicative ").feed == "indicative"


@pytest.mark.parametrize("bad_type", ["calls", "long", "", 1, True])
def test_request_rejects_unknown_type(bad_type):
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(option_type=bad_type)


def test_request_accepts_call_put_and_none():
    assert make_request(option_type="call").option_type == "call"
    assert make_request(option_type="PUT").option_type == "put"
    assert make_request(option_type=None).option_type is None


@pytest.mark.parametrize(
    "gte,lte",
    [
        ("2026-09-30", "2026-09-01"),  # reversed
        ("2026-09-01", "2028-09-01"),  # too wide
        ("2026-13-01", "2026-09-30"),  # malformed
        ("2026-02-30", "2026-09-30"),  # impossible date
        ("2026-09-01", "not-a-date"),
    ],
)
def test_request_rejects_bad_expiration_range(gte, lte):
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(expiration_date_gte=gte, expiration_date_lte=lte)


@pytest.mark.parametrize(
    "gte,lte",
    [
        (Decimal("600"), Decimal("400")),  # reversed
        (Decimal("0"), Decimal("400")),  # non-positive lower bound
        (Decimal("-5"), Decimal("400")),  # negative
        (Decimal("100"), Decimal("9000")),  # too wide
        (float("nan"), Decimal("400")),  # nonfinite
    ],
)
def test_request_rejects_bad_strike_range(gte, lte):
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(strike_price_gte=gte, strike_price_lte=lte)


@pytest.mark.parametrize("bad_limit", [0, -1, 1001, 100000, "10", True, None, 3.5])
def test_request_rejects_bad_limit(bad_limit):
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(limit=bad_limit)


@pytest.mark.parametrize("bad_pages", [0, -1, MAX_PAGES + 1, True, "2", 1.5])
def test_request_rejects_bad_max_pages(bad_pages):
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(max_pages=bad_pages)


# --- Phase 1 hard ceilings: at-ceiling passes, above-ceiling fails --------
# before any HTTP request is made (rejection happens inside
# normalize_option_chain_request, which never touches the network).


def test_ceiling_expiration_span_at_60_days_passes_above_fails():
    make_request(expiration_date_gte="2026-09-01", expiration_date_lte="2026-10-31")  # 60 days

    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(expiration_date_gte="2026-09-01", expiration_date_lte="2026-11-01")  # 61


def test_ceiling_strike_width_at_500_passes_above_fails():
    make_request(strike_price_gte=Decimal("400"), strike_price_lte=Decimal("900"))  # width 500

    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(
            strike_price_gte=Decimal("400"), strike_price_lte=Decimal("900.01")
        )  # width 500.01


def test_ceiling_max_pages_at_10_passes_above_fails():
    assert make_request(max_pages=MAX_PAGES).max_pages == MAX_PAGES == 10

    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(max_pages=MAX_PAGES + 1)


def test_ceiling_max_total_contracts_at_5000_passes_above_fails():
    assert (
        make_request(max_total_contracts=MAX_TOTAL_CONTRACTS).max_total_contracts
        == MAX_TOTAL_CONTRACTS
        == 5_000
    )

    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(max_total_contracts=MAX_TOTAL_CONTRACTS + 1)


def test_ceiling_per_page_limit_at_1000_passes_above_fails():
    assert make_request(limit=MAX_LIMIT).limit == MAX_LIMIT == 1000

    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        make_request(limit=MAX_LIMIT + 1)


def test_strike_range_width_ceiling_is_500():
    assert MAX_STRIKE_RANGE_WIDTH == Decimal("500")


def test_expiration_range_ceiling_is_60_days():
    assert MAX_EXPIRATION_RANGE_DAYS == 60


def test_ceiling_violations_rejected_before_any_http_request(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=chain_payload({}))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        for bad_request_kwargs in (
            {"max_pages": MAX_PAGES + 1},
            {"max_total_contracts": MAX_TOTAL_CONTRACTS + 1},
            {"strike_price_gte": Decimal("400"), "strike_price_lte": Decimal("900.01")},
            {"expiration_date_gte": "2026-09-01", "expiration_date_lte": "2026-11-01"},
        ):
            with pytest.raises(AlpacaOptionsChainInvalidInputError):
                bad_request = make_request(**bad_request_kwargs)
                client.get_chain_snapshot(bad_request, client=http_client)
    assert calls == []


def test_request_error_never_echoes_raw_value():
    secret = "SUPER-SECRET-2099-99-99"
    with pytest.raises(AlpacaOptionsChainInvalidInputError) as exc_info:
        make_request(expiration_date_gte=secret)
    assert secret not in str(exc_info.value)


# --- credentials -------------------------------------------------------------


def test_get_chain_snapshot_raises_without_credentials(isolated_env_file):
    client = AlpacaOptionsChainClient(settings=unconfigured_settings(isolated_env_file))
    with pytest.raises(AlpacaOptionsCredentialsMissingError):
        client.get_chain_snapshot(make_request())


def test_get_chain_snapshot_without_credentials_makes_zero_requests(isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=chain_payload({}))

    client = AlpacaOptionsChainClient(settings=unconfigured_settings(isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsCredentialsMissingError):
        client.get_chain_snapshot(make_request(), client=http_client)
    assert calls == []


def test_get_chain_snapshot_rejects_unnormalized_request(monkeypatch, isolated_env_file):
    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with pytest.raises(AlpacaOptionsChainInvalidInputError):
        client.get_chain_snapshot({"feed": "opra"})  # type: ignore[arg-type]


# --- SPY-only enforcement at the URL ----------------------------------------


def test_get_chain_snapshot_requests_spy_path_and_explicit_feed(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1beta1/options/snapshots/SPY"
        assert request.url.params["feed"] == "opra"
        assert request.headers["APCA-API-KEY-ID"] == FAKE_ALPACA_KEY
        return httpx.Response(200, json=chain_payload({SPY_CALL: snapshot_json(quote=FULL_QUOTE)}))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.get_chain_snapshot(make_request(), client=http_client)


# --- OPRA / indicative feed preservation -----------------------------------


@pytest.mark.parametrize("feed", ["opra", "indicative"])
def test_feed_recorded_verbatim_on_every_snapshot(monkeypatch, isolated_env_file, feed):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["feed"] == feed
        return httpx.Response(
            200,
            json=chain_payload(
                {
                    SPY_CALL: snapshot_json(quote=FULL_QUOTE),
                    SPY_PUT: snapshot_json(quote=FULL_QUOTE),
                }
            ),
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(feed=feed), client=http_client)

    assert isinstance(result, OptionChainSnapshotBatch)
    assert result.retrieved_at.endswith("Z")
    assert {s.feed for s in result.snapshots} == {feed}


# --- call/put and strike/expiration filters -------------------------------


def test_type_filter_forwarded_as_query_param(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "put"
        return httpx.Response(200, json=chain_payload({SPY_PUT: snapshot_json(quote=FULL_QUOTE)}))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(
            make_request(option_type="put"), client=http_client
        )
    assert result.snapshots[0].option_type == "put"


def test_contract_outside_requested_strike_range_is_rejected(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        # strike 800 is outside the 400-600 request window
        return httpx.Response(
            200, json=chain_payload({"SPY260918C00800000": snapshot_json(quote=FULL_QUOTE)})
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)


def test_contract_type_mismatch_with_filter_is_rejected(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chain_payload({SPY_CALL: snapshot_json(quote=FULL_QUOTE)}))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(option_type="put"), client=http_client)


def test_contract_outside_expiration_range_is_rejected(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=chain_payload({"SPY271218C00500000": snapshot_json(quote=FULL_QUOTE)})
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)


def test_non_spy_contract_symbol_is_rejected(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=chain_payload({"QQQ260918C00500000": snapshot_json(quote=FULL_QUOTE)})
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)


# --- full normalization ----------------------------------------------------


def test_full_snapshot_normalization(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=chain_payload(
                {SPY_PUT: snapshot_json(quote=FULL_QUOTE, trade=FULL_TRADE, greeks=FULL_GREEKS)}
            ),
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)

    assert result.retrieved_at is not None
    snap = result.snapshots[0]
    assert snap.provider == "alpaca"
    assert snap.underlying == "SPY"
    assert snap.contract_symbol == SPY_PUT
    assert snap.expiration_date == "2026-09-18"
    assert snap.option_type == "put"
    assert snap.strike_price == Decimal("500")
    assert snap.quote_timestamp == "2026-09-02T15:30:00Z"
    assert snap.bid_price == Decimal("5.25")
    assert snap.bid_size == 10
    assert snap.ask_price == Decimal("5.35")
    assert snap.ask_size == 12
    assert snap.trade_timestamp == "2026-09-02T15:29:55Z"
    assert snap.trade_price == Decimal("5.30")
    assert snap.trade_size == 3
    assert snap.implied_volatility == Decimal("0.1543")
    assert snap.delta == Decimal("-0.42")
    assert snap.gamma == Decimal("0.03")
    assert snap.theta == Decimal("-0.06")
    assert snap.vega == Decimal("0.11")
    assert snap.rho == Decimal("-0.02")
    assert snap.retrieved_at != snap.quote_timestamp
    assert not hasattr(snap, "open_interest")


# --- missing optional fields stay null ------------------------------------


def test_missing_quote_trade_greeks_stay_null(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chain_payload({SPY_CALL: {}}))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)

    snap = result.snapshots[0]
    for field in (
        "quote_timestamp", "bid_price", "bid_size", "ask_price", "ask_size",
        "trade_timestamp", "trade_price", "trade_size", "implied_volatility",
        "delta", "gamma", "theta", "vega", "rho",
    ):
        assert getattr(snap, field) is None


def test_partial_greeks_only_missing_ones_null(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=chain_payload({SPY_CALL: snapshot_json(greeks={"delta": 0.5}, iv=None)})
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)

    snap = result.snapshots[0]
    assert snap.delta == Decimal("0.5")
    assert snap.gamma is None
    assert snap.implied_volatility is None


# --- malformed / nonfinite / negative numeric data -----------------------


@pytest.mark.parametrize(
    "snapshot",
    [
        {"latestQuote": {"bp": -1.0}},
        {"latestQuote": {"ap": float("nan")}},
        {"latestQuote": {"ap": float("inf")}},
        {"latestQuote": {"bs": -5}},
        {"latestQuote": {"bs": True}},
        {"latestQuote": {"as": -1}},
        {"latestTrade": {"p": -0.01}},
        {"latestTrade": {"p": float("inf")}},
        {"latestTrade": {"s": -1}},
        {"latestTrade": {"s": True}},
        {"impliedVolatility": -0.1},
        {"impliedVolatility": float("inf")},
        {"greeks": {"gamma": -0.01}},
        {"greeks": {"vega": -0.5}},
        {"greeks": {"delta": "0.5"}},
        {"greeks": {"theta": float("nan")}},
        {"latestQuote": {"t": "2026-09-02T15:30:00"}},  # naive timestamp
        {"latestTrade": {"t": "not-a-timestamp"}},
        {"latestQuote": "not-an-object"},
    ],
)
def test_malformed_numeric_or_shape_rejected(monkeypatch, isolated_env_file, snapshot):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chain_payload({SPY_CALL: snapshot}))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)


def test_negative_delta_and_theta_are_accepted(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=chain_payload(
                {SPY_PUT: snapshot_json(greeks={"delta": -0.55, "theta": -0.09, "rho": -0.01})}
            ),
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)
    assert result.snapshots[0].delta == Decimal("-0.55")
    assert result.snapshots[0].theta == Decimal("-0.09")


def test_non_utc_offset_timestamp_normalized_to_utc(monkeypatch, isolated_env_file):
    """A timezone-aware, non-Z offset is accepted and normalized to UTC (never made naive)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=chain_payload(
                {SPY_CALL: snapshot_json(quote={"t": "2026-09-02T11:30:00-04:00"})}
            ),
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)

    assert result.snapshots[0].quote_timestamp == "2026-09-02T15:30:00Z"


# --- duplicate contracts --------------------------------------------------


def test_duplicate_contract_symbol_across_pages_rejected(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("page_token")
        one = {SPY_CALL: snapshot_json(quote=FULL_QUOTE)}
        if token is None:
            return httpx.Response(200, json=chain_payload(one, next_page_token="T1"))
        return httpx.Response(200, json=chain_payload(one, next_page_token=None))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)


# --- pagination ---------------------------------------------------------


def test_pagination_traversal_and_deterministic_order(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        token = request.url.params.get("page_token")
        if token is None:
            return httpx.Response(
                200,
                json=chain_payload(
                    {"SPY260918C00550000": snapshot_json(quote=FULL_QUOTE)},
                    next_page_token="T1",
                ),
            )
        return httpx.Response(
            200,
            json=chain_payload(
                {
                    "SPY260918C00500000": snapshot_json(quote=FULL_QUOTE),
                    "SPY260918P00500000": snapshot_json(quote=FULL_QUOTE),
                },
                next_page_token=None,
            ),
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)

    assert len(calls) == 2
    assert "page_token" not in calls[0].url.params
    assert [s.contract_symbol for s in result.snapshots] == [
        "SPY260918C00500000",
        "SPY260918C00550000",
        "SPY260918P00500000",
    ]


def test_pagination_loop_detected(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json=chain_payload({SPY_CALL: snapshot_json(quote=FULL_QUOTE)}, next_page_token="SAME"),
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)
    assert len(calls) == 2


def test_max_pages_bound_enforced(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        token = f"T{len(calls)}"
        strike = 500000 + len(calls) * 1000
        symbol = f"SPY260918C{strike:08d}"
        page = {symbol: snapshot_json(quote=FULL_QUOTE)}
        return httpx.Response(200, json=chain_payload(page, next_page_token=token))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(
        AlpacaOptionsChainTruncatedError
    ) as exc_info:
        client.get_chain_snapshot(make_request(max_pages=3), client=http_client)
    assert len(calls) == 3
    assert exc_info.value.reason == OptionChainTruncationReason.MAX_PAGES_EXCEEDED
    assert isinstance(exc_info.value, AlpacaOptionsChainError)


def test_max_total_contracts_bound_enforced(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        snaps = {
            f"SPY260918C{500000 + i * 1000:08d}": snapshot_json(quote=FULL_QUOTE)
            for i in range(5)
        }
        return httpx.Response(200, json=chain_payload(snaps, next_page_token=None))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(
        AlpacaOptionsChainTruncatedError
    ) as exc_info:
        client.get_chain_snapshot(make_request(max_total_contracts=3), client=http_client)
    assert exc_info.value.reason == OptionChainTruncationReason.MAX_TOTAL_CONTRACTS_EXCEEDED
    assert isinstance(exc_info.value, AlpacaOptionsChainError)


def test_a_duplicate_contract_symbol_is_not_misclassified_as_truncation(
    monkeypatch, isolated_env_file
):
    """A duplicate-symbol failure is a real, unrelated AlpacaOptionsChainError
    -- never the typed AlpacaOptionsChainTruncatedError, even though both are
    raised from the same pagination loop."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("page_token")
        one = {SPY_CALL: snapshot_json(quote=FULL_QUOTE)}
        if token is None:
            return httpx.Response(200, json=chain_payload(one, next_page_token="T1"))
        return httpx.Response(200, json=chain_payload(one, next_page_token=None))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError) as exc_info:
        client.get_chain_snapshot(make_request(), client=http_client)
    assert not isinstance(exc_info.value, AlpacaOptionsChainTruncatedError)


def test_invalid_page_token_type_rejected(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=chain_payload({SPY_CALL: snapshot_json(quote=FULL_QUOTE)}, next_page_token=99)
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)


def test_exactly_one_request_per_page_and_no_retry_on_failure(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"message": "server error"})

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)
    assert len(calls) == 1


# --- empty result -------------------------------------------------------


def test_empty_snapshots_is_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chain_payload({}, next_page_token=None))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)
    assert result.snapshots == ()
    assert result.retrieved_at is not None  # empty result still carries a retrieval instant
    assert result.request.feed == "opra"


# --- malformed response shapes ----------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        ["not", "an", "object"],
        {"snapshots": "not-a-dict"},
        {"next_page_token": None},  # missing snapshots
    ],
)
def test_malformed_response_shape_rejected(monkeypatch, isolated_env_file, body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError):
        client.get_chain_snapshot(make_request(), client=http_client)


def test_malformed_json_rejected(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid json")

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError) as exc_info:
        client.get_chain_snapshot(make_request(), client=http_client)
    assert "{not valid json" not in str(exc_info.value)


# --- error sanitization ---------------------------------------------


def test_errors_never_leak_secrets_urls_or_contract_data(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden", "contract": SPY_CALL})

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError) as exc_info:
        client.get_chain_snapshot(make_request(), client=http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message
    assert "forbidden" not in message
    assert "snapshots" not in message
    assert "403" in message


def test_secrets_sent_as_headers_not_query_params(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert FAKE_ALPACA_KEY not in str(request.url)
        assert FAKE_ALPACA_SECRET not in str(request.url)
        return httpx.Response(200, json=chain_payload({SPY_CALL: snapshot_json(quote=FULL_QUOTE)}))

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)
    assert FAKE_ALPACA_KEY not in repr(result.snapshots[0])


def test_network_error_sanitized_and_no_retry(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("connection failed", request=request)

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaOptionsChainError) as exc_info:
        client.get_chain_snapshot(make_request(), client=http_client)
    assert len(calls) == 1
    assert FAKE_ALPACA_SECRET not in str(exc_info.value)


# --- open interest never present -----------------------------------


def test_open_interest_is_never_produced(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=chain_payload(
                {SPY_CALL: {**snapshot_json(quote=FULL_QUOTE), "openInterest": 12345}}
            ),
        )

    client = AlpacaOptionsChainClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        result = client.get_chain_snapshot(make_request(), client=http_client)

    snap = result.snapshots[0]
    assert not hasattr(snap, "open_interest")
    assert "12345" not in repr(snap)


def test_module_has_no_open_interest_reference():
    import market_intelligence.data_connectors.alpaca_options_chain as module

    src = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "open_interest" not in src
    # the docstring explains its absence, which necessarily mentions the words
    assert "open interest" in src
