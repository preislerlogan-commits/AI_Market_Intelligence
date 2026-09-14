# Project State

This document is the **authoritative source of truth** for the current status
of AI Market Intelligence. It must be read before beginning any work in this
repository, and updated whenever the project's status materially changes.

Last updated: 2026-09-14

## Current State at a Glance

| Area | Status |
|---|---|
| Phase | **Phase 0 — Infrastructure Foundation. Closed 2026-08-28.** Phase 1 (SPY options decision-support workflow) has begun; its first implementation step, read-only SPY option-chain ingestion and local storage, is complete and has been live-exercised once (2026-09-14). The next planned implementation step is the deterministic SPY intraday feature/regime engine. Closure is not a validation, accuracy, repeatability, or profitability claim for any agent, and does not mean every agent has been characterized. |
| Storage | Local DuckDB, healthy at schema version `0009` (9 migrations). Migration `0009` (`option_chain_snapshot_batches` + `option_chain_snapshots` + `option_chain_snapshot_batch_items`, three-table normalized design) has been **applied to the real database** (2026-09-14) — a read-only health check confirmed `healthy=True` at schema version `0009`. |
| Data connectors | Read-only Alpaca (bars, news, market-data snapshot, and a bounded SPY option-chain snapshot connector — now exercised by exactly one live, indicative-feed request, 2026-09-14) and FRED (observations, series metadata). No order/account/position/exercise/execution methods exist. |
| Ingested data | SPY 5-minute IEX bars (~3 trading days); ~20 SPY news articles; 7 FRED macro series (FEDFUNDS, GS10, CPIAUCSL, PCEPI, UNRATE, INDPRO, GDPC1) observations + metadata; one stored SPY indicative option-chain snapshot batch (102 contracts: 51 calls / 51 puts, one expiration, one retrieval instant, 2026-09-14). Each is one bounded ingestion run with limited coverage — none is complete, gap-free, or validated. |
| Orchestration | Deterministic, manually invoked ingestion path: dry-run-first CLI over the three reviewed jobs, per-job failure isolation, fail-closed overlap lock, persistent audit trail; one authorized `--execute` run. Meets the Phase 0 ingestion criterion (with limited-verification caveats). Scheduling, unattended operation, automatic stale-lock recovery, and freshness monitoring are Phase 1 / later and unimplemented. |
| Model boundary | One OpenAI structured-output client (no tools, no retry, `store=False`); live-connectivity-verified. |
| Agents | Market Evidence Agent, News Analyst, seven-series Macro Analyst. Each has exactly one accepted live run. Non-directional guarantee is structurally enforced. |
| Trust layer | **P0-7 met (2026-08-28) — the methodology exists and the first real, human-reviewed Macro characterization is recorded; the other agents are not yet characterized.** `market_intelligence/evaluation/` provides the safe, offline foundation for the methodology: strict Pydantic v2 contracts (agent/severity/citation-classification/citation-reason/finding-category enums, one finding, one human citation adjudication, one evaluation-run record), a deterministic rubric-completeness validator, a symlink-refusing / no-overwrite / atomic / bounded local JSON round trip, and synthetic fixtures. It **also** provides the **first deterministic factual-transcription check — Macro Analyst only, over synthetic inputs** (`macro_factual_transcription.py`): it recognizes only the two exact controlled Macro Analyst statement forms (single stored observation; increase/decrease/unchanged two-observation comparison), verifies series ID / observation date / `Decimal` value / frequency wording / units-when-stated / previous observation / comparison direction, and emits one `info` (exact match — *not* a validation) / `failure` (mismatch, broad category only, no text reproduced) / `warning` (unrecognized wording → human review) finding. It **also** provides the **offline Macro characterization workflow** (`macro_characterization_input.py`, `macro_characterization_workflow.py`, `scripts/characterize_macro_report.py`): a strict local input contract (sanitized label; Macro claim IDs; claim series IDs and summaries; the sanitized evaluation evidence facts the transcription evaluator needs; expected claim/citation pairs — and nothing else), a pure builder that runs the transcription check for every claim, creates an `EvaluationRunRecord` carrying those findings, and emits one pending human-adjudication template per expected pair (never pre-classifying citation support, never treating a transcription match as citation support), a pure completion step that attaches completed human adjudications only when every expected pair has exactly one (refusing missing/duplicate/unexpected pairs; completion is not validation), a dry-run-first, offline, explicit-path **build CLI** (`scripts/characterize_macro_report.py`: `--write` required, no overwrite, no directory creation; with `--write` the resolved output path must be strictly inside gitignored `data/evaluations/local/`, and repository-root, tracked-directory, outside-repository, `..`-traversal, and symlink-escape targets are refused; dry-run behavior is unchanged), and a dry-run-first, offline **completion CLI** (`scripts/complete_macro_characterization.py`: a strict `MacroAdjudicationInput` contract — only the scaffold `run_id` plus a bounded human `CitationAdjudication` list, `extra="forbid"`, no credential/URL/response-ID/path/raw-evidence/model-reasoning/metadata field; `--record`/`--adjudications`/`--output` all confined strictly inside `data/evaluations/local/`; requires the adjudication `run_id` to match the scaffold; records human decisions only, no LLM judge, all four classifications preserved; sanitized counts/classification-tally output only — never a reviewer note, claim ID, citation ID, path, or record text; `--write` required to serialize, no overwrite, no directory creation). Committed fixtures and tests remain synthetic-only; real sanitized evidence facts may be used only through the explicit local characterization workflow under gitignored `data/evaluations/local/`, and real characterization inputs and outputs must never be committed. On 2026-08-28 the **first real, human-reviewed offline Macro Analyst characterization** was completed and recorded with the completion CLI, covering every claim: agent `macro_analyst`, 14 expected claim/citation pairs, 14 human adjudications (one per pair — none missing, duplicated, or unexpected), `rubric_complete: true`, classification tally `supported: 0` / `partially_supported: 14` / `unsupported: 0` / `unable_to_determine: 0`, all 14 reasons `claim_scope_exceeds_single_observation`, finding tally `info: 8` / `warning: 0` / `failure: 0` (seven factual-transcription findings were exact matches; the one scope-boundary information finding was preserved). All 14 pairs are `partially_supported` because each Macro claim is a two-observation comparison depending on two cited observations while each individual claim/citation pair carries only one of those observations. Every adjudication was the human reviewer's; no LLM judge generated, recommended, or changed any classification; no live request or Macro Analyst rerun occurred; the real artifacts remain gitignored under `data/evaluations/local/` and are not committed. This **closes P0-7 and Phase 0** (see [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) and [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md)) — it is **not** a claim that the Macro Analyst is validated, accurate, repeatable, or profitable. It still performs **no** lexical-overlap scoring, and the same transcription check and citation-support adjudication for the Market Evidence Agent and News Analyst, the abstention matrix, cross-agent consistency, and repeatability studies all remain future work that does **not** reopen Phase 0. |
| Test baseline | `python -m pytest`: **2,626 passed, 4 skipped** (2,630 collected). The 4 skipped are symlink-refusal / symlink-escape tests (the evaluation-foundation serialization tests plus the characterization build-CLI and completion-CLI symlink-escape tests), which skip where the OS disallows creating a symlink; they are not passing tests. |
| Not built | Predictive/forecast model, forecast records, agent orchestrator / combined brief, dashboard, trade journal, intraday regime engine, deterministic contract selector, Options Strategy Agent, scheduler, brokerage execution. (The SPY option-chain connector and storage are now applied and have one stored live batch — see "Storage" above — but no selector, agent, recommendation, or execution consumes it.) |
| Phase 1 (design recorded, ready to begin) | An automated, evidence-based SPY options decision-support workflow: deterministic evidence + intraday regime classification (the deterministic regime engine also fixes one bounded scenario-horizon bucket, or `indeterminate`), a deterministic contract-eligibility selector that consumes that validated upstream horizon, then a bounded directional Options Strategy Agent (consumes only the upstream validated structured outputs plus the selector's eligible contract set, never a raw option chain; may reference but cannot invent, extend, or override the supplied horizon; `no_trade` is first-class), and a recorded evaluation before any usefulness claim. The three existing agents stay non-directional; the Options Strategy Agent is a separately bounded directional decision-support agent. **Step b (connector and storage) is implemented and has been live-exercised once; steps c–h remain design-only and unimplemented.** Phase 0 is now closed (2026-08-28), so Phase 1 has begun. Its **first implementation step — read-only SPY option-chain snapshot ingestion and local storage** (own reviewed connector, sanitized, no execution surface) — is **built in code and tests only** (2026-08-28; ceilings tightened and provenance normalized to two tables 2026-09-01; revised again 2026-09-14 to a three-table design after a batch-membership provenance fix): `AlpacaOptionsChainClient` (`data.alpaca.markets` `/v1beta1/options/snapshots/SPY` only, SPY-only, explicit `opra`/`indicative` feed, bounded expiration/strike/type/page/contract inputs — expiration ≤ 60 days, strike width ≤ $500, ≤ 10 pages, ≤ 5,000 contracts, ≤ 1,000/page — deterministic pagination, OCC-symbol parsing/cross-check, sanitized errors, no retry), migration `0009` (`option_chain_snapshot_batches`: one run-level provenance row per stored retrieval, including a zero-contract retrieval; `option_chain_snapshots`: one row per **immutable** contract observation; `option_chain_snapshot_batch_items`: one truthful membership row per contract per batch, so `contract_count` and membership can never drift apart), `OptionChainSnapshotRepository` (also re-validates every request field against its own canonical `normalize_option_chain_request` form before any write), and the dry-run-first `scripts/ingest_alpaca_options_chain.py`. **Superseding update (2026-09-14): migration `0009` has been applied to the real database, and one authorized live, `--execute` ingestion has succeeded** — provider `alpaca`, underlying `SPY`, requested feed `indicative` (explicitly not OPRA), one expiration (2026-09-18), strikes 740–790, 102 contracts received and inserted (51 calls, 51 puts), one successful batch and one successful ingestion run, no retry. **A subsequent read-only structural audit (2026-09-14, same day)** confirmed: 102 batch-membership rows and 102 snapshot rows, zero duplicate or orphan memberships, zero malformed OCC symbols or out-of-range contracts, quote and trade data present for all 102, implied volatility and each Greek present for 78 and unavailable for 24, zero negative/nonfinite values, zero crossed quotes, zero feed mismatches, and exactly one retrieval timestamp. **No recommendation, selector, agent, alert, dashboard, or execution action occurred as part of this ingestion or audit.** `indicative`-feed data may be delayed or modified and must not be described as live OPRA; the audit establishes internal structural consistency only — not pricing accuracy, timeliness, usefulness, predictive edge, strategy validity, or profitability; a future selector that requires IV/Greeks must reject or omit the 24 contracts with missing values, never substitute zero; open interest remains unavailable from this endpoint; and only one batch exists, so recurring reliability is not established. Nothing else in the workflow (regime engine, contract selector, Options Strategy Agent, evaluation) exists, and nothing is validated. **Next planned implementation step: the deterministic SPY intraday feature/regime engine (workflow step c) — not the deterministic contract selector or the Options Strategy Agent.** See [docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md). |

## Current Phase

**Phase 0 — Infrastructure Foundation: closed 2026-08-28. Phase 1 (SPY options
decision-support workflow) may now begin.**

All seven Phase 0 exit criteria (P0-1 … P0-7) are met — see
[docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md). The final criterion, P0-7, was met
on 2026-08-28 when the first real, human-reviewed offline Macro Analyst
characterization was completed and recorded (14 expected claim/citation pairs,
14 human adjudications, `rubric_complete: true`, all 14 `partially_supported` /
`claim_scope_exceeds_single_observation`, findings `info: 8` / `warning: 0` /
`failure: 0`; see the Trust layer row above and Completed Work Log item 39).
**Closure means the required infrastructure and the required agent-evaluation
methodology exist and have each been exercised and recorded once — it is not a
claim that the Macro Analyst or any other agent is validated, universally
accurate, repeatable, or profitable, and only the Macro Analyst has been
characterized.** Phase 1's first implementation step — read-only SPY
option-chain snapshot ingestion and local storage
([docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md)) — is
now done, including a live run (see Completed Work Log item 40): a bounded
`AlpacaOptionsChainClient`, migration `0009`, a repository, and a
dry-run-first ingestion script. **Migration `0009` has been applied to the
real local database (2026-09-14, healthy at schema version `0009`), and one
authorized live, `indicative`-feed ingestion has succeeded** (SPY, one
expiration, strikes 740–790, 102 contracts received/inserted), followed by a
read-only structural audit that found zero integrity or malformed-data
issues — this confirms internal structural consistency only, not pricing
accuracy, timeliness, usefulness, predictive edge, strategy validity, or
profitability. The next planned implementation step is the deterministic SPY
intraday feature/regime engine — not the deterministic contract selector or
the Options Strategy Agent, neither of which exists. The manual-only execution boundary and the non-directional
guarantee on the three existing analysis agents are unchanged.

The prose below is retained as the Phase 0 history. Python environment and
dependency configuration are in place. Read-only Alpaca market-data provider
connectivity has been verified (a single read-only snapshot request — see
Status below). Read-only FRED macroeconomic-data provider connectivity has
also been verified (a single read-only latest-observation request — see
Status below). Read-only Alpaca news provider connectivity has also been
verified (a single read-only SPY-news request — see Status below), and one
explicitly authorized live SPY news ingestion has succeeded through the
storage pipeline (10 received, 10 inserted, 0 failed — see Status below).
A read-only Alpaca historical stock-bars connector now also exists
(`AlpacaBarsClient`). Its first authorized live connectivity check reached
Alpaca but was configured yet unsuccessful (sanitized status
`configured=True, success=False, status_category=4xx`) under the prior,
implicit-SIP default request; the connector was then hardened to
explicitly request the IEX feed, and a second authorized live connectivity
check against the hardened, explicit-IEX connector has now succeeded
(sanitized status `configured=True, success=True, status_category=2xx`,
single-symbol SPY, `5Min` timeframe, `feed=iex`, 5 bars returned — see
Status below). This confirms live connectivity and response normalization
on the IEX feed only; it does not confirm SIP connectivity, and IEX's
narrower single-exchange coverage still applies. **The explicit-IEX bars
connector remains live connectivity-verified as described above** — it has
since also been hardened to explicitly send fixed `adjustment=raw` and
`currency=USD` provenance (alongside the existing `feed=iex`). The earlier
five-bar connectivity check predated this additional hardening, but the
subsequent authorized 2026-08-21 ingestion (see Status below) exercised the
hardened `feed=iex`, `adjustment=raw`, `currency=USD` request combination
live. Connectivity/one successful ingestion run is not the same as a
validated, cataloged data pipeline: no complete, gap-free, bulk,
catalog-validated, or research-validated provider dataset exists yet. A
market-bar storage schema and
repository (`market_bars`, migration `0005`, `BarRepository`) now also
exist as schema/storage-capability infrastructure, originally covered by
tests using temporary databases only. **A separately authorized real-database
migration and ingestion run has since applied migration `0005` to the real
local database and stored a first batch of real bars** (see Status below)
— this is one controlled ingestion run, not a complete, gap-free, or
validated historical dataset. A local DuckDB storage foundation exists; the
real local database was backed up and then upgraded to schema version
`0005` (5 migrations applied) via that authorized run, and a subsequent
read-only health check reported it healthy (see Status below). **One stored
news ingestion also still exists** (10 SPY articles, see below). A
FRED historical-observations connector method, macro-observations schema
(migration `0006`), and repository were added as infrastructure for a
future Macro Analyst agent, initially in repository code and tests only;
a read-only health check against the still-`0005` real database at that
time reported `healthy=False` (behind the latest available migration —
see Status below, preserved as an honest diagnostic record and not
retracted). **On 2026-08-21, a separately authorized run backed up the
real database and applied migration `0006`**, after which a health check
reported the database healthy at schema version `0006` (see Status
below). **A separately authorized first live FRED historical-observations
ingestion (FEDFUNDS, 2025-08-01 through 2026-07-31) then also succeeded**
(see Status below) — this confirms one bounded historical fetch, response
normalization, transactional storage, and local retrieval; it is not a
complete, gap-free, broadly cataloged, or research-validated macro
dataset. **A conservative ingestion-orchestration layer was then added
(2026-08-23) covering exactly the three existing reviewed jobs (Alpaca
news, Alpaca bars, FRED observations), and a first authorized live
orchestration run then succeeded the same day** (see Status below) —
this confirms one controlled, explicitly authorized orchestrated
ingestion run; it does not confirm scheduling, continuous or unattended
operation, dataset completeness, prediction, agent intelligence, options
analysis, or trading execution. A first authorized live OpenAI
structured-output provider connectivity check has also since succeeded
(2026-08-24, see Status below, superseding the earlier "no live OpenAI
request" status) — this confirms only that the existing OpenAI provider
boundary can reach OpenAI, authenticate, and receive/parse one minimal
structured-output response; it is not an agent, prediction, recommendation,
or market-analysis capability. A narrow, single-turn Market Evidence Agent
has also since been added (2026-08-24, code/tests/docs only — see Status
below and
[docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md)): it
summarizes and organizes already-stored evidence behind a deterministic
preflight gate. **A first authorized live run has since been made (also
2026-08-24, see Status below): a dry run against the real database
succeeded (eligible, 20 evidence items), and one authorized live
`--execute` attempt failed structured-output validation
(`OpenAIParseFailureError`) — no analysis was accepted from that attempt.**
**A separately authorized follow-up `--execute` attempt, made the same day
after the PR #20 structured-output hardening described below, then
succeeded: one live analysis was accepted end to end (see the "First
completed live Market Evidence Agent run" Status entry below and
[docs/MARKET_EVIDENCE_EVALUATIONS.md](docs/MARKET_EVIDENCE_EVALUATIONS.md)
for the sanitized record and a manual quality read of that one output —
that manual read is one example, not a validated evaluation methodology or
a claim of factual accuracy).** It is not integrated into
`market_intelligence/orchestration/`. `directional_assessment`/
`trade_recommendation` on every report it produces are always the fixed
value `"not_performed"` — the model-facing schema does not even include
those fields, so this restriction is absolute. Its free-text fields are
additionally screened by a deterministic, fail-closed post-response content
policy check (added 2026-08-24, code/tests/docs only, see
[docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md)) that rejects
known directional-prediction, bullish/bearish-bias, trade-recommendation/
action, and options-related language — a conservative, bounded filter and
defense-in-depth on top of its developer instructions, not proof that every
possible semantic violation is detectable. A second narrow agent, the News
Analyst (`market_intelligence/agents/news_analyst.py`), has also since been
added (2026-08-24, code/tests/docs only — see Status below and
[docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)): it extracts and organizes
provider-reported event claims and conditional market-transmission
mechanisms from already-stored news, built on the existing
`NewsEvidenceBuilder` snapshot behind its own deterministic preflight gate.
It shares the Market Evidence Agent's non-directional guarantee
(`directional_assessment`/`trade_recommendation` always `"not_performed"`,
the model-facing schema excludes those fields entirely) and reuses the same
extracted, shared post-response content-policy matcher
(`market_intelligence/agents/non_directional_output_policy.py`). **A
separately authorized live `--execute` attempt has since been made (also
2026-08-24, symbol SPY, limit=5, see Status below): the deterministic
preflight passed and exactly one live OpenAI request was sent, but that
request failed structured-output validation
(`response_validation_failed`) — no analysis was accepted, and no retry was
made.** Offline diagnosis and recurrence-reduction hardening (conservative
advisory output budgets, added to `AGENT_INSTRUCTIONS` with margin below
every corresponding hard Pydantic maximum) followed the same pattern
already used for the Market Evidence Agent's own 2026-08-24
`response_validation_failed` failure — see the "News Analyst live execute
attempt" Status entry below and
[docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)/[docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)
for full detail. No hard schema bound, citation validation, content-basis
validation, output-policy validation, or the zero-retry behavior was
weakened. **This one-failed-attempt status has since been superseded: three
further separately authorized live attempts completed the sequence
(incomplete at `max_output_tokens=2048`, a local timeout at the then-default
30-second timeout, then a completed run with `max_output_tokens=4096`/
`timeout=120s`) — see the "News Analyst live run sequence completed" Status
entry below. A manual quality read of that completed run's event claims
found one weak, speculative claim built from an article that should not
have been turned into a claim at all, which motivated a market-relevance
hardening change (a required `relevance` classification/rationale per event
claim, new post-response relevance validation, a truthful — reason-free —
deterministic limitation noting how many supplied articles were not
included in retained claims, a safe code-controlled abstained outcome for
when no supplied article is sufficiently relevant, and `Settings`' own
`openai_max_output_tokens`/`openai_request_timeout_seconds` defaults
corrected to 4096/120) — see the same Status entry and
[docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md).** Beyond these two narrow
agents, no other AI analysis or agent orchestration (in the AI-agent sense)
exists yet. A read-only Macro Evidence Snapshot layer
(`market_intelligence/market_features/macro_evidence.py`,
`scripts/build_macro_evidence.py`) has also since been added (2026-08-24,
code/tests/docs only -- see Status below and
[docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md)),
mirroring `MarketContextBuilder`'s/`NewsEvidenceBuilder`'s pattern for
already-stored FRED macro observations. **This is infrastructure for a
future Macro Analyst agent, not an agent itself** -- it makes no model
request, no FRED request, and no prediction, market-regime label, or
transmission-mechanism inference of any kind, and it has not been run
against the real local database as part of this change. A narrow FRED
series-*metadata* pipeline (as distinct from the series *observations*
pipeline above) has also since been added
(2026-08-24, code/tests/docs only -- see Status below and
[docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)/
[docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md)):
`FredMacroDataClient.get_series_metadata()`, migration `0008`
(`macro_series_metadata`), `MacroSeriesMetadataRepository`,
`scripts/ingest_fred_series_metadata.py`, and a read-only
`MacroEvidenceBuilder` update (`metadata_available` plus `title`,
`frequency`, `units`, `seasonal_adjustment` per series, plus an aggregate
`missing_metadata_series` flag) exist so a future Macro Analyst never
interprets an unlabeled number. **This is infrastructure only, exists in
code and tests only, and has not been run live or against the real local
database as part of this change** -- migration `0008` has not been applied
to the real database, which remains at migration `0007`. A bounded Macro
Analyst agent (`market_intelligence/agents/macro_analyst.py`,
`scripts/run_macro_analyst.py`) has also since been added (2026-08-24,
code/tests/docs only -- see Status below and
[docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)), built directly on
`MacroEvidenceBuilder` above, mirroring the Market Evidence Agent's/News
Analyst's single-turn, no-tools pattern. **This is explicitly a first,
bounded, factual macro-evidence analyst -- not a regime classifier,
predictor, directional market model, or trading agent.** Its deterministic
preflight gate is all-or-nothing across every requested series (default
`FEDFUNDS`): it makes zero OpenAI requests unless every requested series has
a stored observation, has stored official metadata, is not stale, is not
future-dated, does not have `latest_is_missing=True`, and has a stable
evidence ID. Every macro claim it can produce is scoped to a single stored
observation and its official metadata only (`content_basis` is a fixed
literal the agent sets itself, never sent to or received from the model);
`directional_assessment`/`trade_recommendation` are always the fixed
`"not_performed"` value, exactly as for the Market Evidence Agent and News
Analyst. A dedicated, deterministic post-response content-scope check
(distinct from the shared non-directional output policy the other two
agents also use) rejects any claim describing a trend, change, acceleration/
deceleration, surprise, historical extreme, correlation, causation, policy
change, or market regime, since a single snapshot observation with no
comparison value or consensus expectation can never support such a
statement. **This is infrastructure/agent code added in code, tests, and
docs only -- it has not been run against the real local database, and no
live OpenAI request has been made using it, as part of this change.** A
first authorized live run has since succeeded (2026-08-24, FEDFUNDS,
`gpt-5-mini`, stored value 3.63 percent, observation date 2026-07-01,
monthly), and a manual review of that one completed output found two
wording/scope weaknesses -- point-in-time phrasing that could mislead about
a stored monthly observation, and a listed transmission channel
(inflation) not actually explained by `conditional_mechanism` -- which
motivated a bounded hardening change (a deterministic, bounded
`recent_observations` excerpt plus one precisely supported
`latest_change_from_previous` comparison added to `MacroEvidenceBuilder`;
frequency-aware wording, a gated two-observation comparison, and
per-channel addressing validation added to `MacroAnalyst`). **One completed
run and one manual review is not a validated evaluation methodology.** See
item 21 below for the full sanitized record of both the live run and this
hardening change. **A second live `--execute` run made after that hardening
(FEDFUNDS, `recent_observations_limit=6`) then failed differently: the
deterministic preflight passed and exactly one OpenAI request was sent, but
the response was rejected by the post-response content-scope check at
`limitations[0]` -- no report was accepted and no retry was made.** Offline
analysis found that this check's fixed denylist could reject a
model-authored limitation merely for using words like "trend"/"regime"/
"correlation"/"causation" even when clearly negated (e.g. "insufficient to
establish a trend"), which are desirable, honest limitations rather than
prohibited claims; a narrow, fail-closed negation allowance for
`limitations` only (never `claim_summary`/`conditional_mechanism`) was then
added to address this false-positive class. See item 22 below for the full
sanitized record of both this second live failure and the fix. A narrow,
committed **Core Macro Basket** configuration and dry-run-first batch
ingestion script have also since been added (2026-08-24, code/tests/docs
only -- see item 23 below and
[docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md)): a fixed, reviewed
list of exactly seven approved FRED series
(`FEDFUNDS`, `DGS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`), each
with a strictly validated category/lookback/limit contract, plus
`scripts/ingest_core_macro_basket.py`, which reuses the existing FRED
metadata/observations connectors, repositories, and the existing
orchestration run lock unmodified. **This is explicitly a first, bounded
basket, not a complete macro model and not proof of predictive
usefulness.** As of this change it has **not** been run live or against the
real local database -- it exists in code, tests, and docs only, and does not
modify `market_intelligence/orchestration/` (no new job type,
`jobs.json` change, migration, or schema change of any kind). **This
`DGS10`-inclusive list has since been superseded: a separately authorized
first live run of this basket succeeded for six series but failed for
`DGS10` observations only (sanitized category `provider_error`; `DGS10`
metadata succeeded), and `DGS10` was then replaced in the committed
configuration by the monthly FRED series `GS10`, a proposed bounded
replacement not yet requested live -- see item 24 below for the full
record. **This "not yet requested live" status for `GS10` has since been
superseded: a separately authorized live `--execute` run of
`scripts/ingest_core_macro_basket.py` targeting `GS10` (2026-08-25)
succeeded (`metadata_status=succeeded`, `observation_status=succeeded`, 13
observations received, 13 inserted, 0 existing/updated, 0 failed), and a
subsequent read-only verification confirmed the real local database
healthy at schema version `0008` (8 migrations applied) with 1 stored
`GS10` metadata row and 13 stored `GS10` observation rows covering
2025-07-01 through 2026-07-01 (0 missing) -- see item 25 below for the
full record.** **The Macro Analyst live-validation milestone is now
complete.** After a frequency-aware staleness fix, a full-basket coverage
fix, a model-facing evidence compaction fix, and a sequence of separately
authorized seven-series `--execute` attempts that were each rejected --
first locally on evidence size, then by structured-output re-validation,
then by the shared content policy, then by transmission-channel
validation, then again by structured-output re-validation (all preserved
below as honest records, items 26-33, none rewritten as successes) -- a
separately authorized post-compaction seven-series Core Macro Basket
`--execute` run (`FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`,
`INDPRO`, `GDPC1`) was accepted end to end for the first time on
2026-08-26: `status="completed"`, `evidence_quality="sufficient"`, one
macro claim retained per requested series, `limitations` empty,
`transmission_channels` empty for every claim, `directional_assessment`
and `trade_recommendation` both the fixed `"not_performed"` value, exactly
one OpenAI request and no retry. All `OpenAIStructuredClient`
structured-output validation and every Macro Analyst post-response
validator passed. **This is one accepted run, not a validated evaluation
methodology and not a claim that any described observation is factually
accurate** -- see item 34 below for the full sanitized record.

## Status

- Repository initialized. Directory scaffold and governing documents
  (README, CLAUDE.md, AGENTS.md, DATA_CATALOG.md, SOURCE_POLICY.md,
  DECISION_RULES.md) are in place.
- A local Python 3.13 virtual environment (`.venv`) has been created.
  `pyproject.toml` defines the runtime dependency set (duckdb, pandas,
  pyarrow, httpx, pydantic, pydantic-settings, python-dotenv) and a `dev`
  optional-dependency group (pytest, pytest-cov, ruff). The project is
  installed into `.venv` in editable mode. See
  [docs/ENVIRONMENT_SETUP.md](docs/ENVIRONMENT_SETUP.md).
- A settings layer (`market_intelligence/config/settings.py`) has been
  added using pydantic-settings. It reads an optional local `.env` file,
  defines optional Alpaca/FRED/OpenAI/Anthropic credential fields protected
  with `SecretStr`, and exposes a `provider_status()` method that reports
  only booleans, never secret values. Settings can be instantiated without
  any credentials present.
- A read-only Alpaca market-data connector
  (`market_intelligence/data_connectors/alpaca_market_data.py`,
  `AlpacaMarketDataClient`) has been added, covering only Alpaca's
  market-data API (`https://data.alpaca.markets`) — no order, account, or
  execution functionality. It validates that both Alpaca credentials are
  configured before requesting, uses explicit timeouts, and returns only
  sanitized results (never headers, keys, secrets, or the raw response). All
  symbols are normalized/validated (trimmed, uppercased, restricted to a
  conservative U.S. ticker character set) before any request is built, so
  invalid or malicious input never reaches the network; malformed or
  non-object JSON responses are also rejected with a sanitized error/status
  rather than surfaced raw. A companion script,
  `scripts/check_alpaca_connection.py`, reports a
  sanitized connection status (configured, success, status category,
  symbol, timestamp). On 2026-08-20 one live, read-only SPY snapshot
  connection check was run using local `.env` credentials and succeeded
  (2xx, market timestamp returned). This confirms connectivity only; it is
  not the same as a validated data pipeline.
- A read-only FRED macroeconomic-data connector
  (`market_intelligence/data_connectors/fred_macro_data.py`,
  `FredMacroDataClient`) has been added, covering only FRED's official API
  (`https://api.stlouisfed.org`) — no methods beyond fetching published
  series observations. It validates that a FRED API key is configured
  before requesting, uses explicit timeouts, and returns only sanitized
  results (never the API key, request URL, query parameters, raw response,
  or observation value). All series IDs are normalized/validated (trimmed,
  uppercased, restricted to a conservative alphanumeric/underscore
  character set) before any request is built, so invalid or malicious
  input never reaches the network; malformed or non-object JSON responses,
  FRED-reported API error payloads, and missing/malformed observations are
  also rejected with a sanitized error/status rather than surfaced raw. A
  companion script, `scripts/check_fred_connection.py`, reports a
  sanitized connection status (configured, success, status category,
  series ID, latest observation date — never the observation value). On
  2026-08-20 one live, read-only FEDFUNDS latest-observation connection
  check was run using local `.env` credentials and succeeded (2xx,
  observation date returned). This confirms connectivity only; it is not
  the same as a validated data pipeline. A separate, later authorized
  historical-observations ingestion (2026-08-21, see the "Historical FRED
  observations" bullet below) has since stored 12 FEDFUNDS observations;
  that is one bounded ingestion run, not a validated dataset — see
  `DATA_CATALOG.md`.
- A read-only Alpaca news connector
  (`market_intelligence/data_connectors/alpaca_news.py`,
  `AlpacaNewsClient`) has been added, covering only Alpaca's read-only
  data host (`https://data.alpaca.markets`) and only its news endpoint
  (`/v1beta1/news`) — no order, account, or execution functionality, and
  it does not write to DuckDB. It validates that Alpaca credentials are
  configured before requesting, uses explicit timeouts, and returns only
  sanitized results (never headers, keys, secrets, or the complete raw
  response). Symbols, result limits, sort direction, and optional
  start/end timestamps are all strictly normalized/validated before any
  request is built, so invalid or malicious input never reaches the
  network; malformed or non-object JSON responses, and individual articles
  missing required fields, are rejected/skipped with a sanitized
  error/status rather than surfaced raw, and duplicate articles (by
  provider article ID) within a single response are deduplicated. The
  normalized news-item model contains only provider-reported metadata
  (provider article ID, headline, source, URL, summary when available,
  publication/update timestamps when available, related symbols, a UTC
  retrieval timestamp kept distinct from publication time, and provider
  name) — no sentiment, impact, or direction is inferred. A companion
  script, `scripts/check_alpaca_news.py`, reports a sanitized connection
  status (configured, success, status category, requested symbol, article
  count, newest publication timestamp) and never prints headlines, URLs,
  summaries, or raw payloads. On 2026-08-20 one live, read-only SPY-news
  connection check was run using local `.env` credentials and succeeded
  (2xx, 10 articles, newest publication timestamp returned). This confirms
  connectivity only; it is not the same as a validated data pipeline.
  Separately, one explicitly authorized SPY ingestion stored 10 normalized
  news articles, as described below. That verifies one successful ingestion
  run; it is not yet a complete or validated news dataset — see
  `DATA_CATALOG.md`.
- A read-only Alpaca historical stock-bars connector
  (`market_intelligence/data_connectors/alpaca_bars.py`, `AlpacaBarsClient`)
  has been added, covering only Alpaca's read-only data host
  (`https://data.alpaca.markets`) and only its single-symbol historical
  bars endpoint (`/v2/stocks/{symbol}/bars`) — no order, account, or
  execution functionality, and it does not write to DuckDB. It supports
  exactly one symbol per request and only the three project-approved
  timeframes (`1Min`, `5Min`, `1Day`; case/spacing variants are normalized
  to those exact values). `start`/`end` must be strict RFC3339 timestamps
  with an explicit UTC offset, are normalized to UTC, and `start` must be
  strictly before `end`. The per-page limit and page count are both
  strictly bounded, so a malformed or endless provider pagination sequence
  cannot loop indefinitely. The normalized `Bar` model contains only
  provider, symbol, timeframe, feed, bar timestamp (UTC, kept distinct from
  local retrieval time), open/high/low/close (as `Decimal`, to avoid
  binary-float rounding artifacts in values intended for reproducible
  analysis), volume, trade_count (nullable), vwap (nullable), and
  retrieved_at — no indicators, returns, labels, sentiment, predictions, or
  trade directions. Numeric fields reject booleans, non-numeric types, and
  non-finite values (NaN/infinity); candles failing basic OHLC consistency
  are rejected. If a non-empty provider bars list contains any malformed
  bar, the entire request fails with a sanitized error rather than
  returning a misleading partial series; exact duplicate bars (by symbol,
  timeframe, feed, timestamp) are deduplicated, while conflicting
  duplicates fail the request. Results are returned in chronological
  order. A companion script, `scripts/check_alpaca_bars.py`, and a
  `check_connection` method report only sanitized connection status
  (configured, success, status category, symbol, timeframe, feed,
  adjustment, currency, bar count, oldest/newest bar timestamp) — never
  OHLCV values, credentials, URLs, raw responses, or page tokens.

  **First authorized live check and feed hardening (2026-08-20):** the
  first authorized live connectivity check (single-symbol SPY, using the
  connector's then-default request, which sent no explicit `feed`
  parameter) reached Alpaca and returned a sanitized status of
  `configured=True, success=False, status_category=4xx` — only this
  sanitized status was recorded; the raw response body, headers, and
  credentials were never printed or stored. Per Alpaca's official
  documentation, the historical single-symbol bars endpoint defaults to
  the SIP feed when no `feed` parameter is sent, and SIP access requires a
  market-data subscription; the likely cause of the observed 4xx is that
  default SIP routing combined with this project's Alpaca subscription not
  covering SIP (Alpaca returns HTTP 403 in that case), not a credentials or
  code defect. In response, the connector was hardened: it now explicitly
  sends `feed=iex` (a fixed constant, `DATA_FEED`, never a caller-supplied
  argument) on every request, including every paginated page and
  `check_connection`, with no automatic fallback between feeds, and `feed`
  is now recorded on every normalized `Bar` and `BarsConnectionStatus` for
  explicit data provenance. **Known limitation:** IEX is a single
  exchange's feed, not the consolidated SIP tape, so it reflects narrower
  market coverage (fewer trades, potentially different prices/volume) than
  SIP. The 4xx above was observed under the prior, pre-hardening default
  (SIP) request; that failed check is preserved here as an honest
  diagnostic record and is not being retracted or overwritten.

  **Second authorized live check, on the hardened explicit-IEX connector
  (2026-08-20):** a separately authorized live connectivity check was run
  against the now-hardened, explicit-IEX connector (single-symbol SPY,
  `5Min` timeframe) and succeeded, returning a sanitized status of
  `configured=True, success=True, status_category=2xx, symbol=SPY,
  timeframe=5Min, feed=iex, bar_count=5, oldest_bar_timestamp=
  2026-08-17T12:25:00Z, newest_bar_timestamp=2026-08-17T13:30:00Z`. Only
  this sanitized status was recorded — no OHLCV values, credentials, URLs,
  raw response body, or page tokens were printed or stored. **This
  confirms only that the hardened, explicit-IEX connector can reach
  Alpaca, authenticate, and normalize a small live response — it verifies
  connectivity and response normalization only.** It does not confirm SIP
  connectivity (SIP remains unverified and is not requested by this
  connector), and it is not a stored, complete, or validated historical
  bars dataset: no bars from this check were written to DuckDB (this
  connector still does not store bars — bars storage is future, separately
  reviewed work), and no coverage, gap, or quality analysis has been
  performed. As of this 2026-08-20 check, no historical bars dataset had
  been retrieved, stored, or validated (see the first authorized live bars
  ingestion below, 2026-08-21, for the first stored batch).

  **Adjustment/currency provenance hardening (2026-08-20):** alongside
  `feed=iex`, every request — including every paginated page and
  `check_connection` — now also explicitly sends `adjustment=raw` (fixed
  constant `DATA_ADJUSTMENT`; split/dividend-unadjusted prices as
  originally reported, matching this project's "no corporate-action
  adjustment applied" policy) and `currency=USD` (fixed constant
  `DATA_CURRENCY`). Neither is ever accepted as a caller-supplied argument
  anywhere in the connector, and both are recorded on every normalized
  `Bar` and `BarsConnectionStatus`, including failed/unconfigured/
  invalid-input statuses. **The explicit-IEX bars connector remains live
  connectivity-verified** as described in the two authorized checks above;
  no new live check was run against this additional adjustment/currency
  hardening as part of this 2026-08-20 entry, so as of that entry it was
  verified only by unit tests (mocked HTTP transport). This hardening has
  since been exercised live by the first authorized bars ingestion
  (2026-08-21, see below), which used `feed=iex`, `adjustment=raw`, and
  `currency=USD` throughout — one controlled ingestion run, not complete
  dataset validation.
- A market-bar storage schema and repository exist. Migration `0005`
  (`market_intelligence/storage/migrations/0005_create_market_bars.sql`)
  defines a `market_bars` table, and
  `market_intelligence/storage/bar_repository.py` (`BarRepository`) accepts
  already-normalized `Bar` objects from `AlpacaBarsClient` and writes them
  transactionally, mirroring `NewsArticleRepository`'s pattern; a manual
  ingestion script, `scripts/ingest_alpaca_bars.py`, also now exists. The
  table stores only provider-reported OHLCV/vwap data plus provenance
  (`provider`, `symbol`, `timeframe`, `feed`, `adjustment`, `currency`,
  `bar_timestamp`, `open`/`high`/`low`/`close`/`vwap` as `DECIMAL(18,6)`,
  `volume`/`trade_count` as `BIGINT`, `retrieved_at`, `first_ingested_at`,
  `last_seen_at`, `ingestion_run_id`) — no indicator, return, label,
  sentiment, prediction, recommendation, option-contract, order, or
  execution field exists. Idempotency is enforced via a `(provider, symbol,
  timeframe, feed, adjustment, currency, bar_timestamp)` primary key; an
  already-known bar identity whose OHLCV/trade_count/vwap values still
  match has only its retrieval/last-seen/run provenance refreshed, while an
  already-known bar identity whose values conflict aborts the entire batch
  (nothing partially persists) and the corresponding `ingestion_runs` row
  is recorded `failed` with a sanitized error category. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for full
  detail. This capability was originally built and tested against
  temporary databases only (mocked bars, no live Alpaca requests).

  **First authorized real-database migration and live bars ingestion
  (2026-08-21):** `data/market_intelligence.duckdb` was backed up, and a
  separately authorized initialization run then applied migration `0005`
  to the real local database (see the local DuckDB storage foundation
  bullet below for the resulting schema version and health check).
  `scripts/ingest_alpaca_bars.py` was then run once, live, against the
  real database with an explicit, bounded request: single-symbol SPY,
  `5Min` timeframe, `feed=iex`, `adjustment=raw`, `currency=USD`,
  requested interval 2026-08-15T00:00:00Z through 2026-08-20T00:00:00Z,
  `max_pages=1`, `limit=500`. The run received 248 bars, inserted 248, had
  0 existing/updated and 0 failed, and the corresponding `ingestion_runs`
  row was recorded `succeeded`; the latest `ingestion_runs` record for
  this dataset is `('alpaca', 'bars', 'succeeded', 248, None)`. A
  subsequent read-only query of `market_bars` verified 248 stored rows
  for this symbol/timeframe/feed, covering 2026-08-17T12:25:00Z through
  2026-08-19T20:00:00Z. **This confirms one controlled ingestion run,
  transactional storage, and successful local retrieval.** It does not
  establish a complete, gap-free, consolidated, or research-validated
  historical dataset; coverage is IEX only, which is narrower than SIP;
  and it carries no claim of predictive value, strategy validity,
  production readiness, or options-trading capability. Only sanitized
  counts, status, and the verified row count/coverage window are recorded
  here — no OHLCV values are reproduced in this document.
- A local DuckDB storage foundation has been initialized
  (`market_intelligence/storage/`, `DuckDBManager` in
  `market_intelligence/storage/database.py`). It provides a versioned,
  checksum-verified, transactional migration runner and the local
  database file — no forecasting/trading tables exist. The migration code
  now defines five tables: `schema_migrations` (tracks applied migrations
  and their checksums), `ingestion_runs` (records
  provider/dataset/timing/status/record-count/sanitized-error-category/
  code-version/schema-version metadata for ingestion runs), `news_articles`
  (migration `0004`), and `market_bars` (migration `0005`, now applied to
  the real database — see below). Migration `0003` added a separate
  `schema_version` column to `ingestion_runs`, distinct from
  `code_version`. The database file defaults to
  `data/market_intelligence.duckdb` (inside this repository's own `data/`
  directory, per `Settings.project_data_path`) and is excluded from
  version control via `.gitignore`. `scripts/initialize_database.py`
  applies pending migrations and prints only the database path, schema
  version, and applied migration count; `scripts/check_database.py`
  performs a read-only health check that also verifies required columns,
  that the applied migration history matches the migration directory (no
  missing files, no checksum mismatches, no gaps/out-of-order versions),
  and that the database is at the latest available migration —
  `healthy` is false if any of these fail. On 2026-08-20 the local
  database was first initialized (schema version `0002`, 2 migrations
  applied), was upgraded to schema version `0003` (1 additional migration
  applied, 3 total) after migration `0003` was added, and — after the
  authorized live news ingestion described below — was upgraded again to
  schema version `0004` (4 migrations applied). A read-only health check
  on 2026-08-20 reported the real local database healthy at `0004`
  (required tables/columns present, migration history and checksums
  valid, at the latest available migration). After migration `0005`
  (`market_bars`) was added to this repository's migration code, a
  read-only health check against the still-`0004` real database reported
  `schema_version=0004, applied_migration_count=4, healthy=False` (`False`
  only because the database was then behind the latest available
  migration — every other health check, including migration-history
  validity and checksums, still passed); this diagnostic record is
  preserved and not retracted.

  **Migration `0005` applied to the real database (2026-08-21):** as part
  of the first authorized bars ingestion described above,
  `data/market_intelligence.duckdb` was backed up, then
  `scripts/initialize_database.py` was run and applied migration `0005`
  to the real local database. A subsequent read-only health check
  reported: `schema_version=0005`, `applied_migration_count=5`,
  `required_tables_present=True`, `required_columns_present=True`,
  `migration_history_valid=True`, `checksums_valid=True`,
  `is_current=True` (database at latest migration), `healthy=True`. **The
  real database is now at migration `0005` and reports healthy.** See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md).

  **Migration `0006` added to repository code only (2026-08-21, not
  applied):** after migration `0006` (`macro_observations`, see the
  "Historical FRED observations" bullet below) was added to this
  repository's migration code, a read-only health check against the
  still-`0005` real database reports `schema_version=0005,
  applied_migration_count=5, healthy=False` (`False` only because the
  database is now behind the latest available migration — every other
  health check, including migration-history validity and checksums, still
  passes, and every previously stored row — 248 SPY bars, 10 SPY news
  articles — remains intact and untouched). This diagnostic record is
  preserved and not retracted, mirroring how the analogous `0004`-behind-
  `0005` entry was handled above.

  **Migration `0006` applied to the real database (2026-08-21):** as part
  of the first authorized FRED historical-observations ingestion described
  below, `data/market_intelligence.duckdb` was backed up, then
  `scripts/initialize_database.py` was run and applied migration `0006`
  to the real local database. A subsequent read-only health check
  reported: `schema_version=0006`, `applied_migration_count=6`,
  `required_tables_present=True`, `required_columns_present=True`,
  `migration_history_valid=True`, `checksums_valid=True`,
  `is_current=True` (database at latest migration), `healthy=True`. **The
  real database is now at migration `0006` and reports healthy.** The
  previously stored 248 SPY bars and 10 SPY news articles remain intact
  and untouched. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md).
- A first persistent news-storage table, `news_articles`, has been added
  via migration `0004`
  (`market_intelligence/storage/migrations/0004_create_news_articles.sql`),
  along with a storage service
  (`market_intelligence/storage/news_repository.py`,
  `NewsArticleRepository`) that accepts already-normalized `NewsItem`
  objects from `AlpacaNewsClient` and writes them transactionally, and a
  manual ingestion script (`scripts/ingest_alpaca_news.py`). The table
  stores only provider-reported article metadata plus provenance
  (`provider`, `provider_article_id`, `headline`, `source`, `article_url`,
  `summary`, `created_at`/`updated_at` as reported by the provider,
  `related_symbols` stored sorted/deduplicated for determinism,
  `retrieved_at`, `first_ingested_at`, `last_seen_at`,
  `ingestion_run_id`) — no sentiment, impact, direction, confidence, model
  output, recommendation, or option-contract field exists. Idempotency is
  enforced via a `(provider, provider_article_id)` primary key; an
  already-known article with matching stable content (headline, source,
  URL, publication time) has its mutable fields and provenance refreshed,
  while an already-known article whose stable content conflicts aborts the
  entire batch (nothing partially persists) and the corresponding
  `ingestion_runs` row is recorded `failed` with a sanitized error
  category. See [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)
  for full detail. On 2026-08-20, one explicitly authorized live SPY news
  ingestion was run via `scripts/ingest_alpaca_news.py` against the real
  local database and succeeded: 10 articles received, 10 inserted, 0
  updated, 0 failed, and the corresponding `ingestion_runs` row recorded
  status `succeeded`. This run upgraded the real local database file to
  schema version `0004`. Only sanitized counts and status are recorded
  here — no article headline, URL, or summary content is reproduced in
  this document. This confirms the storage pipeline succeeded for one
  ingestion run; it is not the same as a validated, cataloged news
  dataset — see `DATA_CATALOG.md` for the dataset-level record and
  required-fields status.
- The `market_bars` table exists (migration `0005`, applied to the real
  database — see above) and now holds one authorized ingestion's worth of
  SPY bars (see above). A macro-observation table, connector method, and
  repository also exist (migration `0006`,
  `market_intelligence/storage/migrations/0006_create_macro_observations.sql`,
  `MacroObservationRepository`, `scripts/ingest_fred_observations.py`) — see
  the "Historical FRED observations" bullet below. **Migration `0006` has
  since been applied to the real local database (2026-08-21, see above),
  and a first authorized live FRED observations ingestion has succeeded**
  (see below). This is one bounded ingestion run, not a complete or
  research-validated macro dataset.
- **Historical FRED observations (2026-08-21, added in code/tests, then
  exercised live — see below).** `FredMacroDataClient` (unchanged single-latest-observation
  connectivity check preserved) now also exposes `get_observations()`: a
  strictly validated, bounded, paginated fetch of historical observations
  for one series over an explicit `observation_start`/`observation_end`
  calendar-date range. `series_id`, `observation_start`, and
  `observation_end` are validated before any request is built (calendar-date
  shape, real calendar date, `observation_start <= observation_end`); the
  per-page limit and page count are both strictly bounded (no caller can
  raise the fixed ceiling), sorted ascending. Every page also explicitly
  sends fixed, non-overridable `realtime_start=1776-07-04`,
  `realtime_end=9999-12-31`, `output_type=1`, and `units=lin`: FRED
  documents that an omitted realtime_start/realtime_end defaults both to
  *today's date* rather than the observation's actual reported revision
  window, so requesting the complete real-time period explicitly is what
  makes `realtime_start`/`realtime_end` describe FRED's real
  revision/vintage window and keeps the storage identity (below) stable and
  meaningful across ingestion runs on different retrieval days, rather than
  merely reflecting the retrieval date. Pagination metadata is hardened:
  every page's `offset` must be a plain nonnegative integer exactly equal
  to the offset requested, every page's `count` must be a plain nonnegative
  integer identical across all pages, and a short/empty page is only
  accepted as complete once `offset + returned` has actually reached
  `count` — any offset/count inconsistency or mismatch, or a short/empty
  page while records remain outstanding, fails the whole request with a
  sanitized error instead of silently returning an incomplete series, and
  exceeding the hard page-count ceiling likewise fails safely. FRED's `"."`
  missing-observation marker is preserved as `value=None, is_missing=True`;
  every other value is parsed as a finite `Decimal` from FRED's own string
  representation (non-finite/malformed values are rejected). Any malformed
  observation in a non-empty response fails the whole fetch rather than
  returning a misleading partial series; exact duplicate observations
  (matched on series_id, observation_date, and the full realtime_start/
  realtime_end vintage) are deduplicated, conflicting duplicates fail the
  fetch, and results are returned in deterministic chronological order.
  Errors and statuses never include the API key, request URL/query
  parameters, raw responses, or observation values. A macro-observations
  schema (migration `0006`, `macro_observations` table), a hardened,
  transactional `MacroObservationRepository` (mirroring `BarRepository`'s
  validate-before-write, single-transaction, conflict-rollback pattern, with
  an identity of `(provider, series_id, observation_date, realtime_start,
  realtime_end)` so FRED revisions/vintages are preserved rather than
  collapsed), and a one-shot manual ingestion script
  (`scripts/ingest_fred_observations.py`) now all exist, covered by tests
  using temporary DuckDB files and mocked HTTP transports only. The
  repository also now strictly enforces `provider == "fred"` (the fixed
  `DEFAULT_PROVIDER` constant): any alternate, blank, malformed, or
  non-string `provider` argument is rejected before any connection is
  opened, any `ingestion_runs` row is written, or any observation is
  written, and the rejected value is never echoed. As of 2026-08-21 (prior
  to the live run below), none of this had been run live: migration `0006`
  had not been applied to the real database (which remained at `0005`,
  healthy for everything already applied, but no longer at the latest
  available migration — see above), no FRED observation had been fetched
  from the live API using this new method, and no observation had been
  stored. FRED connectivity itself was previously verified only via the
  pre-existing single-latest-observation check (2026-08-20, see below).

  **First authorized live FRED historical-observations ingestion
  (2026-08-21):** `data/market_intelligence.duckdb` was backed up, migration
  `0006` was applied (see above), and `scripts/ingest_fred_observations.py`
  was then run once, live, against the real database with an explicit,
  bounded request: series `FEDFUNDS`, requested observation range
  2025-08-01 through 2026-07-31, fixed request provenance
  (`realtime_start=1776-07-04`, `realtime_end=9999-12-31`, `output_type=1`,
  `units=lin`), `limit=1000`, `max_pages=1`. The run received 12
  observations, inserted 12, had 0 existing/updated and 0 failed, and the
  corresponding `ingestion_runs` row was recorded `succeeded`; the latest
  `ingestion_runs` record for this dataset is `('fred', 'macro_observations',
  'succeeded', 12, None)`. A subsequent read-only query of
  `macro_observations` verified 12 stored rows for this series, covering
  2025-08-01 through 2026-07-01, with 0 missing observations. **This
  confirms one bounded historical fetch, response normalization,
  transactional storage, and local retrieval.** It does not establish a
  complete, gap-free, broadly cataloged, or research-validated macro
  dataset. Requesting the complete real-time period
  (`realtime_start=1776-07-04`, `realtime_end=9999-12-31`) means this run's
  vintage window is FRED's actual revision window, not merely today's
  retrieval date — it does not mean all of FEDFUNDS's revision history has
  been retrieved for every observation date outside the requested range.
  `DECIMAL(20,6)` remains a deliberately bounded supported range for this
  project's currently-ingested series, not a claim of universal support
  for every FRED series. Only sanitized counts, status, and the verified
  row count/coverage window are recorded here — no observation values are
  reproduced in this document. The previously stored 248 SPY bars and 10
  SPY news articles remain intact and untouched. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) and
  `DATA_CATALOG.md`.
- No trading execution connected. No brokerage integration exists or is
  planned; Robinhood is used manually, outside this system.
- No validated predictive model. No forecasting, scoring, or evaluation
  logic has been built or tested.
- **This is an independent project.** It does not depend on, read from, or
  otherwise access the separate ORB_Project. Read-only Alpaca provider
  connectivity has been verified; one authorized ingestion run stored 10
  normalized SPY news articles, and a separate authorized ingestion run
  stored 248 normalized SPY bars (IEX, `5Min`, see above). A third
  authorized ingestion run stored 12 normalized FEDFUNDS macro observations
  (see above). No complete, gap-free, or validated provider dataset has
  been cataloged yet. The settings layer's only
  data-path configuration is `project_data_path`, which defaults to this
  repository's own `data/` directory. No code in this repository may
  access files outside the repository unless the user explicitly
  authorizes a specific source.
- **Ingestion-orchestration layer added (2026-08-23, code/tests only, not
  run live at the time it was added).** A new package, `market_intelligence/orchestration/`, adds
  immutable, strictly validated job contracts for exactly the three
  existing reviewed job types (`alpaca_news`, `alpaca_bars`,
  `fred_observations`), a small committed JSON configuration file
  (`market_intelligence/orchestration/jobs.json`) defining the three
  initial reviewed jobs (SPY news, SPY 5-minute IEX/raw/USD bars, FEDFUNDS
  observations), a dry-run-first CLI entry point
  (`scripts/run_ingestion_pipeline.py`), narrow job adapters that call the
  existing reviewed connectors/repositories directly (never a shell
  command, subprocess, or the manual `scripts/ingest_*.py` scripts), a
  conservative fail-closed local run lock, and a persistent orchestration
  audit trail (migration `0007`, `orchestration_runs`/
  `orchestration_job_runs`). See
  [docs/INGESTION_ORCHESTRATION.md](docs/INGESTION_ORCHESTRATION.md) for
  full detail. As initially added, this orchestration layer, including
  migration `0007`, existed in code and tests only — it had not been run
  with `--execute` against the real database, and migration `0007` had not
  been applied to the real database, which remained at migration `0006`. A
  read-only health check at that time reported the real database
  `schema_version=0006, applied_migration_count=6, healthy=False`
  (`False` only because the database was behind the latest available
  migration in the repository's code — every other health check,
  including migration-history validity and checksums, still passed),
  mirroring the same honest-diagnostic pattern already used for the
  `0004`→`0005` and `0005`→`0006` transitions above; this diagnostic
  record is preserved and not retracted. The previously stored 10 SPY
  news articles, 248 SPY bars, and 12 FEDFUNDS observations remained
  intact and unchanged — verified via read-only queries as part of that
  change. No AI agent, analysis, prediction, recommendation, scheduling,
  or brokerage/Robinhood integration was built as part of that change.
  **This code/tests-only state has since been superseded by a first
  authorized live orchestration run — see the entry immediately below.**

- **First authorized live orchestration run (2026-08-23).**
  `data/market_intelligence.duckdb` was backed up, migration `0007`
  (`orchestration_runs`/`orchestration_job_runs`) was applied to the real
  local database, and `scripts/run_ingestion_pipeline.py` was then run
  once, live, with `--all --execute` against the real database, selecting
  all three existing reviewed jobs (`alpaca_news_spy`,
  `alpaca_bars_spy_5min`, `fred_fedfunds_observations`) in one
  orchestrated run.

  A subsequent read-only health check reported: `schema_version=0007`,
  `applied_migration_count=7`, `required_tables_present=True`,
  `required_columns_present=True`, `migration_history_valid=True`,
  `checksums_valid=True`, `is_current=True`, `healthy=True`. **The real
  database is now at migration `0007` and reports healthy.**

  The orchestration run
  (`orchestration_run_id=e63d931e-8957-4357-93ee-ba7076b079d8`) completed
  with overall status `succeeded`. Per-job sanitized results, each
  recorded `succeeded` in `orchestration_job_runs`:

  - `alpaca_news_spy` (Alpaca news, SPY): 10 received, 10 inserted, 0
    existing/updated, 0 failed.
  - `alpaca_bars_spy_5min` (Alpaca bars, SPY, `5Min`, `feed=iex`,
    `adjustment=raw`, `currency=USD`): 334 received, 169 inserted, 165
    existing/updated, 0 failed.
  - `fred_fedfunds_observations` (FRED, FEDFUNDS): 3 received, 0
    inserted, 3 existing/updated, 0 failed.

  A subsequent read-only query confirmed `orchestration_runs` contains
  exactly 1 run and all three `orchestration_job_runs` rows for it are
  recorded `succeeded`. Only sanitized counts and status are recorded
  here — no headline, URL, summary, OHLCV, or observation value from this
  run is reproduced in this document.

  **This confirms one controlled, explicitly authorized orchestration run
  across all three existing reviewed jobs, transactional per-job storage,
  and a persistent orchestration audit trail.** It does not establish
  scheduling, continuous or unattended operation, dataset completeness or
  gap-freedom for any of the three underlying datasets, prediction, agent
  intelligence, options analysis, or trading execution — none of that
  exists or was exercised by this run. The `alpaca_bars_spy_5min` job's
  165 existing/updated bars and the `fred_fedfunds_observations` job's 3
  existing/updated, 0 inserted result reflect idempotent overlap with
  previously stored bars/observations within each job's own bounded
  request window — not a claim of complete or gap-free coverage for
  either dataset. See
  [docs/INGESTION_ORCHESTRATION.md](docs/INGESTION_ORCHESTRATION.md) and
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for full
  detail.

- **Read-only market-context snapshot layer added (2026-08-23, code/tests
  only; not run against the real database as part of this change).** A new
  package, `market_intelligence/market_features/`, adds
  `MarketContextBuilder` — a strictly validated, deterministic, read-only
  builder that assembles exactly one JSON-ready snapshot dict from data
  already stored in the local DuckDB database (latest stored bar plus a
  bounded recent-bar summary, a short-period price return computed only
  when enough stored bars exist, bounded recent news metadata, the latest
  stored macro observation per a bounded set of configured series, source
  provenance, and explicit missing/stale-data flags) — and
  `scripts/build_market_context.py`, a one-shot CLI that prints one
  sanitized snapshot to stdout. It makes no network request of any kind,
  opens the database only via `duckdb.connect(path, read_only=True)`,
  never writes a row or applies a migration, and does not modify any
  connector, ingestion repository, orchestration code, or
  `orchestration/jobs.json`. Symbol, the two result-count limits, and the
  requested macro series IDs are all strictly validated (rejecting
  booleans, zero, negatives, excessive limits, and malformed input) before
  any DuckDB connection is opened. No snapshot is persisted anywhere. See
  [docs/MARKET_CONTEXT_SNAPSHOT.md](docs/MARKET_CONTEXT_SNAPSHOT.md) for
  the full field contract, the fixed staleness thresholds used, and known
  limitations. This adds no sentiment, prediction, trading bias,
  confidence score, options recommendation, or other agent conclusion —
  only already-stored provider data plus this module's own
  provenance/coverage/staleness bookkeeping about it.

- **OpenAI structured-output provider boundary added (2026-08-23, code/tests
  only; no live OpenAI request or connectivity check made as part of this
  change).** A new package,
  `market_intelligence/model_clients/`, adds `OpenAIStructuredClient`
  (`market_intelligence/model_clients/openai_structured.py`) — a minimal,
  defensive wrapper around the official OpenAI Python SDK's Responses API
  (`client.responses.parse`) using native Pydantic Structured Outputs. The
  official `openai` package was added as a runtime dependency
  (`pyproject.toml`, `openai>=1.99.0` as a compatible lower bound; installed
  version at the time of writing is `3.3.1`, which depends on `httpx2`, the
  official SDK's current HTTP-layer dependency per PyPI's published package
  metadata for `openai`).

  `OpenAIStructuredClient.generate()` accepts exactly three inputs — fixed
  developer instructions (a trusted string authored by calling code, never
  derived from untrusted data), one bounded JSON-ready evidence dict, and
  one explicitly supplied Pydantic output model — and makes one request
  with a fixed, non-caller-overridable shape: the single model configured
  via the new `Settings.openai_model` (default `"gpt-5-mini"`), `store=False`,
  no tools (no function calling, web search, file search, or code
  execution), no conversation persistence, and no caller-supplied
  `base_url`/organization/project/headers. The API key comes only from the
  existing `Settings.openai_api_key` (`SecretStr`). All limits and evidence
  size/shape are validated before the OpenAI SDK client is constructed or
  any request is made; evidence is always serialized deterministically and
  wrapped with a fixed, module-owned label and safety appendix instructing
  the model not to treat it as overriding the developer instructions,
  including when it contains news headlines or other third-party text --
  a defense-in-depth mitigation, not a guaranteed prevention of prompt
  injection. The normalized
  `StructuredOutputResult` never raises for a model refusal or an
  incomplete response (both are reported via a `status` category, never
  with refusal text); a fixed, sanitized `OpenAIStructuredError` subclass is
  raised for missing configuration, timeout, connection failure, rate
  limit, authentication failure, parse failure, and any other unexpected
  SDK failure or unrecognized response shape — the entire SDK call and
  response-normalization step is wrapped in one sanitized exception
  boundary, so any exception type not already mapped to a specific
  category (including a malformed response with missing/non-iterable
  output, an unexpected status shape, or a parsed object of the wrong
  type) becomes a fixed `OpenAIUnexpectedError` with no raw
  type/message/body/path/header/evidence attached. Provider-reported
  metadata on the result is also sanitized rather than passed through
  as-is: `response_id` is returned only if it matches OpenAI's bounded
  `resp_...` ID shape (otherwise `None`), token counts are accepted only
  as plain nonnegative integers (otherwise `None`), and
  `incomplete_reason` is mapped only from OpenAI's known fixed categories
  (otherwise `"other"`/`None`) — no raised error or result field ever
  includes the API key, request body, evidence, headline, raw model
  output, raw SDK exception message, URL, or header. Two new non-secret
  `Settings`
  fields (`openai_request_timeout_seconds`, default 30s at the time this was
  added, bounded to `(0, 120]`; `openai_max_output_tokens`, default 2048 at
  the time this was added, bounded to `[1, 16000]` — **both defaults were
  later raised to 120s/4096 tokens, see the "Settings defaults corrected to
  match the News Analyst's live run sequence" Status entry below; the bounds
  themselves are unchanged**) plus `openai_model` are documented in `.env.example`
  (`.env` itself was not touched). The SDK client is injectable
  (`sdk_client=`) so tests never construct a real `openai.OpenAI` client or
  make a network call. See
  [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md) for
  the full contract. This adds no agent prompt, market bias, prediction,
  recommendation, brokerage integration, persistence, migration, dashboard,
  scheduler, Anthropic client, retry framework, or general agent
  framework — it is a single, narrow provider boundary intended as
  groundwork for a future, separately reviewed agent.

  **This "code and mocked tests only, no live OpenAI request" status
  reflects the state as of 2026-08-23. It has since been superseded — see
  the "First authorized live OpenAI connectivity check" entry immediately
  below** — but the underlying claims that remain true (no agent, prompt,
  market bias, forecast, recommendation, brokerage integration, or general
  agent framework exists on top of this boundary) are not retracted by that
  check.

- **First authorized live OpenAI connectivity check (2026-08-24).** A
  separately authorized, minimal live connectivity check was run against
  the real OpenAI API using the existing `OpenAIStructuredClient` and real
  local `.env` credentials, via a minimal fixed instructions string and an
  evidence dict containing only `{"test_type": "provider_connectivity",
  "contains_market_data": false}`. No market data, news, credentials,
  prompts from providers, predictions, recommendations, or agent analysis
  were sent in the request or produced in the response, and no code, test,
  configuration, `.env`, or migration was changed to run it.

  Sanitized results: `configured=True`, connection outcome
  `status="completed"`, `model="gpt-5-mini"`, parsed structured output
  present (`True`), a sanitized `response_id` matching OpenAI's bounded
  `resp_...` ID shape was returned (present, but the value itself is not
  reproduced in this document, consistent with this client's sanitization
  contract), `input_tokens=143`, `output_tokens=63`, `total_tokens=206`.

  **This confirms only that the existing OpenAI provider boundary can reach
  the OpenAI API, authenticate with the configured API key, and receive and
  parse one minimal structured-output response end to end.** It does not
  confirm model output quality, latency under load, rate-limit behavior,
  cost at scale, or any agent, forecast, recommendation, or market-analysis
  capability — none of that was exercised by this check, and none of it
  exists in this repository. See
  [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md) for
  full detail.

- **Market Evidence Agent added (2026-08-24, code/tests/docs only; no live
  database access or live OpenAI request made as part of this change).** A
  new package, `market_intelligence/agents/`, adds `MarketEvidenceAgent`
  (`market_intelligence/agents/market_evidence_agent.py`) and a
  dry-run-first CLI, `scripts/run_market_evidence_agent.py`. This is a
  **single-turn, no-tools analysis component** — not an autonomous or
  multi-agent system — built entirely on three existing, already-reviewed
  pieces of infrastructure: `MarketContextBuilder`, `SessionQualityBuilder`,
  and `OpenAIStructuredClient`. Given a symbol and an optional session date,
  it builds a bounded, deterministic evidence package from the two builders
  (every fact assigned a stable, code-generated evidence ID), evaluates a
  fixed deterministic preflight gate, and — only if that gate passes — makes
  **exactly one** structured-output request asking the model to summarize
  and organize the evidence.

  The preflight gate requires the requested symbol to match both builders'
  reported symbol, and requires `bars_missing=False`, `bars_stale=False`,
  `completeness.complete=True`, `partial_session=False`,
  `missing_data=False`, and an empty
  `unexpected_or_duplicate_timestamps_utc`; if any of these fail, the agent
  returns a deterministic `status="abstained"` report with fixed reason
  categories and makes **zero OpenAI requests** (zero model tokens spent).
  Missing/stale news or macro data does not block execution — it is instead
  surfaced as a deterministic limitation on the final report. Two strict
  Pydantic models (`extra="forbid"`, every field bounded) govern the
  contract: `MarketEvidenceModelAnalysis` (the only schema sent to OpenAI —
  `evidence_quality`, `evidence_summary`, 1–6 `observations` each citing 1–5
  evidence IDs, 0–6 `limitations`) and `MarketEvidenceReport` (the final
  report, adding `status`, `symbol`, `session_date_et`, and two **fixed**
  literal fields, `directional_assessment` and `trade_recommendation`, both
  always `"not_performed"` — the model-facing schema does not even include
  these fields, so the model cannot set them; that restriction is absolute).
  The model's free-text fields (`evidence_summary`, every observation
  `statement`, every model-supplied `limitation`) are additionally screened
  by a deterministic, fail-closed post-response content policy check
  (`MarketEvidencePolicyError`, added 2026-08-24) that rejects known
  directional-prediction, bullish/bearish-bias, trade-recommendation/action,
  and options-related (strikes/contracts/premiums) language before
  `MarketEvidenceReport` is constructed — a conservative, bounded filter and
  defense-in-depth on top of the model's developer instructions, not proof
  that every possible semantic violation is detectable. The rejected text is
  never echoed in the raised error.

  Every evidence ID the model cites in its response is validated after the
  fact against the exact evidence package sent for that request; a missing,
  fabricated, duplicated, or excessive citation raises a sanitized
  `MarketEvidenceCitationError` rather than being accepted. A model refusal
  or an incomplete response is never silently converted into a completed
  analysis — both raise a distinct, sanitized error
  (`MarketEvidenceRefusalError`/`MarketEvidenceIncompleteError`) instead of
  a report claiming `status="completed"`. The CLI's default mode is a dry
  run: it builds the local evidence package and evaluates preflight only,
  making zero OpenAI requests, and prints only eligibility, the fixed reason
  categories, symbol, session date, an evidence-item count, and data flags —
  never the evidence contents themselves. `--execute` is required for a
  paid model request (and only makes one if preflight passes); execute
  output prints the validated structured report plus sanitized model/token
  metadata (model name and token counts only) — never the raw provider
  response, a response ID, the full evidence payload, credentials, or a
  database path.

  Covered by 47 tests using an injected fake `OpenAIStructuredClient`
  (mirroring `test_openai_structured.py`'s injection pattern — no real SDK
  client is ever constructed and no network call is ever made) and fake
  `MarketContextBuilder`/`SessionQualityBuilder` stand-ins returning fixed,
  hand-authored dicts matching each builder's documented contract shape (so
  these tests never open a real database either), plus a small local eval
  fixture set (typical/missing-data/stale-data/partial-session/
  adversarial-headline scenarios — ordinary local pytest tests, **not** the
  OpenAI Evals API). **Passing this test suite demonstrates the
  deterministic scaffolding around the model call is correct — it does not
  validate the model's analytical accuracy, and no claim of validated
  analytical accuracy is made.** As of this entry, `MarketEvidenceAgent` has
  not been run against the real local database, and no live OpenAI request
  has been made using it — both remain separate, future, and not yet
  authorized. It is not integrated into
  `market_intelligence/orchestration/`, adds no persistence or migration,
  and adds no dashboard, alerting, or brokerage/Robinhood integration. See
  [docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md) for full
  detail.

