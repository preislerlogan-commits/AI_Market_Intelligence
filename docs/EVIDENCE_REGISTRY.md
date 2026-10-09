# Evidence Registry — Initial Content

**Status: REVIEWED DESIGN — accepted by the merge that introduced these
documents (2026-09-28). DESIGN ONLY. Registry version label:
`registry-draft-0`** (a fixed identifier for this prose version, not a
review status). This is the human-readable initial
content of the evidence registry designed in
[EVIDENCE_ENVELOPE_DESIGN.md](EVIDENCE_ENVELOPE_DESIGN.md).

- **Merge accepts the design only.** The merge accepts this design but
  authorizes no implementation, migration, provider access, collection,
  dashboard, assistant, ranking, notification, Options Strategy Agent, or
  trading.
- **Only the in-memory registry contract exists.** The offline core
  (`market_intelligence/evidence/registry.py`, PROJECT_STATE item 59)
  defines the registry's shape. No registry file, populated registry,
  envelope adapter, or evidence table exists, and this draft's entries are
  not loaded anywhere. "Implemented" below describes only the
  **existing module** behind a producer, never an envelope emitter.
- **Implementation status (2026-10-08; PROJECT_STATE item 65).** The first
  offline producer adapters now exist in
  `market_intelligence/evidence_adapters/`, for these producers:
  `alpaca_market_bars`, `alpaca_market_snapshot`, `alpaca_news`,
  `fred_macro_observations`, `fred_macro_series_metadata`,
  `system_clock_health` and `spy_regime_engine`.
  - They are pure functions exercised only by synthetic tests with a
    labelled test registry, and are connected to no ingestion, storage or
    registry.
  - This note supersedes the "no envelope adapter exists" wording in this
    document.
  - It changes no registry rule, producer authority, schema or
    authorization table. No registry file exists, and no producer is
    registered or activated.
- **Nothing is authorized.** An entry marked `future` or `unauthorized`
  describes a boundary, not a plan of record. Listing a producer does not
  authorize building it.
- **Governing documents win.** DECISION_RULES.md, SOURCE_POLICY.md, frozen
  research protocols and PROJECT_STATE.md authorization boundaries override
  anything here.
- **Consumer behavior** is in [EVIDENCE_CONSUMER_RULES.md](EVIDENCE_CONSUMER_RULES.md).

## 1. Registry versioning

- **This prose version has no hash.** `registry-draft-0` is prose. The first
  machine-readable registry, once authorized, receives an
  `evr1_<sha256>` identity over its canonical content (design §L).
- **Every change is a new version.** Adding, removing or editing any
  producer, policy, payload schema, scenario definition, rubric,
  authorization record or consumer grant creates a new registry version.
  Old versions are retained; every bundle records the version it used.
- **Unknown means refused.** A producer, version, payload schema or policy
  not present in the registry version in force is refused by the store and
  the bundle builder.

## 2. Producers

### 2.1 Summary

"Module status" is the state of the existing code, not of an envelope
adapter (none exists).

| Producer ID | Module status | Method | Directional authority | Kinds it may emit | Machine-decision eligible |
|---|---|---|---|---|---|
| `alpaca_market_bars` | implemented (stored ingestion) | deterministic | none | fact, missing, stale | yes |
| `alpaca_market_snapshot` | implemented (connector only, not stored) | deterministic | none | fact, missing, stale | yes |
| `alpaca_option_chain_snapshot` | implemented (stored ingestion) | deterministic | none | fact, missing, stale | yes |
| `alpaca_news` | implemented (stored ingestion) | deterministic | none | fact, missing, stale | yes (metadata only) |
| `fred_macro_observations` | implemented (stored ingestion) | deterministic | none | fact, missing, stale | yes |
| `fred_macro_series_metadata` | implemented (stored ingestion) | deterministic | none | fact, missing | yes |
| `market_context_builder` | implemented | deterministic | none | calculation, missing, stale | yes |
| `session_quality_builder` | implemented | deterministic | none | calculation, missing | yes |
| `news_evidence_snapshot` | implemented | deterministic | none | calculation, missing, stale | yes |
| `macro_evidence_snapshot` | implemented | deterministic | none | calculation, missing, stale | yes |
| `spy_regime_engine` | implemented (offline) | deterministic | none | calculation, missing | yes |
| `spy_vwap_reversion_evaluator` | implemented (offline research tool) | deterministic | none | calculation, missing | **no** (research context only) |
| `spy_vwap_confirmation_result` | result recorded; publisher not implemented | deterministic | none | research result | yes, as context only |
| `deterministic_contract_selector` | implemented (offline) | deterministic | none | calculation, missing | yes |
| `market_evidence_agent` | implemented | model | none | inference, missing | no |
| `news_agent` | implemented (`NewsAnalyst`) | model | none | inference, missing | no |
| `macro_agent` | implemented (`MacroAnalyst`) | model | none | inference, missing | no |
| `system_clock_health` | **future**, unimplemented | deterministic | none | fact, missing | yes |
| `evidence_conflict_detector` | **future**, unimplemented | deterministic | none | conflicts only | n/a |
| `evidence_bundle_builder` | **future**, unimplemented | deterministic | none | bundles only | n/a |
| `future_trend_day_engine` | **future**, unimplemented, **unauthorized**; no research exists | deterministic (intended) | none | none until authorized | no |
| `future_explanation_layer` | **future**, unimplemented, **unauthorized** | model or hybrid | none | inference, missing | no |
| `future_scenario_setup_component` | **future**, unimplemented, **unauthorized** | hybrid (intended) | none | inference, missing | no |
| `future_contract_ranker` | **future**, unimplemented, **unauthorized** | hybrid (intended) | none | inference, missing | no |
| `future_dashboard` | **future**, unimplemented, **unauthorized** | n/a | none | none (consumer); manual conflict acknowledgements only | no |
| `future_notification_layer` | **future**, unimplemented, **unauthorized** | deterministic | none | dispatch records only (future payload) | no |

