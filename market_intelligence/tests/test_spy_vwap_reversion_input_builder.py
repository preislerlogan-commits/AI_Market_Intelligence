"""Tests for market_intelligence.orchestration.spy_vwap_reversion_input_builder.

Every test uses synthetic bars only -- either pure ``StoredBarRow`` lists or
an isolated temporary DuckDB database (Settings whose project_data_path is
tmp_path), initialized with the real migrations and populated by direct
INSERTs so that other-provenance and deliberately malformed rows can be
stored. Nothing here touches the real repository database or makes a
network request.
"""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    SpyVwapReversionEvaluationInput,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    input_from_json_str,
    input_to_json_str,
)
from market_intelligence.market_features.spy_regime_contracts import (
    EASTERN,
    BreadthState,
    CatalystState,
)
from market_intelligence.orchestration import spy_vwap_reversion_input_builder as builder
from market_intelligence.orchestration.spy_vwap_reversion_input_builder import (
    PriorDayStatus,
    SessionExclusionReason,
    SpyVwapInputBuildError,
    SpyVwapInputValidationError,
    StoredBarRow,
    assemble_evaluation_input,
    build_spy_vwap_reversion_input,
    previous_weekday,
)
from market_intelligence.storage.database import DuckDBManager

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = (
    REPO_ROOT / "market_intelligence" / "orchestration" / "spy_vwap_reversion_input_builder.py"
)

MON, TUE, WED, THU, FRI = (date(2026, 8, d) for d in range(17, 22))
NEXT_MON = date(2026, 8, 24)

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


# --- Synthetic data helpers (shared with the CLI tests) ----------------------------


def et_utc(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=EASTERN).astimezone(
        UTC
    )


def session_rows(day: date, *, base: Decimal = Decimal("500.00")) -> list[StoredBarRow]:
    """78 deterministic, valid, grid-aligned regular-session rows for ``day``."""
    rows = []
    start = et_utc(day, 9, 30)
    for i in range(78):
        open_ = base + Decimal(i % 7) * Decimal("0.10")
        close = open_ + (Decimal("0.05") if i % 2 == 0 else Decimal("-0.05"))
        rows.append(
            StoredBarRow(
                timestamp_utc=start + timedelta(minutes=5 * i),
                open=open_,
                high=max(open_, close) + Decimal("0.03"),
                low=min(open_, close) - Decimal("0.03"),
                close=close,
                volume=1000 + i,
            )
        )
    return rows


def extended_hours_rows(day: date) -> list[StoredBarRow]:
    return [
        StoredBarRow(
            timestamp_utc=et_utc(day, hour, minute),
            open=Decimal("1.00"),
            high=Decimal("9.00"),
            low=Decimal("1.00"),
            close=Decimal("2.00"),
            volume=5,
        )
        for hour, minute in ((8, 0), (9, 25), (16, 0), (17, 30))
    ]


def isolated_settings(tmp_path: Path) -> Settings:
    return Settings(project_data_path=tmp_path / "data", _env_file=tmp_path / "missing.env")


def initialized_settings(tmp_path: Path) -> Settings:
    settings = isolated_settings(tmp_path)
    DuckDBManager(settings=settings).initialize()
    return settings


def insert_rows(
    settings: Settings,
    rows: list[StoredBarRow],
    *,
    provider: str = "alpaca",
    symbol: str = "SPY",
    timeframe: str = "5Min",
    feed: str = "iex",
    adjustment: str = "raw",
    currency: str = "USD",
) -> None:
    path = DuckDBManager(settings=settings).database_path
    stamp = datetime(2026, 8, 22, 0, 0)
    connection = duckdb.connect(str(path))
    try:
        for row in rows:
            connection.execute(
                "INSERT INTO market_bars (provider, symbol, timeframe, feed, adjustment, "
                "currency, bar_timestamp, open, high, low, close, volume, trade_count, vwap, "
                "retrieved_at, first_ingested_at, last_seen_at, ingestion_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, 'synthetic')",
                [
                    provider,
                    symbol,
                    timeframe,
                    feed,
                    adjustment,
                    currency,
                    row.timestamp_utc.astimezone(UTC).replace(tzinfo=None),
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    row.volume,
                    stamp,
                    stamp,
                    stamp,
                ],
            )
    finally:
        connection.close()


def seeded_week_settings(tmp_path: Path) -> Settings:
    settings = initialized_settings(tmp_path)
    rows: list[StoredBarRow] = []
    for day in (MON, TUE, WED, THU, FRI):
        rows += session_rows(day) + extended_hours_rows(day)
    insert_rows(settings, rows)
    return settings


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- Pure assembly ---------------------------------------------------------------------