- **First authorized live Market Evidence Agent run: dry run succeeded, one
  authorized execute attempt failed structured-output validation
  (2026-08-24).** A dry run of `MarketEvidenceAgent` was run against the real
  local database and reported `eligible=true`, `evidence_item_count=20`, with
  every blocking deterministic preflight quality flag passing (`bars_missing`,
  `bars_stale`, session completeness/partial-session/missing-data, and
  unexpected-or-duplicate-timestamps all clear) — zero OpenAI requests were
  made for this dry run, as designed. One separately authorized `--execute`
  attempt was then made: the deterministic preflight passed and **exactly
  one** live OpenAI request was sent (tokens were spent; no exact count was
  recorded — see the diagnostic finding below on why this client cannot
  capture token/response metadata for this particular failure). That request
  did not produce an accepted analysis: it failed with a sanitized
  `{"error": "agent_error", "detail": "OpenAI response failed
  structured-output validation."}` — an `OpenAIParseFailureError` raised
  inside `OpenAIStructuredClient.generate()` and propagated unchanged through
  `MarketEvidenceAgent.run()`. **No analysis was accepted, and per this
  task's explicit instruction, no retry or second live request was made** —
  the failure was diagnosed entirely offline, from the sanitized error
  category alone, using no raw model output (this client never captures or
  logs it).

  **Offline diagnosis (2026-08-24, no live request made to investigate).**
  Reading `OpenAIStructuredClient.generate()`
  (`market_intelligence/model_clients/openai_structured.py`) and the
  installed OpenAI SDK's own source (`openai==3.3.1`,
  `openai/lib/_parsing/_responses.py`, `openai/lib/_pydantic.py`) confirms
  that `pydantic.ValidationError` inside `generate()` can *only* originate
  from the SDK's own client-side re-validation of the model's response text
  against `output_model` (`parse_text()` → `model_validate_json()`) — never
  from strict-schema *construction*, which instead raises `TypeError`/
  `ValueError`/`pydantic.PydanticInvalidForJsonSchema` (already mapped to a
  different, generic code path prior to this change). This structurally
  proves the live failure occurred *after* a request was sent and a response
  was received — this was a response-content validation failure, not a
  request-construction failure.

  Locally regenerating the real, production strict JSON schema for
  `MarketEvidenceModelAnalysis` via the installed SDK's own builder
  (`openai.lib._pydantic.to_strict_json_schema`) confirms the schema itself
  is structurally valid and buildable under the installed SDK — correct
  `additionalProperties: false` throughout, every property required, enums,
  nested arrays, and `$defs`/`$ref` all resolve correctly. **The schema is
  not incompatible with OpenAI's strict Structured Outputs subset, so no
  schema restructuring was needed or made.** That same inspection also shows
  the schema sent to OpenAI includes `minLength`/`maxLength`/`minItems`/
  `maxItems` bound keywords (on `evidence_summary`, every observation
  `statement`, every `limitation`, and every `evidence_ids` list) — keywords
  the SDK's schema builder accepts and forwards, and which only this
  client's own Pydantic re-validation of the response is confirmed to
  enforce. **Because this client never captures or logs raw model output (by
  design — see `docs/OPENAI_PROVIDER_BOUNDARY.md`), the exact field/value
  that violated a bound in this one live attempt is unavailable and cannot
  be proven from local evidence alone, and that limitation is stated here
  honestly rather than guessed at.** Exceeding one of these Pydantic-only
  bounds is one plausible, locally reproducible failure mode for a response
  that was otherwise type/enum/shape-conformant — it is **not** established
  as the proven cause of this specific live attempt, and OpenAI has not
  published official documentation establishing that its Structured Outputs
  generation leaves these bound keywords unenforced specifically for the
  non-fine-tuned `gpt-5-mini` model this project uses. No strict structured
  output, citation validation, output-policy validation, or field bound was
  removed or weakened to work around this — the instructions given for this
  diagnosis explicitly required preserving all of them, and the
  schema-buildability check above showed no structural incompatibility
  existed to correct.

  **Fix applied (2026-08-24, code/tests only — no live request made): sanitized
  failure classification hardened, regression tests added.** Every
  `OpenAIStructuredError` (`market_intelligence/model_clients/openai_structured.py`)
  and `MarketEvidenceAgentError`
  (`market_intelligence/agents/market_evidence_agent.py`) subclass now
  exposes a fixed, sanitized `category` string attribute (e.g.
  `request_schema_invalid`, `response_validation_failed`, `refusal`,
  `incomplete`, `citation_invalid`, `policy_violation`) so a failure's class
  can be identified programmatically without parsing message text. A new
  `OpenAIRequestSchemaError` (category `request_schema_invalid`) is now
  raised — before any SDK client is built or network call is made — whenever
  `output_model` itself cannot be converted into a valid strict JSON schema
  by the installed SDK; this is proven offline with a synthetic
  schema-incompatible Pydantic model (zero SDK calls recorded). **This
  initial implementation of the `OpenAIRequestSchemaError` pre-check used the
  installed OpenAI SDK's own private `openai.lib._pydantic.to_strict_json_schema`
  helper — this has since been superseded the same day, see the follow-up
  entry immediately below, to remove that private-SDK production
  dependency.** This is distinct from and never confused with the existing
  `OpenAIParseFailureError`/`response_validation_failed` category, which now
  unambiguously means a request was sent and a response was received but its
  content failed validation. `scripts/run_market_evidence_agent.py` now also
  prints this sanitized `category` alongside its existing sanitized `detail`
  message for any `agent_error` result.

  New, focused offline regression tests were added to
  `market_intelligence/tests/test_openai_structured.py` using the real,
  production `MarketEvidenceModelAnalysis` schema (not only that file's
  pre-existing generic toy model): one proves the schema passes production's
  own schema preflight (`_validate_output_model`, public Pydantic API only);
  one proves a synthetic, schema-and-bound-conformant response round-trips
  through `generate()` unchanged; and one proves a synthetic response
  violating one of the schema's Pydantic-only length bounds reproduces the
  same sanitized error class, category, and message text this client raises
  for `response_validation_failed` in general — a plausible, locally
  reproducible failure signature consistent with the live failure, not proof
  of that attempt's exact cause — entirely offline, no network, no
  credentials. `market_intelligence/tests/test_run_market_evidence_agent.py`
  gained matching CLI-level regression tests confirming the new `category`
  field. No live OpenAI request, real-database access, dependency addition,
  or orchestration integration was made as part of this diagnostic/hardening
  change; the full test suite and `ruff check` were run and pass.

