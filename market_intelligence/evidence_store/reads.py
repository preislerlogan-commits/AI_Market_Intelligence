"""Point-in-time and consumer reads (docs/EVIDENCE_CARD_STORAGE_DESIGN.md §8, §9, §12).

Readers use short-lived read-only connections and serve only after the
store passed its startup verification (checkpoint, chain and the complete
authoritative scan, held as ``store.verified``); each returned record is still
validated at read time. Reads that
must record a ``consumer_access`` event are writes too, so they wait until the
store is ``serving``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from market_intelligence.evidence.contracts import EvidenceBundleManifest, EvidenceItem
from market_intelligence.evidence.enums import ConsumerId, SubjectType
from market_intelligence.evidence.holdout import HOLDOUT_FIRST_SESSION, HOLDOUT_LAST_SESSION
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.enums import (
    IntegrityFinding,
    StoreAuditEventType,
    StoreState,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.store import EvidenceStore, verify_row, visible_through
from market_intelligence.evidence_store.store_io import select_one, select_rows
from market_intelligence.setup_cards.contracts import SetupCard
from market_intelligence.setup_cards.enums import DisplayStatus
from market_intelligence.setup_cards.supersession import display_status, validate_card_history

_READABLE = frozenset({StoreState.SERVING, StoreState.CHECKPOINT_RECONCILIATION_REQUIRED})


@dataclass(frozen=True)
class RecordedCard:
    card: SetupCard
    setup_definition_registry_version_id: str
    commit_seq: int
    recorded_at_utc: datetime


def _require_readable(store: EvidenceStore) -> None:
    store._require_state(_READABLE)
    if store.verified is None:
        raise StoreRefusal("checkpoint_not_confirmed")


def reproduce_bundle(store: EvidenceStore, bundle_id: str) -> EvidenceBundleManifest:
    """Rebuild a stored bundle from what was visible at its as-of; the
    ``evb1_`` must match exactly or the read fails closed."""
    _require_readable(store)
    with store.read_connection() as conn:
        row = store.load_bundle(conn, bundle_id)
        if row is None:
            raise StoreRefusal("bundle_not_stored")
        bundle = rec.parse_bundle(row["record_json"])
        if visible_through(conn, bundle.as_of_utc) != row["visible_through_commit_seq"]:
            raise StoreRefusal("bundle_not_reproducible")
        rebuilt, _, _ = store.rebuild_bundle(conn, bundle)
    if rebuilt.bundle_id != bundle_id:
        raise StoreRefusal("bundle_not_reproducible")
    return rebuilt


def _card_from_row(row: dict) -> RecordedCard:
    verify_row("setup_cards", row)
    return RecordedCard(
        card=rec.parse_card(row["record_json"]),
        setup_definition_registry_version_id=row["setup_definition_registry_version_id"],
        commit_seq=row["commit_seq"],
        recorded_at_utc=row["recorded_at_utc"],
    )


def read_card(store: EvidenceStore, card_id: str) -> RecordedCard:
    _require_readable(store)
    with store.read_connection() as conn:
        row = select_one(conn, "SELECT * FROM setup_cards WHERE card_id = ?", [card_id])
        if row is None:
            raise StoreRefusal("card_not_stored")
        recorded = _card_from_row(row)
        if (
            select_one(
                conn,
                "SELECT registry_version_id FROM registry_versions WHERE registry_version_id = ?",
                [recorded.setup_definition_registry_version_id],
            )
            is None
        ):
            store._stop(IntegrityFinding.CARD_REGISTRY_VERSION_MISSING)
    return recorded


def card_history(store: EvidenceStore, card_id: str) -> list[RecordedCard]:
    _require_readable(store)
    with store.read_connection() as conn:
        row = select_one(
            conn, "SELECT chain_key_sha256 FROM setup_cards WHERE card_id = ?", [card_id]
        )
        if row is None:
            raise StoreRefusal("card_not_stored")
        rows = select_rows(
            conn,
            "SELECT * FROM setup_cards WHERE chain_key_sha256 = ? ORDER BY revision_number",
            [row["chain_key_sha256"]],
        )
    history = [_card_from_row(r) for r in rows]
    try:
        validate_card_history([r.card for r in history])
    except Exception:  # noqa: BLE001 - any stored history failure is ambiguity
        store._stop(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY)
    return history


def card_tip_at(store: EvidenceStore, card_id: str, at: datetime) -> RecordedCard | None:
    """The chain tip visible at ``at`` (commit_seq <= N_T)."""
    history = card_history(store, card_id)
    with store.read_connection() as conn:
        n_t = visible_through(conn, at)
    visible = [r for r in history if r.commit_seq <= n_t]
    superseded = {r.card.card_revision.supersedes_card_id for r in visible}
    tips = [r for r in visible if r.card.card_id not in superseded]
    if len(tips) > 1:
        store._stop(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY)
    return tips[0] if tips else None


def render_display_status(
    store: EvidenceStore, card_id: str, *, now: datetime, view_max_age_seconds: int | None
) -> DisplayStatus:
    """Render-time only; never stored."""
    history = card_history(store, card_id)
    card = next(r.card for r in history if r.card.card_id == card_id)
    return display_status(
        card,
        history=[r.card for r in history],
        now=now,
        view_max_age_seconds=view_max_age_seconds,
    )


def consumer_read_card(store: EvidenceStore, card_id: str, *, consumer: ConsumerId) -> SetupCard:
    """Returned only if the consumer's grant covers the card's bundle purpose,
    and only once the ``consumer_access`` event is recorded."""
    store._require_state(frozenset({StoreState.SERVING}))
    recorded = read_card(store, card_id)
    with store.read_connection() as conn:
        bundle_row = store.load_bundle(conn, recorded.card.decision_context_bundle_id)
        if bundle_row is None:
            store._stop(IntegrityFinding.CARD_BUNDLE_MISSING)
        registry, _ = store.evidence_registry_at(conn, store.clock())
    grant = registry.consumer_grant(consumer)
    if grant is None or bundle_row["purpose"] not in {p.value for p in grant.purposes}:
        raise StoreRefusal("purpose_not_granted")
    store.record_audit_event(
        store._event(
            StoreAuditEventType.CONSUMER_ACCESS,
            "consumer_access",
            actor=consumer.value,
            subject=card_id,
        )
    )
    return recorded.card


_PRICE_SUBJECT_TYPES = {
    SubjectType.INSTRUMENT,
    SubjectType.OPTION_CONTRACT,
    SubjectType.MARKET_SESSION,
}


def audit_read_items(
    store: EvidenceStore, *, subject_id: str, subject_type: SubjectType, first: date, last: date
) -> list[EvidenceItem]:
    """A direct record read by subject and window (audit only). A request
    targeting SPY price scope inside the holdout window is refused before any
    row is read (``holdout_read_refused``), exposing nothing."""
    store._require_state(frozenset({StoreState.SERVING}))
    spy_scope = subject_type in _PRICE_SUBJECT_TYPES and (
        "SPY" in subject_id or subject_type is SubjectType.MARKET_SESSION
    )
    if spy_scope and first <= HOLDOUT_LAST_SESSION and last >= HOLDOUT_FIRST_SESSION:
        store.record_audit_event(
            store._event(
                StoreAuditEventType.HOLDOUT_READ_REFUSED,
                "holdout_restricted",
                actor=StoreWriterId.STORE_SERVICE.value,
            )
        )
        raise StoreRefusal("holdout_restricted")
    with store.read_connection() as conn:
        rows = select_rows(
            conn,
            "SELECT i.* FROM evidence_items i JOIN "
            "evidence_item_subjects s ON s.item_id = i.item_id "
            "WHERE s.subject_id = ? ORDER BY i.item_id",
            [subject_id],
        )
        registry, _ = store.evidence_registry_at(conn, store.clock())
    found = []
    for row in rows:
        verify_row("evidence_items", row)
        item = rec.parse_item(row["record_json"])
        day = item.effective_at_utc.date()
        if not first <= day <= last:
            continue
        from market_intelligence.evidence.holdout import is_holdout_restricted

        if is_holdout_restricted(item, registry):
            store.state = StoreState.READ_REFUSED
            raise IntegrityStop(IntegrityFinding.PERSISTED_HOLDOUT_VIOLATION)
        found.append(item)
    return found