"Machine-decision eligible" is the registry's derivation input (design
§B.3). Missing and stale status items are eligible so that a ranker sees
absence, but they never satisfy a requirement. `current_inference` is not
eligible today: machine-decision use of inference is default-deny, and no
inference-input authorization exists (design §H.2).

### 2.2 Common failure modes and availability states

Every implemented producer may report the six availability states in design
§F.1. The reason codes below are **existing** fixed strings in the code
where one exists; an adapter must reuse them, not rename them.

### 2.3 Producer entries

Each entry lists:
- identity: ID, display name, status, method, directional authority;
- emissions: kinds, subjects, required inputs, freshness policy, cadence;
- failure modes and reason codes;
- consumers: allowed consumers and forbidden consumers or actions;
- code: module location and schema versions.

Display names are for the UI only and never enter an identity.

#### `alpaca_market_bars`
- **Display name:** Alpaca SPY 5-minute bars. **Status:** implemented as
  read-only connector plus stored ingestion (`market_bars`, migration
  `0005`); manual runs only, no scheduler. **Method:** deterministic.
  **Directional authority:** none.
- **Kinds:** `confirmed_fact` (one stored bar, as provider-reported),
  `missing_evidence`, `stale_evidence`.
- **Subjects:** `instrument:us_equity:SPY`, `market_session`,
  `provider_health` (`provider:alpaca:market_data`).
- **Required inputs:** stored `market_bars` rows (primary key
  `provider, symbol, timeframe, feed, adjustment, currency, bar_timestamp`)
  and their `ingestion_run_id`.
- **Freshness policy:** `fp.bars_stored_snapshot.v1` (briefing and
  research use); `fp.bars_live_intraday.v0` (unset) for live use.
- **Cadence:** per manual ingestion run.
- **Failure modes:** missing credentials, 401/403, 429, 5xx, timeout,
  invalid response, empty result, off-grid bar.
- **Reason codes:** `bars_missing`, `bars_stale`, `missing_data`,
  `unexpected_or_duplicate_timestamps`, `symbol_mismatch`,
  `holdout_restricted`.
- **Uncertainty codes:** `iex_partial_volume` (IEX is a single venue, not
  consolidated volume).
- **Allowed consumers:** bundle builder (all purposes), regime engine inputs.
- **Forbidden:** any SPY bar for a session from 2026-09-23 through
  2026-12-04 before the holdout is recorded (design §O); any order or
  account endpoint.
- **Module:** `market_intelligence/data_connectors/alpaca_bars.py`,
  `market_intelligence/storage/bar_repository.py`.
- **Schema versions:** `evidence-envelope-1`; payload `market_bar_fact.v1`.

#### `alpaca_market_snapshot`
- **Display name:** Alpaca SPY market snapshot. **Status:** connector
  implemented (`AlpacaMarketDataClient.get_snapshot`), used by the
  selector-capture coordinator; **not stored**. **Method:** deterministic.
  **Directional authority:** none.
- **Kinds:** `confirmed_fact` (latest trade/quote as provider-reported),
  `missing_evidence`, `stale_evidence`.
