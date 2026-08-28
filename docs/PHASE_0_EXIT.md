# Phase 0 Exit Criteria

Phase 0 is **Infrastructure Foundation**. This document states the explicit,
testable criteria for closing it and the current status of each. It is the
companion to the "Current State at a Glance" and "Next Planned Work" sections
of [PROJECT_STATE.md](../PROJECT_STATE.md), which remains the authoritative
status record.

**Phase 0 is closed as of 2026-08-28. This document is retained as the closure
record; every caveat and historical failure note in it remains in force.**

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
| P0-7 | **Repeatable agent evaluation methodology.** A recorded, repeatable evaluation methodology covering (a) deterministic factual-transcription checks where the claim structure permits them (does a claim's stated value/date/unit match the cited stored evidence?), and (b) recorded citation-support adjudication using a human-review rubric — each citation classified `supported` / `partially_supported` / `unsupported` / `unable_to_determine`, with a reason from a fixed list, for every claim in a characterization. Lexical overlap is advisory triage only and does not by itself satisfy this criterion. This is not a claim of automated proof of semantic correctness. | **Passed (2026-08-28).** The offline contracts + rubric-completeness validator + serialization boundary, the Macro-Analyst-only deterministic factual-transcription check (`market_intelligence/evaluation/macro_factual_transcription.py`), and the offline Macro characterization workflow — a strict local input contract, a pure builder that runs the transcription check for every claim and emits one pending human-adjudication template per expected claim/citation pair (never pre-classifying citation support), a pure completion step, a dry-run-first offline **build CLI**, and a dry-run-first offline **completion CLI** that records the completed human citation adjudications onto a scaffold (strict `run_id`-match, all three paths confined to `data/evaluations/local/`, sanitized tally-only output, no LLM judge; completion is not validation) — all exist and are documented (`market_intelligence/evaluation/macro_characterization_workflow.py`, `market_intelligence/evaluation/macro_characterization_input.py`, `scripts/characterize_macro_report.py`, `scripts/complete_macro_characterization.py`; see [AGENT_EVALUATION_HARNESS.md](AGENT_EVALUATION_HARNESS.md) and [PROJECT_STATE.md](../PROJECT_STATE.md) Completed Work Log items 35–39). Earlier the workflow had been exercised only against synthetic fixtures; **on 2026-08-28 the first real, human-reviewed offline Macro Analyst characterization was completed and recorded** using those existing workflows, covering every claim. Sanitized totals: agent `macro_analyst`; 14 expected claim/citation pairs; 14 human adjudications recorded (one per pair — none missing, duplicated, or unexpected); `rubric_complete: true`; classification tally `supported: 0`, `partially_supported: 14`, `unsupported: 0`, `unable_to_determine: 0`; all 14 reasons `claim_scope_exceeds_single_observation`; finding tally `info: 8`, `warning: 0`, `failure: 0` — seven factual-transcription findings were exact matches and the one scope-boundary information finding was preserved. Every adjudication was made by the human reviewer; no LLM judge generated, recommended, or changed any classification; no live request and no Macro Analyst rerun occurred. All 14 pairs are `partially_supported` because each Macro claim is a two-observation comparison that depends on two cited observations, while each individual claim/citation pair carries only one of those two observations — so no single pair, on its own, backs the whole comparative claim. The real characterization artifacts remain gitignored under `data/evaluations/local/` and are not committed. **Closure on this criterion means the required characterization methodology was exercised and recorded — not that the Macro Analyst or any other agent is validated, universally accurate, repeatable, or profitable.** Still future work, and it does **not** reopen Phase 0: the same transcription check for the Market Evidence Agent and News Analyst, citation-support adjudication of their real output, lexical-overlap triage, the abstention matrix, cross-agent consistency, and repeatability studies. Only one manual read still exists per agent; `docs/MARKET_EVIDENCE_EVALUATIONS.md` has a single entry. |

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

**Phase 0 (Infrastructure Foundation) is closed as of 2026-08-28.** Going
criterion by criterion:

- P0-1, P0-2, P0-3, P0-4, P0-5 — **Passed** (P0-2, P0-4, and P0-5 with the
  explicit caveats recorded in the table above; none is a blocker).
- P0-6 (documentation accuracy and catalog coverage) — **Passed** on merge of
  the `docs/phase0-reconciliation` change; recorded here for history. Before
  it: `README.md` stated no APIs/database/pipelines existed; `DATA_CATALOG.md`
  and `docs/OPENAI_PROVIDER_BOUNDARY.md` carried stale, self-contradicting
  status lines; no dataset had a completed catalog record. That change
  corrected all of these.
- P0-7 (repeatable agent evaluation methodology) — **Passed as of 2026-08-28.**
  The deterministic factual-transcription harness and the human
  citation-support rubric both exist and are documented, and the first real,
  human-reviewed offline Macro Analyst characterization was completed and
  recorded on that date, covering every claim, with all findings preserved
  (sanitized totals in the P0-7 row above). Earlier progress on this criterion
  was only over synthetic inputs; that history is preserved in the P0-7 row and
  in [PROJECT_STATE.md](../PROJECT_STATE.md)'s Completed Work Log.

**Phase 0 closure statement.** Phase 0 is closed because every exit criterion
above is met: the local storage foundation, the reviewed read-only connectors
with controlled ingestions, the model-provider safety boundary, one bounded
end-to-end run per agent, the deterministic manually invoked ingestion path,
the reconciled top-level documentation, and — as of 2026-08-28 — a recorded,
repeatable agent-evaluation methodology that has been exercised on real agent
output at least once (the first Macro Analyst characterization: 14 expected
pairs, 14 human adjudications, `rubric_complete: true`, all 14
`partially_supported` / `claim_scope_exceeds_single_observation`, findings
`info: 8` / `warning: 0` / `failure: 0`). **Closure means the required
infrastructure and the required characterization methodology exist and have each
been exercised and recorded — it does not mean the Macro Analyst or any other
agent is validated, universally accurate, repeatable, or profitable, and it does
not mean every agent has been characterized.** Every prior caveat in this
document and every historical failure record in
[PROJECT_STATE.md](../PROJECT_STATE.md)'s Completed Work Log remain in force and
must not be removed.

Closing Phase 0 does **not** require that every agent passes, and does not
imply the agents are validated. The first characterization describes the Macro
Analyst's trust gaps for one report; passing it is not a claim of factual
accuracy, repeatability, or predictive edge. See
[PROJECT_STATE.md](../PROJECT_STATE.md), "Next Planned Work".

## What closing Phase 0 will still not establish

- That any agent `claim_summary` accurately transcribes the underlying stored
  value or metadata.
- That any agent output is repeatable across re-runs of the same evidence.
- That any ingested dataset is complete, gap-free, or research-validated.
- Any predictive, directional, or trading usefulness of any component.
