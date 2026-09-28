"""Product-side SPY holdout guard (design §O). Every in-window item here is
synthetic and exists only to prove the guard refuses it; no real SPY data,
holdout observation or holdout result is used."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.evidence import holdout
from market_intelligence.evidence.enums import MissingProducerReason
from market_intelligence.evidence.errors import (
    EvidenceValidationError,
    HoldoutRestrictedError,
)
from market_intelligence.evidence.holdout import (
    HOLDOUT_FIRST_SESSION,
    HOLDOUT_LAST_SESSION,
    enforce_holdout_guard,
    in_holdout_window,
    is_holdout_restricted,
)
from market_intelligence.evidence.primitives import format_utc
from market_intelligence.evidence.registry import PayloadSchemaEntry
from market_intelligence.evidence.validation import validate_item, validate_lineage
from market_intelligence.tests import evidence_fixtures as f

REGISTRY = f.make_registry()


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)


def test_window_is_the_preregistered_holdout():
    assert HOLDOUT_FIRST_SESSION == date(2026, 9, 23)
    assert HOLDOUT_LAST_SESSION == date(2026, 12, 4)
    assert in_holdout_window(date(2026, 9, 23)) and in_holdout_window(date(2026, 12, 4))
    assert not in_holdout_window(date(2026, 9, 22))
    assert not in_holdout_window(date(2026, 12, 5))


@pytest.mark.parametrize(
    "day", [date(2026, 9, 23), date(2026, 10, 14), date(2026, 12, 4)]
)
def test_synthetic_spy_price_evidence_in_the_window_is_refused(day):
    item = f.bar_item(_at(day))
    assert is_holdout_restricted(item, REGISTRY)
    with pytest.raises(HoldoutRestrictedError):
        validate_item(item, REGISTRY, payload_models=f.PAYLOAD_MODELS)


@pytest.mark.parametrize("day", [date(2026, 9, 22), date(2026, 12, 7), date(2027, 1, 12)])
def test_synthetic_fixtures_outside_the_window_are_accepted(day):
    item = f.bar_item(_at(day))
    assert not is_holdout_restricted(item, REGISTRY)
    validate_item(item, REGISTRY, payload_models=f.PAYLOAD_MODELS)


def test_payload_timestamps_inside_the_window_are_caught():
    item = f.bar_item(
        payload={"bar_timestamp": format_utc(f.HOLDOUT_T0), "close": "500.1"}
    )
    assert is_holdout_restricted(item, REGISTRY)


def test_derived_calculations_and_inference_are_blocked_too():
    calc_in_window = f.calc_item([], at=f.HOLDOUT_T0)
    assert is_holdout_restricted(calc_in_window, REGISTRY)


def test_derived_items_inherit_the_restriction_through_lineage():
    in_window = f.bar_item(f.HOLDOUT_T0)
    later_calc = f.calc_item([in_window], at=_at(date(2027, 1, 12)))
    with pytest.raises(HoldoutRestrictedError):
        validate_lineage(later_calc, {in_window.item_id: in_window}, REGISTRY)


def test_selector_results_carrying_an_underlying_price_are_blocked_in_window():
    item = f.selector_item()
    assert not is_holdout_restricted(item, REGISTRY)
    shifted = f.rebuild(
        item, payload={**item.payload, "as_of_timestamp": "2026-10-14T15:00:00Z"}
    )
    assert is_holdout_restricted(shifted, REGISTRY)


def test_price_free_status_items_may_report_the_restriction():
    missing = f.missing_item(
        f.HOLDOUT_T0,
        availability=f.EvidenceAvailability(
            state=f.AvailabilityState.REFUSED, reason_code="holdout_restricted"
        ),
        payload={
            "checked_at_utc": format_utc(f.HOLDOUT_T0),
            "expected_component": None,
            "expected_producer_id": f.BARS,
            "expected_subject_id": "instrument:us_equity:SPY",
            "query_sha256": "0" * 64,
            "reason_code": "holdout_restricted",
        },
    )
    assert not is_holdout_restricted(missing, REGISTRY)
    validate_item(missing, REGISTRY, payload_models=f.PAYLOAD_MODELS)


def test_non_spy_evidence_is_unaffected():
    clock = f.clock_item(f.HOLDOUT_T0)
    assert not is_holdout_restricted(clock, REGISTRY)


def _registry_without_calc_schema():
    return f.make_registry(
        payload_schemas=[
            s for s in REGISTRY.payload_schemas if s.payload_schema_id != "synthetic_calc.v1"
        ],
        producers=[p for p in REGISTRY.producers if p.producer_id not in (f.CALC, f.FUTURE)],
        selection_rules=[
            r
            for r in REGISTRY.selection_rules
            if not {f.CALC, f.FUTURE} & {q.producer_id for q in r.requirements}
        ],
    )


def test_unknown_schema_is_refused_before_holdout_evaluation():
    registry = _registry_without_calc_schema()
    in_window = f.calc_item([], at=f.HOLDOUT_T0)
    for check in (holdout.is_holdout_restricted, holdout.enforce_holdout_guard):
        with pytest.raises(EvidenceValidationError) as excinfo:
            check(in_window, registry)
        assert excinfo.value.reason == "unknown_payload_schema"
        assert not isinstance(excinfo.value, HoldoutRestrictedError)


def test_validate_item_refuses_unknown_schema_before_the_holdout_guard():
    registry = _registry_without_calc_schema()
    in_window = f.calc_item([], at=f.HOLDOUT_T0)
    with pytest.raises(EvidenceValidationError) as excinfo:
        validate_item(in_window, registry, payload_models=f.PAYLOAD_MODELS)
    assert excinfo.value.reason in {"unknown_producer", "unknown_payload_schema"}
    assert not isinstance(excinfo.value, HoldoutRestrictedError)


def test_every_schema_must_declare_price_content_explicitly():
    with pytest.raises(ValidationError):
        PayloadSchemaEntry(payload_schema_id="synthetic_new.v1")
    with pytest.raises(ValidationError):
        PayloadSchemaEntry(payload_schema_id="synthetic_new.v1", spy_price_content=None)
    with pytest.raises(ValidationError):
        PayloadSchemaEntry(payload_schema_id="synthetic_new.v1", spy_price_content="false")


def test_guard_applies_only_to_registered_price_bearing_schemas():
    price_free = f.make_registry(
        payload_schemas=[
            s.model_copy(update={"spy_price_content": False})
            if s.payload_schema_id == "synthetic_calc.v1"
            else s
            for s in REGISTRY.payload_schemas
        ]
    )
    in_window = f.calc_item([], at=f.HOLDOUT_T0)
    assert is_holdout_restricted(in_window, REGISTRY)
    assert not is_holdout_restricted(in_window, price_free)


def test_bundle_builder_excludes_restricted_items_and_reports_the_producer():
    in_window = f.bar_item(f.HOLDOUT_T0)
    as_of = f.HOLDOUT_T0 + timedelta(minutes=1)
    manifest = f.bundle(
        f.recorded(in_window, f.clock_item(f.HOLDOUT_T0), at=f.HOLDOUT_T0),
        as_of=as_of,
        q=f.query(
            effective_from_utc=f.HOLDOUT_T0 - timedelta(hours=1),
            effective_to_utc=f.HOLDOUT_T0 + timedelta(hours=1),
        ),
    )
    assert in_window.item_id not in {e.item_id for e in manifest.entries}
    reasons = {m.producer_id: m.reason for m in manifest.missing_required_producers}
    assert reasons[f.BARS] is MissingProducerReason.HOLDOUT_RESTRICTED
    assert not manifest.machine_decision_ready


def test_the_guard_has_no_disable_switch():
    for function in (is_holdout_restricted, enforce_holdout_guard):
        assert list(inspect.signature(function).parameters) == ["item", "registry"]
    names = {name.lower() for name in vars(holdout)}
    assert not any(word in name for name in names for word in ("disable", "bypass", "override"))