- **Subjects:** SPY, `provider_health`.
- **Required inputs:** one sanitized provider response, referenced by a
  `provider_request` source reference (no URL, headers or body).
- **Freshness policy:** `fp.snapshot_live.v0` (unset).
- **Cadence:** on request only.
- **Failure modes:** as for bars.
- **Allowed consumers:** bundle builder (`live_market_state`,
  `contract_review`); the selector's input.
- **Forbidden:** holdout-window SPY prices (design §O); storing raw bodies.
- **Module:** `market_intelligence/data_connectors/alpaca_market_data.py`.
- **Schema versions:** `evidence-envelope-1`; payload
  `market_snapshot_fact.v1`.
- **Gap:** without storage, the fact's source reference is a provider
  request only. It cannot be recomputed later. A storage decision is open
  (design §Q.3).

#### `alpaca_option_chain_snapshot`
- **Display name:** Alpaca SPY option-chain snapshot. **Status:**
  implemented, stored (migration `0009`), one authorized `indicative`
  ingestion. **Method:** deterministic. **Directional authority:** none.
- **Kinds:** `confirmed_fact` (one contract observation, as
  provider-reported), `missing_evidence`, `stale_evidence`.
- **Subjects:** `option_contract`, SPY, `provider_health`.
- **Required inputs:** `option_chain_snapshot_batches`,
  `option_chain_snapshots`, `option_chain_snapshot_batch_items` rows.
- **Freshness policy:** `fp.option_chain_selector.v1`.
- **Cadence:** manual ingestion only.
- **Failure modes:** as for bars, plus page or contract ceilings reached.
- **Uncertainty codes:** `indicative_feed` for every `indicative` item. It
  is a delayed/derived feed, not licensed live OPRA data.
- **Allowed consumers:** the selector's input; bundle builder
  (`contract_review`, only for contracts the selector returned);
  `audit_export` (the full evaluated batch).
- **Forbidden:** presenting `indicative` data as live OPRA; any use as an
  eligibility decision (only the selector decides eligibility); rendering
  batch contracts the selector did not return as individual contracts.
- **Module:** `market_intelligence/data_connectors/alpaca_options_chain.py`,
  `market_intelligence/storage/option_chain_snapshot_repository.py`.
- **Schema versions:** `evidence-envelope-1`; payload
  `option_quote_fact.v1`.

#### `alpaca_news`
- **Display name:** Alpaca news (SPY). **Status:** implemented, stored
  (`news_articles`, migration `0004`). **Method:** deterministic.
  **Directional authority:** none.
- **Kinds:** `confirmed_fact`, `missing_evidence`, `stale_evidence`.
- **What the fact covers.** It covers the **publication metadata only**:
  provider, article ID, `created_at`/`updated_at`, `retrieved_at`, source
  name and related symbols. The headline and summary are provider content.
  They are carried only as display-only payload fields, with uncertainty
  code `provider_reported_unverified`. **The article's claim is never a
  fact.**
- **Subjects:** `news_item`, SPY, `provider_health`.
- **Required inputs:** stored `news_articles` rows (primary key
  `provider, provider_article_id`).
- **Freshness policy:** `fp.news_snapshot.v1`.
- **Cadence:** manual ingestion only.
- **Failure modes:** as for bars.
- **Reason codes:** `news_missing`, `news_stale`,
  `future_timestamp_detected`, `no_articles_returned`, `symbol_mismatch`.
- **Allowed consumers:** bundle builder; `news_agent` inputs.
- **Forbidden:** headline or summary text as a machine-decision input; the
  article URL in any model-facing excerpt (SOURCE_POLICY).
- **Module:** `market_intelligence/data_connectors/alpaca_news.py`,
  `market_intelligence/storage/news_repository.py`.
- **Schema versions:** `evidence-envelope-1`; payload
  `news_publication_fact.v1`.

#### `fred_macro_observations`
- **Display name:** FRED macro observations. **Status:** implemented,
  stored (`macro_observations`, migration `0006`, vintage-aware key).
  **Method:** deterministic. **Directional authority:** none.
- **Kinds:** `confirmed_fact`, `missing_evidence`, `stale_evidence`.
- **Subjects:** `macro_series:fred:<SERIES_ID>` (the seven core-basket
  series).
- **Required inputs:** `macro_observations` rows (primary key
  `provider, series_id, observation_date, realtime_start, realtime_end`).
- **Freshness policy:** `fp.macro_monthly.v1` or `fp.macro_quarterly.v1`,
  chosen by the stored `frequency_short`. An unrecognized frequency
  evaluates to `unknown`.
