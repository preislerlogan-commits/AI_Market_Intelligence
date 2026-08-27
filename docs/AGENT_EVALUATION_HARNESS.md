# Agent Evaluation Harness — Scope Boundary

**Status: planned, not implemented.** This document records the intent and the
hard boundaries for the offline-first agent evaluation harness named in
[PROJECT_STATE.md](../PROJECT_STATE.md)'s "Next Planned Work" (item 2) and
[docs/PHASE_0_EXIT.md](PHASE_0_EXIT.md) (criterion P0-7). No harness code,
tests, fixtures, or CLI exist yet. This file exists so the boundaries are
agreed before implementation starts; it is documentation only and adds no
behaviour.

## Purpose

The Market Evidence Agent, News Analyst, and Macro Analyst have each produced
exactly one accepted live run. That proves bounded execution works once. It
does not establish that a claim's stated value matches the cited stored
evidence, that a cited evidence ID is topically relevant to the sentence
citing it, that the same evidence package yields a stable answer on re-run, or
that two agents describing the same stored fact agree.

The harness's job is to **characterize those trust gaps with deterministic,
repeatable checks** — not to certify the agents as correct.

## In scope (to be designed)

- **Factual-transcription checks** — for each claim, every number/date/unit
  token that purports to state a stored value must match (by numeric / date
  equality) a value in the cited evidence fact. Applies to all three agents.
- **Citation-support checks** — lexical overlap between a statement and the
  evidence fact it cites. **Advisory only. Lexical overlap is not proof of
  support**, and a low-overlap result is a flag for human review, never an
  automated failure that blocks anything.
- **Abstention matrix** — assert each agent abstains (with the correct
  reasons) on stale / missing / partial / future-dated / mismatch fixtures,
  and proceeds on healthy fixtures.
- **Cross-agent consistency** — where two agents reference the same stored
  fact, assert the stated values agree.
- **Repeatability characterization** — re-run one fixed evidence package N
  times and report variance (claim count, `evidence_quality`, channel sets,
  transcribed values).

## Data-handling boundary (binding)

- **May be committed:** synthetic or redacted fixtures — hand-authored
  evidence-package shapes and hand-authored model-output shapes that contain
  no real provider content.
- **Must not be committed, ever:** real evidence packages built from the real
  database, real article headline/summary text, article URLs, credentials,
  OpenAI response IDs, and full live model outputs.
- **Any future live evaluation capture** (recording a real agent's real
  structured output for analysis) must be written to a **local, sanitized,
  gitignored** location. Committing any such capture requires separate,
  explicit review.
- The harness never relaxes the sanitization contract of
  `OpenAIStructuredClient` or of any agent (see
  [OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md),
  [MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md),
  [NEWS_ANALYST.md](NEWS_ANALYST.md), [MACRO_ANALYST.md](MACRO_ANALYST.md)).

## Out of scope for the first version

- Certifying or "validating" any agent. The first version **characterizes
  trust gaps**; it does not automatically validate the agents, and passing it
  is not a claim of factual accuracy or repeatability.
- An LLM judge as the sole or gating evaluator. Any model-assisted relevance
  signal is secondary and non-gating.
- Any change to agent behaviour, provider behaviour, schemas, migrations, or
  the non-directional / manual-only-trading boundaries.
