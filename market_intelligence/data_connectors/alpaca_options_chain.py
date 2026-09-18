"""Read-only Alpaca SPY option-chain snapshot connector.

This module talks only to Alpaca's official read-only market-data host
(``https://data.alpaca.markets``) and only to its option-chain snapshot
endpoint::

    GET /v1beta1/options/snapshots/{underlying_symbol}

It must never import, implement, or reference brokerage order, account,
position, portfolio, exercise, or execution functionality; it must never
touch ``paper-api.alpaca.markets`` or the option-contract/trading API; and
it must never connect to Robinhood. Credential values are read from
``Settings`` and used only in request headers -- they are never printed,
logged, or included in exception messages. This module does not write to
DuckDB or any other storage; it only retrieves and normalizes snapshots.

Scope: **SPY only.** ``UNDERLYING`` is a fixed module constant and is never
accepted as a caller-supplied value -- a request for any other underlying is
rejected before any HTTP request is constructed.

Feed: the caller must pass an explicit feed of exactly ``"opra"`` or
``"indicative"``. There is no default and no automatic fallback. The feed
the caller requested is recorded verbatim on every normalized snapshot so
provenance is always explicit. ``indicative`` data may be delayed or
modified by the provider and must never be described as live OPRA data.

Open interest: Alpaca's option-chain **snapshot** endpoint does not supply
open interest, so this connector never produces it. Open interest is
documented as unavailable for this milestone (see DATA_CATALOG.md /
docs/OPTIONS_DECISION_WORKFLOW.md); it is not invented, inferred, or
defaulted to zero.

Numeric representation: normalized price/strike/Greek/implied-volatility
values are stored as ``decimal.Decimal``, built from the provider's JSON
numeric value via ``Decimal(str(value))`` rather than ``Decimal(value)`` --
converting through ``str()`` avoids baking IEEE-754 binary floating-point
representation error into values intended for reproducible analysis. Sizes
(bid/ask/trade) are plain ``int``.

Nullability: the latest quote, the latest trade, implied volatility, and any
individual Greek may all legitimately be absent from a snapshot. Every one
of them is modelled as optional and left ``None`` when the provider does not
supply it -- a missing field is never replaced with a zero.

Retrieval result: ``get_chain_snapshot`` returns an ``OptionChainSnapshotBatch``
(the single UTC retrieval instant, the request, and the normalized
snapshots) rather than a bare list, so a successful retrieval that returned
zero contracts still carries a retrieval instant and request bounds that the
storage layer can record durably (see
``market_intelligence/storage/option_chain_snapshot_repository.py``).

Request ceilings: ``MAX_EXPIRATION_RANGE_DAYS``, ``MAX_STRIKE_RANGE_WIDTH``,
``MAX_PAGES``, and ``MAX_TOTAL_CONTRACTS`` are conservative Phase 1 safety
ceilings for the first SPY intraday milestone, not contract-selection rules
-- callers must still supply explicit, bounded ranges.

Truncation is a typed, public condition, not a string to match on: when a
bounded retrieval would need more pages or contracts than its own requested
``max_pages`` / ``max_total_contracts`` allows, ``get_chain_snapshot`` raises
the public ``AlpacaOptionsChainTruncatedError`` (a subclass of
``AlpacaOptionsChainError``) with a fixed ``reason``
(``OptionChainTruncationReason.MAX_PAGES_EXCEEDED`` /
``.MAX_TOTAL_CONTRACTS_EXCEEDED``) -- callers that need to distinguish
"the window needed more data than requested" from any other chain failure
should catch this type and read ``reason``, never match on exception text.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx

from market_intelligence.config.settings import Settings

OPTIONS_BASE_URL = "https://data.alpaca.markets"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# SPY only. This is a fixed, project-approved constant -- it is never
# accepted as a caller-supplied parameter anywhere in this module, so no
# caller can point this connector at a different underlying.
UNDERLYING = "SPY"

PROVIDER = "alpaca"

# The caller must pick exactly one of these explicitly. "opra" is the
# consolidated options tape; "indicative" is a delayed/derived feed that
# must never be represented as live OPRA data. There is no default feed and
# no automatic fallback between feeds.
ALLOWED_FEEDS = frozenset({"opra", "indicative"})

ALLOWED_OPTION_TYPES = frozenset({"call", "put"})

MIN_LIMIT = 1
# Alpaca's snapshot endpoint accepts a per-page limit up to 1000; this
# project uses that same ceiling and requires callers to paginate rather
# than raise it.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 1000

# Hard ceiling on the number of pages a single get_chain_snapshot() call
# will follow, so a malformed or endless provider pagination sequence cannot
# loop indefinitely. No caller can raise this. Conservative for the first
# SPY intraday milestone: a bounded SPY chain slice for initial decision
# support fits well within 10 pages of up to 1000 contracts each.
MAX_PAGES = 10

# Hard ceiling on the total number of normalized contracts a single
# get_chain_snapshot() call will accumulate across all pages. A bounded SPY
# chain slice is far smaller than this; exceeding it means the request was
# not actually bounded and the whole call fails rather than returning an
# unbounded result. Conservative Phase 1 safety ceiling (not a
# contract-selection rule): callers must still pass explicit ranges.
MAX_TOTAL_CONTRACTS = 5_000

# Strike-range guard rails. A request whose strike window is non-positive,
# reversed, or absurdly wide is rejected before any HTTP request is built.
# MAX_STRIKE_RANGE_WIDTH is a conservative Phase 1 safety ceiling for
# initial SPY decision support -- a $500 window comfortably brackets SPY's
# near-the-money strikes; it is not a contract-selection rule.
MIN_STRIKE_PRICE = Decimal("0")
MAX_STRIKE_PRICE = Decimal("100000")
MAX_STRIKE_RANGE_WIDTH = Decimal("500")

# Expiration-range guard rails (strict YYYY-MM-DD calendar dates). A
# reversed range, or one spanning more than this many calendar days, is
# rejected before any HTTP request is built. Conservative Phase 1 safety
# ceiling for initial SPY intraday decision support (not a contract-selection
# rule): 60 calendar days covers the near-dated expirations that milestone
# needs while keeping the retrieved slice bounded.
MAX_EXPIRATION_RANGE_DAYS = 60

MAX_TIMESTAMP_LENGTH = 40
MAX_CONTRACT_SYMBOL_LENGTH = 30

# Strict RFC3339 date-time: a complete calendar date and time-of-day with an
# explicit "Z" or numeric UTC offset. Fully anchored -- date-only values,
# naive timestamps, and embedded junk cannot match.
_RFC3339_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])"
    r"[Tt](?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d)(?:\.\d+)?"
    r"(?P<offset>[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)

# Strict calendar date: YYYY-MM-DD only.
_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])$"
)

# OCC-style option contract symbol as Alpaca reports it (no padding
# spaces): a 1-6 character alphabetic root, a 6-digit YYMMDD expiration, a
# single "C"/"P", and an 8-digit strike (strike price x 1000). Fully
# anchored, so the root is unambiguously everything before the fixed
# 15-character trailing "YYMMDD[CP]NNNNNNNN".
_OCC_SYMBOL_PATTERN = re.compile(
    r"^(?P<root>[A-Za-z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CPcp])(?P<strike>\d{8})$"
)


class AlpacaOptionsCredentialsMissingError(RuntimeError):
    """Raised when an option-chain request is attempted without configured credentials."""


class AlpacaOptionsChainError(RuntimeError):
    """Raised for a sanitized Alpaca option-chain request failure.

    The message never includes request headers, credential values, page
    tokens, the request URL/query parameters, contract payloads, or the raw
    response body -- only a fixed category description plus, where consistent
    with the other connectors, a status code or exception type name.
    """


class AlpacaOptionsChainInvalidInputError(AlpacaOptionsChainError):
    """Raised when a request input fails normalization/validation.

    Validation happens before any HTTP request is constructed, so invalid or
    malicious input never reaches the network. The message never echoes raw,
    unvalidated input.
    """


class OptionChainTruncationReason(StrEnum):
    """Which bounded ceiling a retrieval would have needed to exceed to
    keep going. A fixed, two-value enum -- never inferred from provider
    text -- so a caller can distinguish the two truncation causes without
    ever matching on exception message text."""

    MAX_PAGES_EXCEEDED = "max_pages_exceeded"
    MAX_TOTAL_CONTRACTS_EXCEEDED = "max_total_contracts_exceeded"


class AlpacaOptionsChainTruncatedError(AlpacaOptionsChainError):
    """Public, typed truncation boundary.

    Raised in place of a plain ``AlpacaOptionsChainError`` specifically when
    ``get_chain_snapshot`` would need more pages, or more contracts, than
    the request's own ``max_pages`` / ``max_total_contracts`` ceiling
    allows -- i.e. the retrieval was genuinely bounded away from a further
    page or contract that existed, not merely failed. No partial result is
    ever returned in this case (same guarantee as every other
    ``AlpacaOptionsChainError``).

    A caller that needs to distinguish "the bounded window needed more data
    than requested" from any other chain failure should catch this specific
    type (subclass of ``AlpacaOptionsChainError``, so existing broad
    ``except AlpacaOptionsChainError`` handling is unaffected) and read
    ``reason`` -- never match on ``str(exc)``, which is not part of this
    exception's stable interface. ``reason`` and the resulting message are
    both drawn from the fixed ``OptionChainTruncationReason`` enum -- never
    a raw provider value, request parameter, or page token.
    """

    def __init__(self, reason: OptionChainTruncationReason) -> None:
        self.reason = reason
        if reason == OptionChainTruncationReason.MAX_PAGES_EXCEEDED:
            message = "Alpaca option-chain pagination exceeded the maximum page count."
        else:
            message = (
                "Alpaca option-chain response exceeded the maximum total contract count."
            )
        super().__init__(message)


class _MalformedSnapshotError(Exception):
    """Internal signal that a single raw snapshot failed normalization.

    Never raised across the public API -- callers only ever see the
    sanitized ``AlpacaOptionsChainError`` raised when this is caught.
    """


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def normalize_underlying(value: Any) -> str:
    """Validate that ``value`` names the one supported underlying (SPY).

    Accepts case/whitespace variants of ``"SPY"`` only. Raises
    ``AlpacaOptionsChainInvalidInputError`` for anything else -- including
    every other ticker -- before any request is built. The error never
    echoes the rejected value.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise AlpacaOptionsChainInvalidInputError("Invalid underlying: expected a string.")
    if value.strip().upper() != UNDERLYING:
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid underlying: this connector supports {UNDERLYING} only."
        )
    return UNDERLYING