- **Cadence:** manual ingestion only.
- **Uncertainty codes:** `vintage_revisable`.
- **Revision:** a new vintage for the same observation date is a
  `source_revision` of the earlier item.
- **Allowed consumers:** bundle builder; `macro_agent` inputs.
- **Forbidden:** treating observation date as release time. Release times
  need a scheduled-catalyst source, and none exists.
- **Module:** `market_intelligence/data_connectors/fred_macro_data.py`,
  `market_intelligence/storage/macro_observation_repository.py`.
- **Schema versions:** `evidence-envelope-1`; payload
  `macro_observation_fact.v1`.

#### `fred_macro_series_metadata`
- **Display name:** FRED series metadata. **Status:** implemented, stored
  (`macro_series_metadata`, migration `0008`; refreshed in place, no
  history). **Method:** deterministic. **Directional authority:** none.
- **Kinds:** `confirmed_fact`, `missing_evidence`.
- **Subjects:** `macro_series`.
- **Freshness policy:** `fp.reference_metadata.v1`.
- **Gap:** the table keeps no metadata history, so a fact item must record
  its `retrieved_at_utc`. An earlier metadata state cannot be recovered
  from storage.
- **Module:** `market_intelligence/storage/macro_series_metadata_repository.py`.
- **Schema versions:** `evidence-envelope-1`; payload
  `macro_series_metadata_fact.v1`.

#### `market_context_builder`, `session_quality_builder`, `news_evidence_snapshot`, `macro_evidence_snapshot`
- **Display names:** market context snapshot; session quality report; news
  evidence snapshot; macro evidence snapshot.
- **Status:** implemented, deterministic, read-only over stored data.
  **Directional authority:** none.
- **Kinds:** `deterministic_calculation` (coverage, counts, staleness
  flags, latest-change comparisons), `missing_evidence`, and
  `stale_evidence` (from their existing `stale` flags).
- **Subjects:** SPY, `market_session`, `macro_series`, `news_item`.
- **Required inputs:** the stored rows each builder already reads.
- **Freshness policy:** inherits from the underlying fact policy.
- **Existing citation keys.** The existing `macro_<16 hex>` and
  `news_<16 hex>` evidence IDs, and keys such as `bars_latest`, are
  **snapshot-local citation keys**, not envelope IDs. An adapter maps each
  one to the `evi1_` item of the same stored row and keeps the old key as a
  payload field `legacy_citation_key`.
- **Allowed consumers:** bundle builder; the three agents' inputs.
- **Forbidden:** changing the existing thresholds by adapter configuration.
- **Modules:** `market_intelligence/market_features/market_context.py`,
  `session_quality.py`, `news_evidence.py`, `macro_evidence.py`.
- **Schema versions:** `evidence-envelope-1`; payloads
  `market_context_calc.v1`, `session_quality_calc.v1`,
  `news_evidence_calc.v1`, `macro_evidence_calc.v1`.

#### `spy_regime_engine`
- **Display name:** SPY intraday regime engine. **Status:** implemented,
  offline only, synthetic tests (`spy-regime-engine-1`). **Method:**
  deterministic. **Directional authority:** none.
- **Kinds:** `deterministic_calculation` (`RegimeFeatures`,
  `RegimeClassificationResult`), `missing_evidence`.
- **What the regime label is.** `trend_continuation`,
  `vwap_mean_reversion`, `range`, `event_driven` and `indeterminate` are
  **calculated regime labels**. They are not researched setups, not a
  qualified trend or reversion signal, and not a direction.
- **Subjects:** SPY, `market_session`.
- **Required inputs:** stored bars for the session; optional prior-day
  levels and volume baseline; `catalyst_state` and `breadth_state`.
  Catalyst and breadth have **no source today**, so they are
  `unknown`/`unavailable`, never fabricated.
- **Freshness policy:** inherits the bars policy through
  `source_observed_at_utc`.
- **Cadence:** on demand.
- **Failure modes:** validation refusal (off-grid, out-of-session, too many
  bars); an incomplete session forces `indeterminate`.
- **Allowed consumers:** bundle builder; the selector's input.
- **Forbidden:** presenting a regime label as a trade signal
  (DECISION_RULES "A large VWAP extension alone is not a trade signal").
- **Modules:** `market_intelligence/market_features/spy_regime_contracts.py`,
  `spy_regime_features.py`, `spy_regime_classifier.py`.
- **Schema versions:** `evidence-envelope-1`; payloads
  `spy_regime_features.v1`, `spy_regime_classification.v1` (both wrap
  `spy-regime-engine-1`).

