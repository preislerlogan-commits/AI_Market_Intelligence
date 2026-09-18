"""Tests for market_intelligence/contract_selection/serialization.py.

Mirrors test_spy_vwap_reversion_serialization.py's coverage: round trip,
no-overwrite, symlink refusal, bounded read, parent-must-exist, and
sanitized errors.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    ContractSelectorResult,
    FeedProvenance,
    OptionChainBatch,
    OptionContractQuote,
    OptionType,
    RejectionReason,
    SelectorConfig,
    SelectorConfigSnapshot,
    SelectorStatus,
)
from market_intelligence.contract_selection.serialization import (
    ContractSelectorSerializationError,
    from_json_str,
    input_from_json_str,
    input_to_json_str,
    read_input,
    read_record,
    to_json_str,
    write_input,
    write_record,
)
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon

AS_OF = datetime(2026, 9, 16, 15, 0, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 9, 16, 14, 58, tzinfo=UTC)


def _quote() -> OptionContractQuote:
    return OptionContractQuote(
        contract_symbol="SPY260916C00680000",
        option_type=OptionType.CALL,
        expiration_date=date(2026, 9, 16),
        strike_price=Decimal("680"),
        bid_price=Decimal("1.00"),
        bid_size=10,
        ask_price=Decimal("1.10"),
        ask_size=10,
        implied_volatility=Decimal("0.2"),
        delta=Decimal("0.40"),
        gamma=Decimal("0.05"),
        theta=Decimal("-0.10"),
        vega=Decimal("0.10"),
        rho=Decimal("0.01"),
    )


def _selector_input() -> ContractSelectorInput:
    batch = OptionChainBatch(
        provider="alpaca", feed=FeedProvenance.OPRA, retrieved_at=RETRIEVED_AT,
        contracts=[_quote()],
    )
    return ContractSelectorInput(
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        regime_as_of_timestamp=RETRIEVED_AT,
        underlying_price_timestamp=RETRIEVED_AT,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        batch=batch,
    )


def _result() -> ContractSelectorResult:
    config = SelectorConfig()
    snapshot = SelectorConfigSnapshot(
        horizon_expiration_windows=dict(config.horizon_expiration_windows),
        min_moneyness=config.min_moneyness,
        max_moneyness=config.max_moneyness,
        min_abs_delta=config.min_abs_delta,
        max_abs_delta=config.max_abs_delta,
        max_abs_spread=config.max_abs_spread,
        max_pct_spread=config.max_pct_spread,
        min_quote_size=config.min_quote_size,
        max_snapshot_age_seconds=config.max_snapshot_age_seconds,
        max_regime_to_price_gap_seconds=config.max_regime_to_price_gap_seconds,
        max_price_to_chain_gap_seconds=config.max_price_to_chain_gap_seconds,
        max_quote_age_seconds=config.max_quote_age_seconds,
        allow_indicative_for_research=config.allow_indicative_for_research,
    )
    return ContractSelectorResult(
        symbol="SPY",
        generated_at=AS_OF,
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        requested_option_type=None,
        regime_as_of_timestamp=RETRIEVED_AT,
        underlying_price_timestamp=RETRIEVED_AT,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        feed=FeedProvenance.OPRA,
        feed_is_live_opra=True,
        retrieved_at=RETRIEVED_AT,
        config_snapshot=snapshot,
        status=SelectorStatus.ELIGIBLE,
        candidate_contract_count=1,
        eligible_contract_count=1,
        research_only_contract_count=0,
        rejection_counts={reason: 0 for reason in RejectionReason},
        eligible_contracts=[_quote()],
        research_only_contracts=[],
        notes=["no_open_interest_available"],
    )


def _symlink_or_skip(src, dst, target_is_directory=False):
    try:
        os.symlink(src, dst, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")


# ---------------------------------------------------------------------------
# Pure round trip
# ---------------------------------------------------------------------------


def test_record_round_trips_through_json():
    record = _result()
    assert from_json_str(to_json_str(record)) == record


def test_input_round_trips_through_json():
    selector_input = _selector_input()
    assert input_from_json_str(input_to_json_str(selector_input)) == selector_input


def test_to_json_str_is_deterministic_and_sorted():
    record = _result()
    first = to_json_str(record)
    second = to_json_str(record)
    assert first == second
    assert first.endswith("\n")


def test_from_json_str_rejects_invalid_json():
    with pytest.raises(ContractSelectorSerializationError):
        from_json_str("not json")


def test_from_json_str_rejects_a_non_object():
    with pytest.raises(ContractSelectorSerializationError):
        from_json_str("[1, 2, 3]")


def test_from_json_str_rejects_schema_violations():
    with pytest.raises(ContractSelectorSerializationError):
        from_json_str('{"symbol": "SPY"}')


def test_input_from_json_str_rejects_invalid_json():
    with pytest.raises(ContractSelectorSerializationError):
        input_from_json_str("not json")


# ---------------------------------------------------------------------------
# write_record / read_record
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path):
    record = _result()
    path = tmp_path / "result.json"
    write_record(record, path)
    assert read_record(path) == record


def test_write_record_refuses_to_overwrite_by_default(tmp_path):
    record = _result()
    path = tmp_path / "result.json"
    write_record(record, path)
    with pytest.raises(ContractSelectorSerializationError):
        write_record(record, path)


def test_write_record_overwrites_when_requested(tmp_path):
    record = _result()
    path = tmp_path / "result.json"
    write_record(record, path)
    write_record(record, path, overwrite=True)
    assert read_record(path) == record


def test_write_record_never_creates_a_directory(tmp_path):
    record = _result()
    path = tmp_path / "missing" / "result.json"
    with pytest.raises(ContractSelectorSerializationError):
        write_record(record, path)
    assert not path.parent.exists()


def test_write_record_refuses_a_symlinked_parent_directory(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    _symlink_or_skip(real_dir, link_dir, target_is_directory=True)
    with pytest.raises(ContractSelectorSerializationError):
        write_record(_result(), link_dir / "result.json")


def test_write_record_refuses_a_symlinked_target_file(tmp_path):
    real_file = tmp_path / "real.json"
    real_file.write_text("sentinel", encoding="utf-8")
    link_file = tmp_path / "link.json"
    _symlink_or_skip(real_file, link_file)
    with pytest.raises(ContractSelectorSerializationError):
        write_record(_result(), link_file)


def test_read_record_refuses_a_missing_file(tmp_path):
    with pytest.raises(ContractSelectorSerializationError):
        read_record(tmp_path / "nope.json")


def test_read_record_refuses_a_directory(tmp_path):
    directory = tmp_path / "adir"
    directory.mkdir()
    with pytest.raises(ContractSelectorSerializationError):
        read_record(directory)


def test_read_record_refuses_an_oversized_file(tmp_path, monkeypatch):
    from market_intelligence.contract_selection import serialization as module

    monkeypatch.setattr(module, "MAX_RECORD_BYTES", 10)
    path = tmp_path / "result.json"
    write_record(_result(), path)
    with pytest.raises(ContractSelectorSerializationError):
        read_record(path)


# ---------------------------------------------------------------------------
# read_input
# ---------------------------------------------------------------------------


def test_read_input_round_trips(tmp_path):
    selector_input = _selector_input()
    path = tmp_path / "input.json"
    path.write_text(input_to_json_str(selector_input), encoding="utf-8")
    assert read_input(path) == selector_input


def test_read_input_refuses_a_missing_file(tmp_path):
    with pytest.raises(ContractSelectorSerializationError):
        read_input(tmp_path / "nope.json")


def test_read_input_refuses_a_symlinked_file(tmp_path):
    real_file = tmp_path / "real.json"
    real_file.write_text(input_to_json_str(_selector_input()), encoding="utf-8")
    link_file = tmp_path / "link.json"
    _symlink_or_skip(real_file, link_file)
    with pytest.raises(ContractSelectorSerializationError):
        read_input(link_file)


def test_read_input_refuses_an_oversized_file(tmp_path, monkeypatch):
    from market_intelligence.contract_selection import serialization as module

    monkeypatch.setattr(module, "MAX_INPUT_BYTES", 10)
    path = tmp_path / "input.json"
    path.write_text(input_to_json_str(_selector_input()), encoding="utf-8")
    with pytest.raises(ContractSelectorSerializationError):
        read_input(path)


def test_read_input_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ContractSelectorSerializationError):
        read_input(path)


# ---------------------------------------------------------------------------
# write_input
# ---------------------------------------------------------------------------


def test_write_input_then_read_round_trips(tmp_path):
    selector_input = _selector_input()
    path = tmp_path / "input.json"
    write_input(selector_input, path)
    assert read_input(path) == selector_input


def test_write_input_refuses_to_overwrite_by_default(tmp_path):
    selector_input = _selector_input()
    path = tmp_path / "input.json"
    write_input(selector_input, path)
    with pytest.raises(ContractSelectorSerializationError):
        write_input(selector_input, path)


def test_write_input_overwrites_when_requested(tmp_path):
    selector_input = _selector_input()
    path = tmp_path / "input.json"
    write_input(selector_input, path)
    write_input(selector_input, path, overwrite=True)
    assert read_input(path) == selector_input


def test_write_input_never_creates_a_directory(tmp_path):
    selector_input = _selector_input()
    path = tmp_path / "missing" / "input.json"
    with pytest.raises(ContractSelectorSerializationError):
        write_input(selector_input, path)
    assert not path.parent.exists()


def test_write_input_refuses_a_symlinked_parent_directory(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    _symlink_or_skip(real_dir, link_dir, target_is_directory=True)
    with pytest.raises(ContractSelectorSerializationError):
        write_input(_selector_input(), link_dir / "input.json")


def test_write_input_refuses_a_symlinked_target_file(tmp_path):
    real_file = tmp_path / "real.json"
    real_file.write_text("sentinel", encoding="utf-8")
    link_file = tmp_path / "link.json"
    _symlink_or_skip(real_file, link_file)
    with pytest.raises(ContractSelectorSerializationError):
        write_input(_selector_input(), link_file)
