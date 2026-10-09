"""Offline Evidence producer adapters: valid output, strict input validation,
timestamps, deterministic identity, lineage, missing evidence, news claim
restrictions, the holdout guard, isolation and bundle compatibility.
Synthetic data and a labelled synthetic test registry only."""

from __future__ import annotations

import ast
import dataclasses
import json
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from market_intelligence.evidence.bundle import RecordedItem, build_bundle, query_request_sha256
from market_intelligence.evidence.contracts import (
    EvidenceConsumerContext,
    EvidenceItem,
    EvidenceQuery,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    BundlePurpose,
    ConsumerId,
    ConsumerPermission,
    EndpointClass,
    EvidenceKind,
    RevisionReason,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.validation import validate_item
from market_intelligence.evidence_adapters import clock as clk
from market_intelligence.evidence_adapters import fred, news
from market_intelligence.evidence_adapters import market_calculations as mc
from market_intelligence.evidence_adapters import market_data as md
from market_intelligence.evidence_adapters.payloads import ADAPTER_PAYLOAD_MODELS
from market_intelligence.market_features.spy_regime_classifier import classify
from market_intelligence.market_features.spy_regime_contracts import PriorDayLevels, Regime
from market_intelligence.market_features.spy_regime_features import compute_features
from market_intelligence.tests import evidence_adapter_fixtures as fx

PACKAGE = Path(md.__file__).parent
SNAP_REF = fx.provider_reference(EndpointClass.ALPACA_STOCK_SNAPSHOT, feed="iex")


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _bar_item(source=None, **ctx):
    source = source or fx.bar()
    return md.adapt_market_bar(
        source, reference=fx.bar_reference(source), context=fx.context(md.BARS_CONFIGURATION, **ctx)
    )


def _snapshot_item(body=None, **kwargs):
    return md.adapt_market_snapshot(
        fx.snapshot() if body is None else body,
        symbol=kwargs.pop("symbol", "SPY"),
        feed=kwargs.pop("feed", "iex"),
        reference=kwargs.pop("reference", SNAP_REF),
        context=fx.context(md.SNAPSHOT_CONFIGURATION, **kwargs),
    )


def _news_item(article=None, **ctx):
    article = article or fx.news_item()
    return news.adapt_news_publication(
        article,
        reference=fx.news_reference(article),
        context=fx.context(news.NEWS_CONFIGURATION, **ctx),
    )


def _observation_item(obs=None, *, metadata=None, supersedes=None, **ctx):
    obs = obs or fx.observation()
    return fred.adapt_fred_observation(
        obs,
        series_metadata=metadata or fx.series_metadata(),
        reference=fx.observation_reference(obs),
        context=fx.context(fred.OBSERVATIONS_CONFIGURATION, **ctx),
        query_sha256=fx.QUERY,
        supersedes=supersedes,
    )


def _metadata_item(meta=None, **ctx):
    meta = meta or fx.series_metadata()
    return fred.adapt_fred_series_metadata(
        meta,
        reference=fx.metadata_reference(meta.series_id),
        context=fx.context(fred.METADATA_CONFIGURATION, **ctx),
    )


def _reading(**overrides):
    fields = dict(
        measured_at_utc=fx.RETRIEVED,
        offset_ms=5,
        last_sync_at_utc=fx.RETRIEVED - timedelta(minutes=2),
    )
    fields.update(overrides)
    return clk.ClockHealthReading(**fields)


def _clock_item(reading=None, **ctx):
    return clk.adapt_clock_health(
        reading or _reading(),
        reference=fx.clock_reference(),
        context=fx.context(clk.CLOCK_CONFIGURATION, **ctx),
    )


def _regime(count=6, **input_overrides):
    bars = fx.session_bars(count)
    items = [_bar_item(b) for b in bars]
    engine_input = fx.engine_input(bars, **input_overrides)
    return bars, items, engine_input, compute_features(engine_input)


def _features_item(features, engine_input, items, **kwargs):
    return mc.adapt_regime_features(
        features,
        engine_input=engine_input,
        bar_items=items,
        context=fx.context(mc.REGIME_CONFIGURATION),
        **kwargs,
    )


def _all_items() -> list[EvidenceItem]:
    _, bar_items, engine_input, features = _regime()
    features_item = _features_item(features, engine_input, bar_items)
    return [
        *bar_items,
        _snapshot_item(),
        _news_item(),
        _observation_item(),
        _metadata_item(),
        _clock_item(),
        features_item,
        mc.adapt_regime_classification(
            classify(features),
            features_item=features_item,
            context=fx.context(mc.REGIME_CONFIGURATION),
        ),
        _observation_item(fx.observation(value=None, is_missing=True)),
        md.missing_market_bars(
            checked_at=fx.RETRIEVED,
            query_sha256=fx.QUERY,
            context=fx.context(md.BARS_CONFIGURATION),
        ),
        news.missing_news(
            checked_at=fx.RETRIEVED,
            query_sha256=fx.QUERY,
            context=fx.context(news.NEWS_CONFIGURATION),
        ),
    ]


# --- Valid output from each adapter ------------------------------------------------------------


def test_market_bar_is_a_confirmed_fact_with_source_fields_only():
    item = _bar_item()
    assert item.evidence_kind is EvidenceKind.CONFIRMED_FACT
    assert item.payload == {
        "adjustment": "raw",
        "bar_end_utc": "2027-01-12T14:35:00.000000Z",
        "bar_start_utc": "2027-01-12T14:30:00.000000Z",
        "close": "500.2",
        "currency": "USD",
        "feed": "iex",
        "high": "500.5",
        "low": "499.8",
        "open": "500",
        "provider": "alpaca",
        "within_regular_hours_window": True,
        "symbol": "SPY",
        "timeframe": "5Min",
        "trade_count": 150,
        "volume": 12000,
        "vwap": "500.15",
    }
    assert item.effective_at_utc == datetime(2027, 1, 12, 14, 35, tzinfo=UTC)  # bar end
    assert [s.subject_id for s in item.subjects] == [
        "instrument:us_equity:SPY",
        "session:us_equity_regular_hours_window:2027-01-12",
    ]
    assert item.freshness_policy_id == "fp.bars_stored_snapshot.v1"  # from the registry
    assert item.quality.uncertainty_codes == ["iex_partial_volume"]
    assert item.provenance.source_references[0].reference_type == "duckdb_row"


def test_snapshot_reads_latest_trade_and_quote_only():
    item = _snapshot_item()
    assert item.payload["trade_price"] == "501.37" and item.payload["bid_price"] == "501.36"
    assert item.payload["trade_at_utc"] == "2027-01-12T20:59:58.123456Z"  # truncated, not rounded
    assert item.effective_at_utc == datetime(2027, 1, 12, 20, 59, 59, 500000, tzinfo=UTC)
    assert "minute" not in json.dumps(item.payload)


def test_news_publication_metadata_is_fact_and_claims_are_unverified():
    item = _news_item()
    p = item.payload
    assert item.effective_at_utc == datetime(2027, 1, 12, 15, 0, tzinfo=UTC)  # publication time
    assert p["content_status"] == "provider_reported_unverified"
    assert p["related_symbols"] == ["QQQ", "SPY"]
    assert item.quality.uncertainty_codes == ["provider_reported_unverified"]
    assert set(p) == {
        "provider",
        "provider_article_id",
        "publisher",
        "created_at_utc",
        "updated_at_utc",
        "related_symbols",
        "content_status",
        "headline",
        "headline_truncated",
        "summary",
        "summary_truncated",
    }
    assert "example.com" not in item.model_dump_json()  # the URL is never carried


def test_fred_observation_preserves_series_period_vintage_units_and_frequency():
    item = _observation_item()
    assert item.payload == {
        "frequency_short": "M",
        "observation_date": "2026-12-01",
        "provider": "fred",
        "realtime_end": "9999-12-31",
        "realtime_start": "2027-01-13",
        "series_id": "CPIAUCSL",
        "units_short": "Index 1982-1984=100",
        "value": "321.456",
    }
    scope = item.temporal_scope
    assert (scope.observation_period_start, scope.source_vintage_start) == (
        datetime(2026, 12, 1).date(),
        datetime(2027, 1, 13).date(),
    )
    assert item.effective_at_utc == datetime(2027, 1, 13, tzinfo=UTC)  # vintage start, not release
    assert item.quality.uncertainty_codes == ["vintage_revisable"]


def test_fred_metadata_is_effective_at_retrieval_and_drops_notes():
    item = _metadata_item()
    assert item.effective_at_utc == datetime(2027, 1, 14, 12, tzinfo=UTC)
    assert "notes" not in item.payload and "popularity" not in item.payload
    assert item.payload["units"] == "Index 1982-1984=100"


def test_clock_reading_is_translated_without_any_clock_read():
    item = _clock_item()
    assert item.payload_schema_id == "clock_health_fact.v1"
    assert item.payload["offset_ms"] == 5
    assert item.effective_at_utc == fx.RETRIEVED


def test_regime_results_become_calculations_with_complete_lineage():
    _, bar_items, engine_input, features = _regime()
    features_item = _features_item(features, engine_input, bar_items)
    assert features_item.evidence_kind is EvidenceKind.DETERMINISTIC_CALCULATION
    assert features_item.provenance.parent_evidence_ids == sorted(i.item_id for i in bar_items)
    classification = mc.adapt_regime_classification(
        classify(features), features_item=features_item, context=fx.context(mc.REGIME_CONFIGURATION)
    )
    assert classification.provenance.parent_evidence_ids == [features_item.item_id]
    window = "session:us_equity_regular_hours_window:2027-01-12"
    for item in (features_item, classification, *bar_items):
        assert [s.subject_id for s in item.subjects] == ["instrument:us_equity:SPY", window]
    assert "setup" not in classification.model_dump_json()


# --- Strict input validation ------------------------------------------------------------------


class _Partial:
    provider = "alpaca"
    symbol = "SPY"


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (None, "source_missing"),
        (_Partial(), "source_incomplete"),
        (fx.bar(symbol="QQQ"), "symbol_mismatch"),
        (fx.bar(timeframe="1Min"), "unsupported_timeframe"),
        (fx.bar(feed="sip"), "unknown_feed"),
        (fx.bar(adjustment="split"), "unsupported_bar_variant"),
        (fx.bar(timestamp="2027-01-12T14:30:00"), "timestamp_invalid"),  # no offset
        (fx.bar(open=500.0), "source_invalid"),  # a float price
        (fx.bar(low=Decimal("501")), "bar_ohlc_inconsistent"),
        (fx.bar(retrieved_at="2027-01-12T14:32:00Z"), "bar_not_complete"),
    ],
)
def test_malformed_or_incomplete_bars_are_refused(source, reason):
    reference = fx.bar_reference(fx.bar())
    assert (
        _reason(
            md.adapt_market_bar,
            source,
            reference=reference,
            context=fx.context(md.BARS_CONFIGURATION),
        )
        == reason
    )


