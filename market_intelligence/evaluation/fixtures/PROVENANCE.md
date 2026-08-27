# Synthetic evaluation fixtures — provenance

Every `*.json` file in this directory is **synthetic and hand-authored** (built
by a small offline generator, then committed). They exist solely to exercise
the evaluation contracts, the rubric-completeness validator, and the
serialization boundary in `market_intelligence/evaluation/`.

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

All identifiers (`claim-alpha`, `cite-1`, …) and evidence-shape names
(`synthetic/redacted-*`) are placeholders with no connection to real data.