def test_complete_week_yields_five_sessions_with_honest_context():
    rows = [r for day in (MON, TUE, WED, THU, FRI) for r in session_rows(day)]
    result = assemble_evaluation_input(rows, start_date=MON, end_date=FRI)

    assert isinstance(result.evaluation_input, SpyVwapReversionEvaluationInput)
    sessions = result.evaluation_input.sessions
    assert [s.session_date for s in sessions] == [MON, TUE, WED, THU, FRI]
    for session in sessions:
        assert len(session.bars) == 78
        assert session.same_time_historical_volume_baseline is None
        assert session.catalyst_state == CatalystState.UNKNOWN
        assert session.breadth_state == BreadthState.UNAVAILABLE
    assert result.summary.included_session_count == 5
    assert all(count == 0 for count in result.summary.excluded_session_counts.values())


def test_prior_day_comes_only_from_the_actual_preceding_session():
    rows = session_rows(MON, base=Decimal("400")) + session_rows(TUE, base=Decimal("600"))
    result = assemble_evaluation_input(rows, start_date=MON, end_date=TUE)

    monday, tuesday = result.evaluation_input.sessions
    assert monday.prior_day is None
    mon_rows = session_rows(MON, base=Decimal("400"))
    assert tuesday.prior_day.high == max(r.high for r in mon_rows)
    assert tuesday.prior_day.low == min(r.low for r in mon_rows)
    assert tuesday.prior_day.close == mon_rows[-1].close
    assert result.summary.prior_day_counts == {
        PriorDayStatus.AVAILABLE: 1,
        PriorDayStatus.PREVIOUS_WEEKDAY_NOT_STORED: 1,
        PriorDayStatus.PREVIOUS_WEEKDAY_SESSION_EXCLUDED: 0,
    }


def test_predecessor_before_start_date_supplies_prior_day_but_is_not_emitted():
    rows = session_rows(MON) + session_rows(TUE)
    result = assemble_evaluation_input(rows, start_date=TUE, end_date=TUE)

    (tuesday,) = result.evaluation_input.sessions
    assert tuesday.session_date == TUE
    assert tuesday.prior_day is not None
    assert result.summary.weekdays_in_range == 1


def test_monday_uses_previous_friday_across_the_weekend():
    rows = session_rows(FRI) + session_rows(NEXT_MON)
    result = assemble_evaluation_input(rows, start_date=NEXT_MON, end_date=NEXT_MON)
    assert result.evaluation_input.sessions[0].prior_day is not None
    assert previous_weekday(NEXT_MON) == FRI


def test_missing_weekday_is_never_skipped_over_for_prior_day():
    rows = session_rows(MON) + session_rows(WED)
    result = assemble_evaluation_input(rows, start_date=MON, end_date=WED)

    assert [s.session_date for s in result.evaluation_input.sessions] == [MON, WED]
    assert result.evaluation_input.sessions[1].prior_day is None
    counts = result.summary.excluded_session_counts
    assert counts[SessionExclusionReason.NO_REGULAR_SESSION_BARS] == 1
    assert result.summary.prior_day_counts[PriorDayStatus.PREVIOUS_WEEKDAY_NOT_STORED] == 2


def test_excluded_predecessor_gives_no_prior_day():
    rows = session_rows(MON)[:-1] + session_rows(TUE)
    result = assemble_evaluation_input(rows, start_date=MON, end_date=TUE)

    (tuesday,) = result.evaluation_input.sessions
    assert tuesday.prior_day is None
    assert result.summary.excluded_session_counts[SessionExclusionReason.INCOMPLETE_SESSION] == 1
    assert (
        result.summary.prior_day_counts[PriorDayStatus.PREVIOUS_WEEKDAY_SESSION_EXCLUDED] == 1
    )


def test_extended_hours_bars_are_dropped_and_only_counted():
    rows = session_rows(MON) + extended_hours_rows(MON)
    result = assemble_evaluation_input(rows, start_date=MON, end_date=MON)

    (monday,) = result.evaluation_input.sessions
    assert len(monday.bars) == 78
    assert result.summary.outside_regular_session_bar_count == 4