def test_bar_reference_must_name_the_same_row():
    other = fx.bar(fx.OPEN_UTC + timedelta(minutes=5))
    assert (
        _reason(
            md.adapt_market_bar,
            fx.bar(),
            reference=fx.bar_reference(other),
            context=fx.context(md.BARS_CONFIGURATION),
        )
        == "source_reference_mismatch"
    )


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "source_invalid"),
        ({"latestTrade": {"t": "2027-01-12T20:59:58Z", "p": "501", "s": 1}}, "source_invalid"),
        ({"latestTrade": {"t": "2027-01-12T20:59:58Z", "p": True, "s": 1}}, "source_invalid"),
        ({"latestTrade": {"t": "nope", "p": 1.0, "s": 1}}, "timestamp_invalid"),
        (
            {
                "latestTrade": {"t": "2027-01-12T20:59:58Z", "p": 1.0, "s": 1},
                "latestQuote": {"t": "2027-01-12T20:59:58Z", "bp": 1.0},
            },
            "source_invalid",
        ),
    ],
)
def test_malformed_snapshots_are_refused(body, reason):
    assert _reason(_snapshot_item, body) == reason


def test_snapshot_feed_symbol_and_reference_fail_closed():
    assert _reason(_snapshot_item, feed="opra") == "unknown_feed"
    assert _reason(_snapshot_item, symbol="QQQ") == "symbol_mismatch"
    wrong = fx.provider_reference(EndpointClass.ALPACA_STOCK_BARS, feed="iex")
    assert _reason(_snapshot_item, reference=wrong) == "source_reference_mismatch"


