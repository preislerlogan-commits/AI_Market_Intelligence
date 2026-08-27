# Agent Evaluation Harness — Scope Boundary

**Status: offline foundation implemented; the first (Macro-Analyst-only)
deterministic factual-transcription check implemented over synthetic inputs;
methodology (P0-7) not complete, and no real agent output has been evaluated.**
This document records the intent and the hard boundaries for the offline-first
agent evaluation harness named in
[PROJECT_STATE.md](../PROJECT_STATE.md)'s "Next Planned Work" (item 2) and
[docs/PHASE_0_EXIT.md](PHASE_0_EXIT.md) (criterion P0-7).

Two deliberately narrow slices now exist as code, tests, and synthetic
fixtures under `market_intelligence/evaluation/` — see "Implemented foundation"
and "Implemented: Macro Analyst deterministic factual-transcription check"
below. Together they provide the safe, offline data foundation (strict
contracts, a deterministic rubric-completeness validator, a symlink-refusing,
no-overwrite, atomic, bounded local JSON round trip) **plus** the first
deterministic factual-transcription check — Macro Analyst only, exercised only
against hand-authored synthetic claim/evidence inputs.

They still perform **no** lexical-overlap scoring, **no** citation-support
adjudication, **no** abstention matrix, **no** cross-agent consistency, **no**
repeatability requests, **no** live-output recording, and **no**
factual-transcription check for the Market Evidence Agent or News Analyst, and
**no real agent output has been evaluated**. There is no factual-transcription
result of any live agent run and no citation-support adjudication of any real
agent output anywhere in this repository, and no first characterization has
been recorded. The rest of this file remains the agreed boundary for the work
still to come.

## Implemented foundation (`market_intelligence/evaluation/`)

Added as code, tests, and synthetic fixtures only — no connector, OpenAI, or
DuckDB access; no agent call; no `--record` flag; no LLM judge; no change to
any agent, provider, schema, or migration.

- **`contracts.py` — strict Pydantic v2 shapes** (`extra="forbid"`, every
  string and list bounded, timestamps timezone-aware and normalized to UTC,
  no field for a raw model response / provider response ID / credential / URL /
  database path / free metadata dict):
  - `AgentIdentifier` — `market_evidence` / `news_analyst` / `macro_analyst`.
  - `FindingSeverity` — `info` / `warning` / `failure`.
  - `CitationClassification` — `supported` / `partially_supported` /
    `unsupported` / `unable_to_determine`.
  - `CitationReason` — the eight fixed reasons from "Citation-support rubric"
    below (`value_matches_evidence`, `value_absent_from_evidence`,
    `value_conflicts_with_evidence`, `evidence_is_off_topic`,
    `claim_adds_unsupported_characterization`,
    `claim_scope_exceeds_single_observation`,
    `sanitized_material_insufficient`, `other`).
  - `FindingCategory` — fixed bounded enum (`factual_transcription`,
    `citation_support`, `abstention_behavior`, `cross_agent_consistency`,
    `repeatability`, `rubric_completeness`, `scope_boundary`, `other`).
  - `ClaimCitationPair`, `CitationAdjudication` (one human adjudication;
    `reason="other"` requires a short bounded `reviewer_note`, every other
    reason forbids one; `classification` and `reason` must additionally be a
    permitted pairing — see "Classification / reason compatibility matrix"
    below), `EvaluationFinding` (one finding), and `EvaluationRunRecord` (one
    run) with a locally, deterministically generated `run_id` (`build_run_id`,
    a digest of agent + label + UTC timestamp — never a provider ID).
  - `unable_to_determine` is a permitted, permanent classification. Nothing in
    the contracts or the rubric converts it into a pass or a failure.
  - `CLASSIFICATION_REASON_MATRIX` — one explicit, immutable
    (`MappingProxyType` of `frozenset`s) mapping of each `CitationClassification`
    to the `CitationReason` values it may pair with, enforced by a
    `CitationAdjudication` model validator. An incompatible pairing raises with
    the fixed `INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE`, which never
    reproduces a reviewer note, an identifier, a path, or record content. See
    the table in "Classification / reason compatibility matrix" below.
