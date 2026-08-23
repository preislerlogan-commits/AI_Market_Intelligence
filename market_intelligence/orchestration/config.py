"""Loads the committed orchestration job configuration into ``JobContract`` objects.

Uses only the Python standard library's ``json`` module -- no new
dependency is added merely to parse configuration. The configuration file
(``jobs.json``, in this same directory) is a small, fully committed,
human-reviewed list of job definitions; loading it never performs network
I/O, never touches ``Settings``/credentials, and never opens a database
connection or run lock -- it is safe to call unconditionally, even for a
dry run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_intelligence.orchestration.contracts import (
    JOB_TYPE_ALPACA_BARS,
    JOB_TYPE_ALPACA_NEWS,
    JOB_TYPE_FRED_OBSERVATIONS,
    AlpacaBarsJobParams,
    AlpacaNewsJobParams,
    FredObservationsJobParams,
    JobContract,
    JobContractValidationError,
    JobParams,
)

DEFAULT_JOBS_CONFIG_PATH = Path(__file__).resolve().parent / "jobs.json"

_ALLOWED_ROOT_FIELDS = frozenset({"jobs"})

_REQUIRED_JOB_FIELDS = frozenset(
    {"job_id", "job_type", "enabled", "provider", "dataset_name", "params"}
)
# Job entries have no optional fields, so the required set is also the
# complete allowed set -- any other key (e.g. a shell command, a URL, a SQL
# fragment) is rejected outright, matching ``_ALLOWED_PARAM_FIELDS_BY_JOB_TYPE``
# below.
_ALLOWED_JOB_FIELDS = _REQUIRED_JOB_FIELDS

_ALLOWED_PARAM_FIELDS_BY_JOB_TYPE: dict[str, frozenset[str]] = {
    JOB_TYPE_ALPACA_NEWS: frozenset({"symbol", "limit"}),
    JOB_TYPE_ALPACA_BARS: frozenset(
        {
            "symbol",
            "timeframe",
            "lookback_days",
            "limit",
            "max_pages",
            "feed",
            "adjustment",
            "currency",
        }
    ),
    JOB_TYPE_FRED_OBSERVATIONS: frozenset({"series_id", "lookback_days", "limit", "max_pages"}),
}


class JobConfigError(ValueError):
    """Raised for a malformed or invalid job configuration file.

    This file is committed, non-secret, project-authored configuration --
    unlike provider/network errors, its own validation-error messages may
    freely describe what is wrong.
    """


def _build_params(job_type: str, raw_params: Any) -> JobParams:
    if not isinstance(raw_params, dict):
        raise JobConfigError(f"Invalid params for job type '{job_type}': expected an object.")

    allowed_param_fields = _ALLOWED_PARAM_FIELDS_BY_JOB_TYPE.get(job_type)
    if allowed_param_fields is not None:
        unknown_param_fields = raw_params.keys() - allowed_param_fields
        if unknown_param_fields:
            raise JobConfigError(
                f"Unknown param field(s) for job type '{job_type}': "
                f"{sorted(unknown_param_fields)}"
            )

    try:
        if job_type == JOB_TYPE_ALPACA_NEWS:
            return AlpacaNewsJobParams(symbol=raw_params["symbol"], limit=raw_params["limit"])
        if job_type == JOB_TYPE_ALPACA_BARS:
            return AlpacaBarsJobParams(
                symbol=raw_params["symbol"],
                timeframe=raw_params["timeframe"],
                lookback_days=raw_params["lookback_days"],
                limit=raw_params["limit"],
                max_pages=raw_params["max_pages"],
                feed=raw_params["feed"],
                adjustment=raw_params["adjustment"],
                currency=raw_params["currency"],
            )
        if job_type == JOB_TYPE_FRED_OBSERVATIONS:
            return FredObservationsJobParams(
                series_id=raw_params["series_id"],
                lookback_days=raw_params["lookback_days"],
                limit=raw_params["limit"],
                max_pages=raw_params["max_pages"],
            )
    except KeyError as exc:
        raise JobConfigError(f"Missing required param field: {exc}") from None
    raise JobConfigError(f"Unknown job type: '{job_type}'.")


def _build_contract(raw: Any) -> JobContract:
    if not isinstance(raw, dict):
        raise JobConfigError("Invalid job entry: expected an object.")
    missing = _REQUIRED_JOB_FIELDS - raw.keys()
    if missing:
        raise JobConfigError(f"Job entry missing required field(s): {sorted(missing)}")
    unknown = raw.keys() - _ALLOWED_JOB_FIELDS
    if unknown:
        raise JobConfigError(f"Job entry has unknown field(s): {sorted(unknown)}")

    params = _build_params(raw["job_type"], raw["params"])
    try:
        return JobContract(
            job_id=raw["job_id"],
            job_type=raw["job_type"],
            enabled=raw["enabled"],
            provider=raw["provider"],
            dataset_name=raw["dataset_name"],
            params=params,
        )
    except JobContractValidationError as exc:
        raise JobConfigError(f"Invalid job entry '{raw.get('job_id')}': {exc}") from None


def load_job_contracts(path: Path | None = None) -> tuple[JobContract, ...]:
    """Load and strictly validate every job contract from the committed config file.

    Returns contracts sorted by ``job_id`` for deterministic ordering,
    regardless of the file's own entry order. Raises ``JobConfigError`` for
    a missing/unreadable file, malformed JSON, a malformed entry, an
    unknown job type, or a duplicate ``job_id``. Performs no network I/O
    and constructs no ``Settings``, client, database connection, or lock.
    """
    config_path = path or DEFAULT_JOBS_CONFIG_PATH
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JobConfigError(f"Could not read job configuration file: {exc}") from None

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise JobConfigError(f"Job configuration file is not valid JSON: {exc}") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise JobConfigError("Job configuration file must be an object with a 'jobs' list.")
    unknown_root_fields = payload.keys() - _ALLOWED_ROOT_FIELDS
    if unknown_root_fields:
        raise JobConfigError(
            f"Job configuration file has unknown root field(s): {sorted(unknown_root_fields)}"
        )

    contracts = [_build_contract(raw) for raw in payload["jobs"]]

    job_ids = [contract.job_id for contract in contracts]
    if len(set(job_ids)) != len(job_ids):
        raise JobConfigError("Duplicate job_id detected in job configuration.")

    return tuple(sorted(contracts, key=lambda contract: contract.job_id))