def test_incomplete_news_and_fred_sources_are_refused():
    assert _reason(_news_item, fx.news_item(created_at=None)) == "publication_time_missing"
    assert _reason(_news_item, fx.news_item(created_at="2027-01-12T17:00:00Z")) == (
        "future_timestamp_detected"
    )
    assert _reason(_news_item, fx.news_item(provider_article_id="bad id!")) == "source_invalid"
    assert _reason(_observation_item, metadata=fx.series_metadata(series_id="UNRATE")) == (
        "series_metadata_mismatch"
    )
    assert _reason(_observation_item, fx.observation(realtime_start="2026-11-01")) == (
        "vintage_window_invalid"
    )
    assert _reason(_observation_item, fx.observation(value=Decimal("1"), is_missing=True)) == (
        "source_invalid"
    )
    assert _reason(_metadata_item, fx.series_metadata(last_updated="2027-02-01T00:00:00Z")) == (
        "future_timestamp_detected"
    )
    assert _reason(_clock_item, object()) == "source_invalid"


def test_unknown_configuration_producer_or_schema_fails_closed():
    assert _reason(_bar_item, configuration_identity="cfg_none") == "unknown_configuration"
    registry = fx.make_test_registry()
    without_bars = registry.model_copy(
        update={
            "producers": [p for p in registry.producers if p.producer_id != "alpaca_market_bars"]
        }
    )
    assert (
        _reason(
            md.adapt_market_bar,
            fx.bar(),
            reference=fx.bar_reference(fx.bar()),
            context=fx.context(md.BARS_CONFIGURATION, registry=without_bars),
        )
        == "unknown_producer"
    )
    # A producer with no rule for the adapter's schema fails closed.
    no_rule = registry.model_copy(
        update={
            "producers": [
                p.model_copy(
                    update={
                        "emission_rules": [
                            r
                            for r in p.emission_rules
                            if r.payload_schema_id != "market_bar_fact.v1"
                        ]
                    }
                )
                if p.producer_id == "alpaca_market_bars"
                else p
                for p in registry.producers
            ]
        }
    )
    assert (
        _reason(
            md.adapt_market_bar,
            fx.bar(),
            reference=fx.bar_reference(fx.bar()),
            context=fx.context(md.BARS_CONFIGURATION, registry=no_rule),
        )
        == "kind_or_payload_not_allowed"
    )
    # A registry naming an unregistered schema cannot even be constructed.
    without_schema = registry.model_copy(
        update={
            "payload_schemas": [
                s for s in registry.payload_schemas if s.payload_schema_id != "market_bar_fact.v1"
            ]
        }
    )
    with pytest.raises(ValidationError):
        fx.context(md.BARS_CONFIGURATION, registry=without_schema)


