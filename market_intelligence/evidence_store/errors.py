"""Sanitized store errors (docs/EVIDENCE_CARD_STORAGE_DESIGN.md §10.3, §11).

Two distinct families, never confused:

- ``StoreRefusal``: an ordinary fail-closed refusal of one request (invalid
  input, an unauthorized operation, a holdout refusal, a chain-rule
  refusal). It never disables unrelated reads.
- ``IntegrityStop``: authoritative storage is known to be wrong. The store
  enters read-refused mode.

Both carry one bounded token only. No message ever contains a payload, a
record, SQL, a path, a key or exception text.
"""

from __future__ import annotations

from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence_store.enums import IntegrityFinding


class StoreRefusal(EvidenceValidationError):
    """An ordinary refusal with a bounded reason token."""


class IntegrityStop(RuntimeError):
    """Authoritative storage failed verification; the store stops reads."""

    def __init__(self, finding: IntegrityFinding) -> None:
        self.finding = finding
        self.reason = finding.value
        super().__init__(finding.value)