- **Follow-up hardening (2026-08-24, same day, code/tests only — no live
  request made): recurrence-reduction budgets added, private SDK dependency
  removed.** Two remaining gaps in the fix above were addressed:

  1. **Recurrence reduction.** The prior fix diagnosed and classified the
     2026-08-24 live failure but did not reduce its likelihood. `AGENT_INSTRUCTIONS`
     (`market_intelligence/agents/market_evidence_agent.py`) now includes
     explicit, conservative advisory output budgets — `evidence_summary` at
     most 600 characters, each observation `statement` at most 300
     characters, each `limitation` at most 200 characters, and 1-4
     observations preferred (only more if genuinely necessary), using
     concise, factual wording only. Each budget carries deliberate margin
     below its corresponding hard Pydantic maximum (800 / 400 / 300 / 6
     respectively — `MAX_SUMMARY_LENGTH`/`MAX_STATEMENT_LENGTH`/
     `MAX_LIMITATION_LENGTH`/`MAX_OBSERVATIONS`, all unchanged). **This is
     instruction-level guidance only: no bound was changed, and the agent
     still performs zero truncation, silent modification, retry, or
     acceptance of invalid output** — a response that ignores this guidance
     and still violates a hard bound still fails schema validation exactly
     as before. Seven new focused tests in
     `market_intelligence/tests/test_market_evidence_agent.py` prove each
     advisory budget is present in `AGENT_INSTRUCTIONS` and is numerically
     strictly below its corresponding enforced schema maximum, and that the
     schema maxima themselves are unchanged (800/400/6).

  2. **Private OpenAI SDK dependency removed from production code.** The
     `OpenAIRequestSchemaError` pre-check in
     `market_intelligence/model_clients/openai_structured.py` no longer
     imports or calls `openai.lib._pydantic` (or any other
     underscore-prefixed OpenAI SDK module) — that was a same-day
     regression introduced by the fix above, corrected before any commit.
     `_validate_output_model()` now uses only public Pydantic v2 API
     (`BaseModel.model_json_schema()`) to catch an `output_model` that is
     fundamentally unrepresentable as JSON Schema at all (e.g. a
     `Callable`-typed field), with zero tokens spent. This is a *basic*
     preflight, not an exact replica of OpenAI's stricter Structured
     Outputs subset — an `output_model` that passes this basic check but is
     still incompatible with OpenAI's stricter rules would only be
     discovered later, inside `generate()`'s existing sanitized exception
     boundary, as `OpenAIUnexpectedError`. A grep of `market_intelligence/`
     and `scripts/` confirms zero remaining production references to
     `openai.lib` (only explanatory prose/comments naming it, and no
     `import`). **A version-specific SDK-compatibility test
     (`test_real_market_evidence_schema_builds_a_valid_strict_json_schema`
     in `market_intelligence/tests/test_openai_structured.py`) originally
     added here still imported `openai.lib._pydantic` directly. Its
     docstring named `openai==3.3.1` as the installed version it was written
     against, but `pyproject.toml` only declares `openai>=1.99.0` — no exact
     version is actually pinned, so that docstring overstated the guarantee
     the test provided. This has since been superseded in the PR #20 review
     response below: that test was replaced with
     `test_real_market_evidence_schema_passes_the_production_schema_preflight`,
     which exercises production's own `_validate_output_model` preflight and
     `MarketEvidenceModelAnalysis.model_json_schema()` (public Pydantic API
     only), eliminating the private-SDK test dependency entirely.**

  `docs/OPENAI_PROVIDER_BOUNDARY.md` and `docs/MARKET_EVIDENCE_AGENT.md`
  were updated to match. All existing schema bounds, citation validation,
  output-policy validation, zero-automatic-retry behavior, and sanitized
  error categories were preserved unchanged. No live OpenAI request,
  DuckDB access/modification, or dependency addition was made. Full test
  suite: 1412 passed (up from 1405). `ruff check .` and `git diff --check`
  both pass.

- **PR #20 review response (2026-08-24, same day, docs/tests-only — no live
  request, DuckDB access, or dependency change made).** Two review findings
  on the diagnosis/hardening above were addressed, with zero runtime
  behavior change (no schema bound, advisory budget, error category, retry
  behavior, dependency, or the recorded 2026-08-24 live failure itself was
  altered):

  1. **Overclaiming corrected.** Every claim in code, tests, and docs stating
     or implying that OpenAI's Structured Outputs generation is *documented*
     not to enforce `minLength`/`maxLength`/`minItems`/`maxItems`, or that
     exceeding one of these bounds was the *proven* cause of the one
     authorized 2026-08-24 live failure, has been corrected
     (`market_intelligence/model_clients/openai_structured.py`'s
     `OpenAIParseFailureError` docstring,
     `market_intelligence/agents/market_evidence_agent.py`'s advisory-budget
     comment, `docs/OPENAI_PROVIDER_BOUNDARY.md`, this file, and the
     `test_openai_structured.py` regression-test docstrings/comments) to
     instead state plainly: the exact violated response field/value from
     that live attempt was, and remains, unavailable (this client never
     captures or logs raw model output); exceeding a Pydantic-only bound is
     one plausible, locally reproducible failure mode for that attempt, not
     its established/proven cause; the advisory prompt budgets added above
     reduce that plausible risk but do not guarantee any future request will
     pass validation; and no claim is made that official OpenAI
     documentation establishes this non-enforcement behavior specifically
     for the non-fine-tuned `gpt-5-mini` model this project uses.
  2. **Version-specific private-SDK test removed.** The one remaining
     `openai.lib._pydantic`-importing test (see item 2 immediately above)
     has been replaced with
     `test_real_market_evidence_schema_passes_the_production_schema_preflight`,
     which exercises production's own `_validate_output_model` preflight and
     the real `MarketEvidenceModelAnalysis.model_json_schema()` output (both
     public Pydantic v2 API only). `market_intelligence/tests/
     test_openai_structured.py` now has zero imports of any
     private/underscore-prefixed OpenAI SDK module, and this test suite no
     longer depends on the installed OpenAI SDK version at all (previously
     the test's docstring named `openai==3.3.1` as a version it assumed,
     even though `pyproject.toml` never pinned an exact version).

  Full test suite: 1412 passed (unchanged from the count recorded above —
  one test was replaced, not added or removed). `ruff check .` and
  `git diff --check` both pass.

- **First completed live Market Evidence Agent run (2026-08-24, same day,
  after the PR #20 hardening above).** A separately authorized follow-up
  `--execute` attempt was made against the real database (symbol `SPY`,
  `session_date_et=2026-08-21`; preflight `eligible=true`,
  `evidence_item_count=20`, matching the earlier dry run). This attempt
  completed: **one live model response was accepted end to end** — schema
  validation, evidence-ID citation validation, and the post-response content
  policy check all passed — the first time this agent has produced an
  accepted `status="completed"` `MarketEvidenceReport` from a live run.
  `evidence_quality="sufficient"`, 5 observations were returned,
  `directional_assessment`/`trade_recommendation` were both the fixed
  `"not_performed"` value as always, model `gpt-5-mini`,
  `input_tokens=1648`, `output_tokens=1535`, `total_tokens=3183`. No
  persistence and no automatic retry occurred — this remains a single-turn,
  no-tools, no-storage component exactly as documented above.

  A sanitized record of this run, plus a manual (human) quality read of the
  one accepted report, is kept in
  [docs/MARKET_EVIDENCE_EVALUATIONS.md](docs/MARKET_EVIDENCE_EVALUATIONS.md)
  as evaluation example #1: structured output, citations, and the output
  policy all passed; the latest stored close and the regular-session close
  were kept distinct in the model's summary; the stated five-bar return was
  internally consistent with its own stated endpoints; news/macro/
  session-scope limitations were correctly surfaced; and one minor citation-
  relevance issue was observed (the data-quality observation cited
  `session_same_date_bars` without actually discussing its premarket/
  after-hours counts — a valid evidence ID, but not clearly necessary to the
  statement it supported). **This is one manually read example, not an
  automated evaluation, not a validated evaluation methodology, and not
  proof of factual accuracy, prediction quality, analytical reliability, or
  trading usefulness** — see `docs/MARKET_EVIDENCE_EVALUATIONS.md` for the
  full caveats. No raw model output, response ID, full evidence payload, or
  credential is reproduced in either document. No code, test, schema bound,
  or agent behavior was changed to produce or record this run.

- **News Evidence Snapshot layer added (2026-08-24, code/tests/docs only;
  read-only, no database write, no live provider request, no OpenAI/
  Anthropic call).** A new module,
  `market_intelligence/market_features/news_evidence.py`
  (`NewsEvidenceBuilder`), and a companion CLI,
  `scripts/build_news_evidence.py`, add a strictly validated, deterministic,
  read-only builder that assembles exactly one JSON-ready snapshot dict from
  data already stored in `news_articles` (migration `0004`) -- infrastructure
  for a future News Analyst agent, not an AI agent or model request itself.
  It makes no network request of any kind, opens the database only via
  `duckdb.connect(path, read_only=True)`, never writes a row or applies a
  migration, and imports only one small, already-reviewed read-only
  validation helper from `data_connectors/` (`normalize_symbol`) -- no
  connector HTTP client, storage-repository write path, or model client is
  imported. Symbol is validated via the same `normalize_symbol` used
  throughout this project; the result-count `limit` is strictly bounded
  `[1, 20]` (default `10`), rejecting booleans, non-integers, and
  out-of-range values before any DuckDB connection is opened. Articles are
  returned newest-first by publication timestamp, tie-broken by the full
  remaining stored identity (`provider` ascending, then
  `provider_article_id` ascending), so two articles from different
  providers sharing the same `created_at` (and even the same
  `provider_article_id`, unique only per provider) still sort
  deterministically. A latest stored `created_at` more than a fixed,
  documented 5-minute clock-skew tolerance ahead of `as_of` sets
  `freshness.future_timestamp_detected`/`freshness.stale` both `true`
  without discarding or rewriting the stored timestamp; at or within the
  tolerance it feeds the normal elapsed-time freshness calculation. Every
  article's `headline`/`provider_summary` is preserved exactly as stored,
  including an empty or whitespace-only summary -- this module performs no
  HTML stripping, prompt-injection filtering, truncation, whitespace
  normalization, or other interpretation of that text, since it is
  explicitly treated as untrusted, third-party provider content throughout.
  `content_scope` (`"headline_only"`/`"headline_and_provider_summary"`)
  classifies a `None`, empty, or whitespace-only summary the same as "no
  summary" for that purpose, without altering the stored value itself.
  Each article carries a stable, code-generated
  `evidence_id` (a truncated SHA-256 hash of
  `(provider, provider_article_id)`, never derived from headline/summary
  text), so the same stored article always produces the same ID across
  snapshots. Article URLs are deliberately excluded from the per-article
  evidence fields; they appear only in a separate top-level
  `audit_provenance` field (keyed by the same `evidence_id`), which itself
  states in the snapshot that a future model-facing consumer (e.g. a News
  Analyst) must exclude it from any payload sent to a model. A missing
  database file, a missing `news_articles` table, or a symbol with no stored
  articles all produce a valid, non-crashing snapshot with
  `freshness.missing`/`freshness.stale` both `true`, mirroring
  `MarketContextBuilder`'s/`SessionQualityBuilder`'s established behavior.
  The database connection is always closed on every code path, and a
  close() failure never masks an earlier, already-sanitized read failure.
  See [docs/NEWS_EVIDENCE_SNAPSHOT.md](docs/NEWS_EVIDENCE_SNAPSHOT.md) for
  the full field contract and known limitations.

  Covered by 41 tests (temporary DuckDB databases only; no live network
  access; no access to the real repository database) covering: input
  validation before any DuckDB access, missing database/table/rows,
  populated ordering and tie-breaking (including same-`created_at`,
  same-`provider_article_id` rows from different providers),
  limit bounds, `content_scope` for present/absent/blank (empty and
  whitespace-only) summaries, freshness/staleness around the fixed
  168-hour threshold, future-publication-timestamp detection at and beyond
  the fixed 5-minute clock-skew tolerance (including that the stored
  timestamp and article are preserved, never discarded or rewritten),
  exact preservation of adversarial headline/summary text, connection-close
  success/failure behavior (including that a close failure never masks an
  already-sanitized read failure), sanitized CLI errors, and that neither
  module imports a network or model library.

  As a read-only sanity check (no separate authorization sought, mirroring
  the same reasoning already documented for `MarketContextBuilder`/
  `SessionQualityBuilder` -- a read-only operation has nothing to roll
  back), `scripts/build_news_evidence.py --symbol SPY --limit 3` was run
  once against the real local database and returned a valid snapshot
  reflecting the 20 already-stored SPY articles
  (`total_stored_article_count_for_symbol=20`, matching the evidence-item
  count already recorded for the Market Evidence Agent's dry run above) --
  no row was written, no migration was applied, and no article content is
  reproduced in this document. This is not a live provider request, an AI
  analysis, or a validated news dataset -- see `DATA_CATALOG.md` for the
  underlying news dataset's own status.

- **News Analyst added (2026-08-24, code/tests/docs only; no live database
  access or live OpenAI request made as part of this change).** A new
  agent, `NewsAnalyst` (`market_intelligence/agents/news_analyst.py`), and a
  dry-run-first CLI, `scripts/run_news_analyst.py`, exist, mirroring the
  Market Evidence Agent's pattern but built on `NewsEvidenceBuilder` (see
  above) instead of `MarketContextBuilder`/`SessionQualityBuilder`. This is
  a **single-turn, no-tools analysis component** -- not an autonomous or
  multi-agent system -- that extracts and organizes provider-reported event
  claims and conditional market-transmission mechanisms from already-stored
  news. It **never predicts SPY (or any symbol's) direction, never states or
  implies a bullish/bearish bias, never recommends a trade, and never
  discusses options** -- `directional_assessment`/`trade_recommendation` on
  every report it produces are always the fixed value `"not_performed"`, and
  the model-facing schema does not even include those fields, so this
  restriction is absolute, exactly as for the Market Evidence Agent.

  A fixed, deterministic preflight gate (symbol match, `freshness.missing`/
  `freshness.stale`/`freshness.future_timestamp_detected` all `False`, and
  at least one article returned) must pass before any OpenAI request is
  made; any failure returns a truthful `status="abstained"` report with
  fixed reason categories and makes **zero OpenAI requests**. The
  model-facing evidence package is built from the snapshot's `articles`
  only -- `audit_provenance` and every article URL are never read or
  referenced anywhere in that construction -- and every headline/
  provider_summary is labeled directly in the evidence payload itself as
  untrusted, provider-reported text, never independently verified fact.
  Every model-authored event claim must cite 1-5 of the exact evidence IDs
  supplied (validated post-response against fabrication, duplication, and
  excess), and a claim asserting the richer
  `content_basis="headline_and_provider_summary"` must actually cite an
  article with that content scope in the evidence sent, or the response is
  rejected (`NewsAnalystContentBasisError`) rather than accepted with an
  overstated evidentiary basis.

  The Market Evidence Agent's post-response content policy check (rejecting
  known directional-prediction, bullish/bearish-bias, trade-recommendation/
  action, and options-related language) was extracted into a small shared
  module, `market_intelligence/agents/non_directional_output_policy.py`,
  exposing exactly one function
  (`find_prohibited_content_category(text) -> str | None`); the Market
  Evidence Agent's own `MarketEvidencePolicyError` and behavior are
  unchanged (its existing 95-test suite -- `test_market_evidence_agent.py`,
  its eval-fixtures file, `test_run_market_evidence_agent.py`, and
  `test_news_evidence.py` -- was re-run after this extraction and still
  passes unchanged), and the News Analyst applies the same shared matcher
  to every event claim's `claim_summary`/`conditional_mechanism` and every
  model-supplied `limitation`, raising its own sanitized
  `NewsAnalystPolicyError` on a match. **This remains a conservative,
  bounded filter and defense-in-depth on top of developer instructions for
  both agents -- not proof that every possible semantic violation is
  detectable.**

  Covered by 75 tests (`test_news_analyst.py`,
  `test_news_analyst_eval_fixtures.py`, `test_run_news_analyst.py`,
  `test_non_directional_output_policy.py`) against fake evidence-builder/
  model-client stand-ins -- no real database or network access in tests.
  The CLI's default mode is a dry run (zero OpenAI requests, prints only
  eligibility, reasons, symbol, article count, freshness flags, and
  headline-only/summary-available counts); `--execute` is required for one
  billed OpenAI request, and execute output never prints a response ID,
  article URLs, `audit_provenance`, the full evidence payload, credentials,
  a database path, the raw provider response, or a traceback. See
  [docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md) for the full contract. As of
  this entry, `NewsAnalyst` has not been run against the real local
  database, and no live OpenAI request has been made using it -- both
  remain separate, future, and not yet authorized. It is not integrated
  into `market_intelligence/orchestration/`, adds no persistence or
  migration, and adds no dashboard, alerting, or brokerage/Robinhood
  integration. Full test suite: 1528 passed. `ruff check .` and
  `git diff --check` both pass.