# --- Timestamps and timezones --------------------------------------------------------------


def test_equivalent_offsets_give_the_same_item():
    z = _bar_item(fx.bar(timestamp="2027-01-12T14:30:00Z"))
    utc = _bar_item(fx.bar(timestamp="2027-01-12T14:30:00+00:00"))
    eastern = _bar_item(fx.bar(timestamp="2027-01-12T09:30:00-05:00"))
    assert z.payload == utc.payload == eastern.payload


def _window(start: datetime) -> EvidenceItem:
    """A bar at ``start`` (UTC), retrieved and generated well after it."""
    return _bar_item(
        fx.bar(start, retrieved_at=(start + timedelta(days=1)).isoformat()),
        generated_at_utc=start + timedelta(days=2),
    )


def _in_window(start: datetime) -> bool:
    return _window(start).payload["within_regular_hours_window"]


def test_weekend_bars_are_outside_the_window():
    for day in (16, 17):  # Saturday and Sunday, 2027-01-16/17, 09:30 New York
        item = _window(datetime(2027, 1, day, 14, 30, tzinfo=UTC))
        assert not item.payload["within_regular_hours_window"]
        assert [s.subject_id for s in item.subjects] == ["instrument:us_equity:SPY"]
        assert item.temporal_scope.session_date is None


def test_a_normal_weekday_bar_is_inside_the_window():
    item = _window(datetime(2027, 1, 12, 14, 30, tzinfo=UTC))  # Tuesday 09:30 New York
    assert item.payload["within_regular_hours_window"]
    assert item.temporal_scope.session_date == datetime(2027, 1, 12).date()


def _window_subjects(item: EvidenceItem) -> list[str]:
    return [s.subject_id for s in item.subjects if s.subject_type.value == "market_session"]


def test_a_holiday_is_not_detected_because_no_calendar_exists():
    # 2027-01-18 is Martin Luther King Jr. Day: the exchange is closed, but the
    # field and the window subject are a clock-window check only.
    item = _window(datetime(2027, 1, 18, 15, 0, tzinfo=UTC))
    assert item.payload["within_regular_hours_window"]
    assert _window_subjects(item) == ["session:us_equity_regular_hours_window:2027-01-18"]


def test_an_early_close_is_not_detected_because_no_calendar_exists():
    # 2027-11-26 closes early at 13:00 New York; a 14:00 bar still lies in the
    # ordinary clock window, which is all the field and subject state.
    item = _window(datetime(2027, 11, 26, 19, 0, tzinfo=UTC))
    assert item.payload["within_regular_hours_window"]
    assert _window_subjects(item) == ["session:us_equity_regular_hours_window:2027-11-26"]


