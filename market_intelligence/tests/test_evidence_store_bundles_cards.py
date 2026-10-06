"""Bundles (point-in-time reproduction) and setup cards (exact registry binding,
one-root chains). Synthetic data; test-only synthetic setup definitions are
used only in offline unauthenticated mode, never the production format."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from market_intelligence.evidence.bundle import build_bundle
from market_intelligence.evidence.contracts import EvidenceBundleManifest
from market_intelligence.evidence.enums import BundlePurpose, ConsumerId
from market_intelligence.evidence_store.contracts import BundleBuilderIdentity
from market_intelligence.evidence_store.enums import RegistryKind, StoreState
from market_intelligence.evidence_store.errors import StoreRefusal
from market_intelligence.evidence_store.reads import (
    card_history,
    card_tip_at,
    consumer_read_card,
    read_card,
    render_display_status,
    reproduce_bundle,
)
from market_intelligence.evidence_store.registry_files import (
    SetupDefinitionRegistry,
    compute_setup_registry_version_id,
)
from market_intelligence.evidence_store.store_io import connect, select_rows
from market_intelligence.evidence_store.verification import verify_database
from market_intelligence.setup_cards.builder import build_no_qualified_setup_card, build_setup_card
from market_intelligence.setup_cards.contracts import (
    CardRevision,
    SetupCardContent,
    execution_field_names,
    seal_card,
)
from market_intelligence.setup_cards.enums import (
    CardKind,
    CardRevisionReason,
    DisplayStatus,
    Lane,
    LaneQualificationAvailability,
    SetupLifecycle,
    SetupQualification,
)
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx
from market_intelligence.tests import setup_card_fixtures as s

BUILDER = BundleBuilderIdentity(
    builder_version="1.0.0",
    builder_code_commit_sha="d" * 40,
    builder_configuration_identity="cfg_none",
)


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _seed(env, at: datetime = f.T0, *, evaluation=None):
    """Store bars, research, clock and a setup evaluation, one envelope per producer."""
    bar = f.bar_item(at)
    research = f.research_item()
    clock = f.clock_item(at)
    evaluation = evaluation or s.evaluation_item(parents=[bar], at=at)
    for producer, items, key in (
        (f.BARS, [bar], "1"),
        (f.RESEARCH, [research], "2"),
        (f.CLOCK_PRODUCER, [clock], "3"),
        (s.EVALUATOR, [evaluation], "4"),
    ):
        env.store.append_envelope(
            fx.envelope(
                items, env.evidence_id, producer=producer, run_key=(key + str(at.minute)) * 32
            )
        )
    return {"bar": bar, "research": research, "clock": clock, "evaluation": evaluation}


def _bundle(
    env, as_of, purpose=BundlePurpose.SETUP_DETAIL, *, query=None
) -> EvidenceBundleManifest:
    with env.store.read_connection() as conn:
        registry = env.store.load_registry(conn, env.evidence_id)
        items, conflicts, _ = env.store.records_as_of(conn, as_of, registry)
    q = query or f.query(
        effective_from_utc=as_of - timedelta(hours=6), effective_to_utc=as_of + timedelta(hours=6)
    )
    return build_bundle(
        registry=registry,
        purpose=purpose,
        as_of=as_of,
        consumer_context=f.context(q, purpose=purpose),
        query=q,
        recorded_items=items,
        recorded_conflicts=conflicts,
        built_at=as_of + timedelta(seconds=1),
    )


def _env(tmp_path, *, synthetic=True):
    mapping = {fx.EMPTY_SETUP_REGISTRY_ID: (s.SYNTHETIC_DEFINITION,)} if synthetic else None
    env = fx.make_env(tmp_path, synthetic=mapping)
    env.evidence_id, env.setup_id = fx.setup_registries(env)
    return env


def _card(env, bundle, items, **overrides):
    with env.store.read_connection() as conn:
        ctx = env.store.card_context(conn, bundle, env.setup_id)
    kwargs = dict(
        card_kind=CardKind.SETUP,
        lane=Lane.VWAP_REVERSION,
        provenance=s.PROVENANCE,
        setup_subject_id=s.SETUP_SUBJECT,
        supporting_ids=[items["bar"].item_id],
        context_ids=[items["research"].item_id],
    )
    kwargs.update(overrides)
    return build_setup_card(ctx, **kwargs)


def _reseal(card, **changes):
    fields = {name: getattr(card, name) for name in SetupCardContent.model_fields}
    fields.update(changes)
    return seal_card(SetupCardContent(**fields))


# --- Bundles -------------------------------------------------------------------------------------


def test_bundle_is_stored_only_after_exact_rebuild(tmp_path):
    env = _env(tmp_path)
    _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    assert reproduce_bundle(env.store, bundle.bundle_id).bundle_id == bundle.bundle_id
    with connect(env.store.database_path, read_only=True) as conn:
        row = select_rows(conn, "SELECT * FROM evidence_bundles")[0]
        entries = select_rows(conn, "SELECT * FROM evidence_bundle_entries")
        outcomes = select_rows(conn, "SELECT * FROM evidence_bundle_requirement_outcomes")
    assert row["builder_code_commit_sha"] == "d" * 40
    assert row["visible_through_commit_seq"] >= 1
    assert len(entries) == len(bundle.entries)
    assert {o["requirement_id"] for o in outcomes} == {"req.bars.spy", "req.research.vwap"}
    assert not verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS).findings


def test_builder_identity_is_outside_the_bundle_identity(tmp_path):
    env = _env(tmp_path)
    _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    other = BUILDER.model_copy(update={"builder_code_commit_sha": "e" * 40})
    env.store.append_bundle(bundle, other)
    assert reproduce_bundle(env.store, bundle.bundle_id).bundle_id == bundle.bundle_id


def test_bundle_as_of_must_be_in_the_past(tmp_path):
    env = _env(tmp_path)
    _seed(env)
    env.clock.set(s.AS_OF)
    assert (
        _reason(env.store.append_bundle, _bundle(env, s.AS_OF), BUILDER) == "bundle_as_of_not_past"
    )


def test_bundle_must_use_the_registry_in_force(tmp_path):
    env = _env(tmp_path)
    _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, env.clock() - timedelta(hours=3))  # before any activation
    assert _reason(env.store.append_bundle, bundle, BUILDER) == "bundle_registry_not_in_force"


def test_bundle_not_matching_stored_records_is_refused(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    with env.store.read_connection() as conn:
        registry = env.store.load_registry(conn, env.evidence_id)
    q = f.query()
    fabricated = build_bundle(
        registry=registry,
        purpose=BundlePurpose.SETUP_DETAIL,
        as_of=s.AS_OF,
        consumer_context=f.context(q, purpose=BundlePurpose.SETUP_DETAIL),
        query=q,
        recorded_items=f.recorded(items["bar"], f.bar_item(f.T0 - timedelta(minutes=5))),
        built_at=s.AS_OF + timedelta(seconds=1),
    )
    assert _reason(env.store.append_bundle, fabricated, BUILDER) == "bundle_not_reproducible"


def test_later_records_never_change_a_stored_bundle(tmp_path):
    env = _env(tmp_path)
    _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    env.store.append_envelope(
        fx.envelope([f.bar_item(f.T0 + timedelta(minutes=1))], env.evidence_id, run_key="9" * 64)
    )
    assert reproduce_bundle(env.store, bundle.bundle_id).bundle_id == bundle.bundle_id


def test_ambiguity_is_preserved_through_storage(tmp_path):
    env = _env(tmp_path)
    _seed(env)
    twin = f.bar_item(close="500.2")
    env.store.append_envelope(fx.envelope([twin], env.evidence_id, run_key="8" * 64))
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    assert bundle.ambiguous_requirements
    env.store.append_bundle(bundle, BUILDER)
    rebuilt = reproduce_bundle(env.store, bundle.bundle_id)
    assert rebuilt.ambiguous_requirements == bundle.ambiguous_requirements
    assert not rebuilt.machine_decision_ready


# --- Cards ---------------------------------------------------------------------------------------


def test_production_format_refuses_every_card(tmp_path):
    env = _env(tmp_path, synthetic=False)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    with env.store.read_connection() as conn:
        ctx = env.store.card_context(conn, bundle, env.setup_id)
    assert ctx.setup_definitions == ()
    with pytest.raises(Exception):  # the builder itself refuses: no definition
        build_setup_card(
            ctx,
            card_kind=CardKind.SETUP,
            lane=Lane.VWAP_REVERSION,
            provenance=s.PROVENANCE,
            setup_subject_id=s.SETUP_SUBJECT,
            supporting_ids=[items["bar"].item_id],
        )
    # A card built elsewhere with synthetic definitions is still refused by the store.
    synthetic_env = _env(tmp_path / "other")
    other_items = _seed(synthetic_env)
    synthetic_env.clock.set(f.T0 + timedelta(minutes=5))
    other_bundle = _bundle(synthetic_env, s.AS_OF)
    synthetic_env.store.append_bundle(other_bundle, BUILDER)
    card = _card(synthetic_env, other_bundle, other_items)
    env.store.append_envelope  # noqa: B018 - the production store has no synthetic definitions
    assert _reason(env.store.append_card, card) in {
        "card_bundle_not_stored",
        "setup_definition_not_registered",
    }
    with connect(env.store.database_path, read_only=True) as conn:
        assert select_rows(conn, "SELECT * FROM setup_cards") == []


def test_card_is_stored_with_its_exact_setup_definition_registry(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    card = _card(env, bundle, items)
    env.store.append_card(card)
    stored = read_card(env.store, card.card_id)
    assert stored.card == card
    assert stored.setup_definition_registry_version_id == env.setup_id
    # A later setup-definition activation never rebinds the stored card.
    later = SetupDefinitionRegistry(registry_label="setup-definitions-empty-1", definitions=[])
    later_id = fx.register(env, RegistryKind.SETUP_DEFINITION_REGISTRY, later)
    from market_intelligence.evidence_store.enums import ActivationReason

    fx.activate(
        env,
        RegistryKind.SETUP_DEFINITION_REGISTRY,
        later_id,
        reason=ActivationReason.VERSION_UPGRADE,
    )
    assert later_id == compute_setup_registry_version_id(later) != env.setup_id
    assert read_card(env.store, card.card_id).setup_definition_registry_version_id == env.setup_id
    assert not verify_database(
        env.store.database_path,
        payload_models=fx.PAYLOAD_MODELS,
        synthetic_setup_definitions_for_tests={env.setup_id: (s.SYNTHETIC_DEFINITION,)},
    ).has_stop()


def test_card_needs_its_bundle_stored(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    card = _card(env, bundle, items)
    assert _reason(env.store.append_card, card) == "card_bundle_not_stored"


def test_card_chain_has_one_root_and_no_branches(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    root = _card(env, bundle, items)
    env.store.append_card(root)
    second_root = _card(env, bundle, items, context_ids=[])
    assert _reason(env.store.append_card, second_root) == "card_chain_already_started"
    revision = _card(
        env, bundle, items, prior_card=root, revision_reason=CardRevisionReason.NEWER_BUNDLE
    )
    env.store.append_card(revision)
    # A second successor of the root (a branch) is refused: it must supersede the tip.
    branch = _card(
        env, bundle, items, prior_card=root, revision_reason=CardRevisionReason.EVIDENCE_CORRECTION
    )
    assert branch.card_id != revision.card_id
    assert _reason(env.store.append_card, branch) == "card_predecessor_not_tip"
    orphan = _reseal(
        revision,
        card_revision=CardRevision(
            revision_number=2,
            supersedes_card_id="scd1_" + "0" * 64,
            revision_reason=CardRevisionReason.NEWER_BUNDLE,
        ),
    )
    assert _reason(env.store.append_card, orphan) == "card_predecessor_not_tip"
    history = card_history(env.store, root.card_id)
    assert [h.card.card_id for h in history] == [root.card_id, revision.card_id]
    assert card_tip_at(env.store, root.card_id, env.clock()).card.card_id == revision.card_id


def test_lifecycle_regression_is_refused(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    complete = _card(env, bundle, items)
    env.store.append_card(complete)
    later = f.T0 + timedelta(minutes=10)
    bar = f.bar_item(later)
    developing = s.evaluation_item(
        lifecycle=SetupLifecycle.DEVELOPING,
        qualification=SetupQualification.NOT_EVALUATED,
        at=later,
        parents=[bar],
    )
    for producer, item, key in (
        (f.BARS, bar, "6"),
        (f.CLOCK_PRODUCER, f.clock_item(later), "7"),
        (s.EVALUATOR, developing, "5"),
    ):
        env.store.append_envelope(
            fx.envelope([item], env.evidence_id, producer=producer, run_key=key * 64)
        )
    env.clock.set(later + timedelta(minutes=5))
    as_of = later + timedelta(minutes=1)
    window = f.query(effective_from_utc=later - timedelta(minutes=1), effective_to_utc=as_of)
    second_bundle = _bundle(env, as_of, query=window)
    env.store.append_bundle(second_bundle, BUILDER)
    fresh = _card(env, second_bundle, {"bar": bar, "research": items["research"]})
    regression = _reseal(
        fresh,
        card_revision=CardRevision(
            revision_number=2,
            supersedes_card_id=complete.card_id,
            revision_reason=CardRevisionReason.LIFECYCLE_CHANGE,
        ),
    )
    assert _reason(env.store.append_card, regression) == "lifecycle_transition_forbidden"


def test_display_status_is_computed_at_render_time(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    root = _card(env, bundle, items)
    env.store.append_card(root)
    assert render_display_status(
        env.store, root.card_id, now=env.clock(), view_max_age_seconds=None
    ) is (DisplayStatus.OUTDATED_VIEW)
    revision = _card(
        env, bundle, items, prior_card=root, revision_reason=CardRevisionReason.NEWER_BUNDLE
    )
    env.store.append_card(revision)
    assert render_display_status(
        env.store, root.card_id, now=env.clock(), view_max_age_seconds=10_000
    ) is (DisplayStatus.SUPERSEDED)
    with connect(env.store.database_path, read_only=True) as conn:
        columns = {
            r["column_name"]
            for r in select_rows(
                conn,
                "SELECT column_name FROM information_schema.columns WHERE "
                "table_name = 'setup_cards'",
            )
        }
    assert "display_status" not in columns


def test_consumer_reads_follow_grants_and_are_audited(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    card = _card(env, bundle, items)
    env.store.append_card(card)
    assert consumer_read_card(env.store, card.card_id, consumer=ConsumerId.DASHBOARD) == card
    # No assistant grant exists: the proposed setup_detail grant is not added.
    assert (
        _reason(consumer_read_card, env.store, card.card_id, consumer=ConsumerId.ASSISTANT)
        == "purpose_not_granted"
    )
    with connect(env.store.database_path, read_only=True) as conn:
        events = [
            r["event_type"] for r in select_rows(conn, "SELECT event_type FROM store_audit_events")
        ]
    assert "consumer_access" in events


def test_unavailable_lanes_never_produce_a_stored_card(tmp_path):
    env = _env(tmp_path)
    items = _seed(env)  # no lane-level conclusion exists
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env, s.AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    with env.store.read_connection() as conn:
        ctx = env.store.card_context(conn, bundle, env.setup_id)
    result = build_no_qualified_setup_card(ctx, lane=Lane.VWAP_REVERSION, provenance=s.PROVENANCE)
    assert result.card is None
    assert result.availability is LaneQualificationAvailability.UNAVAILABLE
    del items
    with connect(env.store.database_path, read_only=True) as conn:
        assert select_rows(conn, "SELECT * FROM setup_cards") == []


def test_card_tables_have_no_execution_columns(tmp_path):
    env = _env(tmp_path)
    import re

    from market_intelligence.setup_cards.contracts import FORBIDDEN_FIELD_FRAGMENTS

    assert execution_field_names() == set()
    with connect(env.store.database_path, read_only=True) as conn:
        columns = {
            r["column_name"]
            for table in ("setup_cards", "setup_card_chains")
            for r in select_rows(
                conn,
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [table],
            )
        }
    allowed = {"session_date", "revision_reason"}
    for column in columns - allowed:
        assert not any(
            re.search(rf"(^|_){fragment}", column) for fragment in FORBIDDEN_FIELD_FRAGMENTS
        ), column
    assert env.store.state is StoreState.SERVING
