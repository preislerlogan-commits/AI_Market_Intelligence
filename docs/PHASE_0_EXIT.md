# Phase 0 Exit Criteria

Phase 0 is **Infrastructure Foundation**. This document states the explicit,
testable criteria for closing it and the current status of each. It is the
companion to the "Current State at a Glance" and "Next Planned Work" sections
of [PROJECT_STATE.md](../PROJECT_STATE.md), which remains the authoritative
status record.

No criterion below implies predictive usefulness, factual accuracy, or
repeatability of any agent output. Those are explicitly out of scope for
Phase 0 and are not claimed anywhere in this repository.

## Criteria and status

| # | Criterion | Status |
|---|---|---|
| P0-1 | **Local storage foundation.** Versioned, checksum-verified, transactional migration runner; path-safety guard; read-only health check; per-dataset repositories with conflict rollback. | **Passed.** Real DB healthy at schema version `0008`. |
| P0-2 | **Reviewed read-only connectors + at least one controlled ingestion.** A credential-safe, sanitized, read-only connector for each planned data class (equity bars, news, macro observations, macro series metadata), each exercised by at least one authorized live ingestion that lands transactionally in storage. | **Passed, with a limited-coverage caveat.** All four connector classes have had an authorized live ingestion. Coverage is narrow: SPY only for bars/news (bars ~3 trading days, IEX feed only); 7 FRED series with short history. No dataset is complete, gap-free, or validated. |
| P0-3 | **Model-provider safety boundary.** A single structured-output client: credentials only from settings, never per-request; no tools; no automatic retry; `store=False`; sanitized errors and results; one bounded request per call. | **Passed.** Live-connectivity-verified. |
| P0-4 | **Bounded end-to-end agent execution.** At least one bounded, non-directional agent that runs end to end against real stored data and a real model, behind a deterministic preflight gate and deterministic post-response validators, with the non-directional guarantee structurally enforced (the model-facing schema cannot express a direction or recommendation). | **Passed once per agent.** Market Evidence Agent, News Analyst, and the seven-series Macro Analyst have each produced exactly one accepted live run. One accepted run is not repeated, characterized, or validated behaviour. |
| P0-5 | **Deterministic, manually invoked ingestion path.** A dry-run-first, manually invoked path to run the reviewed ingestion jobs through explicit contracts, with per-job failure isolation, overlap protection (a fail-closed run lock), and a persistent audit trail. Scheduling, unattended operation, and recurring reliability are explicitly **not** part of this criterion — see the Phase boundary note below. | **Passed, with limited-verification caveats.** The dry-run-first orchestration CLI (`scripts/run_ingestion_pipeline.py`) exists with all four capabilities and has completed exactly **one** authorized `--execute` run, recorded in the `orchestration_*` audit tables. Limited verification: one live run only — no repeated runs, no failure-path exercise in production, no soak testing. Not production-ready, not continuously reliable, not fully validated. |
| P0-6 | **Documentation accuracy and catalog coverage.** Top-level status documents (`README.md`, `PROJECT_STATE.md`, `DATA_CATALOG.md`, `docs/`) accurately describe what exists; at least one ingested dataset has an honest catalog record (source, provenance, schema/table, actual coverage, freshness limitations, validation status). | **Partial until the `docs/phase0-reconciliation` change merges.** Before it: `README.md` stated no APIs/database/pipelines existed; `DATA_CATALOG.md` and `docs/OPENAI_PROVIDER_BOUNDARY.md` carried stale, self-contradicting status lines; no dataset had a completed catalog record. That change corrects all of these. |
| P0-7 | **Repeatable agent evaluation methodology.** A recorded, repeatable evaluation methodology covering (a) deterministic factual-transcription checks where the claim structure permits them (does a claim's stated value/date/unit match the cited stored evidence?), and (b) recorded citation-support adjudication using a human-review rubric — each citation classified `supported` / `partially_supported` / `unsupported` / `unable_to_determine`, with a reason from a fixed list, for every claim in a characterization. Lexical overlap is advisory triage only and does not by itself satisfy this criterion. This is not a claim of automated proof of semantic correctness. | **Failed / not yet met.** Progress: the offline contracts + rubric-completeness validator + serialization boundary exist, and the **first deterministic factual-transcription check exists — Macro Analyst only, over synthetic inputs** (`market_intelligence/evaluation/macro_factual_transcription.py`; see [AGENT_EVALUATION_HARNESS.md](AGENT_EVALUATION_HARNESS.md) and [PROJECT_STATE.md](../PROJECT_STATE.md) Completed Work Log items 35–37). An **offline Macro characterization workflow** now also exists — a strict local input contract, a pure builder that runs the transcription check for every claim and emits one pending human-adjudication template per expected claim/citation pair (never pre-classifying citation support), a pure completion step, and a dry-run-first offline CLI (`market_intelligence/evaluation/macro_characterization_workflow.py`, `scripts/characterize_macro_report.py`; item 37). It has been exercised only against synthetic fixtures; **no real characterization has been performed.** Still missing: the same transcription check for the Market Evidence Agent and News Analyst, the human citation-support rubric adjudication of real output, lexical-overlap triage, and — the closure condition — at least one recorded first characterization of a real agent covering every claim. Only one manual read exists per agent; `docs/MARKET_EVIDENCE_EVALUATIONS.md` has a single entry. This remains the only substantive Phase 0 blocker. |

## Phase boundary: ingestion operations

Phase 0's ingestion criterion (P0-5) is deliberately scoped to a
**deterministic, manually invoked** path. The following are **Phase 1 / later
operational capabilities** and are intentionally **not implemented**:

- scheduling / cron-driven runs;
- unattended operation and any claim of recurring or continuous reliability;
- automatic stale-lock recovery (the run lock is fail-closed by design — a
  lock left by a crashed process must be removed manually by an operator who
  has confirmed no run is active);
- freshness monitoring / alerting on stale data;
- recurring, hands-off ingestion of any kind.

None of these blocks closing Phase 0. They are recorded here so the boundary
is explicit and so no later reader mistakes the current manual path for an
operational pipeline.

## Conclusion

**Phase 0 is not yet closed.** Going criterion by criterion:

- P0-1, P0-2, P0-3, P0-4, P0-5 — **Passed** (P0-2, P0-4, and P0-5 with the
  explicit caveats recorded in the table above; none is a blocker).
- P0-6 (documentation accuracy and catalog coverage) — **Partial, blocking,
  cleared by this change.** It is satisfied once the `docs/phase0-reconciliation`
  change (which this document is part of) merges. It is not blocked by any
  further work.
- P0-7 (repeatable agent evaluation methodology) — **Failed / not met. This is
  the only remaining substantive blocker.** Partial progress exists (offline
  foundation; the first Macro-Analyst-only deterministic factual-transcription
  check over synthetic inputs; the offline Macro characterization workflow —
  builder, completion step, and dry-run-first CLI — over synthetic inputs; see
  the P0-7 row above), but no real agent output has been evaluated and no first
  characterization has been recorded.

**After this documentation change merges, the single remaining blocker to
closing Phase 0 is P0-7.** Closing it requires all of:

- the deterministic factual-transcription harness and the human
  citation-support rubric both exist and are documented (see
  [AGENT_EVALUATION_HARNESS.md](AGENT_EVALUATION_HARNESS.md));
- at least one first characterization has been completed and recorded,
  covering every claim in that characterization (not only low-overlap claims);
- its findings, failures, and `unable_to_determine` results are preserved.

Closing Phase 0 does **not** require that every agent passes, and does not
imply the agents are validated. The first characterization describes the
agents' trust gaps; passing it is not a claim of factual accuracy or
repeatability. See [PROJECT_STATE.md](../PROJECT_STATE.md), "Next Planned
Work", item 2.

## What closing Phase 0 will still not establish

- That any agent `claim_summary` accurately transcribes the underlying stored
  value or metadata.
- That any agent output is repeatable across re-runs of the same evidence.
- That any ingested dataset is complete, gap-free, or research-validated.
- Any predictive, directional, or trading usefulness of any component.