def test_out_of_window_bars_carry_no_window_subject():
    for start in (
        datetime(2027, 1, 12, 14, 25, tzinfo=UTC),  # 09:25 New York
        datetime(2027, 1, 12, 21, 0, tzinfo=UTC),  # 16:00 New York
        datetime(2027, 1, 12, 20, 57, tzinfo=UTC),  # crosses 16:00
        datetime(2027, 1, 16, 15, 0, tzinfo=UTC),  # Saturday
    ):
        assert _window_subjects(_window(start)) == []


def test_no_adapter_creates_or_reconstructs_the_old_session_subject():
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "session:us_equity_regular:" not in source, path.name
        assert "session_subject" not in source, path.name
    for item in _all_items():
        for subject in item.subjects:
            assert not subject.subject_id.startswith("session:us_equity_regular:")


def test_the_window_follows_new_york_time_across_dst():
    # DST begins 2027-03-14: 09:30 New York is 14:30 UTC before, 13:30 UTC after.
    assert _in_window(datetime(2027, 3, 12, 14, 30, tzinfo=UTC))
    assert not _in_window(datetime(2027, 3, 12, 13, 30, tzinfo=UTC))  # 08:30
    assert _in_window(datetime(2027, 3, 15, 13, 30, tzinfo=UTC))
    # DST ends 2027-11-07: 09:30 New York is 13:30 UTC before, 14:30 UTC after.
    assert _in_window(datetime(2027, 11, 5, 13, 30, tzinfo=UTC))
    assert not _in_window(datetime(2027, 11, 8, 13, 30, tzinfo=UTC))  # 08:30
    assert _in_window(datetime(2027, 11, 8, 14, 30, tzinfo=UTC))


def test_window_edges_and_a_bar_crossing_16_00():
    assert _in_window(datetime(2027, 1, 12, 20, 55, tzinfo=UTC))  # 15:55-16:00
    assert not _in_window(datetime(2027, 1, 12, 20, 57, tzinfo=UTC))  # 15:57-16:02 crosses
    assert not _in_window(datetime(2027, 1, 12, 21, 0, tzinfo=UTC))  # 16:00 onward
    assert not _in_window(datetime(2027, 1, 12, 14, 25, tzinfo=UTC))  # 09:25-09:30


def test_the_field_never_represents_exchange_calendar_validation():
    from market_intelligence.evidence_adapters.payloads import WINDOW_DESCRIPTION, MarketBarFact

    field = MarketBarFact.model_fields["within_regular_hours_window"]
    assert field.description == WINDOW_DESCRIPTION
    for phrase in (
        "does not prove the exchange was open",
        "that the date was a trading session",
        "that a shortened session was complete",
    ):
        assert phrase in WINDOW_DESCRIPTION
    calendar_terms = (
        "regular_session",
        "trading_session",
        "exchange_open",
        "session_complete",
        "is_trading_day",
        "market_open",
    )
    for name in MarketBarFact.model_fields:
        assert name not in calendar_terms
    assert not set(_bar_item().payload) & set(calendar_terms)


def test_snapshot_fractions_are_truncated_never_rounded_up():
    item = _snapshot_item(fx.snapshot(trade_t="2027-01-12T20:59:58.9999999999Z"[:30] + "Z"))
    assert item.payload["trade_at_utc"] == "2027-01-12T20:59:58.999999Z"


# --- Deterministic identity ----------------------------------------------------------------


def test_identical_input_gives_an_identical_item():
    assert _bar_item() == _bar_item()
    assert _news_item() == _news_item()


def test_generation_time_is_outside_the_identity():
    later = fx.GENERATED + timedelta(days=1)
    assert _bar_item().item_id == _bar_item(generated_at_utc=later).item_id


def test_different_substantive_timestamps_give_different_ids():
    first = _bar_item(fx.bar(fx.OPEN_UTC))
    second = _bar_item(fx.bar(fx.OPEN_UTC + timedelta(minutes=5)))
    assert first.item_id != second.item_id
    old = _observation_item()
    new = _observation_item(
        fx.observation(realtime_start="2027-02-12", retrieved_at="2027-02-13T12:00:00Z"),
        generated_at_utc=datetime(2027, 2, 14, tzinfo=UTC),
    )
    assert old.item_id != new.item_id
    assert (
        _news_item().item_id != _news_item(fx.news_item(created_at="2027-01-12T15:01:00Z")).item_id
    )