- **`rubric.py` — deterministic rubric-completeness validator.**
  `check_rubric_completeness(record)` returns a `RubricCompletenessResult`
  reporting, with stable lexical ordering, whether every expected
  (claim, citation) pair has exactly one adjudication, plus any missing,
  unexpected, duplicate-adjudication, or duplicate-expected pairs. Completeness
  depends only on the pair sets — never on classifications, reasons, or
  findings — so a run whose adjudications are entirely
  `unsupported` / `partially_supported` / `unable_to_determine`, or which
  carries `failure` findings, is still a valid *completed* characterization.
  `COMPLETION_IS_NOT_VALIDATION` states plainly that a complete rubric does not
  mean the agent is validated or that the run universally passes.
- **`serialization.py` — safe local round trip.** `to_json_str` / `from_json_str`
  are pure. `write_record(record, path)` takes an explicit caller path, requires
  the parent directory to already exist, refuses a symlinked target or parent,
  refuses overwrite unless `overwrite=True`, and writes atomically via a
  uniquely named temp file in the same directory. `read_record(path)` refuses a
  symlink and a file larger than `MAX_RECORD_BYTES` before reading. Every
  failure raises `EvaluationSerializationError` with a fixed message that never
  contains the path, the file bytes, or the record content. No database write;
  no automatic output directory.
- **`fixtures/` — synthetic fixtures only.** Six hand-authored
  `EvaluationRunRecord` JSON files (fully supported / partially supported /
  unsupported / unable to determine / incomplete rubric /
  duplicate-and-unexpected adjudication) plus `PROVENANCE.md`. They contain no
  real article text, URLs, live-run evidence IDs or observation values,
  credentials, response IDs, or copied model output, and are not evidence of
  any agent's quality.
- **Tests** (`market_intelligence/tests/test_evaluation_*.py`) cover strict
  enum/schema/bounds behaviour, timezone enforcement, the `other`
  note-required / note-forbidden rule, the full classification × reason
  compatibility matrix (a parametrized sweep over every combination, plus
  direct regressions for the contradictory pairings), every
  rubric-completeness case, deterministic ordering and JSON round trip,
  path-traversal / symlink / overwrite / oversized-file / malformed-JSON /
  leak-sanitization behaviour, synthetic fixture validation, and a static +
  fresh-interpreter proof that no connector, OpenAI client, database, agent, or
  network import occurs.

## Implemented: Macro Analyst deterministic factual-transcription check

Added as code, tests, and synthetic fixtures only
(`market_intelligence/evaluation/macro_factual_transcription.py`,
`market_intelligence/evaluation/fixtures/macro_transcription.py`,
`market_intelligence/tests/test_evaluation_macro_transcription.py`). No
connector, OpenAI, DuckDB, agent, or orchestration import; no network request;
no agent-behaviour, schema, migration, or dependency change. This is
**Macro-Analyst-only** and **offline / synthetic only** — the first slice of the
"Deterministic factual-transcription checks" bullet under "In scope" below. It
does **not** yet apply to the Market Evidence Agent or News Analyst.

- **Input (`MacroTranscriptionInput`).** One evaluation-specific, sanitized
  shape: a local `claim_id`, the claim's declared `claim_series_id`, the
  `claim_summary` string, and one or two `cited_facts`
  (`TranscriptionEvidenceFact`: `citation_id`, `series_id`, `observation_date`,
  a `Decimal` `value`, a `frequency` word, and optional `units`). It never
  carries a raw model response, a provider response ID, a credential, a URL, or
  a database path, and every field is strictly bounded (`extra="forbid"`).

- **Recognized grammar (exactly two controlled forms).** Both are matched by a
  single, fully anchored regular expression with fixed connective text — never
  generic number scraping, never fuzzy or semantic matching:

  1. **Single stored observation:**
     `The stored <frequency> observation dated <YYYY-MM-DD> (is|was) <value>[ <units>][, per official FRED metadata|, as stored].`
  2. **Two-observation comparison** — the Macro Analyst's canonical wording:
     the latest stored observation stated first, then the immediately preceding
     one, with a single direction verb:
     `The stored <frequency> observation dated <latest-date> was <latest-value>; it (increased|decreased) from the stored <frequency> observation dated <previous-date>, which was <previous-value>[, per official FRED metadata|, as stored].`
     and the unchanged variant
     `The stored <frequency> observation dated <latest-date> was <latest-value>; it was unchanged from the stored <frequency> observation dated <previous-date>, which was <previous-value>[, …].`
     There is **no** `Comparing the stored observations dated X and Y, …` form —
     that phrasing is not part of the canonical wording and is reported as
     unrecognized.

  `<frequency>` is one of `daily`, `weekly`, `biweekly`, `monthly`,
  `quarterly`, `semiannual`, `annual` (the seven words the Macro Analyst's own
  `FREQUENCY_SHORT_WORDS` maps to). `<units>` is a fixed closed vocabulary. The
  grammar has no free-text region, so any sentence carrying a digit outside the
  date / value slots simply fails to match and is reported as unrecognized
  wording (see outcomes). Because every value/date token is read from a named
  capture group, extra digit runs elsewhere in a recognized sentence (e.g. the
  two dates in a comparison) are never scraped as a value.

