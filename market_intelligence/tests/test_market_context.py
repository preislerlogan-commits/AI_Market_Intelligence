"""Tests for market_intelligence.market_features.market_context.

These tests never make a network request. Repository fixtures (bars, news,
macro observations) are stored through the existing, already-reviewed
repositories against an isolated temporary DuckDB database (Settings whose
project_data_path is tmp_path), mirroring
market_intelligence/tests/test_bar_repository.py,
test_news_repository.py, and test_macro_observation_repository.py. The
snapshot builder itself is never pointed at the real repository database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import Bar
from market_intelligence.data_connectors.alpaca_news import NewsItem
from market_intelligence.data_connectors.fred_macro_data import FredObservation
from market_intelligence.market_features import market_context
from market_intelligence.market_features.market_context import (
    BARS_SESSION_SCOPE,
    BARS_STALE_AFTER,
    MACRO_STALE_AFTER,
    MAX_MACRO_SERIES_IDS,
    MAX_RECENT_BARS_LIMIT,
    NEWS_STALE_AFTER,
    RETURN_PERIOD_BARS,
    MarketContextBuilder,
    MarketContextError,
    MarketContextValidationError,
    normalize_bounded_limit,
    normalize_macro_series_ids,
)
from market_intelligence.storage.bar_repository import BarRepository
from market_intelligence.storage.database import DuckDBManager, default_database_path
from market_intelligence.storage.macro_observation_repository import MacroObservationRepository
from market_intelligence.storage.news_repository import NewsArticleRepository

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]


@pytest.fixture(autouse=True)
def clear_credential_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def isolated_settings(tmp_path: Path, isolated_env_file: Path) -> Settings:
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def fixed_clock(value: datetime):
    def _clock() -> datetime:
        return value

    return _clock


DEFAULT_AS_OF = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


def bar_timestamp(
    index: int, *, base_hour: int = 9, base_minute: int = 30, step_minutes: int = 5
) -> str:
    """A valid RFC3339 timestamp ``index`` steps of ``step_minutes`` after the base time.

    Handles minute overflow past 60 by rolling into the hour, so a loop of
    arbitrarily many bars never produces an invalid timestamp like ``09:75``.
    """
    total_minutes = base_hour * 60 + base_minute + index * step_minutes
    hour, minute = divmod(total_minutes, 60)
    return f"2026-08-19T{hour:02d}:{minute:02d}:00Z"


def make_bar(
    *,
    provider: str = "alpaca",
    symbol: str = "SPY",
    timeframe: str = "5Min",
    feed: str = "iex",
    adjustment: str = "raw",
    currency: str = "USD",
    timestamp: str = "2026-08-19T09:30:00Z",
    open: Decimal | float = Decimal("100.0"),
    high: Decimal | float = Decimal("100000.0"),
    low: Decimal | float = Decimal("0.01"),
    close: Decimal | float = Decimal("100.5"),
    volume: int = 1000,
    trade_count: int | None = 50,
    vwap: Decimal | float | None = Decimal("100.2"),
    retrieved_at: str = "2026-08-19T09:35:00+00:00",
) -> Bar:
    return Bar(
        provider=provider,
        symbol=symbol,
        timeframe=timeframe,
        feed=feed,
        adjustment=adjustment,
        currency=currency,
        timestamp=timestamp,
        open=open if isinstance(open, Decimal) else Decimal(str(open)),
        high=high if isinstance(high, Decimal) else Decimal(str(high)),
        low=low if isinstance(low, Decimal) else Decimal(str(low)),
        close=close if isinstance(close, Decimal) else Decimal(str(close)),
        volume=volume,
        trade_count=trade_count,
        vwap=(vwap if vwap is None or isinstance(vwap, Decimal) else Decimal(str(vwap))),
        retrieved_at=retrieved_at,
    )


def make_news_item(
    *,
    provider: str = "alpaca",
    provider_article_id: str = "1",
    headline: str = "Fed signals rate pause",
    source: str = "benzinga",
    url: str = "https://example.com/news/1",
    summary: str | None = "A summary.",
    created_at: str | None = "2026-08-20T12:00:00Z",
    updated_at: str | None = "2026-08-20T12:05:00Z",
    related_symbols: tuple[str, ...] = ("SPY",),
    retrieved_at: str = "2026-08-20T12:10:00Z",
) -> NewsItem:
    return NewsItem(
        provider=provider,
        provider_article_id=provider_article_id,
        headline=headline,
        source=source,
        url=url,
        summary=summary,
        created_at=created_at,
        updated_at=updated_at,
        related_symbols=related_symbols,
        retrieved_at=retrieved_at,
    )


def make_observation(
    *,
    provider: str = "fred",
    series_id: str = "FEDFUNDS",
    observation_date: str = "2026-08-01",
    value: Decimal | None = Decimal("5.33"),
    is_missing: bool = False,
    realtime_start: str = "2026-08-20",
    realtime_end: str = "2026-08-20",
    retrieved_at: str = "2026-08-20T09:35:00Z",
) -> FredObservation:
    return FredObservation(
        provider=provider,
        series_id=series_id,
        observation_date=observation_date,
        value=value,
        is_missing=is_missing,
        realtime_start=realtime_start,
        realtime_end=realtime_end,
        retrieved_at=retrieved_at,
    )


def initialized_builder(
    tmp_path: Path, isolated_env_file: Path, *, as_of: datetime = DEFAULT_AS_OF
):
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return settings, MarketContextBuilder(settings=settings, clock=fixed_clock(as_of))


# --- Pure validation: normalize_bounded_limit ---------------------------------


@pytest.mark.parametrize("value", [True, False, "5", 3.5, None])
def test_normalize_bounded_limit_rejects_non_int(value):
    with pytest.raises(MarketContextValidationError):
        normalize_bounded_limit(value, field_name="x", maximum=10)


@pytest.mark.parametrize("value", [0, -1, 11])
def test_normalize_bounded_limit_rejects_out_of_range(value):
    with pytest.raises(MarketContextValidationError):
        normalize_bounded_limit(value, field_name="x", maximum=10)


def test_normalize_bounded_limit_accepts_boundaries():
    assert normalize_bounded_limit(1, field_name="x", maximum=10) == 1
    assert normalize_bounded_limit(10, field_name="x", maximum=10) == 10


# --- Pure validation: normalize_macro_series_ids ------------------------------


def test_normalize_macro_series_ids_rejects_bare_string():
    with pytest.raises(MarketContextValidationError):
        normalize_macro_series_ids("FEDFUNDS")


def test_normalize_macro_series_ids_rejects_too_many():
    too_many = [f"SERIES{i}" for i in range(MAX_MACRO_SERIES_IDS + 1)]
    with pytest.raises(MarketContextValidationError):
        normalize_macro_series_ids(too_many)


def test_normalize_macro_series_ids_rejects_invalid_series_id():
    with pytest.raises(MarketContextValidationError):
        normalize_macro_series_ids(["not a valid id!"])


def test_normalize_macro_series_ids_dedupes_and_sorts():
    result = normalize_macro_series_ids(["fedfunds", "UNRATE", "FEDFUNDS"])
    assert result == ("FEDFUNDS", "UNRATE")


def test_normalize_macro_series_ids_accepts_empty():
    assert normalize_macro_series_ids([]) == ()


# --- build_snapshot input validation ------------------------------------------


def test_build_snapshot_rejects_invalid_symbol(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    with pytest.raises(MarketContextValidationError):
        builder.build_snapshot("not a symbol")


def test_build_snapshot_rejects_boolean_recent_bars_limit(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    with pytest.raises(MarketContextValidationError):
        builder.build_snapshot("SPY", recent_bars_limit=True)


def test_build_snapshot_rejects_zero_recent_news_limit(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    with pytest.raises(MarketContextValidationError):
        builder.build_snapshot("SPY", recent_news_limit=0)


def test_build_snapshot_rejects_excessive_recent_bars_limit(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    with pytest.raises(MarketContextValidationError):
        builder.build_snapshot("SPY", recent_bars_limit=MAX_RECENT_BARS_LIMIT + 1)


def test_build_snapshot_rejects_invalid_macro_series_id(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    with pytest.raises(MarketContextValidationError):
        builder.build_snapshot("SPY", macro_series_ids=["bad id!"])


def test_build_snapshot_validation_error_does_not_create_database_file(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    builder = MarketContextBuilder(settings=settings, clock=fixed_clock(DEFAULT_AS_OF))
    with pytest.raises(MarketContextValidationError):
        builder.build_snapshot("not a symbol")
    assert not default_database_path(settings).exists()


# --- Missing / empty database --------------------------------------------------


def test_build_snapshot_missing_database_reports_missing_and_stale(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    assert not default_database_path(settings).exists()
    builder = MarketContextBuilder(settings=settings, clock=fixed_clock(DEFAULT_AS_OF))

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["bars_missing"] is True
    assert snapshot["flags"]["bars_stale"] is True
    assert snapshot["flags"]["news_missing"] is True
    assert snapshot["flags"]["news_stale"] is True
    assert snapshot["flags"]["macro_missing_series"] == ["FEDFUNDS"]
    assert snapshot["price"]["recent_bars"] == []
    assert snapshot["news"]["recent_articles"] == []
    assert snapshot["macro"]["series"][0]["has_stored_observation"] is False
    assert snapshot["bars_provenance"]["session_scope"] == BARS_SESSION_SCOPE


def test_build_snapshot_snapshot_created_at_reflects_injected_clock(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    builder = MarketContextBuilder(settings=settings, clock=fixed_clock(DEFAULT_AS_OF))

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["snapshot_created_at_utc"] == "2026-08-23T12:00:00Z"


def test_build_snapshot_initialized_empty_database_reports_missing(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["bars_missing"] is True
    assert snapshot["flags"]["news_missing"] is True
    assert snapshot["macro"]["series"][0]["has_stored_observation"] is False


# --- Bars section ---------------------------------------------------------------


def test_build_snapshot_reports_latest_close_provenance_and_coverage(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    bars = [
        make_bar(timestamp=f"2026-08-19T09:{minute:02d}:00Z", close=Decimal(f"{100 + minute}.0"))
        for minute in (30, 35, 40, 45)
    ]
    repo.store_bars(bars, provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["price"]["latest_bar_timestamp_utc"] == "2026-08-19T09:45:00Z"
    assert snapshot["price"]["latest_close"] == "145.000000"
    assert snapshot["bars_provenance"] == {
        "provider": "alpaca",
        "symbol": "SPY",
        "timeframe": "5Min",
        "feed": "iex",
        "adjustment": "raw",
        "currency": "USD",
        "session_scope": "provider_returned_unfiltered",
    }
    coverage = snapshot["coverage"]["bars"]
    assert coverage["row_count"] == 4
    assert coverage["earliest_bar_timestamp_utc"] == "2026-08-19T09:30:00Z"
    assert coverage["latest_bar_timestamp_utc"] == "2026-08-19T09:45:00Z"
    assert snapshot["flags"]["bars_missing"] is False


def test_build_snapshot_short_return_present_with_enough_bars(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    bars = [
        make_bar(timestamp=bar_timestamp(i), close=Decimal(f"{100 + i}.0"))
        for i in range(RETURN_PERIOD_BARS + 1)
    ]
    repo.store_bars(bars, provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    short_return = snapshot["price"]["short_return"]
    assert short_return is not None
    assert short_return["period_bars"] == RETURN_PERIOD_BARS
    assert short_return["start_close"] == "100.000000"
    assert short_return["end_close"] == f"{100 + RETURN_PERIOD_BARS}.000000"
    assert short_return["return_pct"] == "0.050000"


def test_build_snapshot_short_return_absent_with_insufficient_bars(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    bars = [
        make_bar(timestamp=bar_timestamp(i), close=Decimal(f"{100 + i}.0"))
        for i in range(RETURN_PERIOD_BARS)
    ]
    repo.store_bars(bars, provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["price"]["short_return"] is None


def test_build_snapshot_recent_bars_respects_limit_and_ordering(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    bars = [
        make_bar(timestamp=bar_timestamp(i), close=Decimal(f"{100 + i}.0")) for i in range(10)
    ]
    repo.store_bars(bars, provider="alpaca")

    snapshot = builder.build_snapshot("SPY", recent_bars_limit=3)

    recent = snapshot["price"]["recent_bars"]
    assert len(recent) == 3
    assert [bar["close"] for bar in recent] == ["109.000000", "108.000000", "107.000000"]


def test_build_snapshot_ignores_other_symbols_bars(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(symbol="SPY"), make_bar(symbol="QQQ")], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["bars_provenance"]["symbol"] == "SPY"
    assert snapshot["coverage"]["bars"]["row_count"] == 1


def test_build_snapshot_bars_selects_latest_identity_only(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    older_5min = make_bar(
        timeframe="5Min", timestamp="2026-08-19T09:30:00Z", close=Decimal("100.0")
    )
    newer_1min = make_bar(
        timeframe="1Min", timestamp="2026-08-19T10:00:00Z", close=Decimal("200.0")
    )
    repo.store_bars([older_5min, newer_1min], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["bars_provenance"]["timeframe"] == "1Min"
    assert snapshot["price"]["latest_close"] == "200.000000"
    assert snapshot["coverage"]["bars"]["row_count"] == 1


def test_build_snapshot_bars_stale_flag_true_when_old(tmp_path, isolated_env_file):
    # More than BARS_STALE_AFTER (72 hours) after the latest stored bar.
    as_of = datetime(2026, 8, 19, 9, 30, 0, tzinfo=UTC) + BARS_STALE_AFTER + timedelta(hours=1)
    settings, builder = initialized_builder(tmp_path, isolated_env_file, as_of=as_of)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(timestamp="2026-08-19T09:30:00Z")], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["bars_stale"] is True


def test_build_snapshot_bars_stale_flag_false_when_recent(tmp_path, isolated_env_file):
    as_of = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)
    settings, builder = initialized_builder(tmp_path, isolated_env_file, as_of=as_of)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(timestamp="2026-08-19T09:30:00Z")], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["bars_stale"] is False


def test_build_snapshot_bars_not_stale_across_weekend(tmp_path, isolated_env_file):
    """A Friday bar is not falsely flagged stale on Saturday.

    2026-08-21 is a Friday and 2026-08-22 is the following Saturday. The
    elapsed time (28 hours) exceeds the old 24-hour threshold but stays
    under the new, deliberately weekend-tolerant 72-hour threshold, so this
    proves the false-staleness regression is fixed -- not just that some
    recent bar isn't stale.
    """
    as_of = datetime(2026, 8, 22, 19, 0, 0, tzinfo=UTC)
    settings, builder = initialized_builder(tmp_path, isolated_env_file, as_of=as_of)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(timestamp="2026-08-21T15:00:00Z")], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["bars_stale"] is False
    assert snapshot["price"]["latest_bar_timestamp_utc"] == "2026-08-21T15:00:00Z"


def test_build_snapshot_bars_stale_after_72_hours(tmp_path, isolated_env_file):
    """A bar more than 72 hours old is still correctly flagged stale."""
    as_of = datetime(2026, 8, 24, 16, 0, 0, tzinfo=UTC)
    settings, builder = initialized_builder(tmp_path, isolated_env_file, as_of=as_of)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(timestamp="2026-08-21T15:00:00Z")], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["bars_stale"] is True
    assert snapshot["price"]["latest_bar_timestamp_utc"] == "2026-08-21T15:00:00Z"


# --- News section ----------------------------------------------------------------


def test_build_snapshot_news_recent_articles_bounded_and_ordered(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = NewsArticleRepository(settings=settings)
    items = [
        make_news_item(
            provider_article_id=str(i),
            created_at=f"2026-08-2{i}T12:00:00Z",
            url=f"https://example.com/news/{i}",
        )
        for i in range(1, 6)
    ]
    repo.store_news_items(items, provider="alpaca")

    snapshot = builder.build_snapshot("SPY", recent_news_limit=2)

    articles = snapshot["news"]["recent_articles"]
    assert len(articles) == 2
    assert articles[0]["provider_article_id"] == "5"
    assert articles[1]["provider_article_id"] == "4"
    assert articles[0]["related_symbols"] == ["SPY"]


def test_build_snapshot_news_missing_when_no_match(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = NewsArticleRepository(settings=settings)
    repo.store_news_items([make_news_item(related_symbols=("QQQ",))], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["news_missing"] is True
    assert snapshot["news"]["recent_articles"] == []


def test_build_snapshot_news_stale_flag(tmp_path, isolated_env_file):
    as_of = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC) + NEWS_STALE_AFTER + timedelta(days=1)
    settings, builder = initialized_builder(tmp_path, isolated_env_file, as_of=as_of)
    repo = NewsArticleRepository(settings=settings)
    repo.store_news_items([make_news_item(created_at="2026-08-20T12:00:00Z")], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["flags"]["news_stale"] is True


# --- Macro section -----------------------------------------------------------------


def test_build_snapshot_macro_latest_observation_and_coverage(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    observations = [
        make_observation(observation_date="2026-06-01", value=Decimal("5.10")),
        make_observation(observation_date="2026-07-01", value=Decimal("5.20")),
        make_observation(observation_date="2026-08-01", value=Decimal("5.33")),
    ]
    repo.store_observations(observations)

    snapshot = builder.build_snapshot("SPY")

    series = snapshot["macro"]["series"][0]
    assert series["series_id"] == "FEDFUNDS"
    assert series["has_stored_observation"] is True
    assert series["observation_date"] == "2026-08-01"
    assert series["value"] == "5.330000"
    assert series["coverage"] == {
        "row_count": 3,
        "earliest_observation_date": "2026-06-01",
        "latest_observation_date": "2026-08-01",
    }
    assert snapshot["flags"]["macro_missing_series"] == []


def test_build_snapshot_macro_missing_series_flag(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)

    snapshot = builder.build_snapshot("SPY", macro_series_ids=["UNRATE"])

    assert snapshot["flags"]["macro_missing_series"] == ["UNRATE"]
    assert snapshot["macro"]["series"][0]["has_stored_observation"] is False


def test_build_snapshot_macro_is_missing_value_preserved(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation(value=None, is_missing=True)])

    snapshot = builder.build_snapshot("SPY")

    series = snapshot["macro"]["series"][0]
    assert series["is_missing"] is True
    assert series["value"] is None
    assert series["has_stored_observation"] is True


def test_build_snapshot_macro_multiple_series_sorted(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(series_id="FEDFUNDS"),
            make_observation(series_id="AAAA", observation_date="2026-08-01"),
        ]
    )

    snapshot = builder.build_snapshot("SPY", macro_series_ids=["FEDFUNDS", "AAAA"])

    series_ids = [entry["series_id"] for entry in snapshot["macro"]["series"]]
    assert series_ids == ["AAAA", "FEDFUNDS"]


def test_build_snapshot_macro_stale_flag(tmp_path, isolated_env_file):
    as_of = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC) + MACRO_STALE_AFTER + timedelta(days=1)
    settings, builder = initialized_builder(tmp_path, isolated_env_file, as_of=as_of)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation(observation_date="2026-08-01")])

    snapshot = builder.build_snapshot("SPY")

    assert snapshot["macro"]["series"][0]["stale"] is True
    assert snapshot["flags"]["macro_stale_series"] == ["FEDFUNDS"]


# --- Serialization / determinism ----------------------------------------------------


def test_build_snapshot_is_json_serializable_with_fixed_decimal_scale(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(close=Decimal("123.4"))], provider="alpaca")

    snapshot = builder.build_snapshot("SPY")

    serialized = json.dumps(snapshot)
    assert '"close": "123.400000"' in serialized


def test_build_snapshot_deterministic_with_fixed_clock(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar()], provider="alpaca")

    first = json.dumps(builder.build_snapshot("SPY"))
    second = json.dumps(builder.build_snapshot("SPY"))

    assert first == second


# --- Connection-close error handling -------------------------------------------

_CLOSE_FAILURE_MARKER = "simulated close failure C:\\secret\\path SELECT * FROM market_bars"
_READ_FAILURE_MARKER = "simulated read failure C:\\secret\\path SELECT * FROM market_bars"


class _CloseFailingConnection:
    """Wraps a real DuckDB connection but always fails on close()."""

    def __init__(self, real_connection):
        self._real_connection = real_connection

    def __getattr__(self, name):
        return getattr(self._real_connection, name)

    def close(self):
        raise RuntimeError(_CLOSE_FAILURE_MARKER)


class _ReadThenCloseFailingConnection:
    """Wraps a real DuckDB connection: the first execute() (table listing)
    succeeds, every subsequent execute() raises duckdb.Error, and close()
    also always fails -- used to prove a close() failure never masks an
    already-sanitized read error."""

    def __init__(self, real_connection):
        self._real_connection = real_connection
        self._execute_count = 0

    def __getattr__(self, name):
        return getattr(self._real_connection, name)

    def execute(self, *args, **kwargs):
        self._execute_count += 1
        if self._execute_count == 1:
            return self._real_connection.execute(*args, **kwargs)
        raise duckdb.Error(_READ_FAILURE_MARKER)

    def close(self):
        raise RuntimeError(_CLOSE_FAILURE_MARKER)


def test_build_snapshot_close_failure_after_successful_read_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar()], provider="alpaca")

    real_connect = market_context.duckdb.connect

    def fake_connect(*args, **kwargs):
        return _CloseFailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(market_context.duckdb, "connect", fake_connect)

    with pytest.raises(MarketContextError) as exc_info:
        builder.build_snapshot("SPY")

    message = str(exc_info.value)
    assert _CLOSE_FAILURE_MARKER not in message
    assert "RuntimeError" not in message
    assert "secret" not in message


def test_build_snapshot_close_failure_does_not_mask_sanitized_read_error(
    tmp_path, isolated_env_file, monkeypatch
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    real_connect = market_context.duckdb.connect

    def fake_connect(*args, **kwargs):
        return _ReadThenCloseFailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(market_context.duckdb, "connect", fake_connect)

    with pytest.raises(MarketContextError) as exc_info:
        builder.build_snapshot("SPY")

    message = str(exc_info.value)
    assert message == "Failed to read market context data from local storage."
    assert _READ_FAILURE_MARKER not in message
    assert _CLOSE_FAILURE_MARKER not in message
    assert "RuntimeError" not in message
    assert "secret" not in message