- **First authorized live News Analyst run: one execute attempt failed
  structured-output validation; offline diagnosis and recurrence-reduction
  hardening applied (2026-08-24, same day).** One separately authorized
  `--execute` attempt was made against the real local database (symbol
  `SPY`, `limit=5`). The deterministic preflight passed and **exactly one**
  live OpenAI request was sent (tokens were spent; no exact count was
  recorded — this client never captures token/response metadata for a
  failed request). That request did not produce an accepted analysis: it
  failed with a sanitized `{"error": "agent_error", "detail": "OpenAI
  response failed structured-output validation.", "category":
  "response_validation_failed"}` — an `OpenAIParseFailureError` raised
  inside `OpenAIStructuredClient.generate()` and propagated unchanged
  through `NewsAnalyst.run()`. **No analysis was accepted, and per this
  task's explicit instruction, no retry or second live request was made** —
  the failure was diagnosed entirely offline, from the sanitized error
  category alone, using no raw model output (this client never captures or
  logs it).

  **Offline diagnosis (2026-08-24, no live request made to investigate).**
  `NewsAnalyst` calls the exact same `OpenAIStructuredClient.generate()`
  used by the Market Evidence Agent, whose own 2026-08-24
  `response_validation_failed` failure was already diagnosed in detail (see
  above and [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)).
  Reading `generate()`'s exception-mapping code
  (`market_intelligence/model_clients/openai_structured.py`) proves the same
  three-way distinction holds for this attempt: (1) **not** a
  request-schema construction failure — `output_model`
  (`NewsAnalystModelAnalysis`) is validated by `_validate_output_model()`
  before any SDK client is built or request sent, which raises
  `OpenAIRequestSchemaError`/`TypeError`/`ValueError`, never
  `pydantic.ValidationError`; a new offline regression test
  (`test_real_news_analyst_schema_passes_the_production_schema_preflight`)
  confirms the real schema passes this preflight and is not structurally
  incompatible, so no schema restructuring was needed or made; (2) **not** a
  refusal or incomplete response — both are reported via
  `StructuredOutputResult.status`, mapped explicitly to
  `NewsAnalystRefusalError`/`NewsAnalystIncompleteError`, neither of which
  was raised; (3) **is** a received-response content validation failure —
  `pydantic.ValidationError` inside `generate()` can only originate from the
  installed OpenAI SDK's own client-side re-validation of an actually
  received response against `output_model`, which structurally proves a
  request was sent and a response was received before validation failed.
  **Because this client never captures or logs raw model output, the exact
  field/value that violated a bound in this one live attempt is unavailable
  and cannot be proven from local evidence alone.** Exceeding one of
  `NewsAnalystModelAnalysis`'s Pydantic-only `minLength`/`maxLength`/
  `minItems`/`maxItems` bounds (e.g. `claim_summary`'s 400-character
  maximum, or `event_claims`' 6-item maximum) is one plausible, locally
  reproducible failure mode for a response that was otherwise
  type/enum/shape-conformant — reproduced offline in two new regression
  tests — **not** established as the proven cause of this specific live
  attempt. OpenAI has not published official documentation establishing
  that its Structured Outputs generation leaves these bound keywords
  unenforced specifically for the non-fine-tuned `gpt-5-mini` model this
  project uses. No strict structured output, citation validation,
  content-basis validation, output-policy validation, or the zero-retry
  behavior was removed or weakened to work around this.

  **Fix applied (2026-08-24, code/tests/docs only — no further live
  request made): advisory output budgets added.** `AGENT_INSTRUCTIONS`
  (`market_intelligence/agents/news_analyst.py`) now includes explicit,
  conservative advisory output budgets — `claim_summary` at most 300
  characters, `conditional_mechanism` at most 200 characters, each
  `limitation` at most 200 characters, and 1-4 event claims preferred (only
  more if genuinely necessary) — mirroring the mitigation already applied to
  the Market Evidence Agent. Each budget carries deliberate margin below its
  corresponding hard Pydantic maximum (400 / 300 / 300 / 6 respectively —
  `MAX_CLAIM_SUMMARY_LENGTH`/`MAX_CONDITIONAL_MECHANISM_LENGTH`/
  `MAX_LIMITATION_LENGTH`/`MAX_EVENT_CLAIMS`, all unchanged). **This is
  instruction-level guidance only: no bound was changed, and the agent still
  performs zero truncation, silent modification, retry, or acceptance of
  invalid output** — a response that ignores this guidance and still
  violates a hard bound still fails schema validation exactly as before.

  Thirteen new focused offline tests were added (no network, no
  credentials): four in `market_intelligence/tests/test_news_analyst.py`
  prove each advisory budget is present in `AGENT_INSTRUCTIONS` and is
  numerically strictly below its corresponding enforced schema maximum, one
  proves `OpenAIParseFailureError`/`response_validation_failed` propagates
  unchanged through `NewsAnalyst.run()` without leaking evidence text; six
  in `market_intelligence/tests/test_openai_structured.py` use the real,
  production `NewsAnalystModelAnalysis` schema — one proves the schema
  passes production's own schema preflight, one proves a synthetic,
  schema-and-bound-conformant response round-trips through `generate()`
  unchanged, two prove distinct representative oversized/invalid responses
  (an oversized `claim_summary`, an excessive `event_claims` count) each
  reproduce the same sanitized `response_validation_failed` category and
  message, and two prove no automatic retry occurs (the constructed SDK
  client is always built with `max_retries=0`, and a failing SDK call is
  made exactly once by `generate()`, whatever the failure category — this
  latter pair covers `OpenAIStructuredClient` generally, not only the News
  Analyst's schema). `docs/NEWS_ANALYST.md` and
  `docs/OPENAI_PROVIDER_BOUNDARY.md` were updated to match. All existing
  schema bounds, citation validation, content-basis validation,
  output-policy validation, and zero-automatic-retry behavior were preserved
  unchanged. No live OpenAI request, DuckDB access/modification, or
  dependency addition was made as part of this diagnostic/hardening change.
  Full test suite: 1541 passed (up from 1528). `ruff check .` and
  `git diff --check` both pass.

- **News Analyst live run sequence completed; market-relevance hardening
  applied and then corrected per review (2026-08-24, same day).** The News
  Analyst's live SPY/`limit=5` execute attempt was retried three more times
  under separate authorization, completing the full, truthful sequence
  documented in [docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)'s "Live run
  sequence and manual quality review" section: (1) `response_validation_failed`
  (already recorded above); (2) the model's response incomplete at
  `max_output_tokens=2048` (`NewsAnalystIncompleteError`,
  `incomplete_reason="max_output_tokens"`); (3) a local request timeout at
  the then-default 30-second timeout after `OPENAI_MAX_OUTPUT_TOKENS` was
  raised to 4096 locally; (4) a **completed** run with
  `OPENAI_MAX_OUTPUT_TOKENS=4096` and `OPENAI_REQUEST_TIMEOUT_SECONDS=120`
  both set locally -- model `gpt-5-mini`, `input_tokens=1575`,
  `output_tokens=2474`, `total_tokens=4049`, 4 cited event claims,
  `directional_assessment`/`trade_recommendation` fixed at
  `"not_performed"` as always. **No automatic retries occurred at any
  point** -- each of the four attempts was a separate, manually authorized
  invocation.

  A manual (human) quality read of the 4 accepted event claims found: (1) an
  article about the expected resignation of the U.S. Army Secretary had been
  converted into a weak, speculative SPY-relevance mechanism involving
  defense procurement -- a connection that should generally not have been
  turned into a claim at all; (2) a Baker Hughes rig-count headline was
  classified `economic_data` with `supply_chain`/`growth` channels --
  defensible, but energy/commodity relevance should not be overstated beyond
  the supplied headline; (3) the remaining PMI and investor-flow claims were
  reasonably grounded in their cited evidence. **This is one manually read
  example from one live run, not an automated evaluation, not a validated
  evaluation methodology, and not proof of extraction quality or reliability
  across other symbols/articles.**

  Finding (1) directly motivated a hardening change (code/tests/docs only --
  no further live OpenAI request or DuckDB access was made to apply it),
  which a same-day review then found two remaining gaps in and one further
  correction for -- all three are described together here, reflecting only
  the final, corrected state (code/tests/docs only throughout; no live
  request or DuckDB access at any point in this process):

  1. **Relevance classification and rationale.** Every event claim
     (`EventClaim`, `market_intelligence/agents/news_analyst.py`) now
     requires a strict, model-authored `relevance` classification
     (`"direct"|"broad_market"|"sector_or_industry"` -- deliberately no
     `"unknown"` value that could permit an unsupported claim) and a
     required, bounded `relevance_rationale` (hard max 300 characters,
     advisory budget 200). A post-response validation step,
     `_validate_relevance` (raising `NewsAnalystRelevanceError`, category
     `relevance_invalid`), rejects a blank/whitespace-only rationale, an
     oversized rationale (defense-in-depth alongside the hard Pydantic
     bound), a rationale matching a fixed, deterministic denylist for a bare
     "could affect markets"-style mechanism with no named channel, and a
     `"broad_market"`/`"sector_or_industry"` claim asserted with zero
     `transmission_channels` (internally incompatible, since those two
     relevance values are only meaningful with at least one supporting
     channel; `"direct"` carries no such requirement). `AGENT_INSTRUCTIONS`
     directs the model to omit an article entirely -- write no event claim
     about it -- whenever its connection to the requested symbol would
     require inventing unstated facts, only a generic mechanism can be
     given, or no recognized transmission channel applies, and states
     explicitly that producing claims for fewer articles than supplied is
     valid and often correct.

  2. **Truthful omission wording (review finding: the deterministic code
     cannot know *why* the model left an article uncited).** When at least
     one supplied article's evidence ID was not cited by any event claim,
     the agent itself (never the model) prepends one fixed, deterministic
     limitation to the final report's `limitations` -- but its wording now
     states only the observable fact, e.g. `"1 of 2 supplied articles were
     not included in retained claims."`, never a claimed reason (e.g.
     "insufficiently relevant") this code cannot prove
     (`_build_report_limitations`). It never names the uncited article, its
     headline, or its audit URL, and truncates the combined limitations list
     to the existing `MAX_LIMITATIONS` bound (6) if necessary. (The original
     version of this change used the wording `"...were omitted as
     insufficiently relevant..."`, which attributed a reason the code cannot
     actually verify -- corrected here before commit.)

  3. **All-irrelevant-evidence path (review finding: a hard minimum of one
     event claim could force the model to fabricate a claim even when
     nothing supplied was relevant).** `MIN_EVENT_CLAIMS` was changed from 1
     to **0**: `event_claims` may now be structurally empty.
     `MAX_EVENT_CLAIMS` (6) and the 1-4 preferred advisory range are
     unchanged -- one claim per article was never required and still is not.
     A new post-response check, `_validate_claims_quality_consistency`
     (also raising `NewsAnalystRelevanceError`), enforces a strict
     biconditional: `event_claims` is empty **if and only if**
     `evidence_quality == "insufficient"`. Empty claims paired with
     `"sufficient"`/`"limited"` are rejected (self-contradictory); nonempty
     claims paired with `"insufficient"` are also rejected as an
     incompatible abstention state -- which additionally closes off using a
     low-effort claim as a fabricated placeholder while still flagging the
     evidence as insufficient. When `event_claims` is empty this way,
     `NewsAnalyst.run()` builds a **code-controlled** `status="abstained"`
     report (the model never sets `status` itself) with the new fixed reason
     `ABSTAIN_REASON_NO_SUFFICIENTLY_RELEVANT_ARTICLES` =
     `"no_sufficiently_relevant_articles"`; `evidence_quality` is still
     recorded (`"insufficient"`) and `model_metadata` is still populated
     (tokens were spent, unlike a preflight abstention). The truthful
     omission limitation from (2) applies here too, correctly reporting that
     all supplied articles were not included in retained claims.

  4. **Settings defaults corrected to match the live run
     (`market_intelligence/config/settings.py`).** `openai_max_output_tokens`'
     default was raised from 2048 to **4096**, and
     `openai_request_timeout_seconds`'s default was raised from 30.0 to
     **120.0** -- the exact values live evidence showed were required for a
     completed run (see the four-attempt sequence above). Both fields' upper
     bounds (`le=16000`/`le=120`) are unchanged, and both remain overridable
     via `.env`/the environment. `.env.example` documents the same values
     explicitly for visibility (unchanged from the prior entry, since the
     values themselves were already correct there -- only the code default
     was previously left at its old, now-insufficient value).

  No existing hard schema bound (other than the deliberate `MIN_EVENT_CLAIMS`
  relaxation in (3), which is itself gated by the new consistency check),
  citation validation, content-basis validation, output-policy validation,
  or zero-automatic-retry behavior was weakened.

  Test coverage: `market_intelligence/tests/test_news_analyst.py` covers the
  full relevance contract (all three relevance values accepted when
  grounded; a `"broad_market"`/`"sector_or_industry"` claim rejected with
  zero transmission channels; blank/whitespace-only/oversized/generic
  rationales rejected; a specific, channel-naming rationale accepted; an
  adversarial, self-asserting-relevance headline unable to force a generic
  rationale past validation; `NewsAnalystRelevanceError` never echoing
  rejected text), the truthful uncited-articles limitation (present with the
  correct wording, absent when every article is cited, never leaking a
  headline/URL, correctly truncated at capacity), and the all-irrelevant-
  evidence path (the schema permitting a structurally empty `event_claims`;
  a code-controlled abstained report with the fixed reason and populated
  token metadata when evidence is genuinely insufficient; the biconditional
  rejecting empty claims paired with `"sufficient"`/`"limited"` evidence
  quality, and rejecting nonempty claims paired with `"insufficient"`).
  `market_intelligence/tests/test_openai_structured.py`'s real-schema
  regression tests were updated for the now-required `relevance`/
  `relevance_rationale` fields and the schema's new `minItems=0` on
  `event_claims`. `market_intelligence/tests/test_run_news_analyst.py`'s CLI
  fixture was updated the same way. `market_intelligence/tests/test_settings.py`
  now asserts the corrected 120.0/4096 defaults. The Market Evidence Agent's
  own test suite was re-run unchanged and still passes -- this change
  touches only `market_intelligence/agents/news_analyst.py`,
  `market_intelligence/config/settings.py`, and their own tests.
  `docs/NEWS_ANALYST.md`, `docs/OPENAI_PROVIDER_BOUNDARY.md`, and
  `.env.example` were updated to match. Full test suite: 1567 passed (up
  from 1541 before this change; up from 1560 after the three review
  corrections above). `ruff check .` and `git diff --check` both pass. No
  live OpenAI request, DuckDB access/modification, commit, or push was made
  as part of this change.

- **Macro Evidence Snapshot layer added (2026-08-24, code/tests/docs only;
  read-only, no database write, no live provider request, no OpenAI/
  Anthropic call).** A new module,
  `market_intelligence/market_features/macro_evidence.py`
  (`MacroEvidenceBuilder`), and a companion CLI,
  `scripts/build_macro_evidence.py`, add a strictly validated,
  deterministic, read-only builder that assembles exactly one JSON-ready
  snapshot dict from data already stored in `macro_observations` (migration
  `0006`) -- infrastructure for a future Macro Analyst agent, not an AI
  agent or model request itself. It makes no network request of any kind,
  opens the database only via `duckdb.connect(path, read_only=True)`, never
  writes a row or applies a migration, and imports only one small,
  already-reviewed read-only validation pair from `data_connectors/`
  (`normalize_series_id`/`FredInvalidSeriesIdError`) -- no connector HTTP
  client, storage-repository write path, or model client is imported. It
  never labels a series bullish/bearish, never classifies a market regime,
  never infers a rate-cut/hike direction, never predicts, never describes a
  transmission mechanism, and never recommends anything (including options
  language); it performs no transformation, interpolation, forward-filling,
  seasonal adjustment, or derived-change calculation of any kind.

  The requested FRED series IDs are strictly validated and normalized
  before any DuckDB connection is opened: a bare string or boolean passed
  as the series collection is rejected (not treated as a sequence), an
  empty selection is rejected, more than `MAX_SERIES_IDS` (`10`) series is
  rejected, each entry is validated via the same `normalize_series_id`
  used by `FredMacroDataClient`, and -- unlike `MarketContextBuilder`'s
  macro-series handling -- a duplicate series ID *after* normalization
  (e.g. `"fedfunds"` and `"FEDFUNDS"`) is rejected outright rather than
  silently deduplicated. The normalized result preserves the caller's
  requested order (never sorted), so the snapshot's `series` array always
  reflects the order actually requested. Default series: `FEDFUNDS`.

  **Vintage handling:** a series' full stored identity is `(provider,
  series_id, observation_date, realtime_start, realtime_end)`. For each
  requested series, exactly one row is selected via a fixed, documented
  ordering -- `ORDER BY observation_date DESC, realtime_start DESC,
  realtime_end DESC LIMIT 1` -- i.e. the latest observation date on file,
  then the most recently reported revision of that date. Every field on
  the resulting entry comes from that single chosen row; fields from a
  different vintage are never mixed in, and `realtime_start`/
  `realtime_end` are always reported explicitly so a future consumer can
  audit exactly which revision window was selected.

  **Freshness:** a fixed, documented `stale_after_days` threshold (`90`
  elapsed days, chosen as a conservative default suitable for monthly
  macro observations, mirroring `MarketContextBuilder`'s `MACRO_STALE_AFTER`
  rationale) is reported on every series entry; a series under this
  threshold is not thereby claimed to be economically current, only "not
  yet flagged stale by this fixed clock." A future-dated observation is
  flagged (`future_date_detected`, which also forces `stale: true`) only
  beyond a small, fixed, documented one-day tolerance
  (`FUTURE_DATE_TOLERANCE_DAYS`) -- absorbing ordinary date/timezone
  rounding around a calendar-only `observation_date` without hiding a
  genuinely implausible future-dated observation -- and the implausible
  date itself is always preserved exactly, never discarded or rewritten.
  FRED's own `"."` missing-observation marker is preserved as
  `latest_value: null`, `latest_is_missing: true`, distinct from
  `has_stored_observation: false` (no row at all).

  Every entry's stable `evidence_id` (prefixed `macro_`, a truncated
  SHA-256 hash) is derived only from the chosen row's full stored identity
  -- provider, series ID, observation date, and realtime window --
  **never from its value**, so re-selecting the same stored vintage always
  produces the same ID and a genuinely different vintage always produces a
  different one. A missing database file, a missing `macro_observations`
  table, or a requested series with no stored observation all produce a
  valid, non-crashing snapshot (`has_stored_observation: false`,
  `freshness.missing`/`freshness.stale` both `true`), mirroring
  `MarketContextBuilder`'s/`NewsEvidenceBuilder`'s established behavior.
  The database connection is always closed on every code path, and a
  close() failure never masks an earlier, already-sanitized read failure.
  Aggregate `flags.missing_series`/`flags.stale_series`/
  `flags.future_dated_series` summarize the per-series flags across the
  whole requested set. See
  [docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md) for
  the full field contract and known limitations.

  Covered by 40 tests (temporary DuckDB databases only; no live network
  access; no access to the real repository database) covering: input
  validation before any DuckDB access (non-sequence, boolean, empty,
  excessive count, malformed ID, boolean entry, duplicate-after-
  normalization), missing database/table/series, deterministic requested
  ordering, partial coverage across a mixed requested set, latest-
  observation/latest-vintage selection (including that vintages are never
  combined), missing-value preservation, staleness at/beyond the 90-day
  threshold, future-date detection at/beyond the one-day tolerance,
  evidence-ID stability and identity-not-value derivation, aggregate flag
  correctness, coverage/missing-observation counts, Decimal/date
  serialization, connection closure/error masking, sanitized CLI failures
  (including a repeatable `--series` argument and rejected duplicates), and
  proof of no network/model imports. Full test suite: 1607 passed (up from
  1567). `ruff check .` and `git diff --check` both pass. This has **not**
  been run against the real local database as part of this change (a
  read-only operation with no separate authorization sought as part of
  this task). No migration, dependency, agent, prompt, OpenAI/FRED call,
  scheduling, persistence, direction, prediction, or trading functionality
  was added.

- **FRED series-metadata pipeline added (2026-08-24, code/tests/docs only;
  no live FRED request, no write to the real database, migration `0008`
  not applied to the real database).** As distinct from the existing
  series-*observations* pipeline (`get_observations()`/
  `macro_observations`/`MacroObservationRepository`, see above), this adds
  a narrow pipeline for FRED series-level *metadata* -- title, units,
  frequency, seasonal adjustment, popularity, notes, observation date
  range, and last-updated timestamp -- so a future Macro Analyst never
  interprets an unlabeled number.

  `FredMacroDataClient.get_series_metadata()`
  (`market_intelligence/data_connectors/fred_macro_data.py`) fetches one
  series' metadata via FRED's official series endpoint
  (`https://api.stlouisfed.org/fred/series`), reusing the same
  `normalize_series_id` validation as every other connector method. It
  validates before any HTTP request is constructed, requests exactly one
  series per call, uses an explicit timeout, reads the API key only from
  `Settings`, and never exposes the request URL, query parameters, the API
  key, the raw response body, or provider-reported free text (`title`,
  `notes`) in any exception or sanitized status output. The response must
  contain exactly one matching series (matched against the requested,
  normalized series ID); every required string field
  (`title`/`frequency`/`frequency_short`/`units`/`units_short`/
  `seasonal_adjustment`/`seasonal_adjustment_short`) must be nonblank;
  `observation_start`/`observation_end` must be strict `YYYY-MM-DD` dates;
  `last_updated` must be a strict, timezone-aware timestamp in FRED's
  documented `"YYYY-MM-DD HH:MM:SS±HH[:MM]"` shape and is normalized to
  UTC; `popularity` must be a plain nonnegative integer (booleans
  explicitly rejected); and `notes` is preserved exactly as FRED reported
  it (or `None` when FRED reports none) -- provider text is never
  interpreted or summarized. Any malformed or mismatched payload fails the
  whole request; no partial `FredSeriesMetadata` object is ever returned.

  Migration `0008`
  (`market_intelligence/storage/migrations/0008_create_macro_series_metadata.sql`)
  defines `macro_series_metadata` with primary key `(provider, series_id)`
  and the exact normalized fields above, plus `first_ingested_at`,
  `last_seen_at`, and `ingestion_run_id`. Unlike `macro_observations`,
  series metadata has no revision/vintage window to preserve as part of
  its identity -- FRED's series endpoint always reports current metadata --
  so `MacroSeriesMetadataRepository`
  (`market_intelligence/storage/macro_series_metadata_repository.py`)
  always refreshes every mutable metadata/provenance column in place on a
  repeat ingestion of an already-known series, rather than treating it as
  a conflict. It requires `provider` to be exactly `"fred"`, validates the
  entire item before opening any connection or creating an ingestion run,
  writes the metadata plus the final `succeeded` `ingestion_runs` status
  update inside one atomic transaction, and mirrors the existing
  repositories' sanitized-failure/rollback behavior (a
  `MacroSeriesMetadataStorageValidationError` for invalid input with zero
  writes; a `failed` `ingestion_runs` row with a sanitized
  `error_category` for a storage failure; a sanitized
  `MacroSeriesMetadataStorageError`, never a raw exception, path, SQL, or
  credential, if rollback or failure-recording itself fails).

  A one-shot script, `scripts/ingest_fred_series_metadata.py --series-id
  FEDFUNDS`, makes at most one bounded, read-only request and stores the
  result; it prints only `configured`, fetch outcome, series ID, and a
  storage outcome/status (inserted vs. updated, ingestion-run status) --
  never the title, units, frequency, seasonal adjustment, notes,
  popularity, last-updated timestamp, database rows, or credentials. **It
  was not run live as part of this change.**

  `MacroEvidenceBuilder`
  (`market_intelligence/market_features/macro_evidence.py`) was updated to
  read this table read-only, alongside `macro_observations`: each series
  entry now also reports `metadata_available` plus (when available)
  `title`, `frequency`, `units`, and `seasonal_adjustment`, read exactly as
  stored -- never inferred from the series ID itself -- and the snapshot
  adds an aggregate `flags.missing_metadata_series` list. Existing
  observation-derived fields (`latest_value`, etc.) are unchanged. A
  missing `macro_series_metadata` table or a series with no stored
  metadata is valid, non-error input, mirroring the builder's existing
  behavior for missing observations; metadata and observations are read
  and reported independently, so either can be present without the other.

  Covered by focused tests reusing existing helpers/patterns (not a full
  matrix): the connector's success/normalization path, exactly-one-series
  matching, per-field validation failures (blank required strings,
  malformed dates, malformed/naive `last_updated`, invalid `popularity`,
  invalid `notes` type), and sanitized-error/no-credential-leak behavior
  (`market_intelligence/tests/test_fred_macro_data.py`); the repository's
  insert/refresh-on-repeat behavior, provider enforcement, field
  validation, atomic-write/rollback/failure-status behavior, and
  no-leakage checks
  (`market_intelligence/tests/test_macro_series_metadata_repository.py`);
  the script's invalid-input/not-configured/success/failure paths and
  sanitized output
  (`market_intelligence/tests/test_ingest_fred_series_metadata.py`); the
  updated `MacroEvidenceBuilder` behavior (metadata available/unavailable,
  independent of observation presence, missing table, partial coverage
  across multiple series, aggregate flag, no inference from series ID,
  values unchanged) added to
  `market_intelligence/tests/test_macro_evidence.py`; and updated
  migration/health-check tests in
  `market_intelligence/tests/test_database.py` (migration `0008` schema,
  primary key, `NULL`-notes support, and a `0007`→`0008` upgrade test
  preserving existing rows). Full test suite passes; `ruff check .` and
  `git diff --check` both pass. **Migrations `0001`–`0007` are unchanged,
  migration `0008` has not been applied to the real database (which
  remains at schema version `0007`), and no live FRED request or real
  database write was made as part of this change.** No agent, prediction,
  regime label, change/delta calculation, trade recommendation, or
  options/execution logic was added.

## Completed Work Log

This section is a historical, append-only record of work already done. It is
not a forward plan — see "Next Planned Work" below for that. Every numbered
entry describes something that has already been built, ingested, or attempted;
"done" and supersession notes throughout reflect that.

1. Data connector design — read-only Alpaca market-data, Alpaca news,
   Alpaca historical bars, and FRED connectors now exist (see above).
   Verified schema/provenance details belong in `DATA_CATALOG.md` once
   bulk data is actually pulled and inspected, not just a connectivity
   check.
2. Provider configuration — Alpaca and FRED credential handling (via
   `.env`, never committed) is in place.
3. First connection tests — done for Alpaca market data (read-only
   snapshot connectivity check) and FRED (read-only latest-observation
   connectivity check), recorded in `DATA_CATALOG.md`/`PROJECT_STATE.md`.
   Done live for Alpaca news (one authorized SPY-news request, see above).
   Alpaca historical bars was checked twice with separate authorization:
   the first implicit-SIP check failed with a sanitized 4xx, and the second
   explicit-IEX check succeeded with a sanitized 2xx. Connectivity and
   response normalization are verified on IEX only. No bars from either of
   those two connectivity checks were stored. A separate, later authorized
   2026-08-21 ingestion (see item 7 below) subsequently stored 248 bars;
   that is one controlled ingestion run, not a complete or validated bars
   dataset.
4. Database initialization — done (see above): the local DuckDB storage
   foundation is initialized under `data/`, now at schema version `0005`
   (`market_bars` applied) after the authorized real-database migration
   run described above; the database was backed up before that migration
   was applied.
5. News storage — done (see above): `news_articles` schema, storage
   service, and manual ingestion script exist, and one authorized live
   ingestion has succeeded (10 received, 10 inserted, 0 failed). This
   confirms the storage pipeline for one run; a validated, cataloged news
   dataset (per `DATA_CATALOG.md`'s Required Fields) is still separate,
   future work.
6. Historical bars connector — done (see above): a read-only,
   single-symbol historical-bars connector exists and is unit-tested
   (`AlpacaBarsClient`) and does not store bars in DuckDB. Its first
   authorized live check reached Alpaca but failed (sanitized 4xx) under
   the connector's prior default (implicit SIP) request — preserved above
   as an honest diagnostic record; the connector was then hardened to
   explicitly request the IEX feed on every request, and a second
   authorized live check against the hardened connector has now succeeded
   (sanitized 2xx, SPY, `5Min`, `feed=iex`, 5 bars). This verifies live
   connectivity and response normalization on IEX only — not SIP. The
   connector was also hardened to explicitly fix `adjustment=raw`/
   `currency=USD` (see above); this hardening has since been exercised by
   a live ingestion run (see item 7 below), in addition to unit tests.
7. Market-bar storage — done (see above): `market_bars` schema (migration
   `0005`), `BarRepository`, and `scripts/ingest_alpaca_bars.py` exist,
   are covered by tests using temporary databases and mocked bars, and
   migration `0005` has now been applied to the real database. A first
   authorized live ingestion has succeeded (SPY, `5Min`, `iex`, `raw`,
   `USD`: 248 received, 248 inserted, 0 failed; 248 rows verified stored,
   covering 2026-08-17T12:25:00Z through 2026-08-19T20:00:00Z). This
   confirms one controlled ingestion run and transactional storage; it is
   not a complete, gap-free, or research-validated historical bars
   dataset. Remaining future work: a validated, cataloged historical bars
   dataset per `DATA_CATALOG.md`'s Required Fields (broader coverage,
   direct inspection of stored data, gap/quality analysis).
8. Reviewed data contracts for actual provider data — versioned migrations
   and storage/repositories exist for both market bars (see above) and
   macro observations (migration `0006`, `MacroObservationRepository`, see
   the "Historical FRED observations" bullet above). Migration `0006` has
   been applied to the real database and a first authorized live FRED
   observations ingestion has succeeded (12 FEDFUNDS observations, see
   above). Remaining future work: a validated, cataloged macro-observations
   dataset per `DATA_CATALOG.md`'s Required Fields (broader series/date
   coverage, direct inspection of stored data, gap/quality analysis).
9. Macro-analyst agent groundwork — the FRED historical-observations
   connector and storage pipeline (see above) now include one authorized
   live ingestion run, in addition to existing infrastructure. No AI agent,
   prediction, sentiment analysis, options logic, or trading execution has
   been built on top of it, and none is planned as part of this
   infrastructure change.
10. Ingestion-orchestration layer — done, including a first authorized live
    run (see above): job contracts, committed configuration, a
    dry-run-first CLI (`scripts/run_ingestion_pipeline.py`), job adapters,
    a fail-closed run lock, and a persistent audit trail (migration
    `0007`) all exist and remain tested against temporary databases and
    mocked HTTP transports. Migration `0007` has now been applied to the
    real database, and one authorized `--all --execute` run has succeeded
    across all three reviewed jobs (see above). Remaining future work: any
    decision on scheduling, unattended/repeated operation, or additional
    reviewed job types remains separate, future, and not yet authorized;
    this orchestration layer is still groundwork for future specialized
    agents, not an agent itself, and this one run is not evidence of
    unattended reliability.
11. Market-context snapshot layer — done, code/tests only (see above):
    `MarketContextBuilder`
    (`market_intelligence/market_features/market_context.py`) and
    `scripts/build_market_context.py` exist, are read-only end to end, and
    are covered by tests against temporary DuckDB databases only. Not yet
    exercised against the real database as part of this change (a
    read-only operation, so nothing to authorize or roll back). Remaining
    future work: any decision to have an actual AI agent consume this
    snapshot, add derived features beyond this bounded set, or persist
    snapshots remains separate, future, and not yet authorized.
12. Market-context snapshot fixes (2026-08-23, code/tests/docs only, not run
    against the real database as part of this change). Two targeted fixes
    were made to the market-context snapshot layer (item 11):
    - **Weekend false-staleness fixed:** `BARS_STALE_AFTER` was changed from
      a 24-hour to a 72-hour elapsed-time threshold, so a Friday-afternoon
      bar is no longer falsely reported `bars_stale: true` over a normal
      weekend. This remains a plain elapsed-time threshold, not an
      exchange-calendar or holiday-aware one — it is deliberately
      weekend-tolerant, not weekend-*aware*. The actual latest stored bar
      timestamp remains exposed (`price.latest_bar_timestamp_utc`,
      `coverage.bars.latest_bar_timestamp_utc`) so a future, stricter,
      calendar-aware consumer can still make its own decision from the raw
      timestamp. Focused tests prove a Friday bar is not stale on Saturday
      and that a bar older than 72 hours is still correctly flagged stale.
    - **Session provenance added:** `bars_provenance` now includes a fixed,
      deterministic `session_scope: "provider_returned_unfiltered"` field.
      Stored bars are not restricted to regular trading hours (no RTH
      filter is applied anywhere in this project's ingestion or this
      snapshot layer) and may include pre-market/after-hours observations,
      so the latest stored bar — and `price.latest_close` — is never
      silently implied to be an official regular-session market close.
    No dependency, market-calendar library, migration, network call,
    persistence, prediction, or agent logic was added as part of this
    change. See [docs/MARKET_CONTEXT_SNAPSHOT.md](docs/MARKET_CONTEXT_SNAPSHOT.md).

