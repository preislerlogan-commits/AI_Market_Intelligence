# Market Evidence Agent — Live Evaluation Log

This file is a compact, sanitized log of live (real database, real OpenAI
request) runs of `MarketEvidenceAgent`, plus a manual (human) quality read of
each accepted report. See
[docs/MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md) for the agent's
full contract, and `PROJECT_STATE.md` for the authoritative project-status
record these entries summarize.

**What this file is not:** an automated evaluation harness, a benchmark, a
statistically meaningful sample, or evidence of factual accuracy, predictive
skill, analytical reliability, or trading usefulness. Each entry is one
human's manual read of one accepted report. Per `CLAUDE.md`'s "Evidence and
Claims" policy, failed and null runs are recorded here alongside successful
ones and are never deleted or silently dropped.

## Evaluation #1 — 2026-08-24

**Run facts (sanitized):**

| Field | Value |
|---|---|
| Symbol | SPY |
| `session_date_et` | 2026-08-21 |
| Preflight eligible | true |
| Evidence item count | 20 |
| First `--execute` attempt | Failed — `response_validation_failed` (`OpenAIParseFailureError`); no analysis accepted, no retry made |
| Follow-up `--execute` attempt (after PR #20 hardening) | Completed |
| Model | gpt-5-mini |
| Input tokens | 1,648 |
| Output tokens | 1,535 |
| Total tokens | 3,183 |
| `evidence_quality` | sufficient |
| Observations returned | 5 |
| `directional_assessment` | not_performed (fixed) |
| `trade_recommendation` | not_performed (fixed) |
| Persistence / automatic retry | None occurred |

No response ID, raw model output, full evidence payload, or credential is
reproduced here, consistent with this agent's and
`OpenAIStructuredClient`'s sanitization contract.

**Manual evaluation (human read of the accepted report):**

- Structured-output schema validation, evidence-ID citation validation, and
  the post-response content policy check all passed.
- The report kept the latest stored close and the regular-session close
  distinct rather than conflating them.
- The stated five-bar return was internally consistent with the endpoints
  the report itself cited.
- News/macro/session-scope limitations were correctly surfaced in
  `limitations`.
- **Minor citation-relevance issue:** the data-quality observation cited
  evidence ID `session_same_date_bars` without actually discussing its
  premarket/after-hours counts. The citation is a valid evidence ID (it
  passed automated citation validation), but it was not clearly necessary
  to the statement it supported — a relevance gap that citation validation
  does not and cannot catch, since that check only confirms an ID exists in
  the evidence package, not that it is topically relevant to the statement
  citing it.

**Caveats:** this is evaluation example #1 — a single manually read output
from a single live run. It is not proof of factual accuracy, prediction
quality, analytical reliability, or trading usefulness, and it does not
establish a repeatable evaluation methodology. Future entries in this file
should record both successful and failed/null runs as they occur.
