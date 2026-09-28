"""Product-side SPY holdout guard (design §O).

Implements, without changing, the preregistration's rule that the holdout is
"not ingested, built, or evaluated before the 2026-12-04 session has
finished". It blocks construction, bundling, display and inference over SPY
price evidence dated 2026-09-23 through 2026-12-04.

- **Unknown schemas are refused first.** An item whose payload schema is
  not registered is refused (``unknown_payload_schema``) before any holdout
  evaluation. Every registered schema must explicitly declare
  ``spy_price_content``; a missing declaration fails registry validation.
- **Price-bearing schemas only.** Evidence counts as SPY price evidence when
  its registered schema declares ``spy_price_content = true`` and it has a
  SPY instrument, SPY option-contract or market-session subject.
- **Every substantive date is checked**, including every ISO date or
  timestamp string inside the payload.
- **No switch.** There is no parameter, flag or configuration that disables
  the guard. Removing it needs the recorded holdout milestone and a separate
  authorization, then a reviewed code change.
- Synthetic fixtures dated outside the window are unaffected.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from market_intelligence.evidence.contracts import EvidenceItemContent
from market_intelligence.evidence.enums import SubjectType
from market_intelligence.evidence.errors import (
    EvidenceValidationError,
    HoldoutRestrictedError,
)
from market_intelligence.evidence.registry import EvidenceRegistry

HOLDOUT_FIRST_SESSION = date(2026, 9, 23)
HOLDOUT_LAST_SESSION = date(2026, 12, 4)

_EASTERN = ZoneInfo("America/New_York")
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def in_holdout_window(value: date) -> bool:
    return HOLDOUT_FIRST_SESSION <= value <= HOLDOUT_LAST_SESSION


def _is_spy_subject(subject_type: SubjectType, subject_id: str) -> bool:
    if subject_type is SubjectType.INSTRUMENT:
        return subject_id == "instrument:us_equity:SPY"
    if subject_type is SubjectType.OPTION_CONTRACT:
        return re.fullmatch(r"option:osi:SPY\d{6}[CP]\d{8}", subject_id) is not None
    return subject_type is SubjectType.MARKET_SESSION


def _eastern_date(value: datetime) -> date:
    return value.astimezone(_EASTERN).date()


def _payload_dates(value: Any) -> Iterator[date]:
    if isinstance(value, str):
        if _ISO_PREFIX.match(value):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return
            if parsed.tzinfo is not None:
                yield _eastern_date(parsed)
            else:
                yield parsed.date()
    elif isinstance(value, list):
        for element in value:
            yield from _payload_dates(element)
    elif isinstance(value, dict):
        for element in value.values():
            yield from _payload_dates(element)


def item_dates(item: EvidenceItemContent) -> Iterator[date]:
    """Every substantive date an item carries (New York dates for instants)."""
    yield _eastern_date(item.effective_at_utc)
    if item.provenance.source_observed_at_utc is not None:
        yield _eastern_date(item.provenance.source_observed_at_utc)
    scope = item.temporal_scope
    if scope.session_date is not None:
        yield scope.session_date
    for instant in (scope.scheduled_event_at_utc, scope.valid_from_utc, scope.valid_until_utc):
        if instant is not None:
            yield _eastern_date(instant)
    for subject in item.subjects:
        if subject.subject_type is SubjectType.MARKET_SESSION:
            yield date.fromisoformat(subject.subject_id[-10:])
    yield from _payload_dates(item.payload)


def carries_spy_price_evidence(item: EvidenceItemContent, registry: EvidenceRegistry) -> bool:
    schema = registry.payload_schema(item.payload_schema_id)
    if schema is None:
        raise EvidenceValidationError("unknown_payload_schema")
    if not schema.spy_price_content:
        return False
    return any(_is_spy_subject(s.subject_type, s.subject_id) for s in item.subjects)


def is_holdout_restricted(item: EvidenceItemContent, registry: EvidenceRegistry) -> bool:
    """True when the item is SPY price evidence dated inside the holdout window."""
    if not carries_spy_price_evidence(item, registry):
        return False
    return any(in_holdout_window(d) for d in item_dates(item))


def enforce_holdout_guard(item: EvidenceItemContent, registry: EvidenceRegistry) -> None:
    if is_holdout_restricted(item, registry):
        raise HoldoutRestrictedError()