13. **Session-quality feature layer added (2026-08-23, code/tests/docs
    only; not run against the real database as part of this change).** A
    new module, `market_intelligence/market_features/session_quality.py`
    (`SessionQualityBuilder`), and a companion CLI,
    `scripts/build_session_quality.py`, add a strictly validated,
    deterministic, read-only report on whether stored `5Min` bars for one
    symbol/session date represent a complete regular trading session --
    intended so a future agent can check data quality before analyzing
    stored price evidence. It makes no network request of any kind, opens
    the database only via `duckdb.connect(path, read_only=True)`, never
    writes a row or applies a migration, and adds no prediction,
    recommendation, or trading-signal logic of any kind.

    Regular session is defined using Python `zoneinfo`
    (`America/New_York`) as weekdays (Monday-Friday) with 78 five-minute
    slots from `09:30` through the slot beginning `15:55` inclusive. **This
    is explicitly weekday/time-window logic only** -- it is not an
    exchange-holiday or early-close calendar, and a stored date that
    happens to be a U.S. market holiday is evaluated with the same rule and
    reported incomplete, never flagged as "no session expected." Symbol and
    an optional explicit session date are strictly validated before any
    DuckDB connection is opened. Exactly one bar identity
    (`provider, symbol, timeframe='5Min', feed, adjustment, currency`) is
    selected per report (whichever identity produced the most recently
    stored `5Min` bar for the symbol) and never mixed with any other
    identity. If no session date is supplied, the most recent stored date
    (under that identity) containing at least one regular-session bar is
    used automatically.

    The report includes: bar provenance; session-definition metadata
    (including the weekday/time-window-only limitation, in the output
    itself); the fixed expected slot count (`78`); the observed
    regular-session slot count; missing expected UTC timestamps; any
    unexpected/off-grid or duplicate timestamps (if detectable -- true
    duplicates are already prevented by `market_bars`'s primary key);
    `complete`/`partial_session`/`missing_data` flags; first/last
    regular-session timestamps; regular-session open (only from an actual
    `09:30` bar) and latest close (explicitly flagged
    `latest_close_is_full_session_close: false` whenever the session is not
    complete, so a partial session's latest observed close is never
    described as an official close); session return (only when both open
    and latest close are available); session high/low/range/total volume
    and a volume-weighted VWAP (only when every observed regular-session
    bar has a stored `vwap`); and same-date premarket/after-hours stored
    bar counts. A missing database file, empty database, or symbol/date
    with no stored regular-session bars all produce a truthful,
    non-crashing report rather than an error. See
    [docs/SESSION_QUALITY.md](docs/SESSION_QUALITY.md) for the full field
    contract and known limitations. No dependency, market-calendar
    library, migration, network call, persistence, prediction, or agent
    logic was added as part of this change; it was not run against the
    real repository database (a read-only operation, so nothing to
    authorize or roll back).

14. **OpenAI structured-output provider boundary added (2026-08-23,
    code/tests/docs only; no live OpenAI request or connectivity check made
    as part of this change).** `OpenAIStructuredClient`
    (`market_intelligence/model_clients/openai_structured.py`) and two new
    non-secret `Settings` fields exist (see above and
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)),
    covered by 37 mocked tests using an injected fake SDK client (no real
    `openai.OpenAI` client is ever constructed in tests, no network call is
    ever made). As of 2026-08-23, remaining future work included any live
    connectivity check. **This has since been superseded: a first
    authorized live OpenAI connectivity check succeeded on 2026-08-24 (see
    Status above and
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)) —
    this confirms connectivity and response normalization only.** Remaining
    future work: any agent that actually calls this client with real
    developer instructions and evidence, and any decision to build
    forecast/recommendation logic on top of it, all remain separate,
    future, and not yet authorized.

15. **Market Evidence Agent added (2026-08-24, code/tests/docs only; no live
    database access or live OpenAI request made as part of this change).**
    `MarketEvidenceAgent` (`market_intelligence/agents/market_evidence_agent.py`)
    and `scripts/run_market_evidence_agent.py` exist (see Status above and
    [docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md)), covered
    by 47 tests against fake builder/model-client stand-ins (no real
    database or network access in tests). **This "not yet run live" status
    has since been superseded — see the "First authorized live Market
    Evidence Agent run" entry in Status above:** a dry run against the real
    database succeeded (eligible, 20 evidence items), and one authorized
    live `--execute` attempt failed structured-output validation
    (`OpenAIParseFailureError`); no analysis was accepted and no retry was
    made. That entry also records the resulting offline diagnosis and the
    sanitized failure-classification hardening (`category` on every
    `OpenAIStructuredError`/`MarketEvidenceAgentError`, a new
    `OpenAIRequestSchemaError`) and new regression tests added against the
    real `MarketEvidenceModelAnalysis` schema. **A successful live
    end-to-end completed run has since been achieved — see the "First
    completed live Market Evidence Agent run" Status entry above and
    [docs/MARKET_EVIDENCE_EVALUATIONS.md](docs/MARKET_EVIDENCE_EVALUATIONS.md)
    — that entry is one accepted example, not repeated or statistically
    characterized reliability.** Remaining future work: any orchestration
    integration, any repeated/broader evaluation of this agent's outputs,
    and any decision to build further agents (e.g. a Macro Analyst agent)
    on this same pattern all remain separate, future, and not yet
    authorized.

16. **News Evidence Snapshot layer** -- done, code/tests/docs only (see
    above): `NewsEvidenceBuilder`
    (`market_intelligence/market_features/news_evidence.py`) and
    `scripts/build_news_evidence.py` exist, are read-only end to end, and
    are covered by 41 tests against temporary DuckDB databases only. A
    read-only sanity check against the real database succeeded (see
    above). Remaining future work: any actual News Analyst agent that
    consumes this snapshot (mirroring `MarketEvidenceAgent`'s pattern), and
    any decision to expand this snapshot's scope, remain separate, future,
    and not yet authorized. **This has since been superseded -- see item 17
    below.**

17. **News Analyst added** -- done, code/tests/docs only (see above):
    `NewsAnalyst` (`market_intelligence/agents/news_analyst.py`) and
    `scripts/run_news_analyst.py` exist (see Status above and
    [docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)), covered by 75 tests
    against fake evidence-builder/model-client stand-ins (no real database
    or network access in tests). **This "not yet run live" status has since
    been superseded — see the "News Analyst live run sequence completed"
    Status entry above:** the SPY/`limit=5` live sequence completed (one
    `response_validation_failed` failure, one incomplete response, one local
    timeout, then one accepted `status="completed"` report), and a manual
    quality read of that accepted report's event claims motivated a
    market-relevance hardening change (required `relevance` classification/
    rationale per claim, new post-response relevance validation, a
    truthful/reason-free deterministic limitation for uncited articles, a
    safe code-controlled abstained outcome when no article is sufficiently
    relevant, and corrected `Settings` defaults of 4096 tokens/120s) — see
    the same Status entry.
    Remaining future work: any orchestration integration, any repeated/
    broader evaluation of this agent's outputs (mirroring
    `docs/MARKET_EVIDENCE_EVALUATIONS.md`), and any decision to build
    further agents on this same pattern all remain separate, future, and
    not yet authorized.

18. **Macro Evidence Snapshot layer** -- done, code/tests/docs only (see
    above): `MacroEvidenceBuilder`
    (`market_intelligence/market_features/macro_evidence.py`) and
    `scripts/build_macro_evidence.py` exist, are read-only end to end, and
    are covered by 40 tests against temporary DuckDB databases only. Not
    yet run against the real database as part of this change (a read-only
    operation, so nothing to authorize or roll back, but no separate
    authorization was sought as part of this task either). Remaining
    future work: any actual Macro Analyst agent that consumes this
    snapshot (mirroring `MarketEvidenceAgent`'s/`NewsAnalyst`'s pattern),
    any decision to add derived macro features (e.g. period-over-period
    change) beyond this bounded set, and any decision to expand this
    snapshot's scope all remain separate, future, and not yet authorized.

19. **FRED series-metadata pipeline** -- done, code/tests/docs only (see
    above): `FredMacroDataClient.get_series_metadata()`, migration `0008`
    (`macro_series_metadata`), `MacroSeriesMetadataRepository`,
    `scripts/ingest_fred_series_metadata.py`, and the corresponding
    `MacroEvidenceBuilder` update all exist and are covered by focused
    tests against temporary DuckDB databases and mocked HTTP transports
    only. Migration `0008` has not been applied to the real database
    (still at `0007`), and no live FRED request has been made. Remaining
    future work: separately authorized application of migration `0008` to
    the real database and a first live ingestion run; extending this
    pattern to additional series beyond FEDFUNDS; and any actual Macro
    Analyst agent that consumes the now-labeled evidence (mirroring
    `MarketEvidenceAgent`'s/`NewsAnalyst`'s pattern) all remain separate,
    future, and not yet authorized. **This last item has since been
    superseded -- see item 20 below.**

20. **Macro Analyst added (2026-08-24, code/tests/docs only; no live
    database access or live OpenAI request made as part of this change).**
    `MacroAnalyst` (`market_intelligence/agents/macro_analyst.py`) and
    `scripts/run_macro_analyst.py` exist (see the intro paragraph above and
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)), covered by 86 tests
    against fake evidence-builder/model-client stand-ins (no real database
    or network access in tests): `test_macro_analyst.py`,
    `test_run_macro_analyst.py`, `test_macro_analyst_eval_fixtures.py`. This
    is a single-turn, no-tools agent built directly on
    `MacroEvidenceBuilder` (item 18 above), explicitly **not** a regime
    classifier, predictor, directional market model, or trading agent. Its
    deterministic preflight gate is all-or-nothing across every requested
    series -- it makes zero OpenAI requests if any requested series is
    missing, lacks official stored metadata, is stale, is future-dated, has
    `latest_is_missing=True`, or has no stable evidence ID, or if the
    snapshot's echoed request does not match the normalized requested
    series list. When eligible, it sends only official stored metadata and
    the single latest stored observation per series (never a database path,
    SQL text, ingestion ID, credential, or raw audit/internal field) and
    makes exactly one OpenAI request. Every macro claim's `content_basis` is
    a fixed literal (`"stored_observation_and_official_metadata"`) the
    agent sets itself -- excluded from the model-facing schema entirely, so
    the model cannot set it to anything else. A dedicated post-response
    content-scope check (`MacroAnalystContentScopeError`, distinct from the
    shared `non_directional_output_policy` denylist the Market Evidence
    Agent and News Analyst also use) rejects trend/change/acceleration/
    deceleration/surprise/historical-extreme/correlation/causation/policy-
    change/market-regime language in every model-authored free-text field,
    since a single snapshot observation with no comparison value or
    consensus expectation can never support such a statement.
    `directional_assessment`/`trade_recommendation` are always the fixed
    `"not_performed"` value, exactly as for the other two agents; no
    sentiment, probability, confidence score, forecast, or options-detail
    field exists anywhere in its schema. **As of this entry, `MacroAnalyst`
    has not been run against the real local database, and no live OpenAI
    request has been made using it** -- both remain separate, future, and
    not yet authorized. It is not integrated into
    `market_intelligence/orchestration/`, adds no persistence or migration,
    and adds no dashboard, alerting, or brokerage/Robinhood integration.
    `python -m pytest` (1873 passed), `python -m ruff check .`, and
    `git diff --check` were all run and pass. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md) for full detail.
    **This "not yet run live" status has since been superseded -- see item
    21 below.**

