"""Immutable, strictly validated orchestration job contracts.

Only three reviewed job types may ever be represented here: ``alpaca_news``,
``alpaca_bars``, and ``fred_observations``. This schema has no field for an
arbitrary Python import, a shell command, an executable path, SQL, a URL, a
caller-supplied API host, an order/account/execution job, a Robinhood job,
or an unreviewed provider name -- those are not merely rejected by
validation, there is simply nowhere for them to be expressed. Every
contract is a frozen dataclass: once constructed and validated, it cannot
be mutated.

Provider and dataset name are not free-form per contract: each job type has
exactly one fixed, reviewed provider and dataset name (matching the
existing repositories' own ``DEFAULT_PROVIDER``/``DEFAULT_DATASET_NAME``
constants), and a contract whose declared provider/dataset_name does not
match its job type's fixed value is rejected. Job-specific parameters reuse
the same connector-level normalization/validation functions already used
by ``AlpacaNewsClient``, ``AlpacaBarsClient``, and ``FredMacroDataClient``,
so this layer can never accept an input those connectors would themselves
reject -- and every parameter value must already be in its normalized
form (mirroring ``MacroObservationRepository``'s own "must already be
normalized" convention), so a contract can never silently coerce a
sloppy/ambiguous value.

``AlpacaBarsJobParams.feed``/``adjustment``/``currency`` must equal the
bars connector's own fixed module constants -- this layer can never
request a different feed, adjustment, or currency than the reviewed
connector already enforces, and can never accept a caller-supplied API
host of any kind (no host field exists on any contract at all).
``FredObservationsJobParams`` similarly never re-specifies or overrides the
FRED connector's own fixed real-time-period/units provenance -- that
remains enforced solely by the connector itself (see
``market_intelligence/data_connectors/fred_macro_data.py``).

Neither this module nor any orchestration job contract computes or stores
a date window directly: windows are always computed deterministically at
run time from one shared injected UTC "as-of" clock (see
``market_intelligence/orchestration/windows.py``), so every job in one run
agrees on "now" and no window ever includes a partial/still-in-progress
current period.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from market_intelligence.data_connectors.alpaca_bars import (
    DATA_ADJUSTMENT,
    DATA_CURRENCY,
    DATA_FEED,
    AlpacaBarsInvalidInputError,
)
from market_intelligence.data_connectors.alpaca_bars import (
    normalize_timeframe as normalize_bars_timeframe,
)
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaInvalidSymbolError,
    normalize_symbol,
)
from market_intelligence.data_connectors.alpaca_news import (
    AlpacaNewsInvalidInputError,
)
from market_intelligence.data_connectors.alpaca_news import (
    normalize_limit as normalize_news_limit,
)
from market_intelligence.data_connectors.fred_macro_data import (
    FredInvalidSeriesIdError,
    normalize_series_id,
)
from market_intelligence.storage.bar_repository import DEFAULT_DATASET_NAME as _BARS_DATASET_NAME
from market_intelligence.storage.macro_observation_repository import (
    DEFAULT_DATASET_NAME as _MACRO_DATASET_NAME,
)
from market_intelligence.storage.macro_observation_repository import (
    DEFAULT_PROVIDER as _FRED_PROVIDER,
)
from market_intelligence.storage.news_repository import DEFAULT_DATASET_NAME as _NEWS_DATASET_NAME

_ALPACA_PROVIDER = "alpaca"

JOB_TYPE_ALPACA_NEWS = "alpaca_news"
JOB_TYPE_ALPACA_BARS = "alpaca_bars"
JOB_TYPE_FRED_OBSERVATIONS = "fred_observations"
JOB_TYPES = (JOB_TYPE_ALPACA_NEWS, JOB_TYPE_ALPACA_BARS, JOB_TYPE_FRED_OBSERVATIONS)

FIXED_PROVIDER_BY_JOB_TYPE: dict[str, str] = {
    JOB_TYPE_ALPACA_NEWS: _ALPACA_PROVIDER,
    JOB_TYPE_ALPACA_BARS: _ALPACA_PROVIDER,
    JOB_TYPE_FRED_OBSERVATIONS: _FRED_PROVIDER,
}
FIXED_DATASET_NAME_BY_JOB_TYPE: dict[str, str] = {
    JOB_TYPE_ALPACA_NEWS: _NEWS_DATASET_NAME,
    JOB_TYPE_ALPACA_BARS: _BARS_DATASET_NAME,
    JOB_TYPE_FRED_OBSERVATIONS: _MACRO_DATASET_NAME,
}

JOB_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")

# Project-conservative bounds. Some intentionally sit below (never above)
# the corresponding connector's own ceiling -- see each constant's comment.
NEWS_MAX_LIMIT = 50  # matches AlpacaNewsClient.MAX_LIMIT; re-validated via normalize_news_limit.
BARS_MAX_LIMIT = 500  # stricter than AlpacaBarsClient.MAX_LIMIT (1000), per the reviewed job spec.
BARS_LOOKBACK_DAYS_MIN = 1
BARS_LOOKBACK_DAYS_MAX = 30
FRED_MAX_LIMIT = 1000  # matches FredMacroDataClient.MAX_OBSERVATIONS_LIMIT.
FRED_LOOKBACK_DAYS_MIN = 1
FRED_LOOKBACK_DAYS_MAX = 180
# A conservative orchestration-level ceiling, stricter than either
# connector's own page-count bound (50): the initial reviewed jobs all use
# max_pages=1, and raising this ceiling is itself a reviewable change.
ORCHESTRATION_MAX_PAGES = 3


class JobContractValidationError(ValueError):
    """Raised when a job contract or its parameters fail validation.

    Never echoes raw untrusted input (e.g. a malformed symbol/series ID) in
    its message -- mirrors the connectors' own input-validation error
    conventions.
    """


def _require_bounded_int(value: object, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JobContractValidationError(f"Invalid {field_name}: expected an integer.")
    if not (minimum <= value <= maximum):
        raise JobContractValidationError(
            f"Invalid {field_name}: must be between {minimum} and {maximum}."
        )
    return value


@dataclass(frozen=True)
class AlpacaNewsJobParams:
    """Bounded parameters for an ``alpaca_news`` job.

    ``symbol`` must already be normalized (as ``normalize_symbol`` would
    produce it); ``limit`` is bounded by the news connector's own limit.
    """

    symbol: str
    limit: int

    def __post_init__(self) -> None:
        try:
            normalized_symbol = normalize_symbol(self.symbol)
        except AlpacaInvalidSymbolError as exc:
            raise JobContractValidationError(f"Invalid symbol: {exc}") from None
        if normalized_symbol != self.symbol:
            raise JobContractValidationError("Invalid symbol: must already be normalized.")

        try:
            normalize_news_limit(self.limit)
        except AlpacaNewsInvalidInputError as exc:
            raise JobContractValidationError(f"Invalid limit: {exc}") from None
        _require_bounded_int(self.limit, field_name="limit", minimum=1, maximum=NEWS_MAX_LIMIT)


@dataclass(frozen=True)
class AlpacaBarsJobParams:
    """Bounded parameters for an ``alpaca_bars`` job.

    ``timeframe`` must already be one of the project-approved, normalized
    values. ``feed``/``adjustment``/``currency`` must equal the bars
    connector's own fixed constants -- this contract can never request a
    different feed, adjustment, or currency than the reviewed connector
    already enforces, and no field on this contract accepts a caller-
    supplied API host of any kind. The date window itself is not stored
    here: it is computed deterministically at run time from
    ``lookback_days`` and one shared injected UTC "as-of" clock (see
    ``market_intelligence/orchestration/windows.py``), so it is always a
    fully completed historical window, never a partial current day.
    """

    symbol: str
    timeframe: str
    lookback_days: int
    limit: int
    max_pages: int
    feed: str
    adjustment: str
    currency: str

    def __post_init__(self) -> None:
        try:
            normalized_symbol = normalize_symbol(self.symbol)
        except AlpacaInvalidSymbolError as exc:
            raise JobContractValidationError(f"Invalid symbol: {exc}") from None
        if normalized_symbol != self.symbol:
            raise JobContractValidationError("Invalid symbol: must already be normalized.")

        try:
            normalized_timeframe = normalize_bars_timeframe(self.timeframe)
        except AlpacaBarsInvalidInputError as exc:
            raise JobContractValidationError(f"Invalid timeframe: {exc}") from None
        if normalized_timeframe != self.timeframe:
            raise JobContractValidationError("Invalid timeframe: must already be normalized.")

        if self.feed != DATA_FEED:
            raise JobContractValidationError(
                "Invalid feed: must match the reviewed connector's fixed feed."
            )
        if self.adjustment != DATA_ADJUSTMENT:
            raise JobContractValidationError(
                "Invalid adjustment: must match the reviewed connector's fixed adjustment."
            )
        if self.currency != DATA_CURRENCY:
            raise JobContractValidationError(
                "Invalid currency: must match the reviewed connector's fixed currency."
            )

        _require_bounded_int(
            self.lookback_days,
            field_name="lookback_days",
            minimum=BARS_LOOKBACK_DAYS_MIN,
            maximum=BARS_LOOKBACK_DAYS_MAX,
        )
        _require_bounded_int(self.limit, field_name="limit", minimum=1, maximum=BARS_MAX_LIMIT)
        _require_bounded_int(
            self.max_pages, field_name="max_pages", minimum=1, maximum=ORCHESTRATION_MAX_PAGES
        )


@dataclass(frozen=True)
class FredObservationsJobParams:
    """Bounded parameters for a ``fred_observations`` job.

    ``series_id`` must already be normalized. The FRED connector itself
    always enforces the full real-time-period/``units=lin`` provenance (see
    ``market_intelligence/data_connectors/fred_macro_data.py``) -- this
    contract does not, and must not, re-specify or override that. The date
    window is computed deterministically at run time from
    ``lookback_days`` and one shared injected UTC "as-of" clock, ending the
    day *before* the as-of date so a still-possibly-revised or
    not-yet-published "today" observation is never requested.
    """

    series_id: str
    lookback_days: int
    limit: int
    max_pages: int

    def __post_init__(self) -> None:
        try:
            normalized_series_id = normalize_series_id(self.series_id)
        except FredInvalidSeriesIdError as exc:
            raise JobContractValidationError(f"Invalid series_id: {exc}") from None
        if normalized_series_id != self.series_id:
            raise JobContractValidationError("Invalid series_id: must already be normalized.")

        _require_bounded_int(
            self.lookback_days,
            field_name="lookback_days",
            minimum=FRED_LOOKBACK_DAYS_MIN,
            maximum=FRED_LOOKBACK_DAYS_MAX,
        )
        _require_bounded_int(self.limit, field_name="limit", minimum=1, maximum=FRED_MAX_LIMIT)
        _require_bounded_int(
            self.max_pages, field_name="max_pages", minimum=1, maximum=ORCHESTRATION_MAX_PAGES
        )


JobParams = AlpacaNewsJobParams | AlpacaBarsJobParams | FredObservationsJobParams

_PARAMS_TYPE_BY_JOB_TYPE: dict[str, type] = {
    JOB_TYPE_ALPACA_NEWS: AlpacaNewsJobParams,
    JOB_TYPE_ALPACA_BARS: AlpacaBarsJobParams,
    JOB_TYPE_FRED_OBSERVATIONS: FredObservationsJobParams,
}


@dataclass(frozen=True)
class JobContract:
    """An immutable, strictly validated orchestration job contract.

    Fields: a stable ``job_id``, the reviewed ``job_type``, an ``enabled``
    boolean, the job type's fixed ``provider`` and ``dataset_name``, and
    job-specific bounded ``params`` carrying this job type's explicit,
    enforced provenance. Deterministic ordering across a set of contracts
    is a property of how they are loaded (see
    ``market_intelligence/orchestration/config.py``, which always returns
    contracts sorted by ``job_id``), not of any single contract.
    """

    job_id: str
    job_type: str
    enabled: bool
    provider: str
    dataset_name: str
    params: JobParams

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not JOB_ID_PATTERN.match(self.job_id):
            raise JobContractValidationError(
                "Invalid job_id: must be 1-64 characters, lowercase letters, digits, "
                "and underscores only."
            )
        if self.job_type not in JOB_TYPES:
            raise JobContractValidationError("Invalid job_type: not a reviewed job type.")
        if not isinstance(self.enabled, bool):
            raise JobContractValidationError("Invalid enabled: must be a boolean.")
        if self.provider != FIXED_PROVIDER_BY_JOB_TYPE[self.job_type]:
            raise JobContractValidationError(
                "Invalid provider: does not match this job type's fixed, reviewed provider."
            )
        if self.dataset_name != FIXED_DATASET_NAME_BY_JOB_TYPE[self.job_type]:
            raise JobContractValidationError(
                "Invalid dataset_name: does not match this job type's fixed, reviewed dataset."
            )
        expected_params_type = _PARAMS_TYPE_BY_JOB_TYPE[self.job_type]
        if not isinstance(self.params, expected_params_type):
            raise JobContractValidationError(
                "Invalid params: does not match this job type's parameter shape."
            )
