# Local agent-evaluation data

This directory is the **local-only** home for real agent-evaluation
characterization inputs and outputs.

## Boundary

- **`local/` is gitignored.** Real `MacroCharacterizationInput` JSON files, the
  scaffold and completed `EvaluationRunRecord` files, and the
  `MacroAdjudicationInput` JSON holding the completed human citation
  adjudications must all live under `data/evaluations/local/` and must **never**
  be committed. See
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
- The offline build CLI (`scripts/characterize_macro_report.py`) **only** writes
  a `--write` output whose resolved path is inside `data/evaluations/local/`.
  Any path outside that directory — the repository root, `docs/`, `tests/`,
  `fixtures/`, `data/evaluations/` itself, an outside-repo path, or one reached
  via `..` traversal or a symlink — is refused. Point its `--output` at
  `data/evaluations/local/`.
- The offline completion CLI (`scripts/complete_macro_characterization.py`)
  records the completed human citation adjudications onto a scaffold. It is
  dry-run / validate by default (`--write` required to serialize the completed
  record) and requires the adjudication `run_id` to match the scaffold. **All
  three** of its `--record`, `--adjudications`, and `--output` paths must
  resolve strictly inside `data/evaluations/local/`; the same repository-root /
  tracked / `data/evaluations/`-itself / outside-repo / `..` / symlink-escape
  paths are refused. It records human decisions only — no classification is
  generated or recommended, there is no LLM judge, and its output is sanitized
  counts and classification tallies only (no reviewer notes, IDs, paths, or
  record text). Completion is not validation.
- Committing any real characterization capture would require separate, explicit
  review and is not done by any automated step.

## Status

**The first real characterization has been performed and recorded (2026-08-28):**
the first human-reviewed offline Macro Analyst characterization — 14 claim/
citation pairs, 14 human adjudications, rubric complete, all pairs
`partially_supported`. This satisfied criterion P0-7 and **Phase 0 is closed**
(see [`docs/PHASE_0_EXIT.md`](../../docs/PHASE_0_EXIT.md) and
[`docs/AGENT_EVALUATION_HARNESS.md`](../../docs/AGENT_EVALUATION_HARNESS.md)). It
is not a claim that the Macro Analyst or any other agent is validated, accurate,
repeatable, or profitable, and only the Macro Analyst has been characterized.
Its real input/scaffold/adjudication/completed-record artifacts live here under
`local/` and remain gitignored and uncommitted.