21. **First live Macro Analyst run (2026-08-24), and a manual-review-driven
    hardening change that followed it.**

    A separately authorized live `--execute` run was made against the real
    local database and the real OpenAI API, requesting the default series
    (`FEDFUNDS`). The deterministic preflight passed and exactly one OpenAI
    request was sent; it completed successfully with `status="completed"`.
    Sanitized results: stored value `3.63` percent, observation date
    `2026-07-01`, frequency `monthly`, model `gpt-5-mini`, `input_tokens=
    1390`, `output_tokens=1356`, `total_tokens=2746`. As required by this
    agent's absolute, model-excluded fields, the report's
    `directional_assessment` and `trade_recommendation` were both
    `"not_performed"` -- no direction or recommendation of any kind was
    produced or could have been produced. Only this sanitized status is
    recorded here -- no raw provider response, response ID, or full
    evidence payload is reproduced.

    **A manual quality review of that one completed output found two
    issues, neither a schema, citation, or policy violation (both passed
    every check that existed at the time), but both genuine wording/scope
    weaknesses:**

    1. Phrasing along the lines of "at 3.63 percent on 2026-07-01" can
       misleadingly imply a live, point-in-time reading of a stored,
       already-published monthly observation. It should instead say
       something like "the stored monthly observation dated 2026-07-01".
    2. The report listed a transmission channel (inflation) among
       `transmission_channels` that `conditional_mechanism` did not
       actually explain -- the channel was named but never addressed.

    The agent **correctly avoided** any change/trend/comparison claim on
    this run, since at that time the evidence package supplied only the
    single latest stored observation with no comparison value -- exactly as
    designed and documented.

    **This one manual review of one completed output is not a validated
    evaluation methodology, and finding two issues in one output is not
    itself proof that only two issues exist or that any future response
    will be free of similar issues.** It motivated a bounded, deterministic
    hardening change (code/tests/docs only, made the same day, not yet
    exercised live as part of this change -- see below): `MacroEvidenceBuilder`
    (`market_intelligence/market_features/macro_evidence.py`) now adds a
    bounded, deterministic, read-only `recent_observations` excerpt (default
    6, bounded 2-24 observations, newest first, one deterministically
    chosen vintage per date, validated before any DuckDB access) and one
    precisely supported `latest_change_from_previous` comparison (an exact
    `Decimal` absolute difference and `"increased"`/`"decreased"`/
    `"unchanged"` direction between the latest and immediately preceding
    stored observation, available only when both are non-missing and both
    are the series' currently valid, non-superseded vintage -- never a
    percentage, annualized, or basis-point change, and two observations are
    never called a trend). It also now exposes each series' official
    `frequency_short` code. `MacroAnalyst`
    (`market_intelligence/agents/macro_analyst.py`) was correspondingly
    hardened: every claim must now use frequency-aware wording ("the stored
    `<frequency>` observation dated ...", using that series' own official
    frequency -- never assumed "monthly" -- and never point-in-time "at ...
    on `<date>`" phrasing), validated by a new
    `_validate_frequency_wording`/`MacroAnalystFrequencyWordingError`
    check; a series whose `frequency_short` is not one of a small,
    recognized set now fails preflight instead
    (`series_frequency_unrecognized`) rather than let the model guess.
    Increase/decrease/unchanged language is now conditionally permitted,
    but only for a fully validated two-observation comparison claim citing
    exactly the supplied latest/previous evidence IDs, stating both exact
    dates and both exact values, and stating a direction matching the
    supplied evidence exactly -- checked by a new
    `_validate_comparison_claims`/`MacroAnalystComparisonError`; change
    words anywhere else (a single-evidence claim, `conditional_mechanism`,
    or a `limitation`) are still always rejected, and every previously
    forbidden category (acceleration/deceleration, surprise, historical
    extreme, trend, correlation, causation, policy change, market regime)
    remains always rejected. A new
    `_validate_transmission_channels`/`MacroAnalystTransmissionChannelError`
    check now requires every listed `transmission_channels` entry to be
    explicitly and verifiably addressed by `conditional_mechanism` (a
    deterministic, word-boundary token check per channel), directly
    addressing finding (2) above; `"other"` can never be verified this way
    and is always rejected if listed. Zero-claim insufficient abstention,
    citation validation, the shared non-directional output policy,
    code-controlled final fields (`directional_assessment`/
    `trade_recommendation` always `"not_performed"`, `content_basis` always
    the fixed literal), the one-OpenAI-request maximum, and the dry-run
    default were all preserved unchanged. `python -m pytest` (1927 passed),
    `python -m ruff check .`, and `git diff --check` were all run and pass.
    **As of this item, none of this hardening has been exercised against a
    live OpenAI response** -- it has been validated only by tests using a
    fake, hand-authored model client and a fake evidence builder; no live
    database access or live OpenAI request was made as part of this change.
    See [docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md)
    and [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md) for full detail.

22. **Second live Macro Analyst run failed on content-scope validation
    (2026-08-24, after the item 21 hardening merged as PR #29), and a
    scope-boundary fix that followed it.**

    A separately authorized live `--execute` run was made against the real
    local database and the real OpenAI API, requesting `FEDFUNDS` with
    `recent_observations_limit=6`. The deterministic preflight passed and
    exactly one OpenAI request was sent. The response was **not** accepted:
    it failed the post-response content-scope check
    (`MacroAnalystContentScopeError`, `category=content_scope_invalid`) at
    `field=limitations[0]`, sanitized as:

    ```json
    {"error": "agent_error", "detail": "Model-authored output described a
    trend, change, comparison, correlation, causation, policy change, or
    market regime not supported by a single-snapshot observation
    (field=limitations[0]). The rejected text is never included in this
    error.", "category": "content_scope_invalid"}
    ```

    **No report was accepted from this attempt, and no retry was made** --
    this agent has always made at most one OpenAI request per `run()` call,
    and a rejected response is never retried, truncated, or silently
    modified. Per this agent's own sanitization contract, the model-authored
    text that triggered the rejection is never recorded anywhere, including
    in this document, so **the exact live wording that tripped the check is
    not available and is not reproduced here or claimed to be known.**

    Offline root-cause analysis (no live/network/database access) found a
    plausible, locally reproducible false-positive class in the fixed
    content-scope denylist that existed at the time of this run: the
    denylist matched the bare presence of words like "trend", "regime",
    "correlation", or "causation" in any model-authored free-text field,
    including a `limitation` where those words are used to **negate** an
    unsupported claim -- e.g. "Six observations are insufficient to
    establish a trend," "No regime conclusion can be drawn from this
    bounded excerpt," or "The supplied evidence does not establish
    causation" are desirable, honest limitations, not prohibited claims,
    but the denylist rejected them identically to an affirmative unsupported
    claim. **This is offline analysis of a plausible failure class
    reproduced with locally authored test fixtures, not a claim to know the
    exact live-rejected wording, and not the only possible explanation for
    the observed rejection.**

    A bounded, deterministic fix (code/tests/docs only, not yet exercised
    live as part of this change) was then made to
    `market_intelligence/agents/macro_analyst.py`'s `_validate_content_scope`:

    - `claim_summary` and `conditional_mechanism` are **unchanged and still
      strictly denylisted with no exemption of any kind** -- any prohibited
      trend/change/comparison/correlation/causation/policy-change/regime
      language in either field is still rejected outright, exactly as
      before.
    - For model-supplied `limitations` **only**, a new, narrow, fail-closed
      allowance (`_limitation_content_scope_violation`) permits a
      prohibited-term match when it is immediately adjacent (within a small,
      bounded word gap, in the same clause) to one of a fixed set of
      negation/insufficiency cues: `no`/`not`, `cannot`/`can't`,
      `insufficient to`, `does not`/`do not`, `unavailable`,
      `limited evidence for`, and `cannot be inferred/established/
      determined/assessed`. Text is first split into clauses (on sentence
      terminators and on a comma before a coordinating conjunction) so a
      negated disclaimer clause never shields a separate, unnegated
      affirmative claim elsewhere in the same limitation (e.g.
      "insufficient data to draw conclusions, but the rate is clearly
      following an accelerating trend" is still rejected, for the second
      clause). Every prohibited match in a limitation must be individually
      negated; a single unnegated match anywhere still rejects the whole
      limitation.
    - The shared non-directional output policy check
      (`_enforce_output_policy`), the comparison-claim validator, the
      frequency-wording validator, the transmission-channel validator, all
      citation/series/quality-consistency checks, the one-OpenAI-request
      maximum, and the dry-run default were all preserved unchanged and
      still run on `limitations` exactly as before -- a limitation that
      passes the new negation allowance is still screened by every other
      existing check.
    - The content-scope error message's stale wording ("not supported by a
      single-snapshot observation") was corrected to "not supported by the
      bounded stored evidence," since the evidence package has, since item
      21, included a bounded `recent_observations` history excerpt, not
      only a single snapshot value. The error remains fully sanitized: it
      never includes the rejected text, only a fixed field name.

    14 new focused tests were added to
    `market_intelligence/tests/test_macro_analyst.py` covering: an accepted
    negated-trend limitation, an accepted no-regime limitation, an accepted
    no-causation/no-correlation limitation, an accepted insufficient-history
    limitation, an accepted limitation with multiple independently negated
    clauses, that an accepted negated limitation still makes exactly one
    model call, a parametrized sweep of affirmative (unnegated)
    trend/regime/causation limitations still rejected, a disclaimer-then-
    affirmative-claim limitation still rejected (with a check that no retry
    followed the rejection), that the rejection error never echoes the
    rejected text, that the identical negated wording is still rejected
    outright in `claim_summary` and in `conditional_mechanism` (no
    exemption), and that the shared non-directional output policy still
    fires on a limitation whose negated scope language separately passes
    the new content-scope allowance. `python -m pytest` (1941 passed),
    `python -m ruff check .`, and `git diff --check` were all run and pass.

    **This fix addresses a locally reproducible false-positive class in the
    content-scope denylist. It is not proof of the exact wording that was
    rejected in the live run above, and it does not weaken the comparison
    citation checks, frequency-wording validation, transmission-channel
    validation, any schema bound, the zero-retry behavior, or any other
    safety boundary** -- all of those were re-run unchanged and still pass.
    As of this item, this fix has **not** been exercised against a live
    OpenAI response; no live database access or live OpenAI request was made
    as part of this change.

23. **Core Macro Basket configuration and dry-run-first batch ingestion
    script added (2026-08-24, code/tests/docs only; not run live or against
    the real database as part of this change).** See
    [docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md) for full detail.

    A new committed configuration file,
    `market_intelligence/config/core_macro_series.json`, and its loader/
    validator, `market_intelligence/config/macro_basket.py`
    (`load_core_macro_series`), define a fixed, reviewed universe of exactly
    seven approved FRED series -- `FEDFUNDS` (`policy_rate`), `DGS10`
    (`long_term_rate`), `CPIAUCSL` (`inflation`), `PCEPI` (`inflation`),
    `UNRATE` (`labor`), `INDPRO` (`growth`), `GDPC1` (`growth`) -- each with
    a strictly validated `enabled`/`observation_lookback_days`/
    `recent_observations_limit` contract. Validation enforces: exact
    root/entry field sets (an unknown field anywhere is rejected); normalized,
    unique series IDs; only the seven approved series IDs; that each series'
    `category` exactly matches this module's own fixed, committed
    `APPROVED_SERIES_CATEGORY` mapping (never accepted as arbitrary
    configuration-file text); plain (non-boolean) integers for
    `observation_lookback_days`/`recent_observations_limit`, each within
    fixed bounds -- a conservative, per-series lookback ceiling
    (`FEDFUNDS`/`CPIAUCSL`/`PCEPI`/`UNRATE`/`INDPRO`: 400 days;
    `GDPC1`: 1,100 days; `DGS10`: 180 days) that exists specifically to
    prevent an unbounded historical request, and `2`-`24` for
    `recent_observations_limit` (mirroring `MacroEvidenceBuilder`'s own
    bounds, fixed locally to avoid a layering dependency onto
    `market_intelligence/market_features/`). Entries are always returned in
    the committed file's own deterministic order. Loading performs no
    network I/O and constructs no `Settings`, client, database connection,
    or lock. **No series title, unit, frequency, seasonal adjustment, note,
    or observation value is hardcoded anywhere in this configuration or its
    loader** -- those come only from the existing, reviewed FRED metadata
    endpoint and local storage, at ingestion time.

    A new dry-run-first CLI, `scripts/ingest_core_macro_basket.py`, reuses
    the existing `FredMacroDataClient`, `MacroSeriesMetadataRepository`,
    `MacroObservationRepository`, and the existing orchestration run lock
    (`market_intelligence/orchestration/lock.py`'s `RunLock`, at its usual
    fixed path) unmodified. `--series SERIES_ID` (repeatable) and `--all`
    are mutually exclusive and one is always required; selection is
    validated purely against the already-loaded committed configuration --
    before `Settings`, any client, the network, the database, or the run
    lock are ever constructed -- and selected series are always processed in
    the committed file's own deterministic order. Default behavior is a dry
    run: it resolves one shared, injected UTC "as-of" instant (called
    exactly once for the whole run), computes each selected series' bounded
    observation window (`start = as_of_date - observation_lookback_days`,
    `end = as_of_date`, via the FRED connector's own strict calendar-date
    normalization), and prints a sanitized plan with **zero `Settings`
    construction, zero network requests, and zero database activity of any
    kind**. `--execute` is required for real activity: it acquires the
    existing run lock, then requires the real local database to **already
    be healthy at exactly schema version `0008`** (checked read-only via the
    existing `DuckDBManager.check_health()`, before any network request --
    this script deliberately never applies a migration itself), then
    requires the FRED client to be configured, then processes each selected
    series **sequentially**: exactly one `get_series_metadata` request and
    one bounded `get_observations` request per series (an empty
    observations result is reported `skipped_empty`, mirroring
    `scripts/ingest_fred_observations.py`'s existing convention), with **no
    automatic retry of any request**. One series' failure is recorded
    truthfully but never prevents a later selected series from being
    attempted; the overall run status is only `succeeded` if every selected
    series' metadata request/storage succeeded and its observations
    request/storage either succeeded or was validly empty. This script does
    **not** write to `orchestration_runs`/`orchestration_job_runs` and does
    **not** modify `market_intelligence/orchestration/jobs.json` or any
    migration/schema -- it is a separate, narrower, manual batch tool, not
    scheduling. All output, in both modes, is limited to series IDs, the
    fixed category, configured bounds, computed request-window calendar
    dates, per-series/overall status, and sanitized counts -- never a
    series title, unit, frequency, seasonal adjustment, note, observation
    value, URL, query parameter, raw exception text, SQL, a database path,
    or a credential.

    44 new focused tests were added
    (`market_intelligence/tests/test_macro_basket_config.py`,
    `market_intelligence/tests/test_ingest_core_macro_basket.py`), covering:
    exact root/entry field validation; approved-series-ID and fixed-category
    enforcement; plain-integer/bounded-value enforcement for both numeric
    fields (including the per-series lookback ceiling); deterministic file
    ordering; invalid/duplicate/disabled/unknown series selection rejected
    before any `Settings`/network/database/lock activity; the shared clock
    called exactly once; correctly computed bounded windows for monthly-,
    quarterly-, and daily-lookback series; a dry run making zero
    `Settings`/network/database activity; execute-mode sequential order and
    per-series failure isolation (one series' provider failure does not
    prevent a later series from being attempted, with no automatic retry);
    an unhealthy or wrong-schema-version database failing before any network
    request; lock contention; an empty-observations result reported
    `skipped_empty` rather than failed; and sanitized output (no title,
    unit, value, note, credential, or internal ever leaked) across both
    success and failure paths. `python -m pytest` (1985 passed),
    `python -m ruff check .`, and `git diff --check` were all run and pass;
    the existing `scripts/ingest_fred_observations.py`/
    `scripts/ingest_fred_series_metadata.py` scripts, and every other
    existing test, were left unmodified and re-verified passing as part of
    the same full-suite run.

    **As of this item, this configuration and script exist in code, tests,
    and docs only.** No live FRED request has been made using this script,
    migration `0008` has not been (newly) applied as part of this change
    (the real database's migration state is unchanged by this item), and no
    row has been written to `macro_series_metadata`/`macro_observations` by
    this script. This does not establish a complete, gap-free, or
    research-validated macro dataset for any of the seven series, and it
    implies no forecast, regime classification, or trading signal of any
    kind.

24. **`DGS10` → `GS10` replacement in the Core Macro Basket (2026-08-24,
    code/tests/docs only; no live FRED request or real-database write as
    part of this change).** A separately authorized first live run of
    `scripts/ingest_core_macro_basket.py --all --execute` (following item 23
    above) succeeded for six of the seven approved series -- `FEDFUNDS`,
    `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1` -- but failed for
    `DGS10`'s **observations** request only, recorded with sanitized error
    category `provider_error`; `DGS10`'s **metadata** request succeeded.
    Only this sanitized category was recorded -- no raw exception text, URL,
    query parameter, or credential was ever printed or stored. **The exact
    provider-side cause of the `DGS10` observations failure is therefore not
    known, and this entry does not claim otherwise.** This diagnostic record
    is preserved here as an honest record and is not rewritten or deleted.

    In response, `DGS10` (daily, `long_term_rate`) was replaced everywhere
    in the committed Core Macro Basket contract by the official monthly
    FRED series `GS10` ("Market Yield on U.S. Treasury Securities at
    10-Year Constant Maturity, Quoted on an Investment Basis" -- monthly,
    percent, not seasonally adjusted), retained under the same
    `long_term_rate` category. `market_intelligence/config/macro_basket.py`'s
    `APPROVED_SERIES_CATEGORY` and `MAX_LOOKBACK_DAYS_BY_SERIES_ID` mappings,
    the committed `market_intelligence/config/core_macro_series.json` entry,
    `docs/CORE_MACRO_BASKET.md`, `DATA_CATALOG.md`, and the corresponding
    tests (`market_intelligence/tests/test_macro_basket_config.py`,
    `market_intelligence/tests/test_ingest_core_macro_basket.py`) were all
    updated accordingly. Because `GS10` is monthly rather than daily, it now
    uses the same existing conservative monthly `observation_lookback_days`
    policy (400 days, the ceiling already used by `FEDFUNDS`/`CPIAUCSL`/
    `PCEPI`/`UNRATE`/`INDPRO`) instead of `DGS10`'s former 180-day daily
    ceiling; `recent_observations_limit` remains unchanged at `6`. No
    connector, repository, migration, dependency, `.env`, or the real
    DuckDB database was modified as part of this change, and `--series`/
    `--all` selection, dry-run planning, execute-mode ordering/failure
    isolation, and sanitized output remain otherwise unchanged.

    **`GS10` is a proposed, bounded, monthly replacement only -- as of this
    item, it has not yet been requested live.** No live FRED request has
    been made for `GS10`, and no row for it has been written to
    `macro_series_metadata`/`macro_observations`. `python -m pytest` (1985
    passed), `python -m ruff check .` (all checks passed), and
    `git diff --check` (no whitespace errors) were all run as part of this
    change and pass. This implies no forecast, regime classification, or
    trading signal of any kind, and does not establish a complete,
    gap-free, or research-validated macro dataset for any of the seven
    series.

25. **First authorized live `GS10` ingestion and read-only verification
    (2026-08-25, documentation update only -- no code, test, migration,
    configuration, dependency, `.env`, or database change was made as part
    of recording this entry).** Following item 24 above, `GS10` was
    requested live for the first time via a separately authorized
    `scripts/ingest_core_macro_basket.py --execute` run targeting `GS10`.

    The run's reported outcome: mode `execute`, overall status
    `succeeded`, `series_id: GS10`, `metadata_status: succeeded`,
    `observation_status: succeeded`, 13 observations received, 13
    inserted, 0 existing/updated, 0 failed.

    A separate, subsequent read-only verification then reported: the real
    local database at schema version `0008` (8 migrations applied),
    `healthy=True`; 1 stored `macro_series_metadata` row for `GS10`; 13
    stored `macro_observations` rows for `GS10`, covering 2025-07-01
    through 2026-07-01, with 0 missing observations; and the latest
    `macro_series_metadata`/`macro_observations` ingestion runs for `GS10`
    both recorded `succeeded` with no `error_category`. The working tree
    was clean before this documentation update was made, and no live
    request of any kind (FRED, OpenAI, or otherwise) was made as part of
    producing this documentation entry itself.

    **Migration `0008` status superseded:** earlier entries in this
    document (see the "Macro series-metadata pipeline" item above and the
    Status section) correctly recorded that migration `0008` had not been
    applied and the real database remained at schema version `0007` as of
    2026-08-24. Since `scripts/ingest_core_macro_basket.py --execute`
    requires the real database to already be healthy at exactly schema
    version `0008` before making any network request (see
    [docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md)), and the
    `GS10` execute run above succeeded, migration `0008` must have been
    applied to the real database prior to that run; the read-only
    verification above independently confirms the database is now at
    schema version `0008`, healthy. Those earlier `0007`/"not applied"
    statements are preserved above as honest, correctly time-scoped
    diagnostic records and are not retracted -- they describe the state
    truthfully as of 2026-08-24, before this change.

    **This confirms one bounded, controlled live ingestion for one series
    (`GS10`) and its corresponding read-only storage verification. It does
    not establish a complete, gap-free, or research-validated macro
    dataset for `GS10` or for any other series in the Core Macro Basket**
    -- the other six series' live status is recorded separately above
    (items 23-24) and is unchanged by this entry. Stored coverage and a
    zero missing-observation count describe what is present in local
    storage; they do not establish economic-data correctness or
    predictive/analytical usefulness of any kind. **No forecasting,
    market-direction assessment, options recommendation, or trading
    execution was performed or implied as part of this run or this
    documentation update, and no live request of any kind was made while
    producing this documentation update itself.**

26. **Frequency-aware macro-evidence staleness policy (2026-08-25, code/tests/docs
    only -- no live FRED/OpenAI request, no migration, no configuration/dependency/
    `.env` change, and no write to the real local database as part of this
    change).** A previously reported read-only dry run of the Macro Analyst
    against the seven-series Core Macro Basket abstained with
    `series_stale` for `GDPC1` only, while its underlying stored data was, on
    inspection, a legitimate current quarterly release: `frequency_short: "Q"`,
    `observation_date: 2026-04-01`, value present and not missing,
    `realtime_end: 9999-12-31` (FRED's currently valid, non-superseded
    vintage). The prior fixed 90-day staleness rule in
    `MacroEvidenceBuilder` (`market_intelligence/market_features/macro_evidence.py`)
    applied the same threshold to every series regardless of its official
    reporting frequency, which is appropriate for a monthly series like
    `FEDFUNDS`/`GS10` but falsely classified this normal ~3-4-month
    quarterly inter-release gap as stale.

    In response, `MacroEvidenceBuilder`'s staleness check was made
    frequency-aware, narrowly: `frequency_short == "M"` keeps the existing
    90-day threshold (`STALE_AFTER_DAYS`); `frequency_short == "Q"` now uses
    a separate, conservative 180-day threshold
    (`STALE_AFTER_DAYS_QUARTERLY`), comfortably exceeding a quarterly
    series' reporting cadence including normal release lag. Every other
    case -- no stored series metadata at all, a stored metadata row whose
    `frequency_short` is `null`/blank, or any `frequency_short` value other
    than `"M"`/`"Q"` -- has **no defined threshold** and **fails closed**:
    `freshness.stale_after_days` is `null` and `freshness.stale` is always
    `true`, regardless of how recent the observation is; no default
    threshold is ever silently assigned. The applied threshold (or `null`)
    is now reported on every series' `freshness.stale_after_days` field, not
    only implied by a module-level constant. `freshness` continues to be
    computed only from the chosen row's already-stored `observation_date`
    (never from FRED's `last_updated` metadata field, which describes when a
    revision was recorded, not observation recency), and the existing
    missing-observation/future-date behavior (including the one-day
    future-date tolerance) is unchanged. Because the Macro Analyst's
    deterministic preflight (`market_intelligence/agents/macro_analyst.py`)
    already consumes `MacroEvidenceBuilder`'s `freshness.stale` field as-is
    and performs no staleness computation of its own, this fix required no
    change to the Macro Analyst's preflight logic itself -- it now simply
    consumes the corrected, frequency-aware result.

    New boundary tests were added to
    `market_intelligence/tests/test_macro_evidence.py`: a monthly series
    exactly at 90 and at 91 elapsed days; a quarterly series exactly at 180
    and at 181 elapsed days; a GDPC1-shaped reproduction (an April 1
    quarterly observation evaluated at an August 25 as-of date, 146 elapsed
    days, correctly not stale); a series with no stored metadata at all
    failing closed; and a series with stored metadata but an unrecognized
    `frequency_short` (e.g. `"W"`) also failing closed. Two pre-existing
    generic freshness tests that stored no series metadata were updated to
    store monthly (`"M"`) metadata, since a missing `frequency_short` now
    fails closed rather than implicitly defaulting to the monthly
    threshold. `python -m pytest` (1990 passed), `python -m ruff check .`
    (all checks passed), and `git diff --check` (no whitespace errors) were
    all run as part of this change and pass.

    **As of this item, this is a narrow, deterministic threshold fix only.**
    It does not add FRED-release-calendar awareness for any frequency, does
    not add a threshold for any `frequency_short` other than `"M"`/`"Q"`
    (a series reported at any other official frequency, e.g. weekly or
    annual, was already unsupported by this project's macro basket and
    continues to fail closed rather than receive a guessed threshold), does
    not change the migration, connector, repository, or macro basket
    configuration, and does not run any live provider or model request. It
    does not by itself confirm that `GDPC1` (or any other quarterly series)
    now passes the Macro Analyst's live preflight against the real
    database -- that would require a separately authorized live dry run,
    not yet performed as part of this change.

27. **Seven-series dry-run confirmation and Macro Analyst full-basket
    coverage fix (2026-08-25, code/tests/docs only -- no live FRED/OpenAI
    request, no migration, no configuration/dependency/`.env` change, and no
    write to the real local database as part of this change).**

    Following item 26 above, a read-only dry run of the Macro Analyst
    (`scripts/run_macro_analyst.py`, no `--execute`) was run against the real
    local database, requesting the complete, approved seven-series Core
    Macro Basket: `FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`,
    `INDPRO`, `GDPC1`. It succeeded: `eligible: true`, all seven series
    echoed back in `series_ids`, `series_count: 7`, and every flag list
    (`missing_series`, `stale_series`, `future_dated_series`,
    `missing_metadata_series`, `latest_missing_series`,
    `no_evidence_id_series`, `frequency_unrecognized_series`) empty. Dry-run
    mode makes **zero** OpenAI requests -- this confirms only that the
    deterministic, all-or-nothing preflight gate now passes for the full
    seven-series basket against the real database; it is not a claim that
    any full-basket model response has ever been requested or evaluated.

    This dry-run pass exposed a contract mismatch that had not previously
    been reachable: `MacroAnalyst` already accepted up to ten requested
    series (`macro_evidence.MAX_SERIES_IDS`), but its model-facing
    schema (`MacroAnalystModelAnalysis.macro_claims`) was hard-bounded by a
    **separate**, independent literal (`MAX_MACRO_CLAIMS = 6`) -- so a fully
    eligible seven-series request could never have structurally retained one
    claim per series in a `"sufficient"`/`"limited"` response. Since every
    `MacroClaim` is bound to exactly one `series_id` (unchanged by this
    fix -- the schema was not redesigned into grouped or cross-series
    claims), covering seven series always requires seven claims, one over
    the old bound.

    The fix, made entirely in
    `market_intelligence/agents/macro_analyst.py` and its tests/docs:

    - `MAX_MACRO_CLAIMS` is now fixed directly to the module-level
      `macro_evidence.MAX_SERIES_IDS` (imported, not duplicated) --
      the two bounds can no longer silently drift apart and reintroduce this
      same gap for a future, larger requested series list (up to the
      existing 10-series cap).
    - A new deterministic, code-controlled check,
      `_validate_coverage`/`MacroAnalystCoverageError`
      (`category = "coverage_invalid"`), now runs on every model response
      before `MacroAnalystReport` is built: whenever `evidence_quality` is
      `"sufficient"` or `"limited"`, every requested `series_id` must appear
      in **exactly one** retained macro claim -- no omission, no series
      claimed twice, no unrequested series. The existing `"insufficient"`/
      zero-claims code-controlled abstention outcome is explicitly exempted
      (checked first, unchanged, by the pre-existing
      `_validate_claims_quality_consistency` biconditional) -- it remains
      the only way to omit series coverage, and it still never fabricates a
      placeholder claim.
    - `AGENT_INSTRUCTIONS` was updated to state this as a mandatory
      requirement -- every requested series must get exactly one claim
      whenever the response is not `"insufficient"` -- replacing the
      previous "prefer 1 to 4 macro claims" advisory range, which is no
      longer accurate now that coverage is mandatory rather than a
      preference (a fixed numeric "preferred" claim count made no sense
      once the correct count is exactly the number of requested series,
      which varies per call).
    - Every existing hard schema bound (other than the now evidence-layer-
      tied `MAX_MACRO_CLAIMS`), citation/series validation, content-scope
      validation, the comparison-claim validator, the frequency-wording
      validator, the transmission-channel validator, the shared
      non-directional output policy, the `directional_assessment`/
      `trade_recommendation` always-`"not_performed"` guarantee, the fixed
      `content_basis` literal, the one-OpenAI-request maximum with no
      automatic retry, and the dry-run default were all preserved unchanged
      and re-verified passing.

    15 new focused tests were added to
    `market_intelligence/tests/test_macro_analyst.py` (141 tests total
    across `test_macro_analyst.py`/`test_run_macro_analyst.py`/
    `test_macro_analyst_eval_fixtures.py`, up from 126), covering: a full
    seven-series basket response accepted with exactly one claim per series
    (and no duplicate series); the identical response accepted with claims
    reordered; a response omitting one requested series rejected; a response
    claiming one series twice (while omitting another) rejected; an extra
    claim for an unrequested series rejected (via the pre-existing series
    check, which runs first); full coverage required and accepted for
    **both** `"sufficient"` and `"limited"` evidence quality, and partial
    coverage rejected for **both** (proving `"limited"` never licenses
    omitting a series); the existing zero-claim `"insufficient"` abstention
    re-verified unaffected for a full seven-series request; a coverage
    violation making zero retry model calls; that the coverage error never
    echoes model-authored claim text; that the coverage error carries the
    `coverage_invalid` category; that `MAX_MACRO_CLAIMS` equals
    `macro_evidence.MAX_SERIES_IDS`; and a dedicated test that builds
    the **real** production `MacroAnalystModelAnalysis`/`MacroClaimDraft`
    schema (not the fake stand-ins used elsewhere in that test file) with a
    valid seven-series response and round-trips it through the real
    `OpenAIStructuredClient` (only its SDK transport faked, never a live
    network call), proving the schema and client stay compatible for a
    full-basket response. `python -m pytest` (2005 passed),
    `python -m ruff check .` (all checks passed), and `git diff --check` (no
    whitespace errors) were all run as part of this change and pass. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md) for full detail.

    **As of this item, this fix has not been exercised against a live
    OpenAI response for the seven-series basket or any other multi-series
    request -- no paid full-basket (or any other) Macro Analyst `--execute`
    request has been made as part of this change.** It has been validated
    only by focused offline tests (fake evidence-builder/model-client
    stand-ins, plus the one real-schema round-trip test described above,
    which still never makes a network call) and by the one read-only,
    zero-OpenAI-request dry run described above. This does not establish
    that a real model response will correctly cover every requested series,
    that any described observation is factually accurate, or that the
    seven-series basket constitutes a complete, gap-free, or
    research-validated macro dataset -- none of that is claimed here. No
    connector, repository, migration, dependency, `.env`, or the real
    DuckDB database was modified as part of this change.

28. **Failed seven-series Macro Analyst `--execute` attempt, and the
    model-facing evidence compaction fix (2026-08-25, code/tests/docs only --
    no live FRED/OpenAI request made as part of this fix itself, no
    migration, no configuration/dependency/`.env` change, and no write to
    the real local database).**

    Following item 27 above, the first authorized live `--execute` attempt
    against the complete seven-series Core Macro Basket (`FEDFUNDS`, `GS10`,
    `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`,
    `recent_observations_limit=6`) passed the deterministic, all-or-nothing
    preflight gate but then failed locally with:

    ```json
    {"error": "agent_error",
     "detail": "Invalid evidence: exceeds the maximum node count.",
     "category": "request_invalid"}
    ```

    This `request_invalid` error is raised entirely by
    `OpenAIStructuredClient`'s local evidence-shape validation
    (`_validate_evidence_shape`, before any SDK client is built or any
    request is sent -- see
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)).
    **This was a zero-provider-request, zero-token failure: no OpenAI
    request was sent, no tokens were spent, and no analysis was accepted.**

    Offline diagnosis found the root cause: the model-facing evidence
    payload built by `MacroAnalyst._build_model_evidence` included every
    `recent_observations` row for every requested series (as well as
    `latest_change_from_previous` and the single-latest-observation fields),
    even though no post-response validator ever reads `recent_observations`
    -- every claim this agent can validate is scoped to either the single
    latest stored observation or one exact two-observation comparison, both
    already fully covered without it. Reconstructing the exact pre-fix
    seven-series, `recent_observations_limit=6` payload offline and running
    it through the same local validation reproduced the identical failure at
    501 nodes -- one over `OpenAIStructuredClient`'s existing, unchanged
    500-node `MAX_EVIDENCE_NODES` cap.

    The fix, made entirely in
    `market_intelligence/agents/macro_analyst.py` (`_build_model_evidence`)
    and its tests/docs: the model-facing evidence payload no longer includes
    `recent_observations` at all. `MacroEvidenceBuilder`'s own snapshot --
    used for local deterministic evidence and audits -- is unchanged and
    still retains up to `recent_observations_limit` recent rows per series;
    only the subset actually sent to OpenAI was narrowed.
    `AGENT_INSTRUCTIONS` was updated to stop telling the model it would
    receive a `recent_observations` excerpt. No global
    `OpenAIStructuredClient` limit (`MAX_EVIDENCE_NODES`/
    `MAX_EVIDENCE_DEPTH`/`MAX_EVIDENCE_BYTES`) was raised, and no comparison,
    citation, series, full-basket-coverage, frequency-wording,
    transmission-channel, content-scope, or non-directional-policy
    validation was weakened -- confirmed by the full existing test suite
    re-passing unchanged.

    Measured evidence-package sizes after the fix (via
    `OpenAIStructuredClient`'s own node-counting/serialization logic, as
    asserted in new tests):

    - **Seven-series Core Macro Basket** (the exact request shape that
      failed live): **200 nodes / 5,643 serialized bytes** -- comfortably
      under `MAX_EVIDENCE_NODES` (500) and `MAX_EVIDENCE_BYTES` (32,000).
    - **Ten-series worst case** (`MAX_SERIES_IDS`, the largest basket this
      agent ever accepts, built with conservative, deliberately oversized
      placeholder title/frequency/units/seasonal-adjustment strings, each
      longer than any real Core Macro Basket series' actual metadata):
      **284 nodes / 11,495 serialized bytes** -- still comfortably under
      both limits. (FRED-metadata `VARCHAR` columns carry no hard database
      length bound, so this is a conservative, practical worst case, not a
      proven upper bound on FRED's actual metadata lengths.)

    Since the model-facing payload no longer includes `recent_observations`
    at all, evidence-package size is now independent of
    `recent_observations_limit` entirely -- a caller may still request any
    value in `MacroEvidenceBuilder`'s existing `[2, 24]` bound without it
    affecting the size of what is sent to OpenAI.

    New tests were added to `market_intelligence/tests/test_macro_analyst.py`
    (147 tests total across `test_macro_analyst.py`/
    `test_run_macro_analyst.py`/`test_macro_analyst_eval_fixtures.py`, up
    from 141), covering: the model-facing payload containing no
    `recent_observations` field for any series, for a full seven-series
    request; a dedicated test asserting the exact preserved field set for a
    series entry (proving every field required by existing citation,
    frequency-wording, and comparison-claim validation survives the
    compaction); the real, production seven-series Core Macro Basket
    evidence package passing the real `OpenAIStructuredClient`'s node/byte
    evidence-size validation end to end (only its SDK transport faked),
    with the measured node/byte counts above asserted directly against the
    client's own `MAX_EVIDENCE_NODES`/`MAX_EVIDENCE_BYTES`; the synthetic
    worst-case ten-series request fitting the same limits; a full
    seven-series response mixing plain-latest and fully supported
    two-observation comparison claims still passing every post-response
    validator after the compaction; and a future, deliberately oversized
    evidence package still being rejected locally by the real
    `OpenAIStructuredClient`, via the real (non-fake) client, with zero SDK
    calls made. `python -m pytest` (2011 passed), `python -m ruff check .`
    (all checks passed), and `git diff --check` (no whitespace errors) were
    all run as part of this change and pass. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)'s "Model-facing evidence
    compaction" section and "Known limitations" entry for full detail.

    **As of this item, this fix has not been exercised against a live
    OpenAI response for the seven-series basket, the ten-series worst case,
    or any other request** -- no further live `--execute` attempt has been
    made as part of this change. It has been validated only by offline,
    deterministic tests (fake evidence-builder/model-client stand-ins for
    the agent-level behavior tests, and the real, production
    `OpenAIStructuredClient` with only its SDK transport faked for the
    size-validation tests -- never a live network call). This does not
    establish that a real seven-series (or ten-series) model response will
    complete successfully, that any described observation is factually
    accurate, or that the Core Macro Basket constitutes a complete,
    gap-free, or research-validated macro dataset -- none of that is
    claimed here. No connector, repository, migration, basket
    configuration, dependency, `.env`, or the real DuckDB database was
    modified as part of this change.

29. **First live post-compaction seven-series Macro Analyst `--execute`
    attempt, rejected by the shared post-response content policy, and the
    narrow negated-directional-prediction disclaimer allowance this
    motivated (2026-08-25, code/tests/docs only for the fix itself -- no
    further live FRED/OpenAI request made as part of the fix, no migration,
    no configuration/dependency/`.env` change, and no write to the real
    local database).**

    Following item 28 above (the model-facing evidence compaction fix), a
    separately authorized live `--execute` attempt against the complete
    seven-series Core Macro Basket was made. This attempt passed the
    deterministic, all-or-nothing preflight gate, and **exactly one paid
    OpenAI request was sent and one structured response was received** --
    the compaction fix worked as intended for reaching the model. However,
    that structured response was then rejected by this agent's local
    post-response content policy validation:

    ```
    field=limitations[1]
    category=policy_violation (shared-policy category: directional_prediction)
    ```

    **No report was accepted from this attempt, and no retry was made.**
    Per this agent's sanitization contract, the model-authored text that
    triggered the rejection was deliberately never captured or recorded, so
    **the exact rejected wording, and its exact cause, are not known and are
    not claimed anywhere in this repository.**

    Offline analysis identified a plausible, locally reproducible
    false-positive class: the shared, deterministic
    `find_prohibited_content_category` check (see
    `market_intelligence/agents/non_directional_output_policy.py`) matches
    known directional-prediction phrasing (e.g. "predict", "forecast")
    unconditionally, with no awareness of negation -- so a `limitation`
    honestly and explicitly disclaiming prediction, e.g. "These observations
    do not predict future market direction," would also be flagged,
    identically to an affirmative directional claim. This is a **hypothesis
    about a plausible failure class, not a proven account of the exact live
    wording or cause** -- it is reproduced only with a locally authored test
    fixture
    (`test_negated_directional_disclaimer_is_still_flagged_by_the_raw_function`
    in `market_intelligence/tests/test_non_directional_output_policy.py`),
    never with the actual rejected text from the live attempt (which was
    never captured).

    The fix, made entirely in
    `market_intelligence/agents/macro_analyst.py` and its tests/docs:

    - A new, narrow, fail-closed allowance,
      `_limitation_policy_violation_category`, applies **only** to
      model-supplied `limitations` (never `claim_summary`/
      `conditional_mechanism`, which remain strictly, unconditionally
      denylisted via the shared module's plain
      `find_prohibited_content_category`, with no exemption of any kind,
      even for clearly negated wording -- exactly as for the Market Evidence
      Agent and News Analyst, unchanged).
    - Within a `limitation`, a match is allowed if and only if: its category
      is specifically `directional_prediction` (bullish/bearish bias, trade
      recommendation/action, and options-related language are **never**
      exempted this way, negated or not); it is immediately negated by one
      of the existing fixed negation/insufficiency cue phrases already used
      by this agent's separate content-scope negation allowance (item 22
      below); and the same clause contains no other prohibited-policy match
      at all. A mixed clause containing both a negated disclaimer and a
      separate affirmative directional/bias/trade/options statement still
      fails closed for that other statement.
    - This reuses (never duplicates) the existing clause-splitting/
      adjacent-negation-cue machinery (`_CLAUSE_SPLIT_RE`/`_match_is_negated`)
      already added for the item-22 content-scope allowance, per this
      change's preference to extend existing machinery over adding new
      independent logic.
    - A new public constant, `non_directional_output_policy.POLICY_PATTERNS`
      -- a **read-only** `Mapping` view (`types.MappingProxyType`) over the
      exact patterns `find_prohibited_content_category` itself already
      iterates over, never a duplicate copy and never a mutable alias any
      caller (including `MacroAnalyst`) could add to, replace an entry in,
      or delete from (attempting any of those raises `TypeError`) -- was
      added to the shared module so this per-match, per-clause check can
      reuse the shared module's own fixed patterns without hand-copying
      them. The module's own internal, mutable `_POLICY_PATTERNS` dict is
      never itself exported.
      `find_prohibited_content_category` itself is completely unchanged, and
      every other caller of it -- the Market Evidence Agent, the News
      Analyst, and this agent's own `claim_summary`/`conditional_mechanism`
      checks -- is unaffected. A dedicated test in
      `market_intelligence/tests/test_news_analyst.py`
      (`test_run_rejects_negated_directional_language_in_limitation`)
      confirms the identical negated directional disclaimer used above is
      still rejected outright by the News Analyst's own, unmodified
      `limitations` handling -- proving the shared policy was **not**
      weakened globally.
    - `AGENT_INSTRUCTIONS` was updated to tell the model that a `limitation`
      must describe a bounded evidence gap directly (what the stored
      evidence lacks or cannot support) and should not mention predictions,
      market direction, trades, or options at all, even as a disclaimer --
      reducing how often this allowance is needed at all, without replacing
      or weakening the deterministic check.
    - Every existing hard schema bound, citation/series validation,
      full-basket coverage validation, the separate, unchanged item-22
      content-scope negation allowance (still scoped to trend/regime/
      correlation/causation/policy-change/historical-extreme language, never
      directional-prediction/bias/trade/options language), the
      comparison-claim validator, the frequency-wording validator, the
      transmission-channel validator, the `directional_assessment`/
      `trade_recommendation` always-`"not_performed"` guarantee, and the
      one-OpenAI-request-with-no-automatic-retry behavior were all preserved
      unchanged and re-verified passing.

    8 new focused tests were added to
    `market_intelligence/tests/test_macro_analyst.py` (155 tests total, up
    from 147), plus 2 new tests in
    `test_non_directional_output_policy.py` and 1 new test in
    `test_news_analyst.py` (2022 tests total in the full suite, up from
    2011), covering: a narrow negated directional-prediction disclaimer
    limitation accepted with exactly one model call made and no retry; an
    affirmative (unnegated) prediction limitation still rejected; a mixed
    disclaimer-then-affirmative-prediction limitation (two clauses) still
    rejected with no retry, and that its rejection error never echoes either
    clause's text; identical negated directional wording still rejected
    outright in `claim_summary` and in `conditional_mechanism` (no
    exemption there, confirming requirement 2's strict-unchanged
    guarantee); a negated bullish/bearish-bias limitation and a negated
    trade-recommendation limitation each still rejected with their own
    correct category (proving the allowance is scoped to
    directional-prediction only); the new public `POLICY_PATTERNS` constant
    covering exactly the same fixed categories, in the same fixed order, as
    `find_prohibited_content_category`; a local reproduction, directly
    against the shared, unmodified `find_prohibited_content_category`, that
    a clearly negated directional disclaimer is still flagged
    `CATEGORY_DIRECTIONAL_PREDICTION` by that shared function itself (proving
    the fix lives entirely in `MacroAnalyst`'s own limitations-only
    post-processing, never in the shared module); and that the same negated
    directional disclaimer is still rejected by the News Analyst's own
    `limitations` handling, confirming the shared policy's behavior for
    other agents is unchanged. `python -m pytest` (2022 passed),
    `python -m ruff check .` (all checks passed), and `git diff --check` (no
    whitespace errors) were all run as part of this change and pass. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)'s "Post-response
    validation" section and "Known limitations" entry for full detail.

    **As of this item, this fix has not been exercised against a live
    OpenAI response** -- no further live `--execute` attempt has been made
    as part of this change. It has been validated only by offline,
    deterministic tests using fake evidence-builder/model-client stand-ins,
    mirroring how items 27/28 above were validated. This does not establish
    that a real model response will phrase a limitation in a way this
    allowance recognizes, that the exact live rejection from this item's
    attempt shared the same root cause as the locally reproduced class
    described above, or that any described observation is factually
    accurate -- none of that is claimed here. No connector, repository,
    migration, basket configuration, dependency, `.env`, or the real DuckDB
    database was modified as part of this change.

30. **Live seven-series `--execute` attempt rejected as
    `transmission_channel_invalid`, and the narrow synonym/word-boundary
    hardening this motivated (2026-08-25, code/tests/docs only for the fix
    itself -- no further live FRED/OpenAI request made as part of the fix,
    no migration, no configuration/dependency/`.env` change, and no write
    to the real local database).**

    A separately authorized seven-series Core Macro Basket dry run against
    the real local database passed the deterministic preflight: `eligible:
    true`, all seven series echoed back, and every flag list (`missing`,
    `stale`, `future_dated`, `missing_metadata`, `latest_missing`,
    `no_evidence_id`, `frequency_unrecognized`) empty. A separately
    authorized `--execute` attempt was then made against the same basket.
    It passed preflight and **exactly one paid OpenAI request was sent and
    one structured response was received**, but that response was rejected
    locally by `_validate_transmission_channels`:

    ```
    error: agent_error
    detail: Model output listed a transmission_channel that
      conditional_mechanism did not clearly and verifiably address.
    category: transmission_channel_invalid
    ```

    **No report was accepted from this attempt, and no retry was made.**
    Per this agent's sanitization contract, the model-authored
    `transmission_channels`/`conditional_mechanism` text that triggered the
    rejection was never captured or recorded, so **the exact live channel,
    mechanism wording, and root cause are not known and are not reproduced
    anywhere in this repository.**

    Offline analysis of `_TRANSMISSION_CHANNEL_PATTERNS`
    (`market_intelligence/agents/macro_analyst.py`) as it existed at the
    time found several plausible, locally reproducible false-positive
    classes -- ordinary, economically accurate ways of describing an
    allowed channel that the prior, narrower tokens did not match:
    "yield(s)" for `rates`; singular "price" for `inflation` (the prior
    pattern required plural "prices" or the exact phrase "price level");
    "expansion" for `growth`; "real estate" for `housing`;
    "risk aversion"/"risk-taking" for `risk_appetite`; "cost of credit" for
    `credit_conditions`; "funding conditions" for `liquidity`; and
    "petroleum"/"gasoline" for `energy`. Offline analysis also found a
    genuine word-boundary bug, independent of vocabulary breadth: the
    `currency` channel's pattern required the literal singular
    `\bexchange\s+rate\b`, which never matched the equally common plural
    "exchange rates" (the trailing "s" sits immediately after "rate" with
    no intervening non-word character, so the required word boundary right
    after "rate" was never satisfied there). **Each of these is offline
    analysis of a plausible failure class, reproduced only with locally
    authored test fixtures -- none of it is proof of the exact wording,
    channel, or cause rejected in the live attempt.**

    The fix, made entirely in `market_intelligence/agents/macro_analyst.py`
    and its tests/docs:

    - `_TRANSMISSION_CHANNEL_PATTERNS` gained a small, carefully reviewed
      set of additional word-boundary-safe synonym tokens per channel (the
      exact list above), and the `currency` pattern was corrected to
      `\bexchange\s+rates?\b` so both singular and plural match. Every
      added token remains a fixed, case-insensitive, word-boundary regex
      alternative checked by the same unchanged
      `_validate_transmission_channels` function -- no semantic model
      judging, retry, truncation, or fuzzy/substring matching was added,
      and `"other"` is still always rejected.
    - `AGENT_INSTRUCTIONS` was strengthened to enumerate the concrete
      economic wording that addresses each specific channel, state
      explicitly that generic language such as "affects markets" or "has
      economic effects" does not count as addressing any channel, and
      instruct the model to omit a channel entirely (never guess or
      include it anyway) when it cannot explicitly and directly explain
      that channel this way.
    - Every other validator -- citation, series, full-basket coverage,
      content-scope, comparison-claim, frequency-wording, and the shared
      non-directional output policy -- and the one-OpenAI-request-with-
      no-automatic-retry behavior are completely unchanged and re-verified
      passing.

    25 new focused tests were added to
    `market_intelligence/tests/test_macro_analyst.py` (155 tests in that
    file, up from 130; 180 across the three Macro Analyst test files, up
    from 155; 2053 in the full repository suite, up from 2028), covering:
    every allowed channel accepted via its pre-existing baseline mechanism;
    every allowed channel accepted via a representative economic synonym
    (reproducing the plausible false-positive classes above, now fixed);
    generic language ("affects markets"/"has economic effects") and an
    unrelated mechanism still rejected; a word-boundary safety case proving
    "rate" appearing only as a substring of unrelated words ("moderate",
    "corporate") never satisfies the `rates` token; multiple listed
    channels where one is addressed via a synonym and the other is not,
    still rejected; exactly one model call with no automatic retry after a
    transmission-channel rejection; a dedicated case that a synonym-based
    rejection still never echoes the rejected mechanism text; and that
    `AGENT_INSTRUCTIONS` states both the omit-if-unaddressed instruction
    and the generic-language exclusion. `python -m pytest` (2053 passed),
    `python -m ruff check .` (all checks passed), and `git diff --check`
    (no whitespace errors) were all run as part of this change and pass.
    The existing Market Evidence Agent, News Analyst, and shared
    `non_directional_output_policy` test suites were re-run unchanged and
    still pass, confirming this fix touches only the Macro Analyst's own
    transmission-channel patterns and instructions. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)'s "Transmission-channel
    addressing" and "Known limitations" sections for full detail.

    **As of this item, this fix has not been exercised against a live
    OpenAI response** -- no further live `--execute` attempt has been made
    as part of this change. It has been validated only by offline,
    deterministic tests using fake evidence-builder/model-client
    stand-ins. This does not establish that a real model response will
    phrase a mechanism using one of these newly recognized tokens, that
    the live rejection above shared the same root cause as any locally
    reproduced class described here, or that any described observation is
    factually accurate -- none of that is claimed here. No connector,
    repository, migration, basket configuration, dependency, `.env`, or
    the real DuckDB database was modified as part of this change.