def test_a_newer_fred_vintage_is_a_source_revision():
    old_obs = fx.observation()
    old = _observation_item(old_obs)
    newer = _observation_item(
        fx.observation(
            realtime_start="2027-02-12", retrieved_at="2027-02-13T12:00:00Z", value=Decimal("321.5")
        ),
        supersedes=old,
        generated_at_utc=datetime(2027, 2, 14, tzinfo=UTC),
    )
    assert newer.revision.revision_reason is RevisionReason.SOURCE_REVISION
    assert newer.revision.supersedes_item_id == old.item_id
    assert _reason(_observation_item, old_obs, supersedes=newer) == "revision_not_a_later_vintage"


# --- Lineage --------------------------------------------------------------------------------


def test_features_must_cite_every_input_bar_exactly():
    _, bar_items, engine_input, features = _regime()
    assert _reason(_features_item, features, engine_input, bar_items[:-1]) == "lineage_incomplete"
    assert _reason(_features_item, features, engine_input, [*bar_items, bar_items[0]]) == (
        "duplicate_parent_bar"
    )
    altered = _bar_item(fx.bar(fx.OPEN_UTC, close="500.3", high=Decimal("500.6")))
    assert _reason(_features_item, features, engine_input, [altered, *bar_items[1:]]) == (
        "parent_bar_mismatch"
    )
    assert _reason(_features_item, features, engine_input, [*bar_items[:-1], _news_item()]) == (
        "parent_not_a_spy_bar_fact"
    )


def test_prior_day_inputs_need_their_own_cited_parent():
    prior = PriorDayLevels(high=Decimal("502"), low=Decimal("498"), close=Decimal("500"))
    _, bar_items, engine_input, features = _regime(prior_day=prior)
    assert _reason(_features_item, features, engine_input, bar_items) == "lineage_incomplete"
    prior_fact = _bar_item(fx.bar(datetime(2027, 1, 11, 20, 55, tzinfo=UTC)))
    item = _features_item(features, engine_input, bar_items, supplementary_parents=[prior_fact])
    assert prior_fact.item_id in item.provenance.parent_evidence_ids


def test_results_that_do_not_follow_from_their_inputs_are_refused():
    _, bar_items, engine_input, features = _regime()
    forged = features.model_copy(update={"session_return_bps": Decimal("999")})
    assert _reason(_features_item, forged, engine_input, bar_items) == "calculation_mismatch"
    features_item = _features_item(features, engine_input, bar_items)
    result = classify(features)
    other = "range" if result.regime.value != "range" else "event_driven"
    tampered = result.model_copy(update={"regime": Regime(other)})
    assert (
        _reason(
            mc.adapt_regime_classification,
            tampered,
            features_item=features_item,
            context=fx.context(mc.REGIME_CONFIGURATION),
        )
        == "calculation_mismatch"
    )
    assert (
        _reason(
            mc.adapt_regime_classification,
            result,
            features_item=bar_items[0],
            context=fx.context(mc.REGIME_CONFIGURATION),
        )
        == "parent_not_regime_features"
    )


# --- Missing evidence -------------------------------------------------------------------------


def test_missing_fred_value_is_explicit_missing_evidence_never_zero():
    item = _observation_item(fx.observation(value=None, is_missing=True))
    assert item.evidence_kind is EvidenceKind.MISSING_EVIDENCE
    assert item.availability.state is AvailabilityState.UNAVAILABLE
    assert item.payload["reason_code"] == "missing_observation"
    assert item.payload["expected_component"] == "value"
    assert "value" not in item.payload and "0" not in item.payload.values()


def test_missing_bars_and_news_are_explicit():
    bars = md.missing_market_bars(
        checked_at=fx.RETRIEVED, query_sha256=fx.QUERY, context=fx.context(md.BARS_CONFIGURATION)
    )
    articles = news.missing_news(
        checked_at=fx.RETRIEVED, query_sha256=fx.QUERY, context=fx.context(news.NEWS_CONFIGURATION)
    )
    assert bars.payload["reason_code"] == "bars_missing"
    assert articles.payload["reason_code"] == "no_articles_returned"


def test_an_absent_quote_is_a_named_missing_component():
    item = _snapshot_item(fx.snapshot(quote=False))
    assert item.availability.state is AvailabilityState.PARTIAL
    assert item.availability.missing_components == ["latest_quote"]
    assert item.payload["bid_price"] is None and item.payload["ask_price"] is None


# --- News claim restrictions -----------------------------------------------------------------


def test_long_provider_text_is_cut_with_an_explicit_flag():
    item = _news_item(fx.news_item(summary="x" * 900))
    assert item.payload["summary_truncated"] and len(item.payload["summary"]) == 500
    no_summary = _news_item(fx.news_item(summary=None))
    assert "headline_only" in no_summary.quality.uncertainty_codes