#### `spy_vwap_reversion_evaluator`
- **Display name:** SPY VWAP-reversion evaluator (research tool).
  **Status:** implemented, offline (`spy-vwap-reversion-evaluation-1`),
  used for the recorded discovery and confirmation studies. **Method:**
  deterministic. **Directional authority:** none.
- **Kinds:** `deterministic_calculation` (per-session evaluation records
  over **historical, recorded** sessions), `missing_evidence`.
- **Subjects:** `research_study:spy_vwap_reversion`, `market_session`.
- **Machine-decision eligible:** **no**. Its outputs are research
  context. Live setup qualification belongs to the shadow recorder, whose
  Stage 3 onward is unauthorized.
- **Freshness policy:** `fp.research_timeless.v1`.
- **Allowed consumers:** dashboard research panel; assistant (as research
  context, cited).
- **Forbidden:**
  - any holdout-window session before the holdout is recorded;
  - re-running with changed parameters and presenting the result as the
    frozen study;
  - use as a live signal.
- **Module:** `market_intelligence/evaluation/spy_vwap_reversion_evaluator.py`.
- **Schema versions:** `evidence-envelope-1`; payload
  `spy_vwap_reversion_evaluation.v1`.

#### `spy_vwap_confirmation_result`
- **Display name:** SPY VWAP-reversion confirmation result. **Status:**
  result recorded and verified (2026-09-25). The **publisher** (the adapter
  that turns it into an item) is not implemented. **Method:**
  deterministic (copies recorded values). **Directional authority:** none.
- **Kinds:** `historical_research_result` only.
- **Research reference (exact values from the recorded result):**
  - study `spy_vwap_reversion`; `result_kind = confirmation_result`;
    `result_schema_version = spy-vwap-reversion-confirmation-1`;
  - `result_sha256 = c8d838f441005a4122cda93f4572e8f1b6eca429b130ed545c1fdcb9ad69877b`;
  - protocol commits `f77d30f8e6e90a6b77eeca11fd11c3da9c9540c1` (base) then
    `1ff654de6dbbd54aeecb471010d1a612ca5abda6` (C1);
  - analysis code commit `cd587f132b914208ef95c892a74bea68f4bd35ef`
    (operator-supplied, as the result records);
  - `primary_label = supported_for_further_shadow_research`;
    `secondary_labels = [below_horizon_dependent]`;
  - `research_status = shadow_pending`;
  - `holdout_state = sealed`;
  - fixed caveats include `underlying_setup_only_no_options_or_pnl`,
    `research_result_not_validation`, and
    `not_a_recommendation_or_trading_action`.
- **Subjects:** `research:spy_vwap_reversion`.
- **Freshness policy:** `fp.research_timeless.v1`.
- **Machine-decision eligible:** as research context only. It can never by
  itself qualify a live setup.
- **Allowed consumers:** all consumers, as cited research context with its
  caveats.
- **Forbidden:**
  - describing it as validation, profitability, an options edge or a
    recommendation;
  - omitting its caveats;
  - exposing any holdout value before the holdout result is recorded.
- **Source:** [SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md](SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md)
  (read-only; unchanged by this design).
- **Schema versions:** `evidence-envelope-1`; payload
  `spy_vwap_confirmation_result.v1`.

#### `deterministic_contract_selector`
- **Display name:** Deterministic SPY contract-eligibility selector.
  **Status:** implemented, offline (`spy-contract-selector-1`). **Method:**
  deterministic. **Directional authority:** none.
- **Kinds:** `deterministic_calculation` (the `ContractSelectorResult`:
  status, eligible and research-only sets, rejection counts),
  `missing_evidence`.
- **Operational eligibility requires OPRA.** `indicative` data yields
  `research_only` at most.
- **Subjects:** `option_contract`, SPY.
- **Required inputs:** a regime classification item, an underlying-price
  fact, and option-chain facts.
- **Configuration identity:** `cfg1_` over `SelectorConfigSnapshot`.
- **Freshness policy:** `fp.option_chain_selector.v1` (its own
  `max_snapshot_age_seconds`, `max_quote_age_seconds` and gap limits).
- **Reason codes:** the existing `RejectionReason` values and the
  `SelectorStatus` values.
- **Allowed consumers:** contract-review UI; bundle builder
  (`contract_review`, `setup_detail`).
- **Exclusive, permanent rights.** It is the **only** producer that may
  classify contracts: returning them as `eligible`, returning them as
  `research_only`, or rejecting them under a bounded `RejectionReason`,
  exposed only as aggregate `rejection_counts` (design §H.3). There is no
  per-contract "ineligible" record. Its returned sets are the only source of
  contract candidates. No authorization record can transfer or share this
  right.
