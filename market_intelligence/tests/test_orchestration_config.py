"""Tests for market_intelligence.orchestration.config.

These tests never touch the network, a database, or Settings -- config
loading is pure, file-based JSON parsing plus contract validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_intelligence.orchestration.config import (
    DEFAULT_JOBS_CONFIG_PATH,
    JobConfigError,
    load_job_contracts,
)

VALID_NEWS_JOB = {
    "job_id": "alpaca_news_spy",
    "job_type": "alpaca_news",
    "enabled": True,
    "provider": "alpaca",
    "dataset_name": "news",
    "params": {"symbol": "SPY", "limit": 10},
}

VALID_BARS_JOB = {
    "job_id": "alpaca_bars_spy_5min",
    "job_type": "alpaca_bars",
    "enabled": True,
    "provider": "alpaca",
    "dataset_name": "bars",
    "params": {
        "symbol": "SPY",
        "timeframe": "5Min",
        "lookback_days": 5,
        "limit": 500,
        "max_pages": 1,
        "feed": "iex",
        "adjustment": "raw",
        "currency": "USD",
    },
}

VALID_FRED_JOB = {
    "job_id": "fred_fedfunds_observations",
    "job_type": "fred_observations",
    "enabled": True,
    "provider": "fred",
    "dataset_name": "macro_observations",
    "params": {"series_id": "FEDFUNDS", "lookback_days": 90, "limit": 1000, "max_pages": 1},
}


def write_config(tmp_path: Path, jobs: list[dict]) -> Path:
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    return path


# --- committed config file loads cleanly ----------------------------------------


def test_default_committed_config_loads_and_validates():
    contracts = load_job_contracts()
    assert len(contracts) == 3
    job_ids = {c.job_id for c in contracts}
    assert job_ids == {"alpaca_news_spy", "alpaca_bars_spy_5min", "fred_fedfunds_observations"}


def test_default_committed_config_path_exists():
    assert DEFAULT_JOBS_CONFIG_PATH.exists()


def test_all_committed_jobs_enabled():
    contracts = load_job_contracts()
    assert all(contract.enabled for contract in contracts)


# --- deterministic ordering ------------------------------------------------------


def test_contracts_are_sorted_by_job_id_regardless_of_file_order(tmp_path):
    path = write_config(tmp_path, [VALID_FRED_JOB, VALID_NEWS_JOB, VALID_BARS_JOB])
    contracts = load_job_contracts(path)
    assert [c.job_id for c in contracts] == [
        "alpaca_bars_spy_5min",
        "alpaca_news_spy",
        "fred_fedfunds_observations",
    ]


def test_loading_same_config_twice_yields_identical_order(tmp_path):
    path = write_config(tmp_path, [VALID_BARS_JOB, VALID_NEWS_JOB])
    first = load_job_contracts(path)
    second = load_job_contracts(path)
    assert [c.job_id for c in first] == [c.job_id for c in second]


# --- duplicate job_id ------------------------------------------------------------


def test_duplicate_job_id_is_rejected(tmp_path):
    path = write_config(tmp_path, [VALID_NEWS_JOB, dict(VALID_NEWS_JOB)])
    with pytest.raises(JobConfigError, match="Duplicate"):
        load_job_contracts(path)


# --- malformed config --------------------------------------------------------------


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(JobConfigError):
        load_job_contracts(tmp_path / "does_not_exist.json")


def test_invalid_json_is_rejected(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(JobConfigError):
        load_job_contracts(path)


def test_non_object_top_level_is_rejected(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(JobConfigError):
        load_job_contracts(path)


def test_missing_jobs_key_is_rejected(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"not_jobs": []}), encoding="utf-8")
    with pytest.raises(JobConfigError):
        load_job_contracts(path)


def test_job_entry_missing_required_field_is_rejected(tmp_path):
    broken = dict(VALID_NEWS_JOB)
    del broken["provider"]
    path = write_config(tmp_path, [broken])
    with pytest.raises(JobConfigError, match="missing required field"):
        load_job_contracts(path)


def test_unknown_job_type_is_rejected(tmp_path):
    broken = dict(VALID_NEWS_JOB)
    broken["job_type"] = "robinhood_orders"
    path = write_config(tmp_path, [broken])
    with pytest.raises(JobConfigError):
        load_job_contracts(path)


def test_params_missing_field_is_rejected(tmp_path):
    broken = json.loads(json.dumps(VALID_BARS_JOB))
    del broken["params"]["feed"]
    path = write_config(tmp_path, [broken])
    with pytest.raises(JobConfigError, match="Missing required param field"):
        load_job_contracts(path)


def test_non_dict_params_is_rejected(tmp_path):
    broken = dict(VALID_NEWS_JOB)
    broken["params"] = "not-a-dict"
    path = write_config(tmp_path, [broken])
    with pytest.raises(JobConfigError):
        load_job_contracts(path)


def test_invalid_contract_field_is_rejected(tmp_path):
    broken = dict(VALID_NEWS_JOB)
    broken["job_id"] = "Not Valid!"
    path = write_config(tmp_path, [broken])
    with pytest.raises(JobConfigError):
        load_job_contracts(path)


def test_empty_jobs_list_is_accepted(tmp_path):
    path = write_config(tmp_path, [])
    assert load_job_contracts(path) == ()
