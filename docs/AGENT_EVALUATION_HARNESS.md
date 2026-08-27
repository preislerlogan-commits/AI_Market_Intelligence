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

The harness's job is to **characterize those trust gaps with a repeatable
methodology** — deterministic checks where a claim's structure permits them,
and a recorded human citation-support adjudication where it does not — not to
certify the agents as correct. It makes no claim of automated proof of
semantic correctness.

## In scope (to be designed)

- **Deterministic factual-transcription checks** — where a claim's structure
  permits a deterministic comparison, every number/date/unit token that
  purports to state a stored value must match (by numeric / date equality) a
  value in the cited evidence fact. Applies to all three agents. A claim whose
  structure does not permit a deterministic token comparison is routed to the
  citation-support rubric below, not silently skipped.
- **Citation-support checks — two layers.**
  1. **Lexical-overlap triage (advisory only).** Lexical overlap between a
     statement and the evidence fact it cites. **Lexical overlap is not proof
     of support.** A low-overlap result flags a claim for closer human
     attention; a high-overlap result is not a pass. It never blocks anything
     automatically and never substitutes for the human adjudication below.
  2. **Human citation-support adjudication.** Every claim in a characterization
     is adjudicated by a human reviewer against the rubric in
     "Citation-support rubric (human review)" below — every claim, not only the
     low-overlap ones.
- **Abstention matrix** — assert each agent abstains (with the correct
  reasons) on stale / missing / partial / future-dated / mismatch fixtures,
  and proceeds on healthy fixtures.
- **Cross-agent consistency** — where two agents reference the same stored
  fact, assert the stated values agree.
- **Repeatability characterization** — re-run one fixed evidence package N
  times and report variance (claim count, `evidence_quality`, channel sets,
  transcribed values).

## Citation-support rubric (human review)

The deterministic factual-transcription checks above can only compare tokens
that a claim's structure exposes. Whether a citation actually *supports* the
sentence citing it is a semantic judgement the harness does not automate. This
rubric is the repeatable method for that judgement. It is applied to **every
claim** in a first characterization, independent of that claim's
lexical-overlap score.

- **Inputs per claim.** The reviewer sees a sanitized / redacted rendering of
  the claim and the specific stored evidence it cites — no credentials, no
  OpenAI response IDs, no raw article URLs, redacted per the data-handling
  boundary below. The reviewer is not shown the model's own reasoning or any
  LLM opinion of the claim.
- **Classification (exactly one per citation):**
  - `supported` — the cited evidence, on its own, states or directly entails
    what the claim says it does.
  - `partially_supported` — the cited evidence backs part of the claim but not
    all of it (e.g. the value matches but the claim adds a characterization the
    evidence does not carry).
  - `unsupported` — the cited evidence does not back the claim, cites the wrong
    fact, or contradicts it.
  - `unable_to_determine` — the reviewer cannot decide from the sanitized
    material available (ambiguous claim, ambiguous evidence, or redaction
    removed what was needed).
- **Reason (exactly one, from this fixed list):**
  - `value_matches_evidence`
  - `value_absent_from_evidence`
  - `value_conflicts_with_evidence`
  - `evidence_is_off_topic`
  - `claim_adds_unsupported_characterization`
  - `claim_scope_exceeds_single_observation`
  - `sanitized_material_insufficient`
  - `other` — a short free-text note is required.
- **Rules.**
  - Review **every** claim in the first characterization, not only low-overlap
    claims.
  - Record the classification and the reason for every claim, including the
    `supported` ones.
  - `unable_to_determine` is a first-class outcome. It is preserved, never
    resolved by guessing.
  - Preserve every `unsupported`, `partially_supported`, and
    `unable_to_determine` result — these are findings, not defects to be
    cleaned away.
  - **An LLM judge must not be the sole or gating reviewer.** A model-assisted
    relevance signal may be recorded alongside the human classification as
    secondary, non-gating context; the recorded adjudication is the human's.
  - The rubric characterizes citation support. A run in which every claim is
    `supported` is still not a validation of the agent.

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

## What Phase 0 closure requires of this work (P0-7)

Closing Phase 0 on P0-7 requires all of:

- the deterministic factual-transcription harness and the human
  citation-support rubric above both exist and are documented;
- at least one first characterization has been completed and recorded,
  covering every claim in that characterization (not only low-overlap claims);
- its findings, failures, and `unable_to_determine` results are preserved.

It does **not** require that every agent passes, and it does not imply the
agents are validated. See [PHASE_0_EXIT.md](PHASE_0_EXIT.md), criterion P0-7.

## Out of scope for the first version

- Certifying or "validating" any agent. The first version **characterizes
  trust gaps**; it does not automatically validate the agents, and passing it
  is not a claim of factual accuracy or repeatability.
- An LLM judge as the sole or gating evaluator. Any model-assisted relevance
  signal is secondary and non-gating.
- Any change to agent behaviour, provider behaviour, schemas, migrations, or
  the non-directional / manual-only-trading boundaries.