def normalize_feed(value: Any) -> str:
    """Validate an explicit option-data feed.

    Requires exactly ``"opra"`` or ``"indicative"`` (case/whitespace
    insensitive). There is no default. Raises
    ``AlpacaOptionsChainInvalidInputError`` for anything else. The error
    never echoes the rejected value.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise AlpacaOptionsChainInvalidInputError("Invalid feed: expected a string.")
    normalized = value.strip().lower()
    if normalized not in ALLOWED_FEEDS:
        raise AlpacaOptionsChainInvalidInputError(
            "Invalid feed: must be explicitly 'opra' or 'indicative'."
        )
    return normalized


def normalize_option_type(value: Any) -> str | None:
    """Validate an optional call/put filter.

    ``None`` means "no type filter" and is returned unchanged. Otherwise
    requires exactly ``"call"`` or ``"put"`` (case/whitespace insensitive).
    Raises ``AlpacaOptionsChainInvalidInputError`` for anything else.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise AlpacaOptionsChainInvalidInputError("Invalid type: expected 'call', 'put', or None.")
    normalized = value.strip().lower()
    if normalized not in ALLOWED_OPTION_TYPES:
        raise AlpacaOptionsChainInvalidInputError("Invalid type: must be 'call' or 'put'.")
    return normalized


