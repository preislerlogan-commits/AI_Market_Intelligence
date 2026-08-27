# Local agent-evaluation data

This directory is the **local-only** home for real agent-evaluation
characterization inputs and outputs.

## Boundary

- **`local/` is gitignored.** Real `MacroCharacterizationInput` JSON files and
  the `EvaluationRunRecord` outputs produced from them must live under
  `data/evaluations/local/` and must **never** be committed. See
  [`docs/AGENT_EVALUATION_HARNESS.md`](../../docs/AGENT_EVALUATION_HARNESS.md),
  "Data-handling boundary (binding)".
- **Only synthetic fixtures are committed**, and they live under
  `market_intelligence/evaluation/fixtures/` — not here.
- **Evaluation evidence facts** (`TranscriptionEvidenceFact`) represent
  *sanitized* stored-observation facts (citation handle, series ID, observation
  date, `Decimal` value, frequency, optional units — and nothing else).
  Committed fixtures and tests must use only synthetic, hand-authored facts.
  Real sanitized facts may be used only by the local characterization workflow
  here in `data/evaluations/local/`, which is gitignored; real inputs and
  outputs must never be committed.
- The offline workflow CLI (`scripts/characterize_macro_report.py`) **only**
  writes a `--write` output whose resolved path is inside
  `data/evaluations/local/`. Any path outside that directory — the repository
  root, `docs/`, `tests/`, `fixtures/`, `data/evaluations/` itself, an
  outside-repo path, or one reached via `..` traversal or a symlink — is
  refused. Point its `--output` at `data/evaluations/local/`.
- Committing any real characterization capture would require separate, explicit
  review and is not done by any automated step.

## Status

**No real characterization has been performed.** The offline workflow exists;
P0-7 and Phase 0 remain open. The next step after this workflow merges is one
separately authorized local characterization run.