- **Deterministic verification.** For a recognized claim: the declared
  `claim_series_id` against every cited fact's `series_id`; the observation
  date; the `Decimal` value (by numeric equality, so trailing-zero formatting
  differences are not a mismatch); the frequency word; the units **only when
  the claim states them**; for a comparison, the previous observation's date
  and value (facts are ordered previous→latest by `observation_date`) and that
  the stated `increased`/`decreased`/`unchanged` direction agrees with the two
  cited values.

- **Outcomes (one `EvaluationFinding` per claim).**
  - **Exact match** → an `info` `factual_transcription` finding. A match is
    **not** a validation — see `MATCH_IS_NOT_VALIDATION`: it confirms only that
    the recognized tokens transcribe the cited synthetic fact.
  - **Mismatch** → a `failure` `factual_transcription` finding naming only the
    broad mismatch *categories* (fixed code slugs: `series_id`,
    `observation_date`, `value`, `frequency`, `units`,
    `previous_observation_date`, `previous_value`, `comparison_direction`,
    `citation_structure`). The finding never reproduces any claim or evidence
    text or value.
  - **Unrecognized / unparseable wording** → a `warning` `factual_transcription`
    finding requiring human citation-support review. This is **never** treated
    as a pass.
  - There is no universal "agent passes" or "validated" result at any level:
    `MacroTranscriptionResult` carries no `passed` / `validated` attribute.

- **Synthetic fixtures only.** `MACRO_TRANSCRIPTION_FIXTURES` covers an exact
  single-observation match, a wrong value, a wrong date, a wrong series, wrong
  units + frequency, an incorrect comparison direction, unsupported wording, a
  comparison whose sentence carries extra unrelated digit runs (which must not
  be scraped), and an exact comparison match. Every fixture is hand-authored
  and contains no real claim/evidence text, live-run evidence IDs, live
  observation values, credentials, or model output (see
  `fixtures/PROVENANCE.md`).

## Still not implemented (unchanged boundary below)

A deterministic factual-transcription check for the **Market Evidence Agent**
and the **News Analyst**, the lexical-overlap triage, the human
citation-support adjudication, the abstention matrix, cross-agent consistency,
the repeatability characterization, any live-output capture / `--record`, any
LLM judge, and the first recorded characterization of a real agent all remain
to be built. **Phase 0 remains open** (P0-7 unmet). The sections that follow
are the agreed design for that work.

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
- **Classification / reason compatibility matrix.** The reason must be
  compatible with the classification. This is a deterministic, closed matrix,
  encoded as the immutable `CLASSIFICATION_REASON_MATRIX` in
  `market_intelligence/evaluation/contracts.py` and enforced by a
  `CitationAdjudication` model validator. It is deliberately the narrowest set
  that excludes only contradictory pairings:

  | Classification | Allowed reasons |
  |---|---|
  | `supported` | `value_matches_evidence`; `other` (+ note) |
  | `partially_supported` | `value_absent_from_evidence`; `claim_adds_unsupported_characterization`; `claim_scope_exceeds_single_observation`; `other` (+ note) |
  | `unsupported` | `value_absent_from_evidence`; `value_conflicts_with_evidence`; `evidence_is_off_topic`; `claim_adds_unsupported_characterization`; `claim_scope_exceeds_single_observation`; `other` (+ note) |
  | `unable_to_determine` | `sanitized_material_insufficient`; `other` (+ note) |

  `other` (with its required bounded note) is the escape hatch for every
  classification. `unable_to_determine` keeps its own narrow allowed set and
  remains a valid first-class outcome. An incompatible pairing is rejected with
  the fixed `INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE`, which reproduces no
  reviewer note, identifier, path, or record content. This matrix is a
  data-shape constraint only — it does not, and cannot, judge whether a chosen
  classification is *correct*; that remains the human reviewer's call.
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