def normalize_expiration_date(value: Any, *, field_name: str) -> str:
    """Validate a required expiration-window bound as a strict YYYY-MM-DD date.

    Rejects non-strings, malformed shapes, and impossible calendar dates
    before any request is built. The error never echoes the rejected value.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid {field_name}: must be a YYYY-MM-DD calendar date."
        )
    trimmed = value.strip()
    if not _DATE_PATTERN.match(trimmed):
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid {field_name}: must be a YYYY-MM-DD calendar date."
        )
    try:
        datetime.strptime(trimmed, "%Y-%m-%d")  # noqa: DTZ007 -- date-only validation
    except ValueError:
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid {field_name}: is not a real calendar date."
        ) from None
    return trimmed


def normalize_strike_price(value: Any, *, field_name: str) -> Decimal:
    """Validate a required strike-window bound as a finite, in-range Decimal.

    Accepts an ``int``, ``float``, ``str``, or ``Decimal`` (booleans
    rejected). Converts through ``str()`` for ``float`` inputs so no
    binary-float artifact is introduced. Rejects non-finite values, values
    at or below zero, and values above ``MAX_STRIKE_PRICE``. The error never
    echoes the rejected value.
    """
    if isinstance(value, bool):
        raise AlpacaOptionsChainInvalidInputError(f"Invalid {field_name}: expected a number.")
    try:
        if isinstance(value, Decimal):
            candidate = value
        elif isinstance(value, int):
            candidate = Decimal(value)
        elif isinstance(value, float):
            candidate = Decimal(str(value))
        elif isinstance(value, str):
            candidate = Decimal(value.strip())
        else:
            raise AlpacaOptionsChainInvalidInputError(
                f"Invalid {field_name}: expected a number."
            )
    except (ArithmeticError, ValueError):
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid {field_name}: is not a valid number."
        ) from None
    if not candidate.is_finite():
        raise AlpacaOptionsChainInvalidInputError(f"Invalid {field_name}: must be a finite number.")
    if candidate <= MIN_STRIKE_PRICE:
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid {field_name}: must be greater than zero."
        )
    if candidate > MAX_STRIKE_PRICE:
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid {field_name}: exceeds the maximum supported strike price."
        )
    return candidate


def normalize_limit(value: Any) -> int:
    """Normalize and validate a per-page result-count limit.

    Raises ``AlpacaOptionsChainInvalidInputError`` unless ``value`` is a
    plain ``int`` (booleans rejected) within ``[MIN_LIMIT, MAX_LIMIT]``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AlpacaOptionsChainInvalidInputError("Invalid limit: expected an integer.")
    if not (MIN_LIMIT <= value <= MAX_LIMIT):
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid limit: must be between {MIN_LIMIT} and {MAX_LIMIT}."
        )
    return value


