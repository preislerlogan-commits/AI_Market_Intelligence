"""Tests for market_intelligence.orchestration.contracts.

These tests never touch the network, a database, or Settings -- job
contracts are pure, strictly validated data.
"""

from __future__ import annotations

import pytest

from market_intelligence.orchestration.contracts import (
    JOB_TYPE_ALPACA_BARS,
    JOB_TYPE_ALPACA_NEWS,
    JOB_TYPE_FRED_OBSERVATIONS,
    AlpacaBarsJobParams,
    AlpacaNewsJobParams,
    FredObservationsJobParams,
    JobContract,
    JobContractValidationError,
)


def make_news_params(**overrides) -> AlpacaNewsJobParams:
    defaults = dict(symbol="SPY", limit=10)
    defaults.update(overrides)
    return AlpacaNewsJobParams(**defaults)


def make_bars_params(**overrides) -> AlpacaBarsJobParams:
    defaults = dict(
        symbol="SPY",
        timeframe="5Min",
        lookback_days=5,
        limit=500,
        max_pages=1,
        feed="iex",
        adjustment="raw",
        currency="USD",
    )
    defaults.update(overrides)
    return AlpacaBarsJobParams(**defaults)


def make_fred_params(**overrides) -> FredObservationsJobParams:
    defaults = dict(series_id="FEDFUNDS", lookback_days=90, limit=1000, max_pages=1)
    defaults.update(overrides)
    return FredObservationsJobParams(**defaults)


def make_contract(**overrides) -> JobContract:
    defaults = dict(
        job_id="alpaca_news_spy",
        job_type=JOB_TYPE_ALPACA_NEWS,
        enabled=True,
        provider="alpaca",
        dataset_name="news",
        params=make_news_params(),
    )
    defaults.update(overrides)
    return JobContract(**defaults)


# --- AlpacaNewsJobParams -------------------------------------------------------


def test_valid_news_params_accepted():
    params = make_news_params()
    assert params.symbol == "SPY"
    assert params.limit == 10


def test_news_params_rejects_unnormalized_symbol():
    with pytest.raises(JobContractValidationError):
        make_news_params(symbol="spy")


def test_news_params_rejects_invalid_symbol():
    with pytest.raises(JobContractValidationError):
        make_news_params(symbol="not a symbol!!")


@pytest.mark.parametrize("limit", [0, 51, -1, "10", True])
def test_news_params_rejects_out_of_bounds_limit(limit):
    with pytest.raises(JobContractValidationError):
        make_news_params(limit=limit)


def test_news_params_is_frozen():
    params = make_news_params()
    with pytest.raises(AttributeError):
        params.symbol = "QQQ"


# --- AlpacaBarsJobParams --------------------------------------------------------


def test_valid_bars_params_accepted():
    params = make_bars_params()
    assert params.timeframe == "5Min"
    assert params.feed == "iex"


def test_bars_params_rejects_unnormalized_timeframe():
    with pytest.raises(JobContractValidationError):
        make_bars_params(timeframe="5min")


def test_bars_params_rejects_unapproved_timeframe():
    with pytest.raises(JobContractValidationError):
        make_bars_params(timeframe="1Hour")


def test_bars_params_rejects_wrong_feed():
    with pytest.raises(JobContractValidationError):
        make_bars_params(feed="sip")


def test_bars_params_rejects_wrong_adjustment():
    with pytest.raises(JobContractValidationError):
        make_bars_params(adjustment="split")


def test_bars_params_rejects_wrong_currency():
    with pytest.raises(JobContractValidationError):
        make_bars_params(currency="EUR")


@pytest.mark.parametrize("limit", [0, 501, -1])
def test_bars_params_rejects_out_of_bounds_limit(limit):
    with pytest.raises(JobContractValidationError):
        make_bars_params(limit=limit)


@pytest.mark.parametrize("lookback_days", [0, 31, -1])
def test_bars_params_rejects_out_of_bounds_lookback(lookback_days):
    with pytest.raises(JobContractValidationError):
        make_bars_params(lookback_days=lookback_days)