def test_news_carries_no_sentiment_impact_direction_or_probability():
    item = _news_item()
    text = json.dumps(item.payload).lower()
    for term in ("sentiment", "impact", "direction", "probability", "bullish", "bearish"):
        assert term not in text
    assert item.quality.calibrated_probability is None
    assert item.quality.graded_strength.value == "confidence_not_available"


def test_unsafe_provider_text_is_refused():
    assert (
        _reason(_news_item, fx.news_item(headline="Synthetic path /home/user/notes"))
        == "source_invalid"
    )


# --- Holdout -------------------------------------------------------------------------------------


def test_spy_price_evidence_in_the_holdout_window_is_refused():
    inside = fx.bar(fx.HOLDOUT_OPEN_UTC, retrieved_at="2026-10-14T21:00:00Z")
    assert _reason(_bar_item, inside) == "holdout_restricted"
    body = fx.snapshot(trade_t="2026-10-14T19:00:00Z", quote=False)
    assert _reason(_snapshot_item, body) == "holdout_restricted"
    assert (
        _reason(
            md.missing_market_bars,
            checked_at=datetime(2026, 10, 14, 15, tzinfo=UTC),
            query_sha256=fx.QUERY,
            context=fx.context(md.BARS_CONFIGURATION),
        )
        == "holdout_restricted"
    )


def test_regime_results_in_the_holdout_window_are_refused():
    bars = fx.session_bars(3, fx.HOLDOUT_OPEN_UTC)
    engine_input = fx.engine_input(bars, session_date=fx.HOLDOUT_OPEN_UTC.date())
    assert _reason(_features_item, compute_features(engine_input), engine_input, []) == (
        "holdout_restricted"
    )


def test_a_registry_that_does_not_declare_price_schemas_is_refused():
    wrong = fx.make_test_registry(price_schemas_declared=False)
    assert _reason(_bar_item, registry=wrong) == "price_schema_not_declared"
    assert _reason(_snapshot_item, registry=wrong) == "price_schema_not_declared"


def test_non_price_evidence_dated_in_the_window_is_not_a_holdout_item():
    item = _news_item(
        fx.news_item(
            created_at="2026-10-14T15:00:00Z", updated_at=None, retrieved_at="2026-10-14T16:00:00Z"
        )
    )
    assert item.effective_at_utc.date() == datetime(2026, 10, 14).date()


# --- Eligibility, kinds and serialization ---------------------------------------------------------


def test_eligibility_is_the_registrys_derivation_never_the_adapters():
    assert _bar_item().machine_decision_eligible  # the test registry grants it
    denied = fx.make_test_registry(eligible=False)
    assert not _bar_item(registry=denied).machine_decision_eligible
    assert not _bar_item(code_tree_clean=False).machine_decision_eligible
    for fn in (
        md.adapt_market_bar,
        md.adapt_market_snapshot,
        news.adapt_news_publication,
        fred.adapt_fred_observation,
        clk.adapt_clock_health,
        mc.adapt_regime_features,
    ):
        assert "machine_decision_eligible" not in fn.__code__.co_varnames


def test_no_adapter_emits_inference_or_research():
    kinds = {i.evidence_kind for i in _all_items()}
    assert kinds == {
        EvidenceKind.CONFIRMED_FACT,
        EvidenceKind.DETERMINISTIC_CALCULATION,
        EvidenceKind.MISSING_EVIDENCE,
    }


def test_inference_cannot_enter_a_calculations_lineage():
    from market_intelligence.tests import evidence_fixtures as ef

    prior = PriorDayLevels(high=Decimal("502"), low=Decimal("498"), close=Decimal("500"))
    _, bar_items, engine_input, features = _regime(prior_day=prior)
    inference = ef.inference_item([ef.bar_item()])
    assert (
        _reason(
            _features_item, features, engine_input, bar_items, supplementary_parents=[inference]
        )
        == "parent_kind_not_allowed"
    )


def test_every_item_survives_serialization_and_revalidation():
    registry = fx.make_test_registry()
    for item in _all_items():
        again = EvidenceItem.model_validate_json(item.model_dump_json())
        assert again == item
        validate_item(again, registry, payload_models=ADAPTER_PAYLOAD_MODELS)


# --- Bundle compatibility ----------------------------------------------------------------------


def test_adapter_items_build_a_bundle():
    first = _bundle()
    assert first.bundle_id == _bundle().bundle_id  # deterministic