def normalize_max_pages(value: Any) -> int:
    """Normalize and validate the ``max_pages`` pagination bound.

    Raises ``AlpacaOptionsChainInvalidInputError`` unless ``value`` is a
    plain ``int`` (booleans rejected) within ``[1, MAX_PAGES]``. This
    enforces ``MAX_PAGES`` as a hard ceiling no caller can raise. The error
    never echoes the raw input.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AlpacaOptionsChainInvalidInputError("Invalid max_pages: expected an integer.")
    if not (1 <= value <= MAX_PAGES):
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid max_pages: must be between 1 and {MAX_PAGES}."
        )
    return value


def normalize_max_total_contracts(value: Any) -> int:
    """Normalize and validate the ``max_total_contracts`` accumulation bound.

    Raises ``AlpacaOptionsChainInvalidInputError`` unless ``value`` is a
    plain ``int`` (booleans rejected) within ``[1, MAX_TOTAL_CONTRACTS]``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AlpacaOptionsChainInvalidInputError(
            "Invalid max_total_contracts: expected an integer."
        )
    if not (1 <= value <= MAX_TOTAL_CONTRACTS):
        raise AlpacaOptionsChainInvalidInputError(
            f"Invalid max_total_contracts: must be between 1 and {MAX_TOTAL_CONTRACTS}."
        )
    return value


@dataclass(frozen=True)
class OptionChainRequest:
    """A fully validated, bounded SPY option-chain snapshot request.

    Only ever produced by ``normalize_option_chain_request`` -- constructing
    one directly bypasses validation and is not supported. ``underlying`` is
    always ``"SPY"`` and ``feed`` is always an explicit ``"opra"`` /
    ``"indicative"``.
    """

    underlying: str
    feed: str
    expiration_date_gte: str
    expiration_date_lte: str
    strike_price_gte: Decimal
    strike_price_lte: Decimal
    option_type: str | None
    limit: int
    max_pages: int
    max_total_contracts: int