@pytest.mark.parametrize("max_pages", [0, 4, -1])
def test_bars_params_rejects_out_of_bounds_max_pages(max_pages):
    with pytest.raises(JobContractValidationError):
        make_bars_params(max_pages=max_pages)


def test_bars_params_is_frozen():
    params = make_bars_params()
    with pytest.raises(AttributeError):
        params.feed = "sip"


# --- FredObservationsJobParams --------------------------------------------------


def test_valid_fred_params_accepted():
    params = make_fred_params()
    assert params.series_id == "FEDFUNDS"


def test_fred_params_rejects_unnormalized_series_id():
    with pytest.raises(JobContractValidationError):
        make_fred_params(series_id="fedfunds")


def test_fred_params_rejects_invalid_series_id():
    with pytest.raises(JobContractValidationError):
        make_fred_params(series_id="../../etc/passwd")


@pytest.mark.parametrize("limit", [0, 1001, -1])
def test_fred_params_rejects_out_of_bounds_limit(limit):
    with pytest.raises(JobContractValidationError):
        make_fred_params(limit=limit)


@pytest.mark.parametrize("lookback_days", [0, 181, -1])
def test_fred_params_rejects_out_of_bounds_lookback(lookback_days):
    with pytest.raises(JobContractValidationError):
        make_fred_params(lookback_days=lookback_days)


@pytest.mark.parametrize("max_pages", [0, 4])
def test_fred_params_rejects_out_of_bounds_max_pages(max_pages):
    with pytest.raises(JobContractValidationError):
        make_fred_params(max_pages=max_pages)


# --- JobContract -----------------------------------------------------------------


def test_valid_news_contract_accepted():
    contract = make_contract()
    assert contract.job_id == "alpaca_news_spy"


def test_valid_bars_contract_accepted():
    contract = make_contract(
        job_id="alpaca_bars_spy_5min",
        job_type=JOB_TYPE_ALPACA_BARS,
        dataset_name="bars",
        params=make_bars_params(),
    )
    assert contract.job_type == JOB_TYPE_ALPACA_BARS


def test_valid_fred_contract_accepted():
    contract = make_contract(
        job_id="fred_fedfunds_observations",
        job_type=JOB_TYPE_FRED_OBSERVATIONS,
        provider="fred",
        dataset_name="macro_observations",
        params=make_fred_params(),
    )
    assert contract.job_type == JOB_TYPE_FRED_OBSERVATIONS


@pytest.mark.parametrize(
    "job_id",
    ["", "   ", "Has-Upper", "has space", "has.dot", "a" * 65, "../etc/passwd", "DROP TABLE x"],
)
def test_contract_rejects_malformed_job_id(job_id):
    with pytest.raises(JobContractValidationError):
        make_contract(job_id=job_id)


def test_contract_rejects_unreviewed_job_type():
    with pytest.raises(JobContractValidationError):
        make_contract(job_type="robinhood_orders")


def test_contract_rejects_non_bool_enabled():
    with pytest.raises(JobContractValidationError):
        make_contract(enabled="true")


def test_contract_rejects_provider_mismatch():
    with pytest.raises(JobContractValidationError):
        make_contract(provider="robinhood")


def test_contract_rejects_unreviewed_provider_even_if_plausible():
    with pytest.raises(JobContractValidationError):
        make_contract(provider="fred")  # alpaca_news must be "alpaca"


def test_contract_rejects_dataset_name_mismatch():
    with pytest.raises(JobContractValidationError):
        make_contract(dataset_name="orders")


def test_contract_rejects_params_type_mismatch():
    with pytest.raises(JobContractValidationError):
        make_contract(params=make_bars_params())


def test_contract_is_frozen():
    contract = make_contract()
    with pytest.raises(AttributeError):
        contract.enabled = False


# --- schema has no field for disallowed concepts --------------------------------


def test_contract_has_no_field_for_disallowed_concepts():
    """The contract schema itself has no place to express any of these."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(JobContract)}
    disallowed_substrings = [
        "import",
        "shell",
        "command",
        "exec",
        "path",
        "sql",
        "url",
        "host",
        "order",
        "account",
        "execution",
        "robinhood",
    ]
    for field_name in field_names:
        for substring in disallowed_substrings:
            assert substring not in field_name.lower()
