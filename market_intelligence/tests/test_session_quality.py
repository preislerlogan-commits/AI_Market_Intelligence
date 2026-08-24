"""Tests for market_intelligence.market_features.session_quality.

These tests never make a network request. Bar fixtures are stored through
the existing, already-reviewed ``BarRepository`` against an isolated
temporary DuckDB database (Settings whose project_data_path is tmp_path),
mirroring market_intelligence/tests/test_market_context.py. The builder
under test is never pointed at the real repository database.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import Bar
from market_intelligence.market_features import session_quality
from market_intelligence.market_features.session_quality import (
    EASTERN,
    EXPECTED_SLOT_COUNT,
    SessionQualityBuilder,
    SessionQualityError,
    SessionQualityValidationError,
    normalize_session_date,
)
from market_intelligence.storage.bar_repository import BarRepository
from market_intelligence.storage.database import DuckDBManager, default_database_path

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


def initialized_builder(tmp_path: Path, isolated_env_file: Path):
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return settings, SessionQualityBuilder(settings=settings)


# A Wednesday in EDT (America/New_York is UTC-4 in August).
SESSION_DATE = date(2026, 8, 19)
# A Saturday.
WEEKEND_DATE = date(2026, 8, 22)


def et_to_utc_str(day: date, hour: int, minute: int) -> str:
    """Convert a wall-clock America/New_York time on ``day`` to an RFC3339 UTC string."""
    et_dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN)
    return et_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def make_bar(
    *,
    provider: str = "alpaca",
    symbol: str = "SPY",
    timeframe: str = "5Min",
    feed: str = "iex",
    adjustment: str = "raw",
    currency: str = "USD",
    timestamp: str,
    open: Decimal | float = Decimal("100.0"),
    high: Decimal | float = Decimal("101.0"),
    low: Decimal | float = Decimal("99.0"),
    close: Decimal | float = Decimal("100.5"),
    volume: int = 1000,
    trade_count: int | None = 50,
    vwap: Decimal | float | None = Decimal("100.2"),
    retrieved_at: str = "2026-08-19T20:00:00+00:00",
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


def full_session_bars(day: date = SESSION_DATE) -> list[Bar]:
    """78 regular-session bars covering every expected slot on ``day``.

    Close prices vary only slightly (within the fixed default high=101/
    low=99 range) so every bar's OHLC stays internally consistent without
    each call needing its own high/low.
    """
    bars = []
    total_minutes = 9 * 60 + 30
    for i in range(EXPECTED_SLOT_COUNT):
        hour, minute = divmod(total_minutes, 60)
        bars.append(
            make_bar(
                timestamp=et_to_utc_str(day, hour, minute),
                close=Decimal("100") + Decimal(i) * Decimal("0.01"),
            )
        )
        total_minutes += 5
    return bars


# --- normalize_session_date -----------------------------------------------------


def test_normalize_session_date_none_returns_none():
    assert normalize_session_date(None) is None


def test_normalize_session_date_valid_string():
    assert normalize_session_date("2026-08-19") == date(2026, 8, 19)


@pytest.mark.parametrize("value", [123, 20260819, ["2026-08-19"], True])
def test_normalize_session_date_rejects_non_string(value):
    with pytest.raises(SessionQualityValidationError):
        normalize_session_date(value)


@pytest.mark.parametrize("value", ["2026/08/19", "08-19-2026", "not-a-date", "2026-8-19", ""])
def test_normalize_session_date_rejects_malformed_format(value):
    with pytest.raises(SessionQualityValidationError):
        normalize_session_date(value)


def test_normalize_session_date_rejects_invalid_calendar_date():
    with pytest.raises(SessionQualityValidationError):
        normalize_session_date("2026-02-30")


# --- build_report input validation -----------------------------------------------


def test_build_report_rejects_invalid_symbol(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    with pytest.raises(SessionQualityValidationError):
        builder.build_report("not a symbol")


def test_build_report_rejects_invalid_session_date(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    with pytest.raises(SessionQualityValidationError):
        builder.build_report("SPY", session_date="19-08-2026")


def test_build_report_validation_error_does_not_create_database_file(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    builder = SessionQualityBuilder(settings=settings)
    with pytest.raises(SessionQualityValidationError):
        builder.build_report("not a symbol")
    assert not default_database_path(settings).exists()


# --- Missing / empty data ---------------------------------------------------------


def test_build_report_missing_database_reports_truthful_empty(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    assert not default_database_path(settings).exists()
    builder = SessionQualityBuilder(settings=settings)

    report = builder.build_report("SPY")

    assert report["session_date_et"] is None
    assert report["session_date_source"] == "none_available"
    assert report["completeness"]["observed_regular_session_slot_count"] == 0
    assert report["completeness"]["missing_data"] is True
    assert report["completeness"]["complete"] is False
    assert report["bar_provenance"]["provider"] is None
    assert report["bar_provenance"]["timeframe"] == "5Min"


def test_build_report_initialized_empty_database_reports_missing(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)

    report = builder.build_report("SPY")

    assert report["completeness"]["missing_data"] is True
    assert report["completeness"]["observed_regular_session_slot_count"] == 0


def test_build_report_no_bars_for_symbol_reports_missing(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(symbol="QQQ", timestamp=et_to_utc_str(SESSION_DATE, 9, 30))])

    report = builder.build_report("SPY")

    assert report["completeness"]["missing_data"] is True
    assert report["bar_provenance"]["provider"] is None


def test_build_report_ignores_other_timeframe_bars(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [make_bar(timeframe="1Min", timestamp=et_to_utc_str(SESSION_DATE, 9, 30))]
    )

    report = builder.build_report("SPY")

    assert report["completeness"]["missing_data"] is True
    assert report["bar_provenance"]["provider"] is None


# --- Identity selection ------------------------------------------------------------


def test_build_report_selects_most_recent_identity_and_never_mixes(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    older_provider_bars = [
        make_bar(provider="alpaca", timestamp=et_to_utc_str(SESSION_DATE, 9, 30))
    ]
    newer_provider_bars = [
        make_bar(
            provider="other_provider",
            timestamp=et_to_utc_str(SESSION_DATE, 9, 35),
            close=Decimal("500"),
            high=Decimal("501"),
        )
    ]
    repo.store_bars(older_provider_bars, provider="alpaca")
    repo.store_bars(newer_provider_bars, provider="other_provider")

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    assert report["bar_provenance"]["provider"] == "other_provider"
    # Only the newer identity's one bar should be counted -- never mixed.
    assert report["completeness"]["observed_regular_session_slot_count"] == 1
    assert report["regular_session"]["latest_close"] == "500.000000"


# --- Auto session-date selection -----------------------------------------------------


def test_build_report_auto_selects_most_recent_regular_session_date(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    earlier_day = date(2026, 8, 18)
    repo.store_bars(
        [
            make_bar(timestamp=et_to_utc_str(earlier_day, 9, 30)),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30)),
        ]
    )

    report = builder.build_report("SPY")

    assert report["session_date_et"] == SESSION_DATE.isoformat()
    assert report["session_date_source"] == "auto_most_recent_with_regular_bars"


def test_build_report_no_regular_session_bars_reports_none_available(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    # Only a premarket bar -- never within the 09:30-15:55 regular window.
    repo.store_bars([make_bar(timestamp=et_to_utc_str(SESSION_DATE, 8, 0))])

    report = builder.build_report("SPY")

    assert report["session_date_et"] is None
    assert report["session_date_source"] == "none_available"
    assert report["completeness"]["missing_data"] is True


# --- Explicit session date ------------------------------------------------------------


def test_build_report_explicit_session_date_used_verbatim(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    earlier_day = date(2026, 8, 18)
    repo.store_bars(
        [
            make_bar(timestamp=et_to_utc_str(earlier_day, 9, 30)),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30)),
        ]
    )

    report = builder.build_report("SPY", session_date=earlier_day.isoformat())

    assert report["session_date_et"] == earlier_day.isoformat()
    assert report["session_date_source"] == "explicit"


def test_build_report_explicit_weekend_date_reports_all_slots_missing(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    # A bar stored with a timestamp that lands in the 09:30-15:55 ET window
    # on a Saturday -- still not a "regular session" bar (weekday required).
    repo.store_bars([make_bar(timestamp=et_to_utc_str(WEEKEND_DATE, 10, 0))])

    report = builder.build_report("SPY", session_date=WEEKEND_DATE.isoformat())

    assert report["session_date_source"] == "explicit"
    assert report["completeness"]["observed_regular_session_slot_count"] == 0
    assert len(report["completeness"]["missing_expected_timestamps_utc"]) == EXPECTED_SLOT_COUNT
    assert report["completeness"]["complete"] is False


# --- Completeness ---------------------------------------------------------------------


def test_build_report_complete_session_reports_complete_true(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(full_session_bars())

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    completeness = report["completeness"]
    assert completeness["observed_regular_session_slot_count"] == EXPECTED_SLOT_COUNT
    assert completeness["missing_expected_timestamps_utc"] == []
    assert completeness["complete"] is True
    assert completeness["partial_session"] is False
    assert completeness["missing_data"] is False
    assert report["regular_session"]["latest_close_is_full_session_close"] is True


def test_build_report_partial_session_reports_missing_timestamps(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    bars = full_session_bars()
    # Drop two slots (indexes 10 and 40) to simulate a gap.
    trimmed = [bar for i, bar in enumerate(bars) if i not in (10, 40)]
    repo.store_bars(trimmed)

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    completeness = report["completeness"]
    assert completeness["observed_regular_session_slot_count"] == EXPECTED_SLOT_COUNT - 2
    assert len(completeness["missing_expected_timestamps_utc"]) == 2
    assert completeness["missing_expected_timestamps_utc"] == sorted(
        completeness["missing_expected_timestamps_utc"]
    )
    assert completeness["complete"] is False
    assert completeness["partial_session"] is True
    assert completeness["missing_data"] is False
    assert report["regular_session"]["latest_close_is_full_session_close"] is False


# --- Premarket / after-hours -----------------------------------------------------------


def test_build_report_premarket_and_after_hours_counts(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 7, 0)),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 0)),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30)),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 17, 0)),
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    assert report["same_date_bars"]["premarket_count"] == 2
    assert report["same_date_bars"]["after_hours_count"] == 1
    assert report["completeness"]["observed_regular_session_slot_count"] == 1


# --- Session open / close / return ------------------------------------------------------


def test_build_report_open_absent_when_0930_slot_missing(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(
                timestamp=et_to_utc_str(SESSION_DATE, 9, 35),
                close=Decimal("110"),
                high=Decimal("111"),
            )
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    assert report["regular_session"]["open"] is None
    assert report["regular_session"]["latest_close"] == "110.000000"
    assert report["regular_session"]["return_pct"] is None


def test_build_report_return_pct_computed_when_open_and_close_present(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30), open=Decimal("100")),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 35), close=Decimal("101")),
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    assert report["regular_session"]["open"] == "100.000000"
    assert report["regular_session"]["latest_close"] == "101.000000"
    assert report["regular_session"]["return_pct"] == "0.010000"


# --- High / low / range / volume / vwap --------------------------------------------------


def test_build_report_high_low_range_and_volume_computed(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(
                timestamp=et_to_utc_str(SESSION_DATE, 9, 30),
                high=Decimal("105"),
                low=Decimal("99"),
                volume=1000,
            ),
            make_bar(
                timestamp=et_to_utc_str(SESSION_DATE, 9, 35),
                high=Decimal("110"),
                low=Decimal("100"),
                volume=2000,
            ),
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    regular = report["regular_session"]
    assert regular["high"] == "110.000000"
    assert regular["low"] == "99.000000"
    assert regular["range"] == "11.000000"
    assert regular["total_volume"] == 3000


def test_build_report_vwap_computed_when_all_bars_have_vwap(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(
                timestamp=et_to_utc_str(SESSION_DATE, 9, 30), volume=100, vwap=Decimal("100")
            ),
            make_bar(
                timestamp=et_to_utc_str(SESSION_DATE, 9, 35), volume=300, vwap=Decimal("104")
            ),
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    # (100*100 + 300*104) / 400 = 103.0
    assert report["regular_session"]["vwap"] == "103.000000"


def test_build_report_vwap_null_when_any_bar_missing_vwap(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30), vwap=None),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 35), vwap=Decimal("104")),
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    assert report["regular_session"]["vwap"] is None


# --- First / last regular-session timestamps -----------------------------------------------


def test_build_report_first_and_last_regular_timestamps(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30)),
            make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 45)),
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    assert report["regular_session"]["first_timestamp_utc"] == et_to_utc_str(SESSION_DATE, 9, 30)
    assert report["regular_session"]["last_timestamp_utc"] == et_to_utc_str(SESSION_DATE, 9, 45)


# --- Unexpected / off-grid timestamps ---------------------------------------------------


def test_build_report_off_grid_bar_reported_as_unexpected(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    et_dt = datetime(2026, 8, 19, 9, 32, tzinfo=EASTERN)
    off_grid_utc = et_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    repo.store_bars([make_bar(timestamp=off_grid_utc)])

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    assert report["completeness"]["observed_regular_session_slot_count"] == 0
    assert len(report["completeness"]["unexpected_or_duplicate_timestamps_utc"]) == 1


# --- Session-definition metadata ----------------------------------------------------------


def test_build_report_session_definition_documents_limitations(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)

    report = builder.build_report("SPY")

    definition = report["session_definition"]
    assert definition["timezone"] == "America/New_York"
    assert definition["expected_slot_count"] == EXPECTED_SLOT_COUNT
    assert "holiday" in definition["limitations"].lower()
    assert "weekday" in definition["limitations"].lower()


# --- Serialization / determinism ----------------------------------------------------------


def test_build_report_is_json_serializable_with_fixed_decimal_scale(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars(
        [
            make_bar(
                timestamp=et_to_utc_str(SESSION_DATE, 9, 30),
                close=Decimal("123.4"),
                high=Decimal("124"),
            )
        ]
    )

    report = builder.build_report("SPY", session_date=SESSION_DATE.isoformat())

    serialized = json.dumps(report)
    assert '"latest_close": "123.400000"' in serialized


# --- Connection-close error handling --------------------------------------------------------

_CLOSE_FAILURE_MARKER = "simulated close failure C:\\secret\\path SELECT * FROM market_bars"
_READ_FAILURE_MARKER = "simulated read failure C:\\secret\\path SELECT * FROM market_bars"


class _CloseFailingConnection:
    def __init__(self, real_connection):
        self._real_connection = real_connection

    def __getattr__(self, name):
        return getattr(self._real_connection, name)

    def close(self):
        raise RuntimeError(_CLOSE_FAILURE_MARKER)


class _ReadThenCloseFailingConnection:
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


def test_build_report_close_failure_after_successful_read_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30))])

    real_connect = session_quality.duckdb.connect

    def fake_connect(*args, **kwargs):
        return _CloseFailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(session_quality.duckdb, "connect", fake_connect)

    with pytest.raises(SessionQualityError) as exc_info:
        builder.build_report("SPY")

    message = str(exc_info.value)
    assert _CLOSE_FAILURE_MARKER not in message
    assert "RuntimeError" not in message
    assert "secret" not in message


def test_build_report_close_failure_does_not_mask_sanitized_read_error(
    tmp_path, isolated_env_file, monkeypatch
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = BarRepository(settings=settings)
    repo.store_bars([make_bar(timestamp=et_to_utc_str(SESSION_DATE, 9, 30))])

    real_connect = session_quality.duckdb.connect

    def fake_connect(*args, **kwargs):
        return _ReadThenCloseFailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(session_quality.duckdb, "connect", fake_connect)

    with pytest.raises(SessionQualityError) as exc_info:
        builder.build_report("SPY")

    message = str(exc_info.value)
    assert _READ_FAILURE_MARKER not in message
    assert _CLOSE_FAILURE_MARKER not in message
    assert "RuntimeError" not in message
    assert "secret" not in message