def normalize_option_chain_request(
    *,
    underlying: Any,
    feed: Any,
    expiration_date_gte: Any,
    expiration_date_lte: Any,
    strike_price_gte: Any,
    strike_price_lte: Any,
    option_type: Any = None,
    limit: Any = DEFAULT_LIMIT,
    max_pages: int = 1,
    max_total_contracts: int = MAX_TOTAL_CONTRACTS,
) -> OptionChainRequest:
    """Validate and normalize every field of an option-chain snapshot request.

    Raises ``AlpacaOptionsChainInvalidInputError`` (before any HTTP request
    is constructed) for: a non-SPY underlying; an unknown or missing feed;
    an unknown option type; a malformed expiration/strike bound; a reversed
    expiration or strike range; an expiration range wider than
    ``MAX_EXPIRATION_RANGE_DAYS`` days or a strike range wider than
    ``MAX_STRIKE_RANGE_WIDTH``; or an out-of-range ``limit`` / ``max_pages``
    / ``max_total_contracts``. No error message echoes raw input.
    """
    normalized_underlying = normalize_underlying(underlying)
    normalized_feed = normalize_feed(feed)
    normalized_type = normalize_option_type(option_type)

    exp_gte = normalize_expiration_date(expiration_date_gte, field_name="expiration_date_gte")
    exp_lte = normalize_expiration_date(expiration_date_lte, field_name="expiration_date_lte")
    exp_gte_dt = datetime.strptime(exp_gte, "%Y-%m-%d")  # noqa: DTZ007
    exp_lte_dt = datetime.strptime(exp_lte, "%Y-%m-%d")  # noqa: DTZ007
    if exp_gte_dt > exp_lte_dt:
        raise AlpacaOptionsChainInvalidInputError(
            "Invalid expiration range: expiration_date_gte must not be after expiration_date_lte."
        )
    if (exp_lte_dt - exp_gte_dt).days > MAX_EXPIRATION_RANGE_DAYS:
        raise AlpacaOptionsChainInvalidInputError(
            "Invalid expiration range: window exceeds the maximum supported span."
        )

    strike_gte = normalize_strike_price(strike_price_gte, field_name="strike_price_gte")
    strike_lte = normalize_strike_price(strike_price_lte, field_name="strike_price_lte")
    if strike_gte > strike_lte:
        raise AlpacaOptionsChainInvalidInputError(
            "Invalid strike range: strike_price_gte must not be greater than strike_price_lte."
        )
    if strike_lte - strike_gte > MAX_STRIKE_RANGE_WIDTH:
        raise AlpacaOptionsChainInvalidInputError(
            "Invalid strike range: window exceeds the maximum supported width."
        )

    return OptionChainRequest(
        underlying=normalized_underlying,
        feed=normalized_feed,
        expiration_date_gte=exp_gte,
        expiration_date_lte=exp_lte,
        strike_price_gte=strike_gte,
        strike_price_lte=strike_lte,
        option_type=normalized_type,
        limit=normalize_limit(limit),
        max_pages=normalize_max_pages(max_pages),
        max_total_contracts=normalize_max_total_contracts(max_total_contracts),
    )


@dataclass(frozen=True)
class ParsedContractSymbol:
    """The four fields parsed out of an OCC-style option contract symbol."""

    underlying: str
    expiration_date: str
    option_type: str
    strike_price: Decimal


def parse_occ_symbol(symbol: Any) -> ParsedContractSymbol:
    """Parse and validate an OCC-style option contract symbol.

    Returns the underlying, expiration date (``YYYY-MM-DD``), option type
    (``call`` / ``put``), and strike price (a ``Decimal``, the 8-digit
    strike field divided by 1000). Raises ``_MalformedSnapshotError`` for a
    non-string, an over-long value, a value that does not match the strict
    OCC shape, or an impossible expiration calendar date. This is an
    internal parser: callers only ever see the sanitized
    ``AlpacaOptionsChainError``.
    """
    if not isinstance(symbol, str):
        raise _MalformedSnapshotError("contract symbol invalid")
    trimmed = symbol.strip()
    if not trimmed or len(trimmed) > MAX_CONTRACT_SYMBOL_LENGTH:
        raise _MalformedSnapshotError("contract symbol invalid")
    match = _OCC_SYMBOL_PATTERN.match(trimmed)
    if match is None:
        raise _MalformedSnapshotError("contract symbol invalid")

    root = match.group("root").upper()
    yy = int(match.group("yy"))
    mm = int(match.group("mm"))
    dd = int(match.group("dd"))
    expiration = f"20{yy:02d}-{mm:02d}-{dd:02d}"
    try:
        datetime.strptime(expiration, "%Y-%m-%d")  # noqa: DTZ007
    except ValueError:
        raise _MalformedSnapshotError("contract symbol invalid") from None

    option_type = "call" if match.group("cp").upper() == "C" else "put"

    strike_int = int(match.group("strike"))
    strike = Decimal(f"{strike_int // 1000}.{strike_int % 1000:03d}")

    return ParsedContractSymbol(
        underlying=root,
        expiration_date=expiration,
        option_type=option_type,
        strike_price=strike,
    )


@dataclass(frozen=True)
class OptionChainSnapshot:
    """A single normalized SPY option-chain snapshot.

    Contains only the fields Alpaca's option-chain *snapshot* endpoint
    actually supplies plus this connector's own provenance. There is
    deliberately **no open-interest field**: the snapshot endpoint does not
    supply open interest, and this connector never invents it.

    ``feed`` is the exact feed the caller requested (``"opra"`` /
    ``"indicative"``) -- recorded on every snapshot so provenance is always
    explicit; ``indicative`` data may be delayed or modified and must not be
    described as live OPRA data. ``contract_symbol``, ``expiration_date``,
    ``option_type``, and ``strike_price`` are parsed from the OCC symbol and
    confirmed to agree with the request filters. Every quote, trade, and
    Greek field is optional and left ``None`` when the provider does not
    supply it -- a missing value is never replaced with zero.
    ``retrieved_at`` is this connector's own UTC retrieval timestamp, set
    once per ``get_chain_snapshot`` call.
    """

    provider: str
    underlying: str
    feed: str
    contract_symbol: str
    expiration_date: str
    option_type: str
    strike_price: Decimal
    quote_timestamp: str | None
    bid_price: Decimal | None
    bid_size: int | None
    ask_price: Decimal | None
    ask_size: int | None
    trade_timestamp: str | None
    trade_price: Decimal | None
    trade_size: int | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    rho: Decimal | None
    retrieved_at: str