31. **Code-review correction: three ambiguous bare transmission-channel
    synonym tokens narrowed to explicit economic phrases (2026-08-25,
    code/tests/docs only -- no live FRED/OpenAI request, no migration, no
    configuration/dependency/`.env` change, and no write to the real local
    database).**

    A review of the synonym expansion in item 30 above found that three of
    its bare tokens were themselves too permissive: bare `\bprices?\b`
    (added for `inflation`) also matches "stock price," "house price," and
    "energy prices," none of which is an inflation concept; bare
    `\byields?\b` (added for `rates`) also matches dividend yield, earnings
    yield, and crop yield, none of which is a rates concept; and bare
    `\bexpansion\b` (added for `growth`) also matches "credit expansion"
    and "balance-sheet expansion," neither of which is a growth concept on
    its own.

    Each of these three tokens was replaced in
    `_TRANSMISSION_CHANNEL_PATTERNS`
    (`market_intelligence/agents/macro_analyst.py`) with explicit phrases:

    - `inflation`: `price level(s)`, `consumer price(s)`, `prices of goods
      and services`, alongside the already-specific `purchasing power`/
      `cost of living` (bare `prices?` removed).
    - `rates`: `bond yield(s)`, `Treasury yield(s)`, `government bond
      yield(s)`, alongside the already-accepted bare `rate`/`rates` and
      `interest rate(s)` (neither of which was ambiguous; bare `yields?`
      removed).
    - `growth`: `economic expansion`, alongside the already-specific
      `growth`/`economic activity`/`output`/`GDP` (bare `expansion`
      removed).

    `AGENT_INSTRUCTIONS` was updated to match exactly. Every other synonym
    token added by item 30 (e.g. "funding conditions", "risk aversion",
    "risk-taking", "cost of credit", plural "exchange rates", "real
    estate", "petroleum", "gasoline") was reviewed for the same ambiguity
    and found to already be a channel-specific phrase, so it is unchanged.
    This is strictly a narrowing: no previously rejected mechanism is
    newly accepted, and every mechanism that relied only on an
    intentionally-retained token (e.g. bare `rate`/`rates`, `growth`,
    `inflation`) is unaffected.

    21 new focused tests were added to
    `market_intelligence/tests/test_macro_analyst.py` (176 tests in that
    file, up from 155; 2074 in the full repository suite, up from 2053),
    covering: explicit inflation phrases (`price level`, `consumer
    prices`, `prices of goods and services`, `purchasing power`, `cost of
    living`, bare `inflation`) accepted; "stock price," "house price," and
    "energy prices" rejected for `inflation`; explicit `rates` phrases
    (`rate`, `interest rate`, `bond yield`, `Treasury yield`, `government
    bond yield`) accepted; dividend yield, earnings yield, and crop yield
    rejected for `rates`; "economic expansion" accepted for `growth`;
    "credit expansion" and "balance-sheet expansion" rejected for
    `growth`; and that `AGENT_INSTRUCTIONS` no longer advertises the
    removed bare synonyms and does state the replacement explicit phrases.
    `python -m pytest` (2074 passed), `python -m ruff check .` (all checks
    passed), and `git diff --check` (no whitespace errors) were all run as
    part of this change and pass. The existing generic-language-rejected,
    unrelated-mechanism-rejected, multi-channel-partial-coverage,
    word-boundary, `"other"`-always-rejected, exactly-one-model-call-with-
    no-retry, and never-echoes-rejected-text tests from item 30 were
    re-run unchanged and still pass, confirming this correction touches
    only the three flagged tokens and `AGENT_INSTRUCTIONS`' wording. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)'s "Transmission-channel
    addressing" and "Known limitations" sections for full detail.

    **This correction has not been exercised against a live OpenAI
    response** -- no live `--execute` attempt was made as part of this
    change. It has been validated only by offline, deterministic tests
    using fake evidence-builder/model-client stand-ins. No connector,
    repository, migration, basket configuration, dependency, `.env`, or
    the real DuckDB database was modified as part of this change.

32. **Live seven-series Macro Analyst `--execute` attempt failed
    `response_validation_failed` before any post-response validator ran, and
    the structured-output validation diagnostics hardening this motivated
    (2026-08-25, code/tests/docs only -- no live FRED/OpenAI request made as
    part of the hardening itself, no migration, no configuration/dependency/
    `.env` change, and no write to the real local database).**

    A separately authorized seven-series Core Macro Basket dry run
    (`FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`)
    against the real local database passed the deterministic preflight
    (`eligible: true`, every flag list empty). A separately authorized
    `--execute` attempt against the same seven series was then made: it
    passed preflight, and exactly one paid OpenAI request reached OpenAI,
    but the response failed OpenAI SDK's own client-side structured-output
    re-validation against `MacroAnalystModelAnalysis` --
    `{"error": "agent_error", "detail": "OpenAI response failed
    structured-output validation.", "category":
    "response_validation_failed"}`. **This failure occurs entirely inside
    `OpenAIStructuredClient.generate()`, upstream of every
    `MacroAnalyst`-owned post-response validator (citation, series,
    full-basket coverage, content-scope, comparison-claim,
    frequency-wording, transmission-channel, and the shared non-directional
    output policy check) -- none of them ever saw this response. No report
    was accepted from this attempt, and no retry was made.** This is a
    distinct failure from the earlier seven-series
    `transmission_channel_invalid` rejection recorded in item 30 above: that
    attempt's response *did* reach and get rejected by a post-response
    validator; this attempt's response never reached one.

    Because `OpenAIStructuredClient` never captures or logs raw model
    output, **the exact schema field/value that failed re-validation for
    this specific occurrence is unavailable and permanently unrecoverable.**
    No bound of `MacroAnalystModelAnalysis`/`MacroClaimDraft` was changed or
    is claimed to be the proven cause -- exceeding one of the schema's
    Pydantic-only `minLength`/`maxLength`/`minItems`/`maxItems` bounds
    remains one plausible, locally reproducible failure mode (reproduced
    offline only, see below), not an established fact about this attempt.

    In direct response, bounded, sanitized structured-output validation
    diagnostics were added: `OpenAIStructuredClient.generate()`'s existing
    `except pydantic.ValidationError` handling (the sole place
    `OpenAIParseFailureError`/`response_validation_failed` is ever raised)
    now also builds a `ValidationDiagnostics` object (via the public
    `pydantic.ValidationError.errors()` API only -- no private/
    underscore-prefixed OpenAI SDK module) and attaches it to the raised
    error's new `diagnostics` attribute, with the error's fixed message and
    `category` completely unchanged. `ValidationDiagnostics` carries only: a
    bounded `issue_count` (capped at 20); and up to 5 deduplicated,
    deterministically ordered `(field_path, category)` pairs, where
    `field_path` is derived only from each Pydantic error's `loc` (sanitized
    to known schema field-name syntax and nonnegative integer indexes,
    bounded to 6 segments/200 characters total, collapsing to the fixed
    string `"unknown"` for anything unrecognized or malformed) and
    `category` is one of a fixed allowlist (`string_too_long`,
    `string_too_short`, `too_many_items`, `too_few_items`, `missing_field`,
    `extra_field`, `literal_or_enum_invalid`, `type_invalid`, `other`) --
    never a raw Pydantic error type string. **Never included, anywhere:** a
    Pydantic error's `input`/`ctx`, a raw error message, an exception
    type/repr, a model-authored value, response text/body, evidence
    content, credentials, URLs, or headers. If the installed OpenAI SDK
    ever wraps or strips the underlying `ValidationError` so `errors()` is
    missing/non-callable/raising/non-list, diagnostics report
    `available=false` and the existing generic error is otherwise preserved
    unchanged -- no private SDK module is ever imported to work around
    this. `scripts/run_macro_analyst.py`'s sanitized `agent_error` JSON
    output now includes `diagnostics_available` and, only when `true`,
    `validation_issue_count`/`validation_issues` (each entry only
    `field_path`/`category`). The Market Evidence Agent and News Analyst
    automatically inherit the same diagnostics on any
    `OpenAIParseFailureError` they propagate -- both already re-raise
    `OpenAIStructuredError` unchanged, so no per-agent code change was
    needed; this was confirmed by extending their own existing real-schema
    regression tests to assert the same diagnostics now appear, with no
    other behavior change to either agent.

    **No hard Pydantic bound, citation check, coverage check, content-scope
    check, comparison check, frequency-wording check, transmission-channel
    check, or output-policy check was weakened, and no
    `MacroAnalystModelAnalysis`/`MacroClaimDraft` bound was changed or even
    speculated about as the cause of this specific live attempt** -- this
    change adds observability for a *future* occurrence of this category
    only. 42 new focused tests were added across
    `market_intelligence/tests/test_openai_structured.py` and
    `market_intelligence/tests/test_run_macro_analyst.py` (2116 in the full
    repository suite, up from 2074), covering: one real
    `pydantic.ValidationError` per required category (oversized/undersized
    string, too many/too few list items, a missing required field, a
    forbidden extra field, an invalid literal, a wrong Python type, and a
    nested list-index path) using a small local test-only schema;
    deterministic ordering/deduplication/bounds for multiple issues;
    malicious/malformed `loc` values (injection-shaped strings, non-str/int
    segments, oversized indexes, excessive depth/length) always collapsing
    to `"unknown"`; a real `ValidationError` whose own embedded
    secret-shaped `input`/`msg` never appearing anywhere in the resulting
    diagnostics; a missing/non-callable/raising/non-list `errors()` all
    falling back to `available=false` without raising; `generate()`
    attaching diagnostics without changing the fixed message/category, with
    exactly one SDK call and no automatic retry; a locally constructed
    seven-series `MacroAnalystModelAnalysis` stress fixture proving a valid
    seven-claim response still parses successfully; separate invalid
    fixtures for an oversized `claim_summary` and excessive `macro_claims`
    (explicitly documented as reproducible possibilities, not the proven
    live cause); the existing `MarketEvidenceModelAnalysis`/
    `NewsAnalystModelAnalysis` regression tests extended to prove the shared
    boundary requires no per-agent change; and the CLI's sanitized
    `diagnostics_available`/`validation_issue_count`/`validation_issues`
    output, including the `diagnostics_available: false` case. `python -m
    pytest` (2116 passed), `python -m ruff check .` (all checks passed),
    and `git diff --check` (no whitespace errors) were all run as part of
    this change and pass. See
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)'s
    "Structured-output validation diagnostics" and "Known structured-output
    validation failure (Macro Analyst, seven-series, 2026-08-25)" sections
    and [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)'s "Known
    limitations" for full detail.

    **As of this item, no further live OpenAI request has been made** --
    this hardening was validated only by offline, deterministic tests. No
    connector, repository, migration, basket configuration, dependency,
    `.env`, or the real DuckDB database was modified as part of this
    change.

33. **Security correction: structured-output validation diagnostics'
    `field_path` now validated against the real output schema, not merely
    identifier syntax (2026-08-26, code/tests/docs only -- no live
    FRED/OpenAI request, no migration, no configuration/dependency/`.env`
    change, and no write to the real local database).**

    A review of item 32's diagnostics feature found that
    `_sanitize_field_path` validated only that each `loc` segment *looked
    like* a schema field-name identifier, never that it actually *was* one.
    For a Pydantic `extra_forbidden` error, `loc` contains the arbitrary,
    model-authored extra JSON key itself -- e.g. a key literally named
    `secret_marker`, or shaped like a credential, URL, SQL text, or a
    path-traversal string. Such a key can be perfectly identifier-shaped
    while still being attacker/model-controlled text that is not a real,
    schema-declared field, so it could previously be reproduced verbatim as
    a `field_path` even though it was never schema-controlled.

    The fix, made entirely in
    `market_intelligence/model_clients/openai_structured.py`: every
    reported `field_path` is now additionally walked against the exact
    `output_model` schema the request used, using only public Pydantic v2
    API (`BaseModel.model_fields`, `FieldInfo.annotation`) and the standard
    library `typing` module (`get_origin`/`get_args`) -- never any private/
    underscore-prefixed Pydantic or OpenAI SDK module. A string segment is
    valid only when the current schema position is a `BaseModel` subclass
    declaring that exact field; an integer segment is valid only when the
    current position is a bounded, single-argument `list[X]` annotation.
    `Annotated`/`Optional`/`X | None` wrappers are unwrapped along the way;
    an ambiguous union (more than one non-`None` member type) is never
    guessed -- the whole path fails closed to `unknown` instead. This walk
    naturally supports nested `BaseModel` fields and lists of `BaseModel`
    fields (e.g. `macro_claims[0].claim_summary`,
    `macro_claims[3].evidence_ids`, `limitations[1]` all remain correctly
    identified), while any extra/unknown key -- identifier-shaped or not --
    now always collapses to `unknown`, with only the broad `extra_field`
    category still reported. `_build_validation_diagnostics`/
    `_sanitize_field_path` now both take the exact `output_model` as a
    required parameter (previously derived `field_path` from `loc` alone).
    `OpenAIParseFailureError`'s fixed message/category, every existing
    depth/segment-length/index/count/deduplication/output-size bound, and
    every other validator are all unchanged.

    20 new focused tests were added to
    `market_intelligence/tests/test_openai_structured.py` (2136 in the full
    repository suite, up from 2116), covering: a parametrized sweep proving
    a forbidden extra key named with a secret, credential, URL, SQL,
    path-traversal, or code-injection marker is never reproduced (always
    `unknown`, category still `extra_field`) regardless of whether the key
    is identifier-shaped; valid top-level and nested schema fields, and
    valid list indexes, remaining visible; out-of-range/malformed schema
    paths (a real field in the wrong position, a string where an index is
    required, a segment past a leaf type, an undeclared field) remaining
    `unknown`; an ambiguous union annotation never being guessed; a
    non-`BaseModel`/malformed `output_model` falling back to `unknown`
    without raising; and the existing multiple-issues/deduplication/bounds,
    malicious-`loc`, credential-leak, unavailable-diagnostics-fallback, and
    real `MarketEvidenceModelAnalysis`/`NewsAnalystModelAnalysis`/
    `MacroAnalystModelAnalysis` regression tests all re-verified passing
    with schema-validated paths (proving Market Evidence, News Analyst, and
    Macro Analyst all inherit the same safe, corrected behavior with no
    per-agent change) -- including exactly one SDK request and zero
    automatic retry, unchanged. `python -m pytest` (2136 passed), `python -m
    ruff check .` (all checks passed), and `git diff --check` (no
    whitespace errors) were all run as part of this change and pass. See
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)'s
    "Structured-output validation diagnostics" section (updated "Field
    paths" contract and new "Security correction" note) for full detail.

    **No live OpenAI request was made as part of this change** -- it was
    validated only by offline, deterministic tests. No connector,
    repository, migration, basket configuration, dependency, `.env`, or the
    real DuckDB database was modified.

34. **Macro Analyst live-validation milestone complete: first successfully
    accepted post-compaction seven-series Core Macro Basket `--execute` run
    (2026-08-26, documentation only -- no code, test, configuration,
    dependency, `.env`, migration, or database change, and no new live
    request made as part of recording this entry).**

    Following items 26-33 above -- the frequency-aware staleness fix, the
    full-basket coverage fix, the model-facing evidence compaction fix, and
    a sequence of separately authorized seven-series `--execute` attempts
    that were each rejected before a report could be accepted (item 28:
    local evidence-node-count rejection, zero tokens; item 29:
    `policy_violation` at `limitations[1]`; item 30:
    `transmission_channel_invalid`; item 32: `response_validation_failed`
    before any post-response validator ran) -- a separately authorized
    `--execute` run of the Macro Analyst against the real local database and
    the real OpenAI API was made on 2026-08-26, requesting the complete,
    approved seven-series Core Macro Basket: `FEDFUNDS`, `GS10`, `CPIAUCSL`,
    `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`. **It was accepted end to end for
    the first time.**

    Sanitized result (no evidence IDs, exact observation values, observation
    dates, snapshot timestamp, or the model report's free text are
    reproduced here):

    - `mode: execute`, `status: completed`
    - `source_series_count: 7`; all seven requested series echoed back in
      `series_ids`
    - `evidence_quality: sufficient`
    - seven macro claims accepted -- exactly one retained claim per
      requested series, satisfying the mandatory full-basket coverage rule
      (item 27)
    - `limitations`: empty
    - `transmission_channels`: empty for every claim
    - `conditional_mechanism` asserted no unsupported transmission mechanism,
      so the empty `transmission_channels` lists were valid -- the
      per-channel addressing validator (`_validate_transmission_channels`)
      has nothing to reject when a claim lists no channel and claims no
      mechanism it would need to substantiate
    - `directional_assessment: not_performed` and
      `trade_recommendation: not_performed` -- both the fixed,
      model-excluded literal, exactly as guaranteed; no direction or
      recommendation was produced or could have been produced
    - `abstained_reasons`: empty
    - `model: gpt-5-mini`
    - `input_tokens: 3829`, `output_tokens: 3272`, `total_tokens: 7101`
    - exactly one OpenAI request occurred; no retry occurred

    **Every `OpenAIStructuredClient` structured-output validation step and
    every Macro Analyst post-response validator passed** for this response:
    the SDK-side structured-output re-validation against
    `MacroAnalystModelAnalysis`, then
    `_validate_claims_quality_consistency`, `_validate_citations_and_series`,
    `_validate_coverage`, `_validate_content_scope`,
    `_validate_comparison_claims`, `_validate_frequency_wording`,
    `_validate_transmission_channels`, and the shared non-directional output
    policy check. The model-facing evidence compaction (item 28) held: the
    seven-series evidence package stayed within
    `OpenAIStructuredClient`'s unchanged node/byte bounds and one paid
    request reached the model.

    **What this establishes and what it does not.** This confirms that the
    deterministic scaffolding around the model call -- the all-or-nothing
    preflight, the compacted evidence package, the structured-output
    contract, and all post-response validators -- can now accept a real,
    complete seven-series response end to end, and that the
    `directional_assessment`/`trade_recommendation` always-`"not_performed"`
    guarantee held on a live run. **It is one accepted run.** It is not a
    validated evaluation methodology, and it is not a claim that any
    `claim_summary` accurately transcribes the underlying stored observation
    or its official metadata -- no automated evaluation of factual accuracy
    exists in this repository (see
    [CLAUDE.md](CLAUDE.md)/[AGENTS.md](AGENTS.md)'s "Evidence and Claims"
    section). It does not establish that any future seven-series (or other)
    request will be accepted, that the Core Macro Basket constitutes a
    complete, gap-free, or research-validated macro dataset, or that this
    agent is a regime classifier, predictor, directional market model, or
    trading agent -- it remains explicitly none of those. The earlier
    rejected attempts in items 26-33 are preserved unchanged as honest
    records and are **not** retroactively reclassified as successes. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md) and
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md) for
    the corresponding entries.

35. **Agent-evaluation foundation added (2026-08-26, code/tests/synthetic
    fixtures/docs only — no connector, OpenAI, DuckDB, agent, orchestration,
    migration, schema, dependency, or `.env` change; no live request; no
    commit or push).** This is *partial* progress on P0-7 (see
    [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) and
    [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md)).
    **Phase 0 remains open.**

    A new package, `market_intelligence/evaluation/`, adds *only* the safe,
    offline data foundation for the repeatable evaluation methodology:

    - **`contracts.py`** — strict Pydantic v2 models (`extra="forbid"`, every
      string and list bounded, timestamps timezone-aware and normalized to
      UTC, locally + deterministically generated `run_id` via `build_run_id`,
      and deliberately **no** field for a raw model response, a provider
      response ID, a credential, a URL, a database path, or an unrestricted
      metadata dict). Public contract: `AgentIdentifier`
      (`market_evidence` / `news_analyst` / `macro_analyst`),
      `FindingSeverity` (`info` / `warning` / `failure`),
      `CitationClassification` (`supported` / `partially_supported` /
      `unsupported` / `unable_to_determine`), `CitationReason` (the eight
      fixed reasons), `FindingCategory` (fixed bounded enum),
      `ClaimCitationPair`, `CitationAdjudication` (`reason="other"` requires a
      short bounded `reviewer_note`; every other reason forbids one; and
      `classification` + `reason` must be a permitted pairing per the
      immutable `CLASSIFICATION_REASON_MATRIX`, enforced by a model validator
      that rejects an incompatible pairing with a fixed sanitized message —
      see [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md)),
      `EvaluationFinding`, and `EvaluationRunRecord`. `unable_to_determine`
      is a permanent, first-class classification — nothing converts it into a
      pass or a failure.
    - **`rubric.py`** — `check_rubric_completeness(record)`: a deterministic,
      pure validator returning, with stable lexical ordering, whether every
      expected (claim, citation) pair has exactly one adjudication, plus any
      missing / unexpected / duplicate pairs. Completeness never depends on
      classifications, reasons, or findings, so a completed characterization
      may be entirely `unsupported` / `partially_supported` /
      `unable_to_determine` and may carry `failure` findings.
      `COMPLETION_IS_NOT_VALIDATION` states that completion does not imply
      agent validation or a universal pass.
    - **`serialization.py`** — pure `to_json_str` / `from_json_str`, plus
      `write_record` (explicit caller path only; parent must already exist;
      refuses symlinked target/parent; refuses overwrite by default; atomic
      write via a uniquely named temp file in the same directory) and
      `read_record` (refuses symlinks; refuses a file over `MAX_RECORD_BYTES`
      before reading). Every failure is a sanitized
      `EvaluationSerializationError` that never reproduces the path, the file
      bytes, or the record content. No database write; no automatic output
      directory.
    - **`fixtures/`** — six hand-authored synthetic `EvaluationRunRecord`
      JSON files (fully supported / partially supported / unsupported /
      unable to determine / incomplete rubric / duplicate-and-unexpected
      adjudication) and `PROVENANCE.md`. They contain no real article text,
      URLs, live-run evidence IDs or observation values, credentials,
      response IDs, or copied model output, and are explicitly not evidence
      of any agent's quality.
    - **Tests** — `market_intelligence/tests/test_evaluation_*.py` (132 tests)
      cover strict enum/schema/bounds behaviour, timezone enforcement, the
      `other` note rule, the full classification × reason compatibility matrix
      (a parametrized sweep over every combination plus direct regressions for
      the contradictory pairings), every rubric-completeness case,
      deterministic ordering and JSON round trip, path-traversal / symlink /
      overwrite / oversized-file / malformed-JSON / leak-sanitization
      behaviour, synthetic-fixture validation, and a static (AST import scan)
      plus
      fresh-interpreter proof that no connector, OpenAI client, database,
      agent, orchestration, or network import occurs. The full pre-existing
      suite remains green.

    **What this establishes and what it does not.** It establishes that the
    offline contracts, the rubric-completeness check, and the local
    serialization boundary exist and behave as documented. **It does not
    evaluate any agent.** No factual-transcription check has been run, no
    citation-support adjudication of any real agent output exists, and no
    first characterization has been recorded. Closing P0-7 still requires the
    deterministic factual-transcription checks, the human rubric applied to a
    real first characterization covering every claim, and preserved
    findings / failures / `unable_to_determine` results (see
    [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md)).

    **Pre-merge review fix (2026-08-26, same branch, not yet merged):**
    `CitationAdjudication` originally validated `classification` and `reason`
    independently, so semantically contradictory pairings (e.g. `supported`
    with `value_conflicts_with_evidence`) were accepted. A single explicit,
    immutable `CLASSIFICATION_REASON_MATRIX` and a `CitationAdjudication`
    model validator were added, rejecting an incompatible pairing with a
    fixed sanitized message that reproduces no reviewer note, identifier,
    path, or record content. The `other`-requires-note / non-`other`-forbids-
    note rules, `unable_to_determine` as a first-class outcome, and
    rubric completeness being independent of classification are all
    preserved; all six synthetic fixtures were already matrix-compatible and
    needed no change. Tests grew by a full classification × reason
    parametrized sweep plus contradictory-pair regressions. No other
    behavioural change.