def _replace(rows: list[StoredBarRow], index: int, **changes) -> list[StoredBarRow]:
    original = rows[index]
    fields = {
        "timestamp_utc": original.timestamp_utc,
        "open": original.open,
        "high": original.high,
        "low": original.low,
        "close": original.close,
        "volume": original.volume,
    }
    fields.update(changes)
    return rows[:index] + [StoredBarRow(**fields)] + rows[index + 1 :]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda rows: rows[:40] + rows[41:], SessionExclusionReason.INCOMPLETE_SESSION),
        (
            lambda rows: _replace(rows, 10, timestamp_utc=rows[10].timestamp_utc
                                  + timedelta(minutes=2)),
            SessionExclusionReason.OFF_GRID_BAR,
        ),
        (
            lambda rows: _replace(rows, 10, timestamp_utc=rows[10].timestamp_utc
                                  + timedelta(seconds=30)),
            SessionExclusionReason.OFF_GRID_BAR,
        ),
        (
            lambda rows: _replace(rows, 5, close=None),
            SessionExclusionReason.INVALID_PRICE_OR_VOLUME,
        ),
        (
            lambda rows: _replace(rows, 5, low=Decimal("0")),
            SessionExclusionReason.INVALID_PRICE_OR_VOLUME,
        ),
        (
            lambda rows: _replace(rows, 5, open=Decimal("NaN")),
            SessionExclusionReason.INVALID_PRICE_OR_VOLUME,
        ),
        (lambda rows: _replace(rows, 5, volume=-1), SessionExclusionReason.INVALID_PRICE_OR_VOLUME),
        (
            lambda rows: _replace(rows, 5, high=rows[5].low - Decimal("0.01")),
            SessionExclusionReason.OHLC_INCONSISTENT,
        ),
        (
            lambda rows: _replace(rows, 5, close=rows[5].high + Decimal("0.01")),
            SessionExclusionReason.OHLC_INCONSISTENT,
        ),
        (lambda rows: [], SessionExclusionReason.NO_REGULAR_SESSION_BARS),
    ],
)
def test_defective_session_is_excluded_with_one_fixed_reason(mutate, reason):
    rows = mutate(session_rows(TUE)) + session_rows(WED)
    result = assemble_evaluation_input(rows, start_date=TUE, end_date=WED)

    assert [s.session_date for s in result.evaluation_input.sessions] == [WED]
    counts = result.summary.excluded_session_counts
    assert counts[reason] == 1
    assert sum(counts.values()) == 1


def test_no_qualifying_session_yields_no_input_rather_than_a_fabricated_one():
    result = assemble_evaluation_input(session_rows(MON)[:77], start_date=MON, end_date=MON)
    assert result.evaluation_input is None
    assert result.summary.included_session_count == 0
    assert result.summary.first_included_session is None


def test_weekend_only_range_counts_no_weekdays():
    result = assemble_evaluation_input([], start_date=date(2026, 8, 22), end_date=date(2026, 8, 23))
    assert result.evaluation_input is None
    assert result.summary.weekdays_in_range == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [(TUE, MON), (date(2025, 1, 1), date(2026, 1, 2))],
)
def test_invalid_date_range_is_rejected(start, end):
    with pytest.raises(SpyVwapInputValidationError):
        assemble_evaluation_input([], start_date=start, end_date=end)


def test_output_is_byte_stable_and_round_trips():
    rows = [r for day in (MON, TUE, WED) for r in session_rows(day)]
    first = input_to_json_str(
        assemble_evaluation_input(rows, start_date=MON, end_date=WED).evaluation_input
    )
    second = input_to_json_str(
        assemble_evaluation_input(list(reversed(rows)), start_date=MON, end_date=WED)
        .evaluation_input
    )
    assert first == second
    assert input_to_json_str(input_from_json_str(first)) == first


def test_summary_contains_no_prices_or_bar_contents():
    rows = session_rows(MON, base=Decimal("123.45")) + extended_hours_rows(MON)
    summary = assemble_evaluation_input(rows, start_date=MON, end_date=MON).summary.to_dict()
    text = str(summary)
    assert "123.45" not in text
    assert "13:30" not in text
    assert set(summary["excluded_session_counts"]) == {r.value for r in SessionExclusionReason}
    assert summary["point_in_time_context"] == {
        "same_time_historical_volume_baseline": None,
        "catalyst_state": "unknown",
        "breadth_state": "unavailable",
    }


# --- Read-only storage access ---------------------------------------------------------


def test_build_from_synthetic_database(tmp_path):
    settings = seeded_week_settings(tmp_path)
    result = build_spy_vwap_reversion_input(start_date=MON, end_date=FRI, settings=settings)

    assert [s.session_date for s in result.evaluation_input.sessions] == [MON, TUE, WED, THU, FRI]
    assert result.summary.outside_regular_session_bar_count == 20
    assert result.summary.prior_day_counts[PriorDayStatus.AVAILABLE] == 4

    pure = assemble_evaluation_input(
        [r for day in (MON, TUE, WED, THU, FRI) for r in session_rows(day)],
        start_date=MON,
        end_date=FRI,
    )
    # Stored DECIMAL(18,6) values keep their stored scale, so compare by value.
    assert result.evaluation_input.model_dump() == pure.evaluation_input.model_dump()


