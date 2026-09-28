"""Shared primitive types for the Evidence Envelope family (design §B.0).

Every type here is strict and bounded. Timestamps must be timezone-aware and
are normalized to UTC; they always serialize as
``YYYY-MM-DDTHH:MM:SS.ffffffZ``. Decimals are carried as canonical strings,
never as floats.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from math import gcd
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from market_intelligence.agents.non_directional_output_policy import (
    find_prohibited_content_category,
)

SCHEMA_VERSION = "evidence-envelope-1"

STRICT_FROZEN = ConfigDict(extra="forbid", frozen=True, strict=True)

_UTC_MIN = datetime(2000, 1, 1, tzinfo=UTC)
_UTC_MAX = datetime(2100, 1, 1, tzinfo=UTC)


def format_utc(value: datetime) -> str:
    """The one canonical timestamp encoding (always six fractional digits)."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    value = value.astimezone(UTC)
    if not (_UTC_MIN <= value < _UTC_MAX):
        raise ValueError("timestamp outside the supported range")
    return value


UtcTimestamp = Annotated[
    datetime,
    AfterValidator(_require_utc),
    PlainSerializer(format_utc, return_type=str),
]

SessionDate = date

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Token = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ProducerId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
VersionLabel = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]

ItemId = Annotated[str, StringConstraints(pattern=r"^evi1_[0-9a-f]{64}$")]
ConflictId = Annotated[str, StringConstraints(pattern=r"^evc1_[0-9a-f]{64}$")]
BundleId = Annotated[str, StringConstraints(pattern=r"^evb1_[0-9a-f]{64}$")]
EnvelopeId = Annotated[str, StringConstraints(pattern=r"^eve1_[0-9a-f]{64}$")]
RegistryVersionId = Annotated[str, StringConstraints(pattern=r"^evr1_[0-9a-f]{64}$")]
ConfigIdentity = Annotated[
    str, StringConstraints(pattern=r"^(cfg1_[0-9a-f]{64}|cfg_none)$")
]

_CANONICAL_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]{0,17})(\.[0-9]{0,11}[1-9])?$")


def _require_canonical_decimal(value: str) -> str:
    if not _CANONICAL_DECIMAL_RE.fullmatch(value) or value == "-0":
        raise ValueError("decimal is not in canonical plain notation")
    return value


CanonicalDecimal = Annotated[str, AfterValidator(_require_canonical_decimal)]


class ExactRational(BaseModel):
    """A reduced rational: ``denominator >= 1``, ``gcd == 1``."""

    model_config = STRICT_FROZEN

    numerator: Annotated[int, Field(ge=-(10**40), le=10**40)]
    denominator: Annotated[int, Field(ge=1, le=10**40)]

    @model_validator(mode="after")
    def _check_reduced(self) -> ExactRational:
        if gcd(self.numerator, self.denominator) != 1:
            raise ValueError("rational must be in lowest terms")
        return self


# --- Sanitization (design §E, §O) ----------------------------------------------

_CREDENTIAL_RE = re.compile(
    r"(?i)(\b(api[_-]?key|apikey|secret[_-]?key|access[_-]?token|client[_-]?secret"
    r"|password|passwd|authorization|apca-api-(key|secret)-id|apca-api-secret-key)\b"
    r"|\bbearer\s+[A-Za-z0-9._~+/-]{8,}|\bsk-[A-Za-z0-9_-]{16,})"
)
_PATH_RE = re.compile(
    r"(^[A-Za-z]:[\\/]|\\\\|(^|\s)(/|~/)(home|users|etc|var|tmp|root|mnt)/"
    r"|\.duckdb\b|(^|[\\/\s])\.env\b)",
    re.IGNORECASE,
)
_EXCEPTION_RE = re.compile(r"Traceback \(most recent call last\)|\bFile \"[^\"]+\", line \d+")


def sensitive_text_category(text: str) -> str | None:
    """Return a fixed category if ``text`` looks like a credential, a local
    path, or raw exception text; otherwise ``None``. Never returns the text."""
    if _CREDENTIAL_RE.search(text):
        return "credential_like"
    if _PATH_RE.search(text):
        return "path_like"
    if _EXCEPTION_RE.search(text):
        return "exception_text"
    return None


def _require_safe_text(value: str) -> str:
    if sensitive_text_category(value) is not None:
        raise ValueError("text contains prohibited sensitive content")
    return value


def _require_display_text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("display text must be NFC-normalized")
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise ValueError("display text must not contain control characters")
    _require_safe_text(value)
    # No producer holds directional authority (registry), so the fixed
    # non-directional content policy applies to every display text.
    if find_prohibited_content_category(value) is not None:
        raise ValueError("display text violates the non-directional content policy")
    return value


SafeText = Annotated[str, AfterValidator(_require_safe_text)]
DisplayText = Annotated[
    str, Field(min_length=1, max_length=400), AfterValidator(_require_display_text)
]