36. **First deterministic factual-transcription check added — Macro Analyst
    only, offline/synthetic (2026-08-26, code/tests/synthetic fixtures/docs
    only — no connector, OpenAI, DuckDB, agent, orchestration, migration,
    schema, dependency, or `.env` change; no live request; no agent-behaviour
    change; no commit or push).** This is *further partial* progress on P0-7
    (see [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) and
    [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md)).
    **Phase 0 remains open; P0-7 is not met.**

    `market_intelligence/evaluation/macro_factual_transcription.py` adds a pure
    evaluator (`evaluate_macro_transcription`) taking one evaluation-specific
    input — a sanitized local `claim_id`, the claim's declared
    `claim_series_id`, the `claim_summary` string, and one or two cited
    *synthetic* evidence facts (`series_id`, `observation_date`, a `Decimal`
    `value`, a `frequency` word, optional `units`). It imports no connector,
    database, OpenAI client, or agent runtime and makes no network request
    (the existing `test_evaluation_offline.py` AST + fresh-interpreter guard
    now also covers it).

    **Exact supported grammar (the only two recognized forms).** Both are
    matched by a single fully anchored regular expression with fixed
    connective text — no generic number scraping, no fuzzy/semantic matching:

    - single stored observation —
      `The stored <frequency> observation dated <YYYY-MM-DD> (is|was) <value>[ <units>][, per official FRED metadata|, as stored].`
    - two-observation comparison, in the Macro Analyst's canonical wording
      (latest observation first, then the immediately preceding one, one
      direction verb) —
      `The stored <frequency> observation dated <latest-date> was <latest-value>; it (increased|decreased) from the stored <frequency> observation dated <previous-date>, which was <previous-value>[, …].`
      plus the unchanged variant
      `The stored <frequency> observation dated <latest-date> was <latest-value>; it was unchanged from the stored <frequency> observation dated <previous-date>, which was <previous-value>[, …].`
      There is no `Comparing the stored observations dated X and Y, …` form —
      that phrasing is not part of the canonical wording and is reported as
      unrecognized.

    `<frequency>` ∈ {`daily`, `weekly`, `biweekly`, `monthly`, `quarterly`,
    `semiannual`, `annual`} (the seven words the Macro Analyst's own
    `FREQUENCY_SHORT_WORDS` maps to); `<units>` is a fixed closed vocabulary.
    Every value/date token is read from a named capture group, so extra digit
    runs elsewhere in a recognized sentence are never scraped.

    **Deterministic verification:** declared `claim_series_id` vs every cited
    fact's `series_id`; observation date; `Decimal` value (numeric equality,
    tolerant of trailing-zero formatting); frequency word; units *only when the
    claim states them*; for a comparison, the previous observation's date and
    value (facts ordered previous→latest by `observation_date`) and that the
    stated `increased`/`decreased`/`unchanged` direction agrees with the two
    cited values.

    **Evaluator outcomes (one `EvaluationFinding` per claim):**
    - exact match → an `info` `factual_transcription` finding.
      `MATCH_IS_NOT_VALIDATION` states this is *not* a validation — it confirms
      only that the recognized tokens transcribe the cited synthetic fact.
    - mismatch → a `failure` finding naming only broad category slugs
      (`series_id`, `observation_date`, `value`, `frequency`, `units`,
      `previous_observation_date`, `previous_value`, `comparison_direction`,
      `citation_structure`) — never any claim or evidence text or value.
    - unrecognized / unparseable wording → a `warning` finding requiring human
      citation-support review; **never** treated as a pass.
    - no universal "agent passes"/"validated" result: `MacroTranscriptionResult`
      has no `passed`/`validated` attribute.

    **Synthetic fixtures** (`fixtures/macro_transcription.py`,
    `MACRO_TRANSCRIPTION_FIXTURES`): exact single-observation match; wrong
    value; wrong date; wrong series; wrong units + frequency; incorrect
    comparison direction; unsupported wording; a comparison sentence carrying
    extra unrelated digit runs (which must not be scraped); and an exact
    comparison match. All hand-authored — no real claim/evidence text, live
    evidence IDs, live observation values, credentials, or model output (see
    `fixtures/PROVENANCE.md`).

    **Tests** (`market_intelligence/tests/test_evaluation_macro_transcription.py`,
    57 tests, all passing): every fixture's outcome/form/categories and finding
    severity; the two-forms-only canonical grammar (recognized phrasings match;
    everything else — including the non-canonical `Comparing the stored
    observations dated X and Y, …` form and paraphrase verbs like "has a value
    of" — is routed to human review); units-only-when-stated; `Decimal`
    trailing-zero tolerance; extra numbers not scraped; comparison direction
    agreement and the unchanged variant; previous-observation date/value checks;
    citation-structure guards; input bounds (`cited_facts` 1–2, frequency
    enum, ISO-date validation, `claim_summary` length, `extra="forbid"`);
    deterministic equality and batch input-order preservation; and that no
    finding (`model_dump_json`) reproduces a synthetic claim value or date.

    **What this establishes and what it does not.** It establishes that a
    deterministic, non-scraping, fail-closed transcription check for the two
    controlled Macro Analyst statement forms exists and behaves as documented
    against synthetic inputs. **It does not evaluate any real agent output.**
    Citation-support adjudication, the same check for the Market Evidence Agent
    and News Analyst, lexical-overlap triage, the abstention matrix,
    cross-agent consistency, repeatability, and — the actual closure condition
    — a recorded first characterization of a real agent covering every claim
    all remain unimplemented. Phase 0 stays open.

37. **Offline Macro characterization workflow added (2026-08-27,
    code/tests/synthetic fixtures/docs only — no connector, OpenAI, DuckDB,
    agent, orchestration, migration, schema, dependency, or `.env` change; no
    live request; no real database access; no agent-behaviour change; no commit
    or push).** This is *further partial* progress on P0-7 (see
    [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) and
    [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md)).
    **Phase 0 remains open; P0-7 is not met; no real characterization has been
    performed.**

    The minimum offline workflow to *characterize* one Macro Analyst report
    (not to run one):

    - **`macro_characterization_input.py` — strict local input contract.**
      `MacroCharacterizationInput` carries **only**: a sanitized
      `characterization_label`; one entry per Macro claim (the local `claim_id`,
      the declared `claim_series_id`, the `claim_summary` string, and the one or
      two sanitized evaluation-evidence `TranscriptionEvidenceFact`s the existing
      Macro transcription evaluator needs); and the expected
      `(claim_id, citation_id)` pairs. Committed fixtures and tests stay
      synthetic-only; real sanitized evidence facts may be supplied only through
      the explicit local characterization workflow under gitignored
      `data/evaluations/local/`, and real characterization inputs and outputs
      must never be committed. It has **no** field for a credential,
      URL, provider response ID, raw provider payload, unrestricted metadata
      dict, database path, or model reasoning, and rejects a duplicate
      `claim_id`, an expected pair naming an unknown claim or an uncited
      `citation_id`, a duplicated expected pair, or a claim not covered by any
      expected pair. `read_characterization_input` reuses the serialization
      boundary's symlink / size / sanitized-error rules.
    - **`macro_characterization_workflow.py` — pure builder + completion.**
      `build_macro_characterization(input, *, created_at)` runs
      `evaluate_macro_transcription` for **every** claim, creates an
      `EvaluationRunRecord` (agent `macro_analyst`, locally derived `run_id`)
      whose `findings` are one `scope_boundary` `info` finding plus every
      claim's transcription finding, and emits one `PendingCitationAdjudication`
      per expected pair. The record's `adjudications` list is **always empty** —
      the builder never pre-classifies citation support and never records a
      transcription match as citation support
      (`TRANSCRIPTION_IS_NOT_CITATION_SUPPORT`).
      `complete_macro_characterization(record, adjudications)` requires
      **exactly one** adjudication per expected pair (missing / duplicate /
      unexpected each raises a sanitized `MacroCharacterizationError` that echoes
      no identifier, note, path, or classification), preserves every
      `supported` / `partially_supported` / `unsupported` /
      `unable_to_determine` classification unchanged, and returns a fully
      re-validated `EvaluationRunRecord`. Completion does **not** imply
      validation (`COMPLETION_IS_NOT_VALIDATION`).
    - **`scripts/characterize_macro_report.py` — offline CLI.** Explicit
      `--input` / `--output` paths only; **dry-run / validate by default**
      (prints a sanitized summary plus the pending-adjudication templates,
      writes nothing); `--write` required to serialize the `EvaluationRunRecord`
      via `evaluation.serialization.write_record` (no overwrite, no symlink,
      atomic, no directory creation); with `--write`, the resolved `--output`
      path must be **strictly inside** gitignored `data/evaluations/local/` —
      a repository-root, tracked-directory (`fixtures/` / `tests/` / `docs/`),
      `data/evaluations/`-itself, outside-repository, `..`-traversal, or
      symlink-escape target is refused; dry-run behavior is unchanged; every
      error is a fixed sanitized marker.
    - **Local-data boundary.** New gitignored `data/evaluations/local/` (with a
      tracked `.gitkeep` and `data/evaluations/README.md`) is the only home for
      real characterization inputs/outputs, and they must never be committed.
      Only synthetic fixtures under
      `market_intelligence/evaluation/fixtures/` are committed
      (`macro_characterization.py` and the byte-stable
      `macro_characterization_input_complete.json`).
    - **Tests** —
      `market_intelligence/tests/test_evaluation_macro_characterization.py` (21
      tests) and `test_characterize_macro_report_cli.py` (17 tests) cover a
      complete multi-claim characterization, transcription match / mismatch /
      human-review outcomes, the adjudication template covering every pair, no
      pre-classification, a complete human rubric, missing / duplicate /
      unexpected adjudications, explicit-path / dry-run / no-overwrite /
      no-directory-creation, the `data/evaluations/local/`-only output allowlist
      (repository-root, tracked-directory, `data/evaluations/`-itself,
      outside-repository, `..`-traversal, and symlink-escape refusal), and
      sanitization CLI behaviour, and (via `test_evaluation_offline.py` plus a
      dedicated AST scan of the CLI script) zero connector / DB / OpenAI /
      agent-runtime imports. Full suite: `python -m pytest` → **2,375 passed,
      3 skipped** (2,378 collected); `python -m ruff check .` clean;
      `git diff --check` clean.

    **What this establishes and what it does not.** It establishes that an
    offline workflow to build and complete one Macro characterization record
    exists and behaves as documented against synthetic inputs. **It does not
    evaluate any real agent output**, makes no live request, and performs no
    real database access. Citation-support adjudication of real output,
    lexical-overlap triage, the abstention matrix, cross-agent consistency,
    repeatability, the same transcription check for the Market Evidence Agent
    and News Analyst, and — the actual closure condition — a recorded first
    characterization of a real agent covering every claim all remain
    unimplemented. The next step after this workflow merges is one separately
    authorized local characterization run. Phase 0 stays open.

38. **Offline Macro characterization *completion* CLI added (2026-08-27,
    code/tests/synthetic fixtures/docs only — no connector, OpenAI, DuckDB,
    agent, orchestration, migration, schema, dependency, or `.env` change; no
    live request; no real database access; no agent-behaviour change; no commit
    or push).** This is *further partial* progress on P0-7 (see
    [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) and
    [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md)).
    **Phase 0 remains open; P0-7 is not met; no real characterization has been
    performed.**

    Item 37 added the pure `complete_macro_characterization` step but no command
    to drive it. This adds only that missing offline command.

    - **`macro_characterization_input.py` — new `MacroAdjudicationInput`
      contract.** Carries **only** the scaffold's deterministic `run_id`
      (`evalrun-<24 hex>`, never a provider response id) and a bounded list
      (1–500) of completed human `CitationAdjudication` records. `extra="forbid"`;
      **no** field for a credential, URL, provider response ID, raw provider
      payload, filesystem path, raw evidence text, model reasoning, or
      unrestricted metadata. New `adjudication_input_to_json_str` /
      `adjudication_input_from_json_str` / `read_adjudication_input` helpers
      mirror the existing ones (sorted-key byte-stable JSON; symlink / size /
      sanitized-error rules on read).
    - **`scripts/complete_macro_characterization.py` — offline completion CLI.**
      Explicit `--record`, `--adjudications`, and `--output` paths;
      **dry-run / validate by default** (prints sanitized counts and
      classification / finding-severity tallies only, writes nothing); `--write`
      required to serialize the completed `EvaluationRunRecord` via
      `evaluation.serialization.write_record` (no overwrite, no symlink, atomic,
      no directory creation). It reads the scaffold with the existing safe
      serialization, validates the adjudication input, **requires the
      adjudication `run_id` to match the scaffold `run_id`** (fixed
      `run_id_mismatch` marker otherwise), and calls
      `complete_macro_characterization` (a missing / duplicate / unexpected pair
      → fixed `completion_failed` marker). **All three resolved paths must be
      strictly inside gitignored `data/evaluations/local/`** — a
      repository-root, tracked (`docs/` / `tests/` / `fixtures/`),
      `data/evaluations/`-itself, outside-repository, `..`-traversal, or
      symlink-escape path is refused with a fixed marker. It **records human
      decisions only**: it never generates, recommends, or second-guesses a
      classification, there is no LLM judge, the four classifications
      (`supported` / `partially_supported` / `unsupported` /
      `unable_to_determine`) are preserved exactly as recorded, and it never
      prints a reviewer note, claim ID, citation ID, path, or record text.
      **Completion is not validation.** Every error is a fixed sanitized marker
      (`record_path_refused`, `adjudications_path_refused`, `output_path_refused`,
      `invalid_record`, `invalid_adjudications`, `run_id_mismatch`,
      `completion_failed`, `write_failed`, `unexpected_error`).
    - **Synthetic fixtures.** `COMPLETE_ADJUDICATION_INPUT` (and the byte-stable
      `macro_adjudication_input_complete.json`) — the completed adjudications for
      `COMPLETE_MULTI_CLAIM`, one per expected pair, exercising all four
      classifications. Hand-authored; `synthetic-reviewer`, the timestamps,
      classifications, and reasons are placeholders and judge no real agent
      output (see `fixtures/PROVENANCE.md`).
    - **Tests** —
      `market_intelligence/tests/test_complete_macro_characterization_cli.py`
      plus additions to `test_evaluation_macro_characterization.py` cover a
      complete characterization, every classification preserved in the tally,
      `run_id` mismatch, missing / duplicate / unexpected pairs, dry-run writing
      nothing, the `--write` output round trip, no overwrite / no directory
      creation, every path refusal and symlink escape for all three paths, that
      no sanitized output or error leaks an ID / note / path / record text, the
      strict input-contract bounds, and (AST scan of the script + a
      fresh-interpreter import check) zero connector / DB / OpenAI / agent-runtime
      imports. Full suite: `python -m pytest` → **2,415 passed, 4 skipped**
      (2,419 collected); `python -m ruff check .` clean; `git diff --check`
      clean.

    **What this establishes and what it does not.** It establishes that an
    offline command to complete one Macro characterization scaffold with human
    citation adjudications exists and behaves as documented against synthetic
    inputs. **It does not evaluate any real agent output**, makes no live
    request, and performs no real database access. Citation-support adjudication
    of real output, lexical-overlap triage, the abstention matrix, cross-agent
    consistency, repeatability, the same transcription check for the Market
    Evidence Agent and News Analyst, and — the actual closure condition — a
    recorded first characterization of a real agent covering every claim all
    remain unimplemented. The next step after this merges is one separately
    authorized local, human-reviewed characterization run. Phase 0 stays open.

    **Pre-merge review fix (2026-08-27, same branch, not yet merged):**
    `complete_macro_characterization` (added in item 37) validated only the
    adjudication pair set, so it would also "complete" a non-Macro
    `EvaluationRunRecord` or re-complete a record that already carried
    adjudications. It now first requires an **unadjudicated Macro scaffold**
    (`agent == macro_analyst` and an empty `adjudications` list); a non-Macro or
    already-adjudicated record raises a fixed, sanitized
    `MacroCharacterizationError` that reproduces no record content, identifier,
    classification, reviewer note, or path, and the completion CLI maps it to its
    existing `completion_failed` marker (no CLI code change). Focused synthetic
    tests were added (non-Macro record refused, already-adjudicated record
    refused, a valid empty Macro scaffold still completes, and the new errors
    leak no IDs or content). Full suite after the fix: `python -m pytest` →
    **2,420 passed, 4 skipped** (2,424 collected); `python -m ruff check .`
    clean; `git diff --check` clean. No other behavioural change.

39. **First real, human-reviewed offline Macro Analyst characterization
    completed and recorded — P0-7 met, Phase 0 closed (2026-08-28).** Two
    separately authorized offline sessions, using only the existing item 37–38
    workflows and CLIs. No code, test, configuration, schema, migration,
    dependency, or `.gitignore` change; no OpenAI / Alpaca / FRED / network /
    connector / agent request; the local DuckDB database was read **read-only**
    solely to resolve the cited evidence records; MacroAnalyst was **not**
    rerun; no commit or push. All real artifacts live only under gitignored
    `data/evaluations/local/` and are not committed.

    - **Session 1 — build.** The accepted seven-claim Macro report (one claim
      per Core Macro Basket series — `FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`,
      `UNRATE`, `INDPRO`, `GDPC1`; each
      claim a two-observation comparison citing two evidence IDs, 14 IDs total)
      was resolved against the real `macro_observations` / `macro_series_metadata`
      rows: all 14 cited evidence IDs exist. Only the sanitized evidence facts
      the transcription evaluator needs (citation handle, series ID, observation
      date, `Decimal` value, frequency word, units) were carried into a
      `MacroCharacterizationInput`; no URL, provider payload, credential,
      database path, response ID, revision window, or model reasoning was
      copied. `scripts/characterize_macro_report.py` was run dry-run
      (validate) then `--write`, producing the scaffold `EvaluationRunRecord`:
      agent `macro_analyst`, 7 claims evaluated, 14 expected pairs,
      `adjudications` empty, findings = one `scope_boundary` `info` finding +
      seven `factual_transcription` findings (all seven exact matches), rubric
      intentionally incomplete (human adjudications pending). A human-review
      worksheet with all 14 pairs (blank classification / reason / note) was
      produced for the reviewer; no classification was generated, recommended,
      inferred, or prefilled.
    - **Session 2 — completion.** The human reviewer adjudicated all 14
      pairs as `partially_supported` / `claim_scope_exceeds_single_observation`
      with no reviewer note. A `MacroAdjudicationInput` (scaffold `run_id` + 14
      `CitationAdjudication` records, claim/citation IDs copied exactly from the
      scaffold) was validated through the repository contract, then
      `scripts/complete_macro_characterization.py` was run dry-run then
      `--write`. Sanitized result: `expected_pair_count` 14,
      `adjudication_count` 14, classification tally `supported: 0` /
      `partially_supported: 14` / `unsupported: 0` / `unable_to_determine: 0`,
      finding-severity tally `info: 8` / `warning: 0` / `failure: 0`,
      `rubric_complete: true`, and the fixed "completion is not validation"
      notice. The completed `EvaluationRunRecord` read back with the repository
      serializer: agent `macro_analyst`, `run_id` unchanged from the scaffold,
      exactly 14 adjudications (every expected pair once — none missing,
      duplicated, or unexpected), all 14 classifications/reasons matching the
      human decision, rubric complete, and all eight original findings
      preserved byte-for-byte.
    - **Why every pair is `partially_supported`.** Each Macro claim is a
      comparison between two stored observations and depends on both cited
      observation IDs together; each individual claim/citation pair contains
      only one of the two observations, so no single pair on its own supports
      the whole comparative claim — hence `partially_supported` with
      `claim_scope_exceeds_single_observation` for all 14.

    **What this establishes and what it does not.** It establishes that the
    required agent-evaluation methodology (deterministic factual-transcription
    harness + human citation-support rubric) exists, is documented, and has been
    exercised and recorded once against real Macro Analyst output covering every
    claim, with all findings preserved — satisfying P0-7 and closing Phase 0. It
    is **not** a claim that the Macro Analyst or any other agent is validated,
    universally accurate, repeatable, or profitable, and only the Macro Analyst
    has been characterized. The same transcription check and citation-support
    adjudication for the Market Evidence Agent and News Analyst, the
    lexical-overlap triage, the abstention matrix, cross-agent consistency, and
    repeatability studies remain future work and do not reopen Phase 0. See
    [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) (criterion P0-7) and
    [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md).

40. **Phase 1 step b — read-only SPY option-chain snapshot connector and
    local storage added (2026-08-28, code/tests/docs only; no live request,
    no stored data; revised 2026-09-01 after pre-commit review — tighter
    request ceilings and a normalized run-level provenance table; revised
    again 2026-09-14 after pre-commit review found a batch-membership
    provenance issue — see below).** The first Phase 1 implementation step
    from
    [docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md),
    built to the existing connector/storage patterns.

    - **Connector.** `market_intelligence/data_connectors/alpaca_options_chain.py`
      (`AlpacaOptionsChainClient`) talks only to `https://data.alpaca.markets`
      and only to `GET /v1beta1/options/snapshots/{underlying}`. It does
      **not** touch `paper-api.alpaca.markets`, the option-contract/trading
      API, or any account / order / position / portfolio / exercise /
      execution surface, and it does not connect to Robinhood. Every call
      requires a fully validated, bounded `OptionChainRequest`: **SPY only**
      (the underlying is a fixed constant, never caller-supplied); an
      **explicit `opra` or `indicative` feed** (no default, no fallback); an
      `expiration_date_gte`/`_lte` window and a `strike_price_gte`/`_lte`
      window (rejecting missing, reversed, or unreasonably wide ranges); an
      optional `call`/`put` filter; and `limit` (1–1000), `max_pages`, and
      `max_total_contracts` all bounded by local constants. **Conservative
      Phase 1 hard ceilings** (safety ceilings for the first SPY intraday
      milestone, not contract-selection rules — callers must still supply
      explicit ranges): expiration span ≤ 60 calendar days, strike-window
      width ≤ $500, per-page limit ≤ 1,000 (the provider's own maximum), max
      pages ≤ 10, max total contracts ≤ 5,000. Pagination
      follows `next_page_token` deterministically and stops at the configured
      bounds, detecting loops and repeated/invalid tokens; there is **no
      automatic retry**. Each snapshot's OCC-style contract symbol is parsed
      into underlying / expiration / type / strike and cross-checked against
      the request filters (mismatches, malformed symbols, and duplicates are
      rejected). Normalization keeps only the fields the endpoint supplies —
      contract symbol, underlying, expiration, type, strike, feed; latest
      quote timestamp and bid/ask price and size; latest trade timestamp,
      price and size; implied volatility; delta/gamma/theta/vega/rho where
      supplied; and a UTC retrieval timestamp. Missing optional
      quote/trade/Greek fields stay **null — never zero**. Negative prices /
      implied volatility / gamma / vega and non-finite numbers are rejected;
      delta / theta / rho may be negative. Errors are sanitized to a fixed
      category — no response body, headers, URL, query parameters,
      credentials, or contract payload.
    - **Storage (revised 2026-09-14 to a three-table design).** Migration
      `0009`
      (`market_intelligence/storage/migrations/0009_create_option_chain_snapshots.sql`)
      adds three tables in a normalized run/row/membership design:
      `option_chain_snapshot_batches` records exactly **one row per
      successfully stored chain retrieval — including a retrieval that
      returned zero contracts** (ingestion-run id as primary key; provider;
      underlying; requested feed; requested expiration/strike window;
      requested option type; retrieved-at UTC; `contract_count`, `0`
      included; `outcome` `succeeded`/`skipped_empty`); `option_chain_snapshots`
      stores one row per **immutable** normalized contract observation,
      whose `ingestion_run_id` records only the run that first inserted it
      and is never reassigned by a later re-observation; and
      `option_chain_snapshot_batch_items` is the normalized batch-membership
      table (one row per `(ingestion_run_id, provider, underlying, feed,
      contract_symbol, retrieved_at)`, written for every contract a
      non-empty successful batch actually returned). **Why the third table:**
      the original two-table design let a re-stored identical snapshot's
      `ingestion_run_id` be updated in place, silently moving it into the
      newer batch while the original batch's `contract_count` kept reporting
      the old count; batch membership is now read exclusively from
      `option_chain_snapshot_batch_items`, so `contract_count` for every
      non-empty successful batch always equals that batch's membership-row
      count, and one immutable observation may legitimately be referenced by
      more than one batch's membership rows.
      `market_intelligence/storage/option_chain_snapshot_repository.py`
      (`OptionChainSnapshotRepository`) stores normalized rows only (never
      raw JSON) — the batch row, every snapshot row, every batch-item row,
      and the ingestion-run status update all in one transaction, mirroring
      `BarRepository`; an empty chain is accepted (an explicit `retrieved_at`
      is required for it) and still writes its batch row (zero snapshot and
      batch-item rows). Idempotency and immutability:
      `option_chain_snapshot_batches` keys on `ingestion_run_id` (always an
      insert); `option_chain_snapshots` keys on
      `(provider, underlying, feed, contract_symbol, retrieved_at)` — a
      repeat of the same connector result never mutates or relinks that row
      (at most refreshing non-historical `last_seen_at` bookkeeping) while
      still inserting a fresh batch-item row for the new batch; a conflicting
      value aborts the whole batch (no new batch row, no partial snapshot
      rows, no partial batch-item rows; `content_conflict`), and a later
      ingestion run (new `retrieved_at`) writes a new batch row and new
      observation/batch-item rows. **`feed` is part of the snapshot and
      batch-item identity, so OPRA and indicative observations are never
      merged.** The repository also re-normalizes every field of a supplied
      `OptionChainRequest` through `normalize_option_chain_request` and
      rejects it unless it matches its own canonical form, so a
      hand-constructed request cannot bypass the connector's request
      ceilings — tested with forged requests above every ceiling.
    - **Phase 1 request ceilings (2026-09-01 pre-commit review; safety
      ceilings, not contract-selection rules — callers must still supply
      explicit ranges).** Tightened for the first SPY intraday milestone:
      expiration span ≤ 60 calendar days (was 400), strike-window width ≤
      $500 (was $5,000), max pages ≤ 10 (was 50), max total contracts ≤
      5,000 (was 20,000); per-page limit stays ≤ 1,000 (the provider's own
      maximum). Enforced in `alpaca_options_chain.py` before any HTTP
      request is built, with boundary tests proving the ceiling value
      passes and one above it fails.
    - **CLI.** `scripts/ingest_alpaca_options_chain.py` is dry-run-first: the
      default run validates arguments and provider configuration and prints
      the sanitized request bounds and intended feed, making **no request and
      writing nothing**; `--execute` performs exactly one bounded ingestion
      (paginated GETs as required, one transactional write, no retry) and
      prints only sanitized counts/status, including `batch outcome`
      (`succeeded`/`skipped_empty`) — never a contract symbol, quote, Greek,
      response text, URL, or credential.
    - **Existing behaviour.** Adding migration `0009` bumped the latest
      schema version; `scripts/ingest_core_macro_basket.py`'s
      `REQUIRED_SCHEMA_VERSION` was moved `0008` → `0009` accordingly (it
      still requires the database at the latest version), and the
      schema-version assertions in the database / bar / macro-observation /
      news repository tests were updated `0008` → `0009`. Bars, news, FRED,
      orchestration, agent, and evaluation behaviour is otherwise unchanged.
    - **Not done.** No live option-chain request has occurred, no real
      option data has been stored, and **migration `0009` has not been
      applied to the real local database** (which remains at schema version
      `0008`). `indicative`-feed data may be delayed or modified by the
      provider and must not be described as live OPRA data. **Open interest
      is not supplied by this snapshot endpoint and is not produced,
      inferred, or stored — it is unavailable in this milestone**, on any of
      the three tables. No contract selector, strategy agent, recommendation,
      ranking, alert, dashboard, or execution surface exists. Phase 1 remains
      in progress and nothing is validated or profitable. Verification
      (2026-09-14, after the batch-membership fix):
      `python -m pytest` (2,626 passed, 4 skipped), `python -m ruff check .`
      (clean), `git diff --check` (clean).

    - **Superseded (2026-09-14, later the same day): first authorized live
      ingestion and read-only structural audit.** The "Not done" paragraph
      above — no live request, no stored data, migration `0009` unapplied —
      was accurate earlier on 2026-09-14 and is preserved here as an honest,
      time-scoped diagnostic record; it is **not** retracted, only superseded.
      Migration `0009` was applied to the real local database (backed up
      beforehand) and a subsequent read-only health check reported schema
      version `0009` (9 migrations applied), `healthy=True`.
      `scripts/ingest_alpaca_options_chain.py` was then run once, live, with
      `--execute`: provider `alpaca`, underlying `SPY`, requested feed
      `indicative` (explicitly not OPRA), one expiration (2026-09-18),
      strikes 740–790. The run received and inserted 102 contracts (51 calls,
      51 puts), wrote one `option_chain_snapshot_batches` row with
      `outcome=succeeded`, and the corresponding `ingestion_runs` row was
      recorded `succeeded`; exactly one request was made, with no retry.

      A separate, subsequent **read-only structural audit** (no network,
      Alpaca, OpenAI, FRED, agent, or provider request; no rerun of
      ingestion; no database modification; no contract symbol, price, quote,
      Greek, timestamp, or raw row printed) confirmed, as aggregate counts
      only: the batch's provider/underlying/requested-feed/requested-window/
      contract-count/outcome all match the ingestion result above; exactly
      102 batch-membership rows and 102 corresponding immutable snapshot
      rows, zero duplicate membership identities, every membership resolving
      to exactly one snapshot, zero orphan memberships, and
      `contract_count` equal to the membership count; 51 calls / 51 puts, 1
      distinct expiration, strikes 740.000000–790.000000, zero contracts
      outside the requested bounds, zero malformed OCC symbols; quote and
      trade data present for all 102, implied volatility and each of
      delta/gamma/theta/vega/rho present for 78 and unavailable (`NULL`) for
      24; zero negative or non-finite numeric values, zero crossed quotes
      (bid > ask), zero feed mismatches between the batch and its snapshots,
      and exactly one distinct retrieval timestamp.

      **Caveats (binding, not relaxed by the above):** `indicative`-feed data
      may be delayed or modified by the provider and must never be described
      as live OPRA data. This audit establishes internal structural
      consistency only — it does **not** establish pricing accuracy,
      timeliness, usefulness, predictive edge, strategy validity, or
      profitability. A future deterministic contract selector that requires
      implied volatility or Greeks must reject or omit the 24 contracts with
      missing values — never substitute zero. Open interest remains
      unavailable from this endpoint and is not produced, inferred, or
      stored. Only one batch exists, so recurring reliability of the
      connector/storage path is not established. No recommendation,
      selector, agent, alert, dashboard, or execution action occurred as
      part of this ingestion or audit — the deterministic contract selector
      and the Options Strategy Agent still do not exist. **Next planned
      implementation step: the deterministic SPY intraday feature/regime
      engine (workflow step c in
      [docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md)),
      not the deterministic contract selector or the Options Strategy
      Agent.**

## Next Planned Work

This is the forward plan. It replaces the historical content now under
"Completed Work Log" above.

1. **Documentation reconciliation (this work).** Bring the repository's
   top-level status documents into line with what actually exists: correct
   `README.md`'s "Setup Status" and "Project Independence"; add this
   at-a-glance summary and split the completed-work log from the forward plan;
   add `docs/PHASE_0_EXIT.md`; reconcile `DATA_CATALOG.md` (migration `0008`,
   the seven-series macro metadata, and honest per-dataset records for the
   SPY bars, SPY news, and macro observations/metadata); correct the stale
   top-level status line in `docs/OPENAI_PROVIDER_BOUNDARY.md`; and clarify
   `SOURCE_POLICY.md`'s distinction between retained source provenance and
   bounded model-facing evidence excerpts. Documentation only — no code,
   tests, configuration, migrations, fixtures, or provider behavior change.

2. **Repeatable agent evaluation methodology (harness + human rubric) — DONE;
   P0-7 and Phase 0 closed 2026-08-28.** The deterministic
   factual-transcription harness (Macro Analyst) and the human citation-support
   rubric both exist and are documented, and the **first real, human-reviewed
   offline Macro Analyst characterization was completed and recorded on
   2026-08-28** using the existing build and completion CLIs, covering every
   claim. Sanitized totals: agent `macro_analyst`; 14 expected claim/citation
   pairs; 14 human adjudications (one per pair — none missing, duplicated, or
   unexpected); `rubric_complete: true`; classification tally `supported: 0` /
   `partially_supported: 14` / `unsupported: 0` / `unable_to_determine: 0`; all
   14 reasons `claim_scope_exceeds_single_observation`; finding tally `info: 8`
   / `warning: 0` / `failure: 0` (seven factual-transcription findings were
   exact matches; the one scope-boundary information finding was preserved). All
   14 pairs are `partially_supported` because each Macro claim is a
   two-observation comparison depending on two cited observations while each
   individual claim/citation pair carries only one observation. Every
   adjudication was the human reviewer's; no LLM judge generated, recommended,
   or changed a classification; no live request or Macro Analyst rerun
   occurred; the real artifacts remain gitignored under
   `data/evaluations/local/` and are not committed. **This closes Phase 0 — it
   does not mean the Macro Analyst or any other agent is validated, universally
   accurate, repeatable, or profitable, and only the Macro Analyst has been
   characterized.** See Completed Work Log item 39,
   [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) (criterion P0-7), and
   [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md).
   **Remaining, non-blocking future work** (does not reopen Phase 0): the same
   deterministic factual-transcription check and human citation-support
   adjudication for the Market Evidence Agent and News Analyst, the
   lexical-overlap triage, the abstention matrix, cross-agent consistency, and
   the repeatability characterization.

   **Historical progress record (2026-08-26 to 2026-08-27): the safe, offline
   foundation was built first** —
   `market_intelligence/evaluation/` provides strict Pydantic v2 contracts
   (the fixed agent / severity / citation-classification / citation-reason /
   finding-category enums, one evaluation finding, one human citation
   adjudication, one evaluation-run record with a locally, deterministically
   generated run ID), a deterministic rubric-completeness validator, pure
   JSON helpers plus a symlink-refusing / no-overwrite / atomic / bounded
   local file round trip, and six synthetic fixtures (see item 35), **plus**
   the first deterministic factual-transcription check — Macro Analyst only,
   over synthetic claim/evidence inputs
   (`macro_factual_transcription.py`): it recognizes only the two exact
   controlled Macro Analyst statement forms, verifies series ID / observation
   date / `Decimal` value / frequency wording / units-when-stated / previous
   observation / comparison direction, and emits one `info` (match, *not* a
   validation) / `failure` (mismatch, broad category only) / `warning`
   (unrecognized wording → human review) finding (see item 36), **plus** the
   offline Macro characterization workflow (items 37–38): a strict local input
   contract, a pure builder that runs the transcription check for every claim
   and emits one pending human-adjudication template per expected claim/citation
   pair (never pre-classifying citation support), a pure completion step
   (refuses missing/duplicate/unexpected pairs; completion is not validation), a
   dry-run-first offline build CLI (`scripts/characterize_macro_report.py`), and
   a dry-run-first offline completion CLI
   (`scripts/complete_macro_characterization.py`: strict `MacroAdjudicationInput`
   contract, `run_id`-match required, records human decisions only with no LLM
   judge, sanitized tally-only output, completion is not validation) — every
   `--write` output and all completion-CLI input paths must resolve strictly
   inside gitignored `data/evaluations/local/`. See items 35, 36, 37, and 38 in
   the Completed Work Log. As of 2026-08-27, no PR had yet evaluated any real
   agent output and no real characterization had been performed; none added
   lexical-overlap scoring, a `--record` flag, or an LLM judge. The remaining
   closure step — one separately authorized local, human-reviewed
   characterization run using the workflow from items 37–38, covering every
   claim, with findings / failures / `unable_to_determine` results preserved —
   was completed on 2026-08-28 (see item 39 and the summary at the top of this
   item). See
   [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md), criterion P0-7, and
   [docs/AGENT_EVALUATION_HARNESS.md](docs/AGENT_EVALUATION_HARNESS.md). Each of
   the three agents had exactly one accepted live run before this methodology
   existed. The method is offline-first and characterizes the trust gap rather
   than certifying the agents. In scope: deterministic factual-transcription
   checks where the
   claim structure permits them (does a claim's stated value/date/unit match
   the cited stored evidence?); a human citation-support rubric that
   adjudicates every claim in a characterization as `supported` /
   `partially_supported` / `unsupported` / `unable_to_determine` with a reason
   from a fixed list, with lexical overlap retained as advisory triage only
   (not proof of support) and an LLM judge never the sole or gating reviewer;
   an abstention matrix across all three agents; cross-agent consistency on
   shared stored facts; and a repeatability characterization. Data-handling
   boundary: synthetic/redacted fixtures may be committed; real evidence
   packages, article text, URLs, credentials, response IDs, and full live
   model outputs must not be committed; any future live evaluation capture
   must be local, sanitized, and gitignored unless separately reviewed.
   Closing Phase 0 on this item requires the harness and rubric to exist, at
   least one first characterization to have been completed and recorded
   (covering every claim, with findings, failures, and `unable_to_determine`
   results preserved), and does not require every agent to pass or imply the
   agents are validated. It makes no claim of automated proof of semantic
   correctness.

3. **Only after evaluation evidence exists**, reconsider — as separate,
   individually reviewed milestones — a combined market-intelligence brief /
   agent orchestrator, agent output persistence, and dashboard work. None of
   these should begin before there is recorded evidence about agent
   trustworthiness, because each one either composes or presents agent
   output.

4. **Phase 1 / later — operational ingestion capabilities.** Scheduling,
   unattended operation, automatic stale-lock recovery, freshness monitoring,
   and any recurring hands-off ingestion are explicitly deferred to a later
   operational phase. They are **not** part of Phase 0 (which requires only
   the deterministic, manually invoked ingestion path that now exists) and
   they are not gated on the evaluation harness — they are simply not yet in
   scope. The current ingestion path must not be described as production-ready,
   continuously reliable, or fully validated.

5. **Phase 1 — options-focused SPY decision-support workflow (design
   recorded, not started).** The agreed design is in
   [docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md): an
   automated, evidence-based SPY options decision-support workflow built from
   deterministic evidence gathering (extending the three existing agents), a
   deterministic intraday regime/setup engine (trend continuation / VWAP
   mean reversion / range / event-driven / indeterminate) that also produces
   one bounded scenario-horizon bucket from a fixed enum (or `indeterminate`
   when no horizon is supported), a deterministic contract-eligibility
   selector (expiration, strike, delta, Greeks, IV, liquidity, bid-ask
   spread, and that validated upstream horizon) that runs before the agent,
   and then a bounded directional Options Strategy Agent that consumes only
   the upstream validated structured outputs plus the selector's eligible
   contract set (never a raw option chain), may reference but cannot invent,
   extend, or override the supplied horizon, and can return `no_trade`. The
   three existing agents stay non-directional; the Options Strategy Agent is
   a separately bounded directional decision-support agent. Distance from
   VWAP is "statistical intraday extension from VWAP", never intrinsic
   over/undervaluation, and a large extension alone is not a trade signal
   (now also in [DECISION_RULES.md](DECISION_RULES.md)).
   Evaluation backtests the underlying setup first, then options performance
   separately with realistic bid-ask/slippage, then a 30–50 session shadow
   test; one good run is not validation. Phase 0 closed on 2026-08-28, so this
   work has begun. Implementation order: **step b, read-only SPY
   option-chain ingestion and local storage** (its own reviewed connector,
   sanitized, no execution surface) — built in code and tests only as of
   2026-08-28, ceilings tightened and provenance normalized to two tables
   2026-09-01, normalized to a three-table design 2026-09-14 (Completed Work
   Log item 40): `AlpacaOptionsChainClient` (expiration ≤ 60 days, strike
   width ≤ $500, ≤ 10 pages, ≤ 5,000 contracts), migration `0009`
   (`option_chain_snapshot_batches` + `option_chain_snapshots` +
   `option_chain_snapshot_batch_items`), `OptionChainSnapshotRepository`, and
   a dry-run-first ingestion script — **is now DONE, including a live run**:
   migration `0009` was applied to the real database (2026-09-14, healthy at
   schema version `0009`), and one authorized live, bounded, `indicative`-feed
   ingestion (SPY, expiration 2026-09-18, strikes 740–790) succeeded with 102
   contracts received/inserted (51 calls, 51 puts), followed by a read-only
   structural audit the same day that found zero integrity or malformed-data
   issues (see Completed Work Log item 40 for full sanitized detail and
   caveats). Open interest remains unavailable and unstored, and OPRA vs.
   indicative observations remain kept separate. **Next: step c, the
   deterministic SPY intraday feature/regime engine** — no model — then the
   VWAP-extension/reversion hypothesis test (step d), deterministic contract
   filtering (step e), the Options Strategy Agent (step f), shadow evaluation
   (step g), and only then any alerts/dashboard (step h). **Apart from step
   b's connector and storage (now live-exercised once), no options code,
   schema, or agent exists, and no options component is validated** — one
   successful ingestion and one structural audit do not validate pricing
   accuracy, timeliness, usefulness, predictive edge, strategy validity, or
   profitability. No brokerage integration, automatic execution, or Robinhood
   automation is authorized.

Manual-only trading is preserved throughout. The three existing analysis
agents (Market Evidence, News, Macro) remain non-directional; the Phase 1
Options Strategy Agent is a separately bounded directional decision-support
agent (still single-turn, no-tools, gated, with `no_trade` always
available). No brokerage integration or execution is planned.

## Notes

- This file should be updated as phases progress. Treat entries here as
  ground truth over any assumptions embedded in code comments, prompts, or
  prior conversations.