def test_other_provenance_rows_are_never_mixed_in(tmp_path):
    settings = seeded_week_settings(tmp_path)
    baseline = input_to_json_str(
        build_spy_vwap_reversion_input(start_date=MON, end_date=FRI, settings=settings)
        .evaluation_input
    )
    decoys = [
        r
        for day in (MON, TUE, WED, THU, FRI)
        for r in session_rows(day, base=Decimal("900"))
    ]
    for override in (
        {"provider": "other"},
        {"symbol": "QQQ"},
        {"timeframe": "1Min"},
        {"feed": "sip"},
        {"adjustment": "all"},
        {"currency": "EUR"},
    ):
        insert_rows(settings, decoys, **override)

    after = input_to_json_str(
        build_spy_vwap_reversion_input(start_date=MON, end_date=FRI, settings=settings)
        .evaluation_input
    )
    assert after == baseline


def test_other_provenance_cannot_fill_a_gap(tmp_path):
    settings = initialized_settings(tmp_path)
    insert_rows(settings, session_rows(MON)[:-1])
    insert_rows(settings, session_rows(MON)[-1:], feed="sip")
    result = build_spy_vwap_reversion_input(start_date=MON, end_date=MON, settings=settings)
    assert result.evaluation_input is None
    assert result.summary.excluded_session_counts[SessionExclusionReason.INCOMPLETE_SESSION] == 1


def test_database_file_is_not_modified(tmp_path):
    settings = seeded_week_settings(tmp_path)
    path = DuckDBManager(settings=settings).database_path
    before = file_digest(path)
    build_spy_vwap_reversion_input(start_date=MON, end_date=FRI, settings=settings)
    assert file_digest(path) == before
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_missing_database_is_an_error_and_is_not_created(tmp_path):
    settings = isolated_settings(tmp_path)
    (tmp_path / "data").mkdir()
    with pytest.raises(SpyVwapInputBuildError):
        build_spy_vwap_reversion_input(start_date=MON, end_date=FRI, settings=settings)
    assert not DuckDBManager(settings=settings).database_path.exists()


def test_database_without_market_bars_is_an_error(tmp_path):
    settings = isolated_settings(tmp_path)
    (tmp_path / "data").mkdir()
    duckdb.connect(str(DuckDBManager(settings=settings).database_path)).close()
    with pytest.raises(SpyVwapInputBuildError) as excinfo:
        build_spy_vwap_reversion_input(start_date=MON, end_date=FRI, settings=settings)
    assert str(tmp_path) not in str(excinfo.value)


# --- Provenance constants and import boundary ------------------------------------------


def test_provenance_constants_match_the_stored_dataset():
    from market_intelligence.data_connectors import alpaca_bars
    from market_intelligence.storage import bar_repository

    assert builder.PROVIDER == bar_repository.DEFAULT_PROVIDER
    assert builder.FEED == alpaca_bars.DATA_FEED
    assert builder.ADJUSTMENT == alpaca_bars.DATA_ADJUSTMENT
    assert builder.CURRENCY == alpaca_bars.DATA_CURRENCY
    assert (builder.SYMBOL, builder.TIMEFRAME) == ("SPY", "5Min")


_FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "http",
    "market_intelligence.data_connectors",
    "market_intelligence.model_clients",
    "market_intelligence.agents",
    "market_intelligence.orchestration",
    "market_intelligence.storage.bar_repository",
)


@pytest.mark.parametrize(
    "path",
    [
        BUILDER_PATH,
        REPO_ROOT / "scripts" / "build_spy_vwap_reversion_input.py",
    ],
    ids=lambda p: p.name,
)
def test_builder_and_cli_import_no_network_or_provider_module(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    # The CLI imports the builder itself; every other orchestration module
    # (e.g. the live, provider-calling capture coordinator) stays forbidden.
    names.discard("market_intelligence.orchestration.spy_vwap_reversion_input_builder")
    for name in names:
        for prefix in _FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), name


def test_builder_issues_no_write_sql():
    source = BUILDER_PATH.read_text(encoding="utf-8")
    for keyword in ("INSERT", "UPDATE ", "DELETE", "CREATE ", "ALTER ", "DROP ", "initialize("):
        assert keyword not in source
    assert "read_only=True" in source
