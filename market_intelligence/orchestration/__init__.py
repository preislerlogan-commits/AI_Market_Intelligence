"""Ingestion-orchestration layer: contracts, planning, and execution.

This package runs the existing, already-reviewed Alpaca news, Alpaca bars,
and FRED observations pipelines through explicit, strictly validated job
contracts. It exists as groundwork for future specialized agents, but it
does not itself build any AI agent, analysis, prediction, recommendation,
scheduling, or brokerage/Robinhood integration -- see
``docs/INGESTION_ORCHESTRATION.md``.

It also holds read-only coordinators that touch storage or providers on
behalf of otherwise pure modules -- e.g. ``spy_vwap_reversion_input_builder``
(read-only DuckDB -> step-d evaluation input) and ``spy_contract_capture`` --
so that ``market_features``' pure step-c regime/feature modules never import
DuckDB, storage, a connector, or orchestration.
"""