@dataclass(frozen=True)
class OptionChainSnapshotBatch:
    """The full result of one bounded, read-only option-chain retrieval.

    ``retrieved_at`` is the single UTC retrieval instant (``Z``-suffixed
    RFC3339) stamped on this call and on every snapshot in ``snapshots``. It
    is populated even when ``snapshots`` is empty, so a successful chain
    retrieval that returned zero contracts is still fully described (feed,
    request bounds, retrieval instant, zero count) and can be recorded
    durably by the storage layer. ``request`` is the validated
    ``OptionChainRequest`` this retrieval was made under. ``snapshots`` is
    ordered deterministically by (expiration, option type, strike, symbol).
    """

    retrieved_at: str
    request: OptionChainRequest
    snapshots: tuple[OptionChainSnapshot, ...]


def _status_category(status_code: int) -> str:
    return f"{status_code // 100}xx"


def _parse_snapshot_timestamp(value: Any) -> str:
    """Parse and normalize a provider-reported timestamp to UTC ("Z" suffix).

    Raises ``_MalformedSnapshotError`` for anything that is not a strict,
    fully-specified RFC3339 date-time.
    """
    if not isinstance(value, str):
        raise _MalformedSnapshotError("timestamp invalid")
    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_TIMESTAMP_LENGTH:
        raise _MalformedSnapshotError("timestamp invalid")
    match = _RFC3339_PATTERN.match(trimmed)
    if match is None:
        raise _MalformedSnapshotError("timestamp invalid")
    offset = match.group("offset")
    candidate = trimmed[:-1] + "+00:00" if offset in ("Z", "z") else trimmed
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise _MalformedSnapshotError("timestamp invalid") from None
    if parsed.tzinfo is None:
        raise _MalformedSnapshotError("timestamp invalid")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_decimal(
    value: Any, *, allow_negative: bool
) -> Decimal | None:
    """Normalize an optional provider numeric field to ``Decimal``.

    ``None`` and an absent key both yield ``None``. Rejects booleans,
    non-numeric types, and non-finite floats (NaN, infinity). When
    ``allow_negative`` is false a negative value is also rejected -- used for
    prices, sizes-as-decimals, implied volatility, and the Greeks that are
    non-negative by construction for standard options (gamma, vega).
    ``delta``, ``theta``, and ``rho`` pass ``allow_negative=True`` because
    they can legitimately be negative.
    """
    if value is None:
        return None
    if not _is_number(value):
        raise _MalformedSnapshotError("numeric field invalid")
    if isinstance(value, float) and not math.isfinite(value):
        raise _MalformedSnapshotError("numeric field invalid")
    result = Decimal(str(value))
    if not allow_negative and result < 0:
        raise _MalformedSnapshotError("numeric field must not be negative")
    return result


