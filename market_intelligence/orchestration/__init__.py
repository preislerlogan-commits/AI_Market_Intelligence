"""Ingestion-orchestration layer: contracts, planning, and execution.

This package runs the existing, already-reviewed Alpaca news, Alpaca bars,
and FRED observations pipelines through explicit, strictly validated job
contracts. It exists as groundwork for future specialized agents, but it
does not itself build any AI agent, analysis, prediction, recommendation,
scheduling, or brokerage/Robinhood integration -- see
``docs/INGESTION_ORCHESTRATION.md``.
"""