- **Forbidden:**
  - any AI ranking over contracts today (a future, separately authorized
    `future_contract_ranker` could only rank within the returned eligible
    set and, separately, the returned research-only set; the
    Options Strategy Agent is unauthorized);
  - presenting `research_only` as eligible;
  - any order path.
- **Modules:** `market_intelligence/contract_selection/contracts.py`,
  `selector.py`.
- **Schema versions:** `evidence-envelope-1`; payload
  `contract_selector_result.v1`.

#### `market_evidence_agent`, `news_agent`, `macro_agent`
- **Display names:** Market Evidence Agent; News Analyst; Macro Analyst.
- **Status:** implemented; bounded single-turn OpenAI structured output;
  one accepted live run each; only the Macro Analyst has been characterized.
  None is validated.
- **Method:** model (behind deterministic preflight and post-response
  validators). **Directional authority:** **none**. The reports' fixed
  `directional_assessment = "not_performed"` and
  `trade_recommendation = "not_performed"` are preserved.
- **Kinds:**
  - `current_inference`: one item per observation, event claim or macro
    claim;
  - `missing_evidence`: for an abstention or a failure, with the existing
    preflight reason codes and sanitized error categories.
- **Model self-reports stay in the payload.** The agents' model-reported
  `evidence_quality` is kept in the payload as
  `model_reported_evidence_quality`. It does **not** set
  `quality.data_quality`, which comes only from deterministic preflight
  flags.
- **Subjects:** SPY, `market_session`, `news_item`, `macro_series`.
- **Required inputs:** the corresponding snapshot calculations and facts.
  Every cited citation key becomes a parent item ID.
- **Freshness policy:** `fp.inference_follows_inputs.v1`.
- **Cadence:** manual runs only.
- **Failure modes:** the existing categories `invalid_input`, `refusal`,
  `incomplete`, `citation_invalid`, `policy_violation` and
  `unexpected_error`, plus the News and Macro specific categories.
- **Allowed consumers:** dashboard, assistant (as labelled inference).
- **Forbidden:**
  - scenario relations of any kind;
  - trade direction, bias, recommendation, or options content;
  - machine-decision use, unless a future inference-input authorization
    record grants it (none exists; design §H.2);
  - any model-generated value relabelled as a fact or calculation.
- **Modules:** `market_intelligence/agents/market_evidence_agent.py`,
  `news_analyst.py`, `macro_analyst.py`, `non_directional_output_policy.py`.
- **Schema versions:** `evidence-envelope-1`; payloads
  `market_evidence_observation.v1`, `news_event_claim.v1`,
  `macro_claim.v1`.

#### `system_clock_health` (future)
- **Status:** unimplemented. **Kinds:** `confirmed_fact` (offset and last
  sync), `missing_evidence`.
- **Why it matters.** Every freshness evaluation that is not `timeless`
  needs a clock-health fact (design §F.4). Until this producer exists, no
  bundle can evaluate anything as `current`.
- **Policy:** `clock_policy.v0` (unset).

#### `evidence_conflict_detector` and `evidence_bundle_builder` (future)
- **Status:** unimplemented.
- **Conflict detector:** emits `EvidenceConflict` records under registered
  rules.
- **Bundle builder:** the **only** component that evaluates freshness and
  builds `EvidenceBundleManifest`s.
- **Forbidden (both):** choosing a winner in a conflict; emitting evidence
  items.

#### `future_trend_day_engine`
- **Status:** unimplemented and **unauthorized**. No trend-day research
  exists. It needs its own discovery, preregistration, confirmation, and
  shadow process (vision §4).
- **Kinds:** none in this registry version.
- **Forbidden:** any trend signal before that process. The regime engine's
  `trend_continuation` label is not a substitute.

#### `future_explanation_layer`
- **Status:** unimplemented and **unauthorized**. **Method:** model or
  hybrid. **Directional authority:** none.
- **Kinds:** `current_inference` (explanations over a bundle, with
  citations), `missing_evidence`.
- **Allowed consumers:** dashboard, assistant.
- **Forbidden:**
  - machine-decision use;
  - contract suggestions;
  - mutating or relabelling any cited item;
  - probabilities;
  - scenario relations (unless a later authorization record is added).

#### `future_scenario_setup_component`
- **Status:** unimplemented and **unauthorized**. **Directional authority:**
  none (a directional grant would need its own authorization record).
- **Kinds:** `current_inference` (scenario support or contradiction, setup
  ranking, setup interpretation), `missing_evidence`.