def _optional_size(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _MalformedSnapshotError("size field invalid")
    if value < 0:
        raise _MalformedSnapshotError("size field must not be negative")
    return value


def _require_dict(value: Any, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _MalformedSnapshotError(f"{field} is not an object")
    return value


def _normalize_snapshot(
    contract_symbol: Any,
    raw: Any,
    *,
    request: OptionChainRequest,
    retrieved_at: str,
) -> OptionChainSnapshot:
    """Normalize a single raw snapshot. Raises ``_MalformedSnapshotError`` if unusable."""
    parsed = parse_occ_symbol(contract_symbol)

    if parsed.underlying != request.underlying:
        raise _MalformedSnapshotError("contract underlying does not match the request")
    if request.option_type is not None and parsed.option_type != request.option_type:
        raise _MalformedSnapshotError("contract type does not match the request filter")
    if not (request.strike_price_gte <= parsed.strike_price <= request.strike_price_lte):
        raise _MalformedSnapshotError("contract strike is outside the requested range")
    if not (
        request.expiration_date_gte <= parsed.expiration_date <= request.expiration_date_lte
    ):
        raise _MalformedSnapshotError("contract expiration is outside the requested range")

    if not isinstance(raw, dict):
        raise _MalformedSnapshotError("snapshot is not an object")

    quote = _require_dict(raw.get("latestQuote"), field="latestQuote")
    trade = _require_dict(raw.get("latestTrade"), field="latestTrade")
    greeks = _require_dict(raw.get("greeks"), field="greeks")

    quote_timestamp = None
    bid_price = bid_size = ask_price = ask_size = None
    if quote is not None:
        if quote.get("t") is not None:
            quote_timestamp = _parse_snapshot_timestamp(quote.get("t"))
        bid_price = _optional_decimal(quote.get("bp"), allow_negative=False)
        bid_size = _optional_size(quote.get("bs"))
        ask_price = _optional_decimal(quote.get("ap"), allow_negative=False)
        ask_size = _optional_size(quote.get("as"))

    trade_timestamp = trade_price = trade_size = None
    if trade is not None:
        if trade.get("t") is not None:
            trade_timestamp = _parse_snapshot_timestamp(trade.get("t"))
        trade_price = _optional_decimal(trade.get("p"), allow_negative=False)
        trade_size = _optional_size(trade.get("s"))

    delta = gamma = theta = vega = rho = None
    if greeks is not None:
        delta = _optional_decimal(greeks.get("delta"), allow_negative=True)
        gamma = _optional_decimal(greeks.get("gamma"), allow_negative=False)
        theta = _optional_decimal(greeks.get("theta"), allow_negative=True)
        vega = _optional_decimal(greeks.get("vega"), allow_negative=False)
        rho = _optional_decimal(greeks.get("rho"), allow_negative=True)

    implied_volatility = _optional_decimal(raw.get("impliedVolatility"), allow_negative=False)

    return OptionChainSnapshot(
        provider=PROVIDER,
        underlying=request.underlying,
        feed=request.feed,
        contract_symbol=contract_symbol.strip(),
        expiration_date=parsed.expiration_date,
        option_type=parsed.option_type,
        strike_price=parsed.strike_price,
        quote_timestamp=quote_timestamp,
        bid_price=bid_price,
        bid_size=bid_size,
        ask_price=ask_price,
        ask_size=ask_size,
        trade_timestamp=trade_timestamp,
        trade_price=trade_price,
        trade_size=trade_size,
        implied_volatility=implied_volatility,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
        retrieved_at=retrieved_at,
    )


def _build_params(request: OptionChainRequest, page_token: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "feed": request.feed,
        "limit": request.limit,
        "expiration_date_gte": request.expiration_date_gte,
        "expiration_date_lte": request.expiration_date_lte,
        "strike_price_gte": format(request.strike_price_gte, "f"),
        "strike_price_lte": format(request.strike_price_lte, "f"),
    }
    if request.option_type is not None:
        params["type"] = request.option_type
    if page_token is not None:
        params["page_token"] = page_token
    return params


def _sort_key(snapshot: OptionChainSnapshot) -> tuple[str, str, Decimal, str]:
    return (
        snapshot.expiration_date,
        snapshot.option_type,
        snapshot.strike_price,
        snapshot.contract_symbol,
    )


class AlpacaOptionsChainClient:
    """Minimal read-only client for Alpaca's SPY option-chain snapshot endpoint.

    Snapshots only: this client has no methods for orders, accounts,
    positions, portfolio, exercise, or execution, and none touch
    ``paper-api.alpaca.markets`` or the option-contract/trading API. It does
    not write to any database. It supports the ``SPY`` underlying only.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings or Settings()
        self._timeout = timeout

    def is_configured(self) -> bool:
        return self._settings.provider_status()["alpaca"]

    def _auth_headers(self) -> dict[str, str]:
        if not self.is_configured():
            raise AlpacaOptionsCredentialsMissingError("Alpaca credentials are not configured.")
        assert self._settings.alpaca_api_key is not None
        assert self._settings.alpaca_api_secret is not None
        return {
            "APCA-API-KEY-ID": self._settings.alpaca_api_key.get_secret_value(),
            "APCA-API-SECRET-KEY": self._settings.alpaca_api_secret.get_secret_value(),
        }

    def get_chain_snapshot(
        self,
        request: OptionChainRequest,
        *,
        client: httpx.Client | None = None,
    ) -> OptionChainSnapshotBatch:
        """Fetch one bounded SPY option-chain snapshot. Read-only.

        ``request`` must be an ``OptionChainRequest`` produced by
        ``normalize_option_chain_request`` -- it is already fully validated.
        Follows pagination deterministically via ``next_page_token``, stops
        at ``request.max_pages`` / ``request.max_total_contracts``, and makes
        no automatic retry.

        Returns an ``OptionChainSnapshotBatch`` carrying the single UTC
        retrieval instant, the request, and the deterministically ordered
        snapshots. An empty snapshot set is a valid, successful result: the
        returned batch still carries ``retrieved_at`` and the request so the
        retrieval is fully auditable.

        Raises ``AlpacaOptionsChainInvalidInputError`` if ``request`` is not
        an ``OptionChainRequest``; ``AlpacaOptionsCredentialsMissingError``
        if credentials are not configured; ``AlpacaOptionsChainTruncatedError``
        (a typed, public ``AlpacaOptionsChainError`` subclass -- see that
        class) if pagination would exceed ``max_pages`` or the total contract
        count would exceed ``max_total_contracts``, distinguished via its
        ``reason`` attribute; or the base ``AlpacaOptionsChainError``
        (sanitized) on any other request failure, malformed JSON, an unusable
        response shape, a malformed/mismatched contract symbol, a malformed
        snapshot field, a duplicate contract symbol, or an invalid or
        repeated pagination token. On any failure -- including a later-page
        failure -- no partial result is returned.
        """
        if not isinstance(request, OptionChainRequest):
            raise AlpacaOptionsChainInvalidInputError(
                "Invalid request: expected a normalized OptionChainRequest."
            )

        headers = self._auth_headers()

        owns_client = client is None
        http_client = client or httpx.Client(base_url=OPTIONS_BASE_URL, timeout=self._timeout)

        retrieved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        snapshots_by_symbol: dict[str, OptionChainSnapshot] = {}
        seen_tokens: set[str] = set()
        page_token: str | None = None

        try:
            for _ in range(request.max_pages):
                params = _build_params(request, page_token)
                try:
                    response = http_client.get(
                        f"/v1beta1/options/snapshots/{request.underlying}",
                        headers=headers,
                        params=params,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    raise AlpacaOptionsChainError(
                        "Alpaca option-chain request failed with status "
                        f"{exc.response.status_code}."
                    ) from None
                except httpx.RequestError as exc:
                    raise AlpacaOptionsChainError(
                        f"Alpaca option-chain request failed: {type(exc).__name__}."
                    ) from None
                except ValueError:
                    raise AlpacaOptionsChainError(
                        "Alpaca option-chain response was not valid JSON."
                    ) from None

                if not isinstance(payload, dict):
                    raise AlpacaOptionsChainError(
                        "Alpaca option-chain response payload was not a JSON object."
                    )

                raw_snapshots = payload.get("snapshots")
                if not isinstance(raw_snapshots, dict):
                    raise AlpacaOptionsChainError(
                        "Alpaca option-chain response payload did not include a snapshots object."
                    )

                for raw_symbol, raw_snapshot in raw_snapshots.items():
                    try:
                        snapshot = _normalize_snapshot(
                            raw_symbol,
                            raw_snapshot,
                            request=request,
                            retrieved_at=retrieved_at,
                        )
                    except _MalformedSnapshotError:
                        raise AlpacaOptionsChainError(
                            "Alpaca option-chain response contained a malformed snapshot."
                        ) from None

                    if snapshot.contract_symbol in snapshots_by_symbol:
                        raise AlpacaOptionsChainError(
                            "Alpaca option-chain response contained a duplicate contract symbol."
                        )
                    snapshots_by_symbol[snapshot.contract_symbol] = snapshot

                    if len(snapshots_by_symbol) > request.max_total_contracts:
                        raise AlpacaOptionsChainTruncatedError(
                            OptionChainTruncationReason.MAX_TOTAL_CONTRACTS_EXCEEDED
                        )

                next_token = payload.get("next_page_token")
                if next_token is None:
                    break
                if not isinstance(next_token, str) or not next_token:
                    raise AlpacaOptionsChainError(
                        "Alpaca option-chain response contained an invalid pagination token."
                    )
                if next_token in seen_tokens:
                    raise AlpacaOptionsChainError(
                        "Alpaca option-chain pagination repeated a page token."
                    )
                seen_tokens.add(next_token)
                page_token = next_token
            else:
                raise AlpacaOptionsChainTruncatedError(
                    OptionChainTruncationReason.MAX_PAGES_EXCEEDED
                )
        finally:
            if owns_client:
                http_client.close()

        return OptionChainSnapshotBatch(
            retrieved_at=retrieved_at,
            request=request,
            snapshots=tuple(sorted(snapshots_by_symbol.values(), key=_sort_key)),
        )