def _bundle():
    _, bar_items, engine_input, features = _regime()
    features_item = _features_item(features, engine_input, bar_items)
    as_of = features.as_of_timestamp + timedelta(minutes=1)
    clock_item = _clock_item(
        _reading(measured_at_utc=as_of, last_sync_at_utc=as_of - timedelta(minutes=1)),
        generated_at_utc=as_of + timedelta(seconds=1),
    )
    query = EvidenceQuery(
        effective_from_utc=as_of - timedelta(hours=2),
        effective_to_utc=as_of + timedelta(hours=1),
        max_entries=100,
    )
    registry = fx.make_test_registry()
    bundle = build_bundle(
        registry=registry,
        purpose=BundlePurpose.LIVE_MARKET_STATE,
        as_of=as_of,
        consumer_context=EvidenceConsumerContext(
            consumer_id=ConsumerId.DASHBOARD,
            purpose=BundlePurpose.LIVE_MARKET_STATE,
            permissions=sorted(
                [ConsumerPermission.READ_CALCULATIONS, ConsumerPermission.READ_FACTS]
            ),
            machine_decision_mode=False,
            request_sha256=query_request_sha256(query),
        ),
        query=query,
        recorded_items=[
            RecordedItem(item=i, recorded_at_utc=as_of - timedelta(seconds=30))
            for i in (*bar_items, features_item, clock_item)
        ],
        built_at=as_of + timedelta(seconds=2),
    )
    assert bundle.missing_required_producers == []
    required = [e for e in bundle.entries if e.required]
    assert [e.item_id for e in required] == [features_item.item_id]
    assert {e.item_id for e in bundle.entries} >= {i.item_id for i in bar_items}
    assert all(e.freshness.state.value == "current" for e in bundle.entries)
    return bundle


# --- Purity: forbidden I/O and imports ------------------------------------------------------------

_FORBIDDEN_MODULES = (
    "duckdb",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "os",
    "pathlib",
    "io",
    "shutil",
    "subprocess",
    "random",
    "secrets",
    "uuid",
    "time",
    "dotenv",
    "openai",
    "anthropic",
    "market_intelligence.data_connectors",
    "market_intelligence.storage",
    "market_intelligence.evidence_store",
    "market_intelligence.model_clients",
    "market_intelligence.agents",
    "market_intelligence.orchestration",
    "market_intelligence.config",
    "market_intelligence.dashboard",
    "market_intelligence.setup_cards",
)
# Attribute calls that read a clock, the environment or randomness
# (``datetime.now()``, ``time.time()``, ``os.getenv()``), and bare calls that
# open files or evaluate code. Constructors such as ``time(9, 30)`` are fine.
_FORBIDDEN_ATTRIBUTE_CALLS = {
    "now",
    "utcnow",
    "today",
    "getenv",
    "urandom",
    "random",
    "randint",
    "choice",
    "uuid4",
    "read_text",
    "read_bytes",
}
_FORBIDDEN_NAME_CALLS = {"open", "input", "eval", "exec", "getenv", "__import__"}
# Clock reads on the ``time`` module. ``datetime.time()`` only extracts a time
# of day from a value already passed in, and reads no clock.
_FORBIDDEN_TIME_MODULE_CALLS = {"time", "monotonic", "perf_counter", "time_ns"}


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_adapters_import_and_call_no_io_clock_randomness_or_model(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            assert not any(name == m or name.startswith(m + ".") for m in _FORBIDDEN_MODULES), (
                module,
                name,
            )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                assert func.attr not in _FORBIDDEN_ATTRIBUTE_CALLS, (module, func.attr)
                receiver = func.value.id if isinstance(func.value, ast.Name) else ""
                assert not (receiver == "time" and func.attr in _FORBIDDEN_TIME_MODULE_CALLS), (
                    module,
                    func.attr,
                )
            elif isinstance(func, ast.Name):
                assert func.id not in _FORBIDDEN_NAME_CALLS, (module, func.id)


def test_adapters_open_no_database_or_connection_at_runtime(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("adapters must not open a database or a connection")

    monkeypatch.setattr(duckdb, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert len(_all_items()) == 16  # six bars and ten other items


def test_source_objects_are_the_real_connector_dataclasses():
    from market_intelligence.data_connectors.alpaca_bars import Bar
    from market_intelligence.data_connectors.fred_macro_data import FredObservation

    assert dataclasses.is_dataclass(Bar) and isinstance(fx.bar(), Bar)
    assert isinstance(fx.observation(), FredObservation)