- **May consume inference** only under design §H.2, and may affect a setup's
  lifecycle or qualification only where that setup's own reviewed research
  and decision rules explicitly permit inference (design §H.4).
- **Forbidden:** changing frozen research results, deterministic VWAP
  eligibility, regime calculations, or contract eligibility.

#### `future_contract_ranker`
- **Status:** unimplemented and **unauthorized**.
- **Kinds:** `current_inference` (a ranking within the returned `eligible`
  set or, separately, the returned `research_only` set, with explanations),
  `missing_evidence`.
- **Required inputs:** a `contract_selector_result` item; optionally
  selected inference under design §H.2.
- **Must:** rank only contracts in the selector's returned sets; rank the
  `eligible` and `research_only` sets separately; carry each contract's
  returned set unchanged; leave rejection counts and reasons untouched.
- **Forbidden:** changing, suppressing or bypassing selector outcomes or
  requirements (design §H.3); ranking, listing or reconstructing rejected
  contracts; introducing contracts; mixing the two returned sets; any order
  path.

#### `future_dashboard`
- **Status:** unimplemented and **unauthorized**. It is a consumer; it
  emits no evidence items.
- **Manual acknowledgements.** A future, separately authorized feature may
  let the user record a manual `acknowledged_unresolvable` conflict
  resolution.

#### `future_notification_layer`
- **Status:** unimplemented and **unauthorized**. SMS is new external
  infrastructure that needs its own security and credential review.
- **Kinds:** a future dispatch record (payload
  `notification_dispatch_record.v0`, **unregistered**), not evidence.
- **Forbidden:** recommendation-style alerts; any alert not caused by a
  registered notification-event rule.

## 3. Freshness policies

**Existing values are copied, not changed.** The values below come from
existing code constants. The live intraday policies are **deliberately
unset** until reviewed. An unset policy evaluates to `unknown`, which gates
like `stale`.

| Policy ID | Kinds | Observed timestamp | Current until | Aging interval | Stale boundary | Off-hours | Missing timestamp | Clock requirement | Source of values |
|---|---|---|---|---|---|---|---|---|---|
| `fp.bars_stored_snapshot.v1` | fact, calculation, stale | latest bar timestamp | 72 h | none | > 72 h | `elapsed_wall_clock` | `unknown` | `clock_policy.v0` | `BARS_STALE_AFTER` (market_context.py) |
| `fp.bars_live_intraday.v0` | fact, calculation | bar close (bar start + 5 min) | **unset** | **unset** | **unset** | `freeze_at_session_close` (needs a calendar) | `unknown` | `clock_policy.v0` | none: needs review |
| `fp.snapshot_live.v0` | fact | provider quote/trade timestamp | **unset** | **unset** | **unset** | `freeze_at_session_close` | `unknown` | `clock_policy.v0` | none: needs review |
| `fp.news_snapshot.v1` | fact, calculation, stale | latest publication time | 7 days | none | > 7 days | `elapsed_wall_clock` | `unknown`; future > 5 min → `unknown` | `clock_policy.v0` | `NEWS_STALE_AFTER`, `FUTURE_TIMESTAMP_TOLERANCE` |
| `fp.macro_monthly.v1` | fact, calculation, stale | `observation_date` | 90 days | none | > 90 days | `elapsed_wall_clock` | `unknown`; future > 1 day → `unknown` | `clock_policy.v0` | `STALE_AFTER_DAYS` |
| `fp.macro_quarterly.v1` | fact, calculation, stale | `observation_date` | 180 days | none | > 180 days | `elapsed_wall_clock` | as monthly | `clock_policy.v0` | `STALE_AFTER_DAYS_QUARTERLY` |
| `fp.option_chain_selector.v1` | fact, calculation | batch `retrieved_at` and quote time | 300 s | none | > 300 s | `elapsed_wall_clock` | `unknown` | `clock_policy.v0` | `SelectorConfig` defaults (`max_snapshot_age_seconds`, `max_quote_age_seconds`) |
| `fp.reference_metadata.v1` | fact | `retrieved_at` | **unset** | **unset** | **unset** | `not_applicable` | `unknown` | `clock_policy.v0` | none: needs review |
| `fp.inference_follows_inputs.v1` | inference | earliest required input's observed time | = the strictest input policy | = that policy | = that policy | from that policy | `unknown` | `clock_policy.v0` | derived |
| `fp.research_timeless.v1` | research result, research-tool calculation | none | `timeless` | — | — | `not_applicable` | n/a | none | fixed |

**Clock policy.** `clock_policy.v0` is **unset**. A candidate for review is
|offset| ≤ 1,000 ms and last sync ≤ 60 min, the rule already used by the
separate latency-test plan. The product would adopt it independently; this
registry does not couple to, or change, that frozen plan.

