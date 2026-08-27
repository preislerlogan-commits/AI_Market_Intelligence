# Synthetic evaluation fixtures — provenance

Every `*.json` file in this directory, and the hand-constructed inputs in
`macro_transcription.py`, are **synthetic and hand-authored** (the JSON files
built by a small offline generator, then committed). They exist solely to
exercise the evaluation contracts, the rubric-completeness validator, the
serialization boundary, and the Macro-Analyst deterministic
factual-transcription check in `market_intelligence/evaluation/`.

## What these fixtures are NOT

- They are **not** evidence of any agent's quality, accuracy, or repeatability.
- They contain **no** real article text or headlines, **no** real URLs, **no**
  evidence IDs from any live agent run, **no** observation values from any live
  run, **no** credentials, **no** provider response IDs, and **no** copied
  model output.
- No agent has been evaluated to produce any of them. There is no
  factual-transcription result and no citation-support adjudication of real
  agent output anywhere in this repository.

## The fixtures

| File | Purpose |
|---|---|
| `fully_supported.json` | Complete rubric; every synthetic pair adjudicated `supported`. |
| `partially_supported.json` | Complete rubric; one pair `partially_supported`. |
| `unsupported.json` | Complete rubric; one pair `unsupported`, with a `FAILURE` finding — still a valid completed characterization. |
| `unable_to_determine.json` | Complete rubric; one pair `unable_to_determine`, preserved as-is. |
| `incomplete_rubric.json` | One expected pair has no adjudication — rubric is incomplete. |
| `duplicate_unexpected_adjudication.json` | A duplicated pair, an unexpected pair, and a missing pair. |

## `macro_transcription.py` — Macro-Analyst transcription-check fixtures

Hand-constructed `MacroTranscriptionInput` cases (synthetic claim summary +
synthetic cited evidence facts) for
`market_intelligence/evaluation/macro_factual_transcription.py`:

| Fixture | Purpose |
|---|---|
| `exact_match_single` | Single-observation claim; every token matches. |
| `wrong_value` | Stated value differs from the cited fact. |
| `wrong_date` | Stated observation date differs from the cited fact. |
| `wrong_series` | Claim's declared series differs from the cited fact. |
| `wrong_units_frequency` | Stated frequency and units both differ. |
| `incorrect_direction` | Comparison claim states the wrong direction for its two values. |
| `unsupported_wording` | Wording is not a recognized controlled form → human review. |
| `extra_unrelated_numbers` | Sentence carries extra digit runs; only the named value groups are read. |
| `canonical_capitalized_monthly` | Single-observation claim in the accepted live spelling ("The stored Monthly observation …"); frequency word case-folded before comparison. |
| `canonical_capitalized_quarterly` | Single-observation claim in the accepted live spelling ("The stored Quarterly observation …"); frequency word case-folded before comparison. |
| `exact_match_comparison` | Comparison claim; dates, values, and direction all match. |

None of these is a real agent output, a claim of accuracy, or evidence of any
agent's quality. All identifiers (`claim-alpha`, `cite-1`, …), series IDs, and
evidence-shape names (`synthetic/redacted-*`) are placeholders with no
connection to real data.

## `macro_characterization.py` / `macro_characterization_input_complete.json`

A single hand-authored synthetic `MacroCharacterizationInput` for the offline
Macro characterization workflow
(`market_intelligence/evaluation/macro_characterization_workflow.py`,
`scripts/characterize_macro_report.py`):

| Fixture | Purpose |
|---|---|
| `COMPLETE_MULTI_CLAIM` (`.py`) / `macro_characterization_input_complete.json` | Complete multi-claim characterization: four synthetic claims exercising the transcription match / mismatch / human-review outcomes plus a two-cited-fact comparison claim; five expected claim/citation pairs covering every claim. |

The JSON file is the byte-stable `input_to_json_str(COMPLETE_MULTI_CLAIM)`
serialization of the `.py` fixture, committed for the CLI test. Series IDs
(`SYNTHRATE`, `SYNTHPRICE`, `SYNTHLABOR`, …), identifiers, dates, and values are
all synthetic placeholders. It is not a real characterization and no real
characterization has been performed.

## `macro_adjudication_input_complete.json` / `COMPLETE_ADJUDICATION_INPUT`

A single hand-authored synthetic `MacroAdjudicationInput` for the offline Macro
characterization **completion** step
(`market_intelligence/evaluation/macro_characterization_workflow.py`
`complete_macro_characterization`, `scripts/complete_macro_characterization.py`):

| Fixture | Purpose |
|---|---|
| `COMPLETE_ADJUDICATION_INPUT` (`.py`) / `macro_adjudication_input_complete.json` | The completed human citation adjudications for `COMPLETE_MULTI_CLAIM`: one per expected pair, exercising all four classifications (`supported` ×2, `partially_supported`, `unsupported`, `unable_to_determine`). |

The `run_id` (`evalrun-…`) is the deterministic local digest of
`macro_analyst` + the characterization label + a fixed synthetic `created_at`
(`CHARACTERIZATION_CREATED_AT`); it is never a provider response id. The
reviewer id (`synthetic-reviewer`), timestamps, classifications, and reasons are
all synthetic placeholders and are **not** a judgement of any real agent output.
The JSON file is the byte-stable `adjudication_input_to_json_str(...)`
serialization of the `.py` fixture. No real characterization has been completed.
