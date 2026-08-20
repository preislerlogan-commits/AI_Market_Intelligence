# Project State

This document is the **authoritative source of truth** for the current status
of AI Market Intelligence. It must be read before beginning any work in this
repository, and updated whenever the project's status materially changes.

Last updated: 2026-08-20

## Current Phase

**Phase 0 — Infrastructure Foundation**

The project is in initial scaffolding. No functional code, data pipelines,
or integrations exist yet.

## Status

- Repository initialized. Directory scaffold and governing documents
  (README, CLAUDE.md, AGENTS.md, DATA_CATALOG.md, SOURCE_POLICY.md,
  DECISION_RULES.md) are in place.
- No live APIs connected. Alpaca and FRED are planned providers but no
  credentials, clients, or connection tests exist yet.
- No trading execution connected. No brokerage integration exists or is
  planned; Robinhood is used manually, outside this system.
- No validated predictive model. No forecasting, scoring, or evaluation
  logic has been built or tested.
- Historical ORB data remains external at `C:\ORB_Project\data`. This data
  has **not** been copied, moved, or ingested into this repository. It must
  be treated as **read-only** until a deliberate, reviewed decision is made
  to reference or import it.

## Next Planned Work

1. Environment setup (Python 3.13 environment, dependency management).
2. Data catalog validation — inspecting actual schemas of external ORB data
   and recording verified findings in `DATA_CATALOG.md`.
3. Provider configuration — Alpaca and FRED credential handling (via
   `.env`, never committed).
4. First connection tests — minimal, read-only checks that provider
   credentials and connectivity work, with results recorded (including
   failures).

## Notes

- This file should be updated as phases progress. Treat entries here as
  ground truth over any assumptions embedded in code comments, prompts, or
  prior conversations.
