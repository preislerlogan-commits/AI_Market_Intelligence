"""Sanitized error type for the Evidence Envelope core.

Every refusal carries one fixed, bounded ``reason`` token (design §O,
"Sanitized errors"). Messages never include payload content, paths, SQL,
provider text, or raw exception text.
"""

from __future__ import annotations

import re

_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class EvidenceValidationError(ValueError):
    """A sanitized refusal. ``reason`` is a fixed token such as
    ``unknown_producer`` or ``lineage_violation``."""

    def __init__(self, reason: str) -> None:
        if not _REASON_RE.fullmatch(reason):
            reason = "invalid_reason_token"
        self.reason = reason
        super().__init__(reason)


class HoldoutRestrictedError(EvidenceValidationError):
    """Refusal by the product-side SPY holdout guard (design §O)."""

    def __init__(self) -> None:
        super().__init__("holdout_restricted")
