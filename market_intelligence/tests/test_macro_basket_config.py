"""Tests for market_intelligence/config/macro_basket.py and the committed
market_intelligence/config/core_macro_series.json Core Macro Basket
configuration.

Loading this configuration must never perform network I/O or touch
``Settings``, a client, a database connection, or a lock -- these tests
never construct any of those. Repetitive per-field/per-series invalid-value
checks are grouped into single test functions (looping over cases
internally) rather than heavily parametrized, to keep the overall test
count focused while still exercising every case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_intelligence.config.macro_basket import (
    APPROVED_SERIES_CATEGORY,
    APPROVED_SERIES_IDS,
    DEFAULT_CORE_MACRO_SERIES_CONFIG_PATH,
    MAX_LOOKBACK_DAYS_BY_SERIES_ID,
    MAX_RECENT_OBSERVATIONS_LIMIT,
    MIN_RECENT_OBSERVATIONS_LIMIT,
    CoreMacroSeriesConfigError,
    load_core_macro_series,
)


def _valid_entry(**overrides) -> dict:
    entry = {
        "series_id": "FEDFUNDS",
        "category": "policy_rate",
        "enabled": True,
        "observation_lookback_days": 400,
        "recent_observations_limit": 6,
    }
    entry.update(overrides)
    return entry


def _write_config(
    tmp_path: Path, entries: list[dict], *, extra_root_fields: dict | None = None
) -> Path:
    payload: dict = {"series": entries}
    if extra_root_fields:
        payload.update(extra_root_fields)
    path = tmp_path / "core_macro_series.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _assert_rejected(tmp_path: Path, entries: list[dict], **kwargs) -> None:
    path = _write_config(tmp_path, entries, **kwargs)
    with pytest.raises(CoreMacroSeriesConfigError):
        load_core_macro_series(path)


# --- the real committed configuration file ------------------------------------------


def test_committed_config_file_loads_successfully():
    configs = load_core_macro_series()
    assert len(configs) == 7


def test_committed_config_contains_exactly_the_seven_approved_series():
    configs = load_core_macro_series()
    series_ids = {config.series_id for config in configs}
    assert series_ids == set(APPROVED_SERIES_IDS)
    assert series_ids == {
        "FEDFUNDS",
        "GS10",
        "CPIAUCSL",
        "PCEPI",
        "UNRATE",
        "INDPRO",
        "GDPC1",
    }


def test_committed_config_every_series_enabled_category_and_lookback_bounds():
    by_id = {config.series_id: config for config in load_core_macro_series()}
    for series_id, config in by_id.items():
        assert config.enabled is True
        assert config.category == APPROVED_SERIES_CATEGORY[series_id]
        assert 1 <= config.observation_lookback_days <= MAX_LOOKBACK_DAYS_BY_SERIES_ID[series_id]
        assert MIN_RECENT_OBSERVATIONS_LIMIT <= config.recent_observations_limit
        assert config.recent_observations_limit <= MAX_RECENT_OBSERVATIONS_LIMIT

    # Exact conservative lookbacks per docs/CORE_MACRO_BASKET.md.
    for series_id in ("FEDFUNDS", "CPIAUCSL", "PCEPI", "UNRATE", "INDPRO", "GS10"):
        assert by_id[series_id].observation_lookback_days == 400
    assert by_id["GDPC1"].observation_lookback_days == 1100


def test_committed_config_path_points_at_real_committed_file():
    assert DEFAULT_CORE_MACRO_SERIES_CONFIG_PATH.name == "core_macro_series.json"
    assert DEFAULT_CORE_MACRO_SERIES_CONFIG_PATH.exists()


def test_loading_config_twice_is_stable_and_side_effect_free():
    assert load_core_macro_series() == load_core_macro_series()


# --- root/entry field shape -----------------------------------------------------------


def test_unknown_root_field_rejected(tmp_path):
    _assert_rejected(tmp_path, [_valid_entry()], extra_root_fields={"extra": 1})


def test_missing_series_root_key_rejected(tmp_path):
    path = tmp_path / "core_macro_series.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(CoreMacroSeriesConfigError):
        load_core_macro_series(path)


def test_unknown_entry_field_rejected(tmp_path):
    entry = _valid_entry()
    entry["notes"] = "unexpected"
    _assert_rejected(tmp_path, [entry])


def test_missing_required_entry_field_rejected(tmp_path):
    for field in (
        "series_id",
        "category",
        "enabled",
        "observation_lookback_days",
        "recent_observations_limit",
    ):
        entry = _valid_entry()
        del entry[field]
        _assert_rejected(tmp_path, [entry])


def test_malformed_json_rejected(tmp_path):
    path = tmp_path / "core_macro_series.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CoreMacroSeriesConfigError):
        load_core_macro_series(path)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(CoreMacroSeriesConfigError):
        load_core_macro_series(tmp_path / "does-not-exist.json")


# --- series ID validation ------------------------------------------------------------


def test_duplicate_series_id_rejected(tmp_path):
    _assert_rejected(tmp_path, [_valid_entry(), _valid_entry()])


def test_non_approved_series_id_rejected(tmp_path):
    _assert_rejected(tmp_path, [_valid_entry(series_id="CPILFESL", category="inflation")])


def test_unnormalized_series_id_rejected(tmp_path):
    _assert_rejected(tmp_path, [_valid_entry(series_id="fedfunds")])


def test_malformed_series_id_rejected(tmp_path):
    _assert_rejected(tmp_path, [_valid_entry(series_id="not a valid series!!")])


# --- category enforcement -------------------------------------------------------------


def test_category_mismatch_for_approved_series_rejected(tmp_path):
    _assert_rejected(tmp_path, [_valid_entry(series_id="FEDFUNDS", category="inflation")])


def test_unknown_category_string_rejected(tmp_path):
    _assert_rejected(tmp_path, [_valid_entry(category="not_a_real_category")])


def test_every_approved_series_accepts_only_its_own_fixed_category(tmp_path):
    for series_id, correct_category in APPROVED_SERIES_CATEGORY.items():
        entry = _valid_entry(
            series_id=series_id,
            category=correct_category,
            observation_lookback_days=min(30, MAX_LOOKBACK_DAYS_BY_SERIES_ID[series_id]),
        )
        configs = load_core_macro_series(_write_config(tmp_path, [entry]))
        assert configs[0].series_id == series_id
        assert configs[0].category == correct_category

        # A different, otherwise-valid category is rejected for this series.
        for other_category in APPROVED_SERIES_CATEGORY.values():
            if other_category == correct_category:
                continue
            _assert_rejected(tmp_path, [entry | {"category": other_category}])
            break


# --- enabled validation ----------------------------------------------------------------


def test_non_boolean_enabled_rejected(tmp_path):
    for bad_value in (1, 0, "true", None, "yes"):
        _assert_rejected(tmp_path, [_valid_entry(enabled=bad_value)])


# --- observation_lookback_days validation ----------------------------------------------


def test_lookback_days_rejects_non_plain_int_or_out_of_bounds(tmp_path):
    for bad_value in (True, False, "400", 400.0, None, 0, -10):
        _assert_rejected(tmp_path, [_valid_entry(observation_lookback_days=bad_value)])


def test_lookback_days_per_series_ceiling_enforced(tmp_path):
    for series_id, ceiling in MAX_LOOKBACK_DAYS_BY_SERIES_ID.items():
        category = APPROVED_SERIES_CATEGORY[series_id]

        # One over the ceiling is rejected.
        _assert_rejected(
            tmp_path,
            [
                _valid_entry(
                    series_id=series_id,
                    category=category,
                    observation_lookback_days=ceiling + 1,
                )
            ],
        )

        # Exactly at the ceiling is accepted.
        configs = load_core_macro_series(
            _write_config(
                tmp_path,
                [
                    _valid_entry(
                        series_id=series_id,
                        category=category,
                        observation_lookback_days=ceiling,
                    )
                ],
            )
        )
        assert configs[0].observation_lookback_days == ceiling


# --- recent_observations_limit validation -----------------------------------------------


def test_recent_observations_limit_rejects_non_plain_int_or_out_of_bounds(tmp_path):
    for bad_value in (
        True,
        False,
        "6",
        6.0,
        None,
        MIN_RECENT_OBSERVATIONS_LIMIT - 1,
        MAX_RECENT_OBSERVATIONS_LIMIT + 1,
    ):
        _assert_rejected(tmp_path, [_valid_entry(recent_observations_limit=bad_value)])


# --- deterministic ordering -------------------------------------------------------------


def test_entries_preserve_committed_file_order_not_sorted(tmp_path):
    entries = [
        _valid_entry(series_id="GDPC1", category="growth", observation_lookback_days=1100),
        _valid_entry(series_id="FEDFUNDS", category="policy_rate"),
        _valid_entry(series_id="GS10", category="long_term_rate", observation_lookback_days=400),
    ]
    configs = load_core_macro_series(_write_config(tmp_path, entries))
    assert [config.series_id for config in configs] == ["GDPC1", "FEDFUNDS", "GS10"]