**Existing thresholds were built for snapshots.** The 72-hour, 7-day and
90/180-day thresholds are generous by design. They mark something "not yet
flagged stale" for research and briefing snapshots. They are **not** live
intraday currency rules, and `live_market_state` bundles must not use them.

## 4. Payload schemas (initial names)

**Reserved names only.** Each payload schema will be a strict, bounded
Pydantic model, defined when implementation is authorized. Payloads that
wrap an existing contract carry that contract's own schema version
unchanged.

| Payload schema | Wraps / contains | Display-only fields |
|---|---|---|
| `market_bar_fact.v1` | one stored bar | none |
| `market_snapshot_fact.v1` | sanitized latest trade/quote | none |
| `option_quote_fact.v1` | one stored contract observation | none |
| `news_publication_fact.v1` | article metadata | `headline`, `summary` (≤ 500 characters each) |
| `macro_observation_fact.v1` / `macro_series_metadata_fact.v1` | one stored row | `title` |
| `market_context_calc.v1`, `session_quality_calc.v1`, `news_evidence_calc.v1`, `macro_evidence_calc.v1` | the existing snapshot sections | none |
| `spy_regime_features.v1`, `spy_regime_classification.v1` | `spy-regime-engine-1` outputs | none |
| `spy_vwap_reversion_evaluation.v1` | `spy-vwap-reversion-evaluation-1` | none |
| `spy_vwap_confirmation_result.v1` | the labels, per-horizon estimates and intervals as `ExactRational`, sample counts, fixed notes of `spy-vwap-reversion-confirmation-1` | none |
| `contract_selector_result.v1` | `spy-contract-selector-1` | none |
| `market_evidence_observation.v1`, `news_event_claim.v1`, `macro_claim.v1` | one agent observation or claim with its bounded enums | `statement` / `claim_summary` / `conditional_mechanism` |
| `clock_health_fact.v1`, `provider_health_fact.v1` | future | none |
| `missing_evidence.v1` | `checked_at_utc` (the as-of), `expected_producer_id`, `expected_subject_id`, `query_sha256`, `expected_component`, `reason_code`; no value field | none |
| `stale_evidence.v1` | status as-of, stale item ID (or `expected_producer_id` and `query_sha256`), `last_observed_at_utc`, `policy_id` | none |

## 5. Scenario definitions, rubrics, and authorization records

**None are registered.** The three tables below are empty by design.

| Registry table | Entries | Effect |
|---|---|---|
| Scenario definitions | **none** | No scenario relation can be emitted |
| Strength rubrics | **none** | Every item uses `confidence_not_available` |
| Authorization records (directional authority, calibrated probability, notification events) | **none** | No producer may emit directional relations, probabilities, or notification events |
| Inference-input authorizations: scenario/setup | **none** | No scenario or setup component may consume `current_inference`. May be separately authorized in future (design §H.2, §H.4) |
| Inference-input authorizations: contract ranking | **none** | No contract ranker may consume `current_inference`. A future grant could cover **ranking only within the returned eligible set and, separately, the returned research-only set** (design §H.3) |
| Contract-eligibility authority | **not a grantable record type** | Contract eligibility is permanently the deterministic selector's; no entry can ever be added here |

Adding an entry to any of these tables is a reviewed registry change, and it
requires the explicit authorization it describes. Adding one never follows
automatically from this design.

## 6. Consumer grants

| Consumer | Purposes | Permissions | Machine-decision mode |
|---|---|---|---|
| `dashboard` (future) | `premarket_briefing`, `live_market_state`, `setup_detail`, `contract_review` | read facts, calculations, research, inferences, conflicts | no |
| `assistant` (future) | `assistant_question_context` | read facts, calculations, research, inferences, conflicts; emit inference (as `future_explanation_layer`) | no |
| `setup_ranker` (future, unauthorized) | `setup_detail` | read facts, calculations, research, conflicts; emit calculation; read inferences and emit inference only with a future §H.2 grant (none exists) | yes |
| `contract_ranker` (future, unauthorized) | `contract_review` | read selector results and their input facts; emit inference (a within-group ranking) only with a future authorization (none exists) | yes |
| `contract_review_ui` (future) | `contract_review` | read facts, calculations, research, conflicts | no |
| `notification_layer` (future, unauthorized) | `notification_decision` | read facts, calculations, conflicts; emit notification event (requires an authorization record; none exists) | yes |
| `audit_export` (future) | any | all reads including superseded | no |
