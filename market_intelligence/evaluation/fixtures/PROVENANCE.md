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
| `exact_match_comparison` | Comparison claim; dates, values, and direction all match. |

None of these is a real agent output, a claim of accuracy, or evidence of any
agent's quality. All identifiers (`claim-alpha`, `cite-1`, …), series IDs, and
evidence-shape names (`synthetic/redacted-*`) are placeholders with no
connection to real data.
