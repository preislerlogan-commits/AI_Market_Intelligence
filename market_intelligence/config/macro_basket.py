"""Loads and strictly validates the committed Core Macro Basket configuration.

``core_macro_series.json`` (in this same directory) is a small, fully
committed, human-reviewed list of exactly the seven approved FRED macro
series this project has reviewed for a first bounded ingestion basket.
Loading it never performs network I/O, never touches ``Settings``/
credentials, and never opens a database connection or run lock -- it is
safe to call unconditionally, even for a dry run, mirroring
``market_intelligence/orchestration/config.py``'s ``load_job_contracts``.

This is a narrow, fixed universe -- **not** a general-purpose FRED series
catalog. Only the seven series below may ever appear in the committed
configuration; an entry for any other series ID is rejected, and each
approved series' ``category`` must match this module's own fixed,
committed mapping (never accepted as arbitrary caller-supplied text). Titles,
units, values, and provider-reported notes are never read or stored by this
module -- those come only from the reviewed FRED metadata/observations
pipelines at ingestion time (see
``market_intelligence/data_connectors/fred_macro_data.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_intelligence.data_connectors.fred_macro_data import (
    FredInvalidSeriesIdError,
    normalize_series_id,
)

DEFAULT_CORE_MACRO_SERIES_CONFIG_PATH = Path(__file__).resolve().parent / "core_macro_series.json"

# --- Approved category enum -------------------------------------------------

CATEGORY_POLICY_RATE = "policy_rate"
CATEGORY_LONG_TERM_RATE = "long_term_rate"
CATEGORY_INFLATION = "inflation"
CATEGORY_LABOR = "labor"
CATEGORY_GROWTH = "growth"

APPROVED_CATEGORIES = frozenset(
    {
        CATEGORY_POLICY_RATE,
        CATEGORY_LONG_TERM_RATE,
        CATEGORY_INFLATION,
        CATEGORY_LABOR,
        CATEGORY_GROWTH,
    }
)

# The fixed, committed, project-reviewed universe: exactly these seven FRED
# series IDs, each mapped to exactly one approved category. A configuration
# entry naming any other series ID, or naming one of these seven series
# under any other category, is rejected -- this mapping is never overridden
# by the configuration file itself.
APPROVED_SERIES_CATEGORY: dict[str, str] = {
    "FEDFUNDS": CATEGORY_POLICY_RATE,
    "DGS10": CATEGORY_LONG_TERM_RATE,
    "CPIAUCSL": CATEGORY_INFLATION,
    "PCEPI": CATEGORY_INFLATION,
    "UNRATE": CATEGORY_LABOR,
    "INDPRO": CATEGORY_GROWTH,
    "GDPC1": CATEGORY_GROWTH,
}
APPROVED_SERIES_IDS = frozenset(APPROVED_SERIES_CATEGORY)

# Conservative, fixed per-series lookback ceilings (calendar days). These are
# hard bounds this module enforces regardless of what the configuration file
# requests -- they exist specifically to prevent an unbounded historical
# request, and are not merely documentation. FEDFUNDS/CPIAUCSL/PCEPI/UNRATE/
# INDPRO (monthly series) are capped at ~400 days; GDPC1 (quarterly) at
# ~1,100 days; DGS10 (daily) at ~180 days -- see docs/CORE_MACRO_BASKET.md.
MAX_LOOKBACK_DAYS_BY_SERIES_ID: dict[str, int] = {
    "FEDFUNDS": 400,
    "CPIAUCSL": 400,
    "PCEPI": 400,
    "UNRATE": 400,
    "INDPRO": 400,
    "GDPC1": 1100,
    "DGS10": 180,
}
MIN_LOOKBACK_DAYS = 1

# Bounds for the forward-looking `recent_observations_limit` field. This
# ingestion script does not itself use this value -- it exists so a future
# basket-level evidence consumer (mirroring
# `market_intelligence/market_features/macro_evidence.py`'s
# `MIN_RECENT_OBSERVATIONS_LIMIT`/`MAX_RECENT_OBSERVATIONS_LIMIT`) has a
# validated, bounded value to read per series without a separate config
# format. The bounds are fixed here (not imported from that module) to avoid
# a layering dependency from configuration onto the market-features package.
MIN_RECENT_OBSERVATIONS_LIMIT = 2
MAX_RECENT_OBSERVATIONS_LIMIT = 24

_ALLOWED_ROOT_FIELDS = frozenset({"series"})
_REQUIRED_ENTRY_FIELDS = frozenset(
    {
        "series_id",
        "category",
        "enabled",
        "observation_lookback_days",
        "recent_observations_limit",
    }
)
# Entries have no optional fields, so the required set is also the complete
# allowed set -- any other key is rejected outright.
_ALLOWED_ENTRY_FIELDS = _REQUIRED_ENTRY_FIELDS


class CoreMacroSeriesConfigError(ValueError):
    """Raised for a malformed or invalid Core Macro Basket configuration file.

    This file is committed, non-secret, project-authored configuration --
    unlike provider/network errors, its own validation-error messages may
    freely describe what is wrong.
    """


@dataclass(frozen=True)
class CoreMacroSeriesConfig:
    """One validated, approved Core Macro Basket series entry.

    ``series_id`` is always one of ``APPROVED_SERIES_IDS`` and ``category``
    always exactly matches this module's fixed
    ``APPROVED_SERIES_CATEGORY[series_id]`` mapping -- both are guaranteed by
    validation before this object is ever constructed by
    ``load_core_macro_series``.
    """

    series_id: str
    category: str
    enabled: bool
    observation_lookback_days: int
    recent_observations_limit: int


def _require_bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreMacroSeriesConfigError(f"Invalid {field_name}: expected a plain integer.")
    if not (minimum <= value <= maximum):
        raise CoreMacroSeriesConfigError(
            f"Invalid {field_name}: must be between {minimum} and {maximum}."
        )
    return value


def _build_entry(raw: Any) -> CoreMacroSeriesConfig:
    if not isinstance(raw, dict):
        raise CoreMacroSeriesConfigError("Invalid series entry: expected an object.")

    missing = _REQUIRED_ENTRY_FIELDS - raw.keys()
    if missing:
        raise CoreMacroSeriesConfigError(
            f"Series entry missing required field(s): {sorted(missing)}"
        )
    unknown = raw.keys() - _ALLOWED_ENTRY_FIELDS
    if unknown:
        raise CoreMacroSeriesConfigError(f"Series entry has unknown field(s): {sorted(unknown)}")

    raw_series_id = raw["series_id"]
    if not isinstance(raw_series_id, str):
        raise CoreMacroSeriesConfigError("Invalid series_id: expected a string.")
    try:
        series_id = normalize_series_id(raw_series_id)
    except FredInvalidSeriesIdError as exc:
        raise CoreMacroSeriesConfigError(f"Invalid series_id: {exc}") from None
    if series_id != raw_series_id:
        raise CoreMacroSeriesConfigError("Invalid series_id: must already be normalized.")

    if series_id not in APPROVED_SERIES_CATEGORY:
        raise CoreMacroSeriesConfigError(
            "Invalid series_id: not one of the approved Core Macro Basket series."
        )

    raw_category = raw["category"]
    expected_category = APPROVED_SERIES_CATEGORY[series_id]
    if not isinstance(raw_category, str) or raw_category != expected_category:
        raise CoreMacroSeriesConfigError(
            "Invalid category: does not match this series' fixed, approved category."
        )

    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise CoreMacroSeriesConfigError("Invalid enabled: must be a boolean.")

    observation_lookback_days = _require_bounded_int(
        raw["observation_lookback_days"],
        field_name="observation_lookback_days",
        minimum=MIN_LOOKBACK_DAYS,
        maximum=MAX_LOOKBACK_DAYS_BY_SERIES_ID[series_id],
    )
    recent_observations_limit = _require_bounded_int(
        raw["recent_observations_limit"],
        field_name="recent_observations_limit",
        minimum=MIN_RECENT_OBSERVATIONS_LIMIT,
        maximum=MAX_RECENT_OBSERVATIONS_LIMIT,
    )

    return CoreMacroSeriesConfig(
        series_id=series_id,
        category=raw_category,
        enabled=enabled,
        observation_lookback_days=observation_lookback_days,
        recent_observations_limit=recent_observations_limit,
    )


def load_core_macro_series(
    path: Path | None = None,
) -> tuple[CoreMacroSeriesConfig, ...]:
    """Load and strictly validate every entry from the committed configuration file.

    Returns entries in the **same order they appear in the committed file**
    (deterministic because the file itself is committed, static input --
    never sorted or otherwise reordered). Raises ``CoreMacroSeriesConfigError``
    for a missing/unreadable file, malformed JSON, an unknown root or entry
    field, a missing required entry field, a non-approved series ID, a
    series ID not already normalized, a category not matching this series'
    fixed approved mapping, a non-boolean ``enabled``, an
    ``observation_lookback_days``/``recent_observations_limit`` that is not a
    plain (non-boolean) integer or is out of its bounds, or a duplicate
    series ID. Performs no network I/O and constructs no ``Settings``,
    client, database connection, or lock.
    """
    config_path = path or DEFAULT_CORE_MACRO_SERIES_CONFIG_PATH
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoreMacroSeriesConfigError(
            f"Could not read Core Macro Basket configuration file: {exc}"
        ) from None

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise CoreMacroSeriesConfigError(
            f"Core Macro Basket configuration file is not valid JSON: {exc}"
        ) from None

    if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
        raise CoreMacroSeriesConfigError(
            "Core Macro Basket configuration file must be an object with a 'series' list."
        )
    unknown_root_fields = payload.keys() - _ALLOWED_ROOT_FIELDS
    if unknown_root_fields:
        raise CoreMacroSeriesConfigError(
            f"Core Macro Basket configuration file has unknown root field(s): "
            f"{sorted(unknown_root_fields)}"
        )

    entries = [_build_entry(raw) for raw in payload["series"]]

    series_ids = [entry.series_id for entry in entries]
    if len(set(series_ids)) != len(series_ids):
        raise CoreMacroSeriesConfigError(
            "Duplicate series_id detected in Core Macro Basket configuration."
        )

    return tuple(entries)
