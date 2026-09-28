# Shared Evidence Envelope — Contract Design

**Status: DESIGN ONLY, drafted and revised before review, awaiting review
(2026-09-28).** This
document proposes the versioned contract family that every evidence
producer would emit and every consumer would read. It is roadmap workstream B,
priority 2 ([PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md)).

- **Nothing is implemented or authorized.** No code, Pydantic model,
  migration, table, registry file, or adapter exists. Implementation needs a
  separate, explicit authorization (§Q.5).
- **Governing documents win.** [DECISION_RULES.md](../DECISION_RULES.md),
  [SOURCE_POLICY.md](../SOURCE_POLICY.md), frozen research protocols, and
  recorded authorization boundaries in [PROJECT_STATE.md](../PROJECT_STATE.md)
  always govern. Where this design seems to conflict with them, they win.
- **Not part of the frozen VWAP protocol.** The frozen SPY VWAP documents are
  unchanged. This design only describes how their *recorded* results may be
  *referenced*. It changes no preregistration, confirmation, shadow-protocol,
  recorder or latency-test rule.
- **Companion documents.**
  [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md) lists producers, freshness
  policies and authorization records.
  [EVIDENCE_CONSUMER_RULES.md](EVIDENCE_CONSUMER_RULES.md) sets what each
  consumer may and may not do.

**The envelope is an evidence-transport contract, not a trading signal.** No
field, state, or combination of fields in this family means "buy", "sell",
"enter", or "this will happen".

---

## A. Core guarantees

Each guarantee is enforced by a named mechanism, not by convention.

| # | Guarantee | Enforcing mechanism |
|---|---|---|
| A1 | No unlabelled mixture of fact and inference | Every item has exactly one `evidence_kind` (§C). One item never mixes kinds; a consumer output that combines items cites each one (consumer rules §1, §7) |
| A2 | No model-generated value presented as a deterministic calculation | `confirmed_fact` and `deterministic_calculation` require `production_path = deterministic`, no `model_run` source reference, and no `current_inference` ancestor (§C.3 lineage rule) |
| A3 | No research result without a result/protocol reference | `historical_research_result` requires an `EvidenceResearchReference` with result hash, schema version and protocol commits (§B.10) |
| A4 | No stale evidence presented as current | Items carry no freshness state; freshness is evaluated only by the bundle builder from the registry policy (§F), is monotone in age, and can never be raised by a consumer |
| A5 | No unsupported directional claim | `scenario_relations` need a registry authorization record; none exists, so no producer can emit one today (§H). Existing agents' `directional_assessment = "not_performed"` is preserved |
| A6 | No fabricated probability, confidence or precision | `calibrated_probability` must be null unless the registry holds a calibrated-model authorization with validation evidence; graded strength needs a rubric ID; otherwise `confidence_not_available` (§G) |
| A7 | No unrestricted free text used for machine decisions | Machine-decision inputs are typed `payload` fields validated against a registered payload schema. `display_text` is bounded, policy-filtered, and never machine-decision eligible |
| A8 | No evidence item silently overriding another | Disagreement becomes an `EvidenceConflict` with no winner field (§I). Revisions link by `supersedes`; nothing is deleted (§M) |
| A9 | No contract suggestion outside the deterministic selector | Contract classification (the returned `eligible` and `research_only` sets and the aggregate rejection counts) is accepted only from `deterministic_contract_selector`, permanently (§H.3). Consumers display individual returned contracts only. A future contract ranking is accepted only from a separately authorized ranker, only within the returned `eligible` set and, separately, the returned `research_only` set |
| A10 | No consumer changing an upstream record | Records are frozen and append-only. Consumers can only emit *new* items with their own producer identity and parent links |
| A11 | Manual final trading decisions | No contract, state or consumer in this family places, routes, or schedules an order. No `action`/`order`/`execute` field exists. DECISION_RULES execution boundaries are unchanged |

---

## B. Versioned contract family `evidence-envelope-1`

### B.0 Conventions for every contract

- **Pydantic v2, strict.** `ConfigDict(extra="forbid", frozen=True, strict=True)`,
  matching the existing regime, selector and confirmation contracts.
- **Versioning.** Every *top-level stored record* (`EvidenceEnvelope`,
  `EvidenceItem`, `EvidenceConflict`, `EvidenceBundleManifest`) carries
  `schema_version: Literal["evidence-envelope-1"]`. Nested contracts have no
  version field of their own; they are versioned by the family of the record
  that contains them. Any change to any contract in the family is a new family
  version (`evidence-envelope-2`); readers refuse versions they do not know.
- **Mutability.** Every contract is immutable once constructed. Stored records
  are append-only (§O).
- **Bounded enums.** Every field that can change behavior is a `StrEnum` or a
  `Literal`, never an open string.
- **No display names in identities.** Human-readable names live in the
  registry and the UI only.

#### Shared primitive types

| Type | Definition |
|---|---|
| `Sha256Hex` | `^[0-9a-f]{64}$` |
| `CommitSha` | `^[0-9a-f]{40}$` (full SHA only) |
| `UtcTimestamp` | timezone-aware, converted to UTC; range 2000-01-01 → 2100-01-01; serialized `YYYY-MM-DDTHH:MM:SS.ffffffZ` (always six fractional digits) |
| `SessionDate` | `YYYY-MM-DD` calendar date |
| `Token` | `^[a-z][a-z0-9_]{0,63}$` (codes, never prose) |
| `ProducerId` | `Token`, 3–64 characters, must exist in the registry version in force |
| `VersionLabel` | `^[a-z0-9][a-z0-9._-]{0,63}$` |
| `CanonicalDecimal` | a string in plain notation: `^-?(0|[1-9][0-9]{0,17})(\.[0-9]{0,11}[1-9])?$` (no exponent, no trailing fractional zeros, no `-0`, no NaN/Infinity) |
| `ExactRational` | `{numerator: int, denominator: int}`, reduced, `denominator ≥ 1` (the existing confirmation-result pattern) |
| `ItemId` | `^evi1_[0-9a-f]{64}$` |
| `ConflictId` | `^evc1_[0-9a-f]{64}$` |
| `BundleId` | `^evb1_[0-9a-f]{64}$` |
| `EnvelopeId` | `^eve1_[0-9a-f]{64}$` |
| `RegistryVersionId` | `^evr1_[0-9a-f]{64}$` |
| `ConfigIdentity` | `^cfg1_[0-9a-f]{64}$` (SHA-256 of the producer's canonical configuration) or the literal `cfg_none` |
| `DisplayText` | 1–400 characters, Unicode NFC, no control characters, must pass the non-directional content policy (`find_prohibited_content_category` returns `None`) for every producer without directional authority |

#### Canonical serialization (used for every hash)

1. Build the model's JSON-mode dump.
2. **Remove the excluded fields** listed for that contract: only the
   record's own ID and operational metadata that does not change its
   meaning (§L.2). Every substantive field, **including every substantive
   temporal field**, stays in (§L.1). Nothing else is removed.
3. Keep every remaining field, including nulls. An absent optional field is
   serialized as `null`, never omitted.
4. Encode decimals as `CanonicalDecimal` strings, rationals as
   `ExactRational` objects, timestamps as `UtcTimestamp` strings, and enums
   as their values.
5. Sort every **set-like list** (subjects after the primary one, source
   references, parent IDs, involved conflict items, bundle entries, tokens) by
   its own canonical JSON, and reject duplicates rather than silently
   removing them. **Ordered lists** (`display_text`, rationale sequences
   copied from an existing engine) keep their order; each such list is marked
   "ordered" below.
6. `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
   allow_nan=False)`, then UTF-8 encode.
7. SHA-256 of those bytes.

This hashing form is deliberately compact. The existing file writers
(`indent=2`, `sort_keys=True`, trailing newline) stay the *display/storage*
form; they are not the identity form.

### B.1 `EvidenceKind` (enum, frozen)

`confirmed_fact`, `deterministic_calculation`, `historical_research_result`,
`current_inference`, `missing_evidence`, `stale_evidence`. See §C.

### B.2 `EvidenceEnvelope` — one producer run

The transport unit a producer emits for one run. Items are the stored unit;
the envelope records that they were produced together.

- **Schema version:** `evidence-envelope-1`.
- **Mutability:** immutable; stored append-only.
- **Source:** the producer's adapter.
- **Machine-decision eligible:** no (its items may be).
- **Prohibited content:** credentials, headers, raw provider bodies, raw
  exception text, prompts, hidden model reasoning, database paths.

| Field | Type | Null | Validation / limit | In identity |
|---|---|---|---|---|
| `schema_version` | `Literal["evidence-envelope-1"]` | no | exact | yes |
| `envelope_id` | `EnvelopeId` | no | recomputed and must match | no (it is the identity) |
| `registry_version_id` | `RegistryVersionId` | no | must be a known registry version | yes |
| `producer_id` | `ProducerId` | no | registered; status `implemented` | yes |
| `producer_version` | `VersionLabel` | no | listed in the registry entry | yes |
| `producer_run_key` | `Sha256Hex` | no | SHA-256 of canonical `{producer_id, producer_version, configuration_identity, run_as_of_utc, input_digest}`; a retry of the same run yields the same key | yes |
| `run_as_of_utc` | `UtcTimestamp` | no | not after `emitted_at_utc` | yes |
| `availability` | `EvidenceAvailability` | no | run-level state (§F.1) | yes |
| `items` | `list[EvidenceItem]` | no | 0–512 items; sorted by `item_id`; each item's producer fields equal the envelope's | yes (via item IDs) |
| `emitted_at_utc` | `UtcTimestamp` | no | wall-clock emission time | **no** |

Rules:
- `availability.state = available` requires ≥ 1 item that is not
  `missing_evidence`.
- `unavailable`, `unsupported`, `refused` or `error` requires that every item
  is `missing_evidence`, and there is at least one. **Absence is always
  explicit**, never an empty envelope.
- Canonical envelope size ≤ 4 MiB.

### B.3 `EvidenceItem` — one atomic piece of evidence

- **Schema version:** `evidence-envelope-1`.
- **Mutability:** immutable forever. Corrections are new items (§M).
- **Source:** exactly one registered producer.
- **Machine-decision eligible:** only when `machine_decision_eligible = true`,
  which is *derived* (below), never chosen by the producer.
- **Prohibited content:** everything prohibited for the envelope, plus trade
  actions, order instructions, price targets, and any probability not
  authorized under §G.

| Field | Type | Null | Validation / limit | In identity |
|---|---|---|---|---|
| `schema_version` | `Literal["evidence-envelope-1"]` | no | exact | yes |
| `item_id` | `ItemId` | no | recomputed and must match | no (it is the identity) |
| `evidence_kind` | `EvidenceKind` | no | exactly one; must be allowed for this producer in the registry | yes |
| `subjects` | `list[EvidenceSubject]` | no | 1–8; `subjects[0]` is the primary subject; the rest sorted, unique; subject types must be allowed for this producer | yes |
| `effective_at_utc` | `UtcTimestamp` | no | the substantive **as-of** instant: the instant the evidence describes, or the as-of state a calculation or inference was computed over (§B.3b) | yes |
| `temporal_scope` | `EvidenceTemporalScope` | no | §B.3a; must agree with the payload's own timestamps | yes |
| `payload_schema_id` | `VersionLabel` | no | registered payload schema allowed for this producer and kind (registry §4) | yes |
| `payload` | object | no | validated against `payload_schema_id`; canonical size ≤ 32 KiB (≤ 64 KiB for `historical_research_result`); no free-text field unless the payload schema marks it display-only | yes |
| `provenance` | `EvidenceProvenance` | no | §B.5 | yes, except the purely operational `generated_at_utc` (§L.2) |
| `freshness_policy_id` | `VersionLabel` | no | registered policy for this producer and kind | yes |
| `availability` | `EvidenceAvailability` | no | item-level (§F.1) | yes |
| `quality` | `EvidenceQualityProfile` | no | §G; consistency rules with kind | yes |
| `research_reference` | `EvidenceResearchReference` | yes | **required** for `historical_research_result`; optional for a `deterministic_calculation` that applies frozen research criteria; forbidden otherwise | yes |
| `inference_basis` | `EvidenceInferenceBasis` | yes | **required** for `current_inference`; forbidden otherwise | yes |
| `scenario_relations` | `list[EvidenceScenarioRelation]` | no | 0–8; allowed only on `current_inference` from a producer holding a matching authorization (§H); empty today for every producer | yes |
| `display_text` | `list[DisplayText]` | no | 0–4, **ordered**; never machine-decision input; policy-filtered | yes |
| `revision` | `EvidenceRevision` | no | §M | yes |
| `machine_decision_eligible` | `bool` | no | must equal the registry derivation (below); a mismatch is refused | yes |

**Machine-decision eligibility derivation.** `true` only if all hold:
- the registry version in force at emission (recorded on the envelope)
  marks this producer × kind × payload schema as eligible;
- `evidence_kind` is `confirmed_fact`, `deterministic_calculation`,
  `historical_research_result`, `missing_evidence` or `stale_evidence`
  (missing and stale status items are eligible so that rankers *see*
  absence, but they never satisfy a requirement, §N.3); **or** it is
  `current_inference` covered by an inference-input authorization record
  (§H.2);
- `provenance.code_tree_clean = true`.

**Default deny for inference.** No inference-input authorization record
exists, so today every `current_inference` item has
`machine_decision_eligible = false`. An item keeps the value derived at
emission; a later authorization never changes an existing item.

### B.3a `EvidenceTemporalScope` (added)

The substantive time coordinates of an item beyond `effective_at_utc` and
`source_observed_at_utc`. Every field is part of the item's identity.

- **Mutability:** immutable. **Source:** producer, copied from stored or
  computed values, never from a wall clock at emission. **Machine-decision
  eligible:** yes, as identification and gating. **Prohibited content:**
  operational times (insertion, receipt, retry, audit).

| Field | Type | Null | Validation |
|---|---|---|---|
| `session_date` | `SessionDate` | yes | required when the evidence is session-scoped (bars, snapshots, session calculations, regime, setups, selector results); equals the America/New_York date of `effective_at_utc` for intraday evidence |
| `observation_period_start` | `date` | yes | required for economic observations (the FRED `observation_date`) |
| `observation_period_end` | `date` | yes | derived deterministically from the start and the stored frequency; null only when the frequency is unrecognized (freshness then evaluates to `unknown`) |
| `source_vintage_start` / `source_vintage_end` | `date` | yes | required for vintage-aware sources (FRED `realtime_start` / `realtime_end`) |
| `scheduled_event_at_utc` | `UtcTimestamp` | yes | required for `scheduled_catalyst` subjects |
| `valid_from_utc` / `valid_until_utc` | `UtcTimestamp` | yes | an explicit validity interval, only where the evidence has one (for example a setup lifecycle state or a scheduled-event status); `valid_until_utc` > `valid_from_utc` |

Payload timestamps (a bar's `bar_timestamp`, a headline's
`created_at`/`updated_at`, a provider `retrieved_at`) are part of the
payload and therefore also part of the identity. Storage bookkeeping
columns such as `first_ingested_at` and `last_seen_at` are operational and
never copied into a payload.

### B.3b Temporal coordinates by evidence type

| Evidence | `effective_at_utc` | `source_observed_at_utc` | Temporal scope and payload times |
|---|---|---|---|
| Stored bar | bar close (`bar_timestamp` + 5 min) | bar close | `session_date`; payload `bar_timestamp`, `retrieved_at` |
| Market snapshot | provider quote/trade time | same | `session_date` |
| News publication | publication time (`created_at`) | publication time | payload `created_at`, `updated_at`, `retrieved_at` |
| Macro observation | retrieval instant of that vintage | retrieval instant | observation period; vintage |
| Scheduled catalyst (future) | calendar retrieval instant | same | `scheduled_event_at_utc` |
| Deterministic calculation | the calculation's as-of (e.g. the regime engine's `as_of_timestamp`) | earliest required input's observed time | `session_date` where session-scoped |
| Current inference | the `as_of_utc` of the bundle it consumed (the inference's as-of state) | earliest required input's observed time | inherited `session_date` where applicable |
| Historical research result | the instant the result was recorded | null (`timeless`) | none |
| `missing_evidence` / `stale_evidence` status | the status item's as-of (the check instant) | the check instant | the expected subject's scope |

### B.4 `EvidenceSubject`

- **Mutability:** immutable. **Source:** producer, validated against the
  registry's subject coverage. **Machine-decision eligible:** yes, as an
  identifier. **Prohibited content:** display names, free text, anything not
  in the canonical form.

| Field | Type | Null | Validation / limit |
|---|---|---|---|
| `subject_type` | `SubjectType` enum | no | see §D |
| `subject_id` | `str` | no | ≤ 160 characters; must match the canonical pattern for `subject_type` (§D.2); must begin with that type's prefix |

### B.5 `EvidenceProvenance`

- **Mutability:** immutable. **Source:** the producer's runtime, never a
  model. **Machine-decision eligible:** used for gating (producer, version,
  path), not as a decision value. **Prohibited content:** credentials, hosts
  with credentials, headers, file-system paths, raw exception text.

| Field | Type | Null | Validation / limit | In identity |
|---|---|---|---|---|
| `producer_id` | `ProducerId` | no | registered | yes |
| `producer_version` | `VersionLabel` | no | listed in the registry entry | yes |
| `producer_type` | `ProducerType` enum | no | `source_adapter`, `deterministic_engine`, `research_result_publisher`, `model_agent`, `conflict_detector`, `bundle_builder`, `explanation_layer`, `notification_layer`; must equal the registry entry | yes |
| `production_path` | `ProductionPath` enum | no | `deterministic` or `model_generated`. A hybrid producer marks the item `model_generated` if **any** model output contributed to it | yes |
| `code_commit_sha` | `CommitSha` | no | the commit of the code that produced the item | yes |
| `code_tree_clean` | `bool` | no | `false` blocks machine-decision eligibility | yes |
| `configuration_identity` | `ConfigIdentity` | no | SHA-256 of the canonical producer configuration (for example the selector's `SelectorConfigSnapshot`), or `cfg_none` if the producer has no configuration | yes |
| `generated_at_utc` | `UtcTimestamp` | no | wall-clock time the item was built; purely operational, because `effective_at_utc` carries the substantive as-of (§L.2) | **no** |
| `source_observed_at_utc` | `UtcTimestamp` | yes | the source instant the item depends on. For an observed fact it is that fact's time (a bar's close, `retrieved_at`, a publication time). For a derived item it is the **earliest** source-observed time among its required inputs, so a derived item is never fresher than its oldest input. For `missing_evidence` it is the instant the absence was checked. Null only for `historical_research_result` (freshness is `timeless`) | yes |
| `source_references` | `list[EvidenceSourceReference]` | no | 0–32, sorted, unique; ≥ 1 for `confirmed_fact`, `deterministic_calculation` and `historical_research_result`; exactly one `model_run` reference iff `production_path = model_generated` | yes |
| `parent_evidence_ids` | `list[ItemId]` | no | 0–64, sorted, unique; each parent must already be stored, and its `generated_at_utc` ≤ this item's; no self-reference; lineage rules (§C.3) | yes |

### B.6 `EvidenceSourceReference` (discriminated union on `reference_type`)

- **Mutability:** immutable. **Source:** the producer's runtime.
  **Machine-decision eligible:** no (audit and recomputation only).
- **Prohibited content:** API keys, tokens, headers, cookies, full request
  URLs with query strings, raw response bodies, raw prompts, local file
  paths, database paths, exception text.

| `reference_type` | Fields (all required unless marked) | Limits |
|---|---|---|
| `duckdb_row` | `table` (registered table enum: `market_bars`, `news_articles`, `macro_observations`, `macro_series_metadata`, `option_chain_snapshot_batches`, `option_chain_snapshots`, `option_chain_snapshot_batch_items`); `primary_key` (**ordered** list of `[column, canonical value]` exactly matching the registered primary-key definition); `db_schema_version` (e.g. `"0009"`); `ingestion_run_id` (nullable where the table has none) | ≤ 8 key columns; each value ≤ 128 characters |
| `ingestion_run` | `ingestion_run_id`; `dataset` (`Token`); `provider` (`Token`); `outcome` (`succeeded`, `failed`, `partial`) | — |
| `provider_request` | `provider` (`Token`); `endpoint_class` (registered token such as `alpaca_stock_bars`, `alpaca_stock_snapshot`, `alpaca_news`, `alpaca_option_chain`, `fred_series_observations`, `fred_series_metadata`); `feed` (nullable enum: `iex`, `sip`, `opra`, `indicative`); `request_params_sha256` (hash of the canonical, secret-free parameter set); `requested_at_utc`; `response_class` (`ok`, `empty`, `rate_limited`, `server_error`, `timeout`, `auth_failure`, `invalid_response`, `refused_by_guard`) | no URL, no header, no body |
| `research_result` | `study_id`; `result_sha256`; `result_schema_version`; `document_path` (repo-relative, `^docs/[A-Z0-9_]+\.md$`); `document_commit_sha` | — |
| `calculation_input` | `input_schema_id` (`VersionLabel`); `input_sha256`; `input_record_count` (0–1,000,000) | — |
| `model_run` | `provider` (`Token`); `model_name` (as returned, ≤ 64 characters); `request_schema_id`; `prompt_template_id`; `prompt_template_sha256`; `evidence_package_sha256`; `output_sha256`; `response_id_sha256` (nullable); `input_tokens`/`output_tokens` (nullable ints) | no prompt text, no evidence text, no reasoning, no raw response ID |
| `document` | `locator_kind` (`repo_document` or `external_publication`); for `repo_document`: `document_path`, `document_commit_sha`, `section_token`; for `external_publication`: `publisher` (`Token`), `https_url` (≤ 2,048 characters, `https` only, no user-info, no query parameter whose name matches a credential pattern), `published_at_utc` (nullable), `retrieved_at_utc` | external URLs are retained provenance, not model-facing text (SOURCE_POLICY) |

### B.7 `EvidenceFreshness` — an *evaluation*, not a property of the item

Freshness depends on the evaluation instant, so it is **never stored in the
immutable item**. It appears only in bundle entries (§B.13), computed by the
bundle builder.

- **Mutability:** immutable per bundle. **Source:** the bundle builder only.
  **Machine-decision eligible:** yes (it gates). **Prohibited content:**
  consumer-supplied values; any state not derived from the policy.

| Field | Type | Null | Validation / limit |
|---|---|---|---|
| `policy_id` | `VersionLabel` | no | equals the item's `freshness_policy_id` |
| `evaluated_at_utc` | `UtcTimestamp` | no | equals the bundle `as_of_utc` |
| `source_observed_at_utc` | `UtcTimestamp` | yes | copied from the item |
| `age_seconds` | `int` | yes | ≥ 0; null when the observed time is null or in the future |
| `state` | `FreshnessState` enum | no | `current`, `aging`, `stale`, `timeless`, `unknown` |
| `reason` | `FreshnessReason` enum | no | `within_current_window`, `within_aging_window`, `beyond_stale_boundary`, `observed_timestamp_missing`, `observed_timestamp_in_future`, `clock_unhealthy`, `clock_health_unknown`, `policy_parameters_unset`, `off_hours_rule_applied`, `timeless_by_policy` |
| `clock_health_item_id` | `ItemId` | yes | the clock-health fact used; required unless `state = timeless` |

### B.8 `EvidenceAvailability`

- **Mutability:** immutable. **Source:** producer. **Machine-decision
  eligible:** yes (gating). **Prohibited content:** exception text, stack
  traces, provider messages.

| Field | Type | Null | Validation / limit |
|---|---|---|---|
| `state` | `AvailabilityState` enum | no | `available`, `partial`, `unavailable`, `unsupported`, `refused`, `error` (§F.1) |
| `reason_code` | `Token` | yes | must be in the producer's registered reason-code list; required unless `state = available` |
| `missing_components` | `list[Token]` | no | 0–16, sorted; registered component tokens; non-empty for `partial` |
| `error_category` | `Token` | yes | one of the producer's registered sanitized categories (for example the existing `invalid_input`, `refusal`, `incomplete`, `citation_invalid`, `policy_violation`, `unexpected_error`); required iff `state = error` |

### B.9 `EvidenceQualityProfile` (added to the minimum set; §G)

- **Mutability:** immutable. **Source:** producer, checked against kind and
  registry. **Machine-decision eligible:** yes, as separate dimensions only;
  never collapsed into one score. **Prohibited content:** any unauthorized
  number; self-assessed "high confidence".

| Field | Type | Null | Validation |
|---|---|---|---|
| `data_quality` | enum `complete`, `partial`, `insufficient`, `not_assessed` | no | — |
| `source_basis` | enum `directly_observed`, `derived`, `rule_inferred`, `model_inferred` | no | must match kind and path (§C.2) |
| `source_tier` | enum `primary_official`, `licensed_market_data`, `company_primary`, `reputable_reporting`, `secondary_commentary`, `not_applicable` | no | the SOURCE_POLICY tier of the underlying source |
| `calculation_completeness` | enum `complete`, `partial`, `insufficient`, `not_applicable` | no | `not_applicable` unless kind is `deterministic_calculation` |
| `inference_support` | enum `supported`, `partially_supported`, `unsupported`, `unable_to_determine`, `not_assessed`, `not_applicable` | no | only a recorded **human adjudication** (the existing evaluation rubric) may set anything other than `not_assessed`/`not_applicable`; the producer can never self-grade |
| `research_status` | enum `exploratory`, `confirmed`, `shadow_pending`, `unsupported`, `not_applicable` | no | must equal the research reference's status when present |
| `graded_strength` | enum `low`, `moderate`, `high`, `confidence_not_available` | no | anything but `confidence_not_available` requires `strength_rubric_id` |
| `strength_rubric_id` | `VersionLabel` | yes | must be a registered rubric; **none is registered today** |
| `calibrated_probability` | `ExactRational` | yes | **must be null** unless the registry holds a calibrated-probability authorization for this producer and payload schema (§G.3); none exists |
| `uncertainty_codes` | `list[Token]` | no | 0–8, sorted; registered vocabulary (for example `provider_reported_unverified`, `headline_only`, `single_provider`, `indicative_feed`, `iex_partial_volume`, `no_exchange_calendar`, `vintage_revisable`) |

### B.10 `EvidenceResearchReference`

- **Mutability:** immutable. **Source:** the research-result publisher,
  which copies values from a recorded, hashed result. **Machine-decision
  eligible:** yes, as context and gating, never as a live signal. **Prohibited
  content:** unrecorded or exploratory observations presented as confirmed;
  holdout values before the holdout result is recorded; edited estimates.

| Field | Type | Null | Validation / limit |
|---|---|---|---|
| `study_id` | `Token` | no | registered study, e.g. `spy_vwap_reversion` |
| `result_kind` | enum `discovery_result`, `confirmation_result`, `holdout_result`, `shadow_result` | no | — |
| `result_schema_version` | `VersionLabel` | no | e.g. `spy-vwap-reversion-confirmation-1` |
| `result_sha256` | `Sha256Hex` | no | equals the hash in the recorded result document |
| `result_document_path` | `str` | no | repo-relative, e.g. `docs/SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md` |
| `result_document_commit_sha` | `CommitSha` | no | the commit that recorded the result |
| `protocol_commit_shas` | `list[CommitSha]` | no | 1–8, **ordered** (base first, then clarifications) |
| `analysis_code_commit_sha` | `CommitSha` | no | as recorded (operator-supplied where the result says so) |
| `primary_label` | `Token` | no | must be a member of the label enum for `result_schema_version` |
| `secondary_labels` | `list[Token]` | no | 0–8, sorted; members of that schema's label enums |
| `research_status` | enum as in §B.9 | no | derived from `result_kind` and label by a registry rule |
| `sample_scope` | `Token` | no | e.g. `confirmation` |
| `holdout_state` | enum `sealed`, `recorded`, `not_applicable` | no | — |
| `fixed_caveats` | `list[Token]` | no | 1–16, sorted; must include every fixed note of the result (e.g. `underlying_setup_only_no_options_or_pnl`, `research_result_not_validation`) |

### B.11 `EvidenceInferenceBasis`

- **Mutability:** immutable. **Source:** inference producer. **Machine-decision
  eligible:** no today (default deny; only a future inference-input
  authorization could change this, §H.2).
  **Prohibited content:** hidden reasoning, chain-of-thought, prompts,
  self-assigned confidence.

| Field | Type | Null | Validation / limit |
|---|---|---|---|
| `inference_method` | enum `model_structured_output`, `deterministic_rule` | no | `model_structured_output` iff `production_path = model_generated` |
| `rule_id` | `VersionLabel` | yes | required iff `deterministic_rule` |
| `input_evidence_ids` | `list[ItemId]` | no | 1–64, sorted; ⊆ `parent_evidence_ids` |
| `claim_basis` | enum `summarizes_inputs`, `interprets_mechanism`, `compares_items`, `explains_scenario_relation`, `explains_absence` | no | `explains_scenario_relation` requires ≥ 1 scenario relation |
| `directional_content` | enum `none`, `scenario_relation` | no | `none` for every producer without directional authority |
| `evaluation_record_ref` | `Sha256Hex` | yes | hash of a recorded evaluation record (for example a characterization) if one exists; its absence means "not evaluated", never "valid" |
| `limitation_codes` | `list[Token]` | no | 0–8, sorted; registered vocabulary |

### B.12 `EvidenceScenarioRelation` (added; §H)

| Field | Type | Null | Validation |
|---|---|---|---|
| `scenario` | `EvidenceSubject` | no | `subject_type = scenario`; scenario definition registered |
| `scenario_class` | enum `non_directional`, `directional` | no | equals the scenario definition |
| `relation` | enum `supports`, `contradicts`, `neutral`, `not_applicable`, `unknown` | no | — |
| `rationale_category` | enum `observed_price_structure`, `regime_classification`, `macro_release`, `news_event`, `research_context`, `data_quality`, `freshness`, `conflict` | no | — |
| `supporting_evidence_ids` | `list[ItemId]` | no | 1–16, sorted; ⊆ the item's parents |
| `authorization_record_id` | `VersionLabel` | no | a registry authorization record that covers this producer, scenario class and relation (§H) |

The inference producer and timestamp are the containing item's
`provenance.producer_id` and `effective_at_utc`.

### B.13 `EvidenceConflict`

- **Schema version:** `evidence-envelope-1`.
- **Mutability:** immutable. A resolution is a new conflict-status record
  that supersedes the prior conflict-status record, never the evidence
  (§I.3).
- **Source:** a registered `conflict_detector` producer, or a recorded
  manual review.
- **Machine-decision eligible:** yes, as a gate (an unresolved critical
  conflict blocks machine-decision bundles).
- **Prohibited content:** a winner, preferred item or overriding value;
  free-text resolution notes used as input.

| Field | Type | Null | Validation / limit | In identity |
|---|---|---|---|---|
| `schema_version` | literal | no | exact | yes |
| `conflict_id` | `ConflictId` | no | recomputed | (identity) |
| `conflict_type` | enum (§I.1) | no | — | yes |
| `involved_item_ids` | `list[ItemId]` | no | 2–16, sorted, unique | yes |
| `detection_method` | enum `deterministic_rule`, `model_detected`, `manual_review` | no | — | yes |
| `detector_producer_id` / `detector_version` | `ProducerId` / `VersionLabel` | no | registered | yes |
| `rule_id` | `VersionLabel` | yes | required iff `deterministic_rule` | yes |
| `severity` | enum `informational`, `material`, `critical` | no | set by the rule's fixed severity table (§I.2), never by a model | yes |
| `status` | enum `unresolved`, `resolved`, `acknowledged_unresolvable` | no | — | yes |
| `resolution` | `ConflictResolution` | yes | required iff `status ≠ unresolved` | yes |
| `evaluated_as_of_utc` | `UtcTimestamp` | no | the substantive as-of at which the conflict status holds (the bundle or detection as-of); not earlier than any involved item's `effective_at_utc` | yes |
| `detected_at_utc` | `UtcTimestamp` | no | operational wall-clock time of detection | **no** |

`ConflictResolution`:
- `resolution_kind`: enum `new_evidence`, `underlying_item_revised`,
  `manual_acknowledgement`. `underlying_item_revised` cites a revision
  that was created by that item's **own** correction process (§M); the
  conflict record never creates or triggers it.
- `resolution_evidence_ids`: 1–16 `ItemId`, sorted. It may be empty only
  for `manual_acknowledgement`.
- `supersedes_conflict_id`: `ConflictId`, required. It names the prior
  **conflict-status record** only; it never refers to an evidence item.

### B.14 `EvidenceCitation`

- **Mutability:** immutable. **Source:** a consumer that makes claims
  (assistant, setup card, notification). **Machine-decision eligible:** no.
  **Prohibited content:** citations to IDs outside the named bundle.

| Field | Type | Null | Validation |
|---|---|---|---|
| `bundle_id` | `BundleId` | no | citations resolve only inside one bundle |
| `claim_index` | `int` | no | 0–255; position of the claim in the consumer output |
| `cited_item_ids` | `list[ItemId]` | no | 1–8, sorted; every ID must be an entry of the bundle |
| `citation_role` | enum `supports_claim`, `contradicts_claim`, `context`, `missing_evidence_notice`, `stale_evidence_notice`, `conflict_notice` | no | — |
| `cited_kinds` | `list[EvidenceKind]` | no | derived from the bundle entries and checked; the consumer must render a label for each |
| `cited_conflict_ids` | `list[ConflictId]` | no | 0–8; required for `conflict_notice` |

### B.15 `EvidenceConsumerContext`

| Field | Type | Null | Validation |
|---|---|---|---|
| `consumer_id` | enum `dashboard`, `assistant`, `setup_ranker`, `contract_ranker`, `contract_review_ui`, `notification_layer`, `bundle_builder`, `audit_export` | no | registered consumer |
| `purpose` | `BundlePurpose` enum (§N) | no | must be allowed for the consumer |
| `permissions` | `list[ConsumerPermission]` | no | sorted; ⊆ the registry grant for the consumer (`read_facts`, `read_calculations`, `read_research`, `read_inferences`, `read_conflicts`, `read_superseded`, `emit_calculation`, `emit_inference`, `emit_notification_event`) |
| `machine_decision_mode` | `bool` | no | `true` only for `setup_ranker` and `notification_layer`; restricts the bundle to eligible items |
| `request_sha256` | `Sha256Hex` | no | hash of the canonical `EvidenceQuery`; the raw user question is never stored here |

### B.16 `EvidenceBundleManifest` (with `EvidenceBundleEntry` and `EvidenceQuery`)

- **Schema version:** `evidence-envelope-1`.
- **Mutability:** immutable. **Source:** the bundle builder only.
- **Machine-decision eligible:** the bundle is usable for a machine decision
  only if `machine_decision_ready = true`.
- **Prohibited content:** items not selected by the recorded query; any
  consumer-set freshness; raw user text.

| Field | Type | Null | Validation / limit | In identity |
|---|---|---|---|---|
| `schema_version` | literal | no | exact | yes |
| `bundle_id` | `BundleId` | no | recomputed | (identity) |
| `purpose` | `BundlePurpose` | no | §N | yes |
| `as_of_utc` | `UtcTimestamp` | no | point-in-time boundary: only items and conflicts **recorded** at or before it | yes |
| `registry_version_id` | `RegistryVersionId` | no | in force at `as_of_utc` | yes |
| `selection_rule_id` | `VersionLabel` | no | the registered selection rule for the purpose | yes |
| `query` | `EvidenceQuery` | no | §N.2 | yes |
| `consumer_context` | `EvidenceConsumerContext` | no | §B.15 | yes |
| `entries` | `list[EvidenceBundleEntry]` | no | 0–2,000, sorted by `item_id` | yes |
| `missing_required_producers` | `list[MissingProducer]` | no | 0–32, sorted; `{producer_id, reason}` where `reason` ∈ `no_item_recorded`, `only_stale_items`, `producer_unavailable`, `producer_not_implemented`, `holdout_restricted` | yes |
| `conflict_ids` | `list[ConflictId]` | no | 0–256, sorted; every recorded conflict touching an included item | yes |
| `unresolved_material_conflict_count` | `int` | no | derived and checked | yes |
| `machine_decision_ready` | `bool` | no | derived (§N.3) | yes |
| `built_at_utc` | `UtcTimestamp` | no | wall clock | **no** |

`EvidenceBundleEntry`: `item_id`; `evidence_kind` (copied and checked);
`producer_id`; `required` (bool, from the selection rule); `freshness`
(`EvidenceFreshness`); `availability_state`; `machine_decision_eligible`
(copied); `superseded_as_of` (bool: a newer revision was recorded at or
before `as_of_utc`); `latest_revision_item_id` (nullable `ItemId`).

---

## C. Evidence-kind taxonomy (frozen for family version 1)

An item has **exactly one** kind. Kinds are never inferred by a consumer and
never changed after emission.

### C.1 Definitions

| Kind | What qualifies | What never qualifies |
|---|---|---|
| `confirmed_fact` | A value **recorded as reported** by an approved source, stored or observed, with its source reference: a stored bar's timestamp and OHLCV; a stored headline's publication timestamp; a scheduled macro release time from an approved official source; a FRED observation value and its vintage; a provider status result; a clock-health reading. "Confirmed" means *confirmed as recorded from an approved source*, not "true in the world" | The **content** of a news headline or summary (the claim is provider-reported and unverified; only its publication metadata is a fact); anything computed; anything a model wrote |
| `deterministic_calculation` | A reproducible result of registered code over recorded inputs, with an input digest: session VWAP; distance from VWAP in bps; regime classification (`spy-regime-engine-1`); option spread; deterministic contract eligibility (`spy-contract-selector-1`); freshness; setup qualification under frozen, registered criteria | Anything using a model output; a threshold chosen at run time; a "regime" label presented as a researched trend or reversion signal (a regime label is a calculation, not a research result and not a qualified setup) |
| `historical_research_result` | A **recorded**, hashed result of a registered study: the frozen SPY VWAP confirmation labels and estimates (`supported_for_further_shadow_research`, secondary `below_horizon_dependent`); a future recorded holdout or trend-day result; a recorded discovery result labelled `exploratory` | An unrecorded exploratory observation; a re-computation with changed parameters; any holdout value before the holdout result is recorded |
| `current_inference` | An interpretation of recorded evidence at a point in time: a non-directional macro interpretation; a news-impact interpretation; an explanation of whether evidence supports or contradicts a registered scenario (only when authorized, §H) | A value presented as fact or calculation; any directional statement from a producer without directional authority |
| `missing_evidence` | An explicit, bounded **absence record** for an expected producer, subject, query and as-of time, with a bounded reason code (no bars for the session, a provider error, a preflight abstention, a refused request, a not-yet-implemented producer) | Silence or an empty list (never interpreted as missing evidence); a neutral or zero-valued fact (absence is never converted into one) |
| `stale_evidence` | An explicit **status item** stating that a required referenced item, or an expected producer's latest output, was already stale at the status item's own as-of time (for example the existing `news_stale` and `bars_stale` preflight reasons). It must reference the stale item's ID, or the expected producer and query when no item can be named. It never carries the stale value as a current value | A replacement for, or change to, the original item; a record issued merely because a bundle evaluated a normal item as stale (that is dynamic freshness, §C.4) |

See §C.4 for how kind differs from evaluated freshness.

### C.2 Kind consistency rules

| Kind | `production_path` | `quality.source_basis` | Other requirements |
|---|---|---|---|
| `confirmed_fact` | `deterministic` | `directly_observed` | ≥ 1 `duckdb_row`, `provider_request` or `document` source reference |
| `deterministic_calculation` | `deterministic` | `derived` | ≥ 1 `calculation_input` reference; recomputation must reproduce `payload` byte-for-byte under the recorded commit and configuration |
| `historical_research_result` | `deterministic` | `derived` | `research_reference` required; `source_observed_at_utc` null; freshness `timeless` |
| `current_inference` | either | `rule_inferred` or `model_inferred` (matching path) | `inference_basis` required; not machine-decision eligible today (default deny, §H.2) |
| `missing_evidence` | `deterministic` | `directly_observed` (the absence itself was observed) | `availability.state ≠ available`; payload schema `missing_evidence.v1` naming the expected producer, subject, query hash, as-of and bounded reason; no value field of any kind |
| `stale_evidence` | `deterministic` | `directly_observed` | payload schema `stale_evidence.v1` with the stale item's ID (or the expected producer and query hash), its `last_observed_at_utc`, the `policy_id`, and the status as-of |

### C.3 Lineage rules (taint, permanent)

- A `confirmed_fact` may have only `confirmed_fact` parents (or none).
- A `deterministic_calculation` may have `confirmed_fact`,
  `deterministic_calculation`, `historical_research_result`,
  `missing_evidence` or `stale_evidence` parents, **never** a
  `current_inference` parent at any depth.
- A `historical_research_result` may not have a `current_inference` ancestor.
- A `current_inference` may have parents of any kind.
- Parent graphs must be acyclic. Content-addressed IDs make cycles
  infeasible, and the store additionally refuses any parent not already
  stored.

### C.4 Evidence kind versus evaluated freshness

These are two independent dimensions:
- **Evidence kind** is what the item *is* epistemically. It is fixed at
  emission and never changes.
- **Evaluated freshness** is whether that item is still usable at a
  particular bundle `as_of_utc`. It is computed by the bundle builder (§F.4)
  and never stored in the item.

An item keeps its original kind as it ages. For one stored bar:

| Bundle as-of | Evidence kind | Evaluated freshness |
|---|---|---|
| T1 | `confirmed_fact` | `current` |
| T2 | `confirmed_fact` | `aging` |
| T3 | `confirmed_fact` | `stale` |

The item, its ID and its kind are identical at T1, T2 and T3. **No producer
issues a `stale_evidence` item or a revision merely because an item aged.** A
`stale_evidence` status item is needed only when a producer, at its own
as-of time, must state that required evidence it looked for was already
stale (for example an agent's preflight abstaining with `news_stale`).

**Readiness uses evaluated freshness**, never evidence kind alone (§N.3).

---

## D. Subject model

### D.1 Subject types (bounded enum)

`instrument`, `index`, `market`, `macro_series`, `news_item`,
`scheduled_catalyst`, `market_session`, `setup_candidate`, `scenario`,
`option_contract`, `research_study`, `provider_health`, `system_component`.

### D.2 Canonical identifiers

| Subject | Canonical form | Pattern | Registered today |
|---|---|---|---|
| SPY | `instrument:us_equity:SPY` | `^instrument:us_equity:[A-Z][A-Z0-9.]{0,9}$` | SPY only |
| Index | `index:<code>` | `^index:[A-Z0-9]{1,16}$` | none (no source) |
| Market | `market:us_equity` | `^market:[a-z_]{1,32}$` | `market:us_equity` |
| Economic series | `macro_series:fred:FEDFUNDS` | `^macro_series:fred:[A-Z0-9_]{1,32}$` | the seven core-basket series |
| News item | `news_item:alpaca:<provider_article_id>` | `^news_item:[a-z_]{1,32}:[A-Za-z0-9._-]{1,64}$` | Alpaca articles |
| Scheduled catalyst | `catalyst:<source>:<event_code>:<YYYY-MM-DD>` | `^catalyst:[a-z_]{1,32}:[a-z0-9_]{1,48}:\d{4}-\d{2}-\d{2}$` | none (no calendar source exists) |
| Session date | `session:us_equity_regular:2026-12-07` | `^session:us_equity_regular:\d{4}-\d{2}-\d{2}$` | any weekday; no exchange-holiday calendar exists |
| Setup candidate | `setup:<lane>:<setup_definition_id>:<symbol>:<decision_time>` e.g. `setup:vwap_reversion:spy_vwap_ext_v1:SPY:20261207T150500Z` | `^setup:(vwap_reversion|trend_continuation):[a-z0-9_]{1,48}:[A-Z]{1,10}:\d{8}T\d{6}Z$` | none (no setup definition is registered for live use) |
| Scenario | `scenario:<scenario_definition_id>:<symbol>:<YYYY-MM-DD>` | `^scenario:[a-z0-9_]{1,48}:[A-Z]{1,10}:\d{4}-\d{2}-\d{2}$` | none |
| Option contract | `option:osi:SPY260918C00750000` | `^option:osi:[A-Z]{1,6}\d{6}[CP]\d{8}$` | SPY |
| Research study | `research:spy_vwap_reversion` | `^research:[a-z0-9_]{1,48}$` | `spy_vwap_reversion` |
| Provider/system health | `provider:alpaca:market_data`, `system:clock` | `^provider:[a-z_]{1,32}:[a-z_]{1,32}$`, `^system:[a-z_]{1,32}$` | Alpaca, FRED, OpenAI; clock, evidence store |
| Evidence bundle | `evb1_<64 hex>` | §L | — |

Rules:
- The option-contract form uses the OCC/OSI symbol the provider already
  returns (stored as `contract_symbol`). Nothing else is added.
- `lane` is `vwap_reversion` or `trend_continuation`. "No qualified setup" is
  not a setup subject; it is a calculation about a `market_session`
  subject (consumer rules §4).
- No subject ID contains a display name, headline, series title, or free text.

---

## E. Provenance and source references

Every item identifies all of the following. The field that carries each is
named.

| Requirement | Carried by |
|---|---|
| Producer ID and version | `provenance.producer_id`, `producer_version` |
| Producer type | `provenance.producer_type` |
| Code commit | `provenance.code_commit_sha` (+ `code_tree_clean`) |
| Configuration identity | `provenance.configuration_identity` |
| Generated-at time | `provenance.generated_at_utc` (not in identity) |
| Source-observed-at time | `provenance.source_observed_at_utc` |
| Source data identities | `duckdb_row`, `ingestion_run`, `calculation_input` references |
| Provider and feed | `provider_request.provider`, `.feed`; `duckdb_row` primary key includes provider/feed where the table does |
| Deterministic vs model path | `provenance.production_path` |
| Parent evidence | `provenance.parent_evidence_ids` |
| Research protocol and result | `research_reference` and `research_result` source reference |

**Never stored anywhere in this family** (validators reject, and the
sanitization tests in §P cover it): credentials or tokens of any kind;
request or response headers; unrestricted provider responses; unrestricted
exception text; hidden model reasoning; prompts (only template ID and hash);
raw provider response IDs (only their hash); local file or database paths.

---

## F. Freshness and availability

### F.1 Availability states

| State | Meaning |
|---|---|
| `available` | The producer produced its evidence for the requested subject |
| `partial` | Some required components are missing; `missing_components` names them |
| `unavailable` | The required source had no usable data (e.g. `bars_missing`, `news_missing`) |
| `unsupported` | The request is outside the producer's registered coverage (e.g. a non-SPY symbol) |
| `refused` | The producer declined under a rule: a preflight abstention, a holdout date guard, an authorization boundary, a model refusal |
| `error` | The producer failed; `error_category` is a sanitized token |

### F.2 Freshness states

| State | Meaning |
|---|---|
| `current` | Within the policy's current window |
| `aging` | Past current, before the stale boundary; shown with its age |
| `stale` | Past the stale boundary |
| `timeless` | The policy declares age irrelevant (recorded research results; fixed reference facts) |
| `unknown` | Freshness cannot be established: missing or future timestamp, unhealthy or unknown clock, unset policy parameters. **`unknown` is treated like `stale` for every gate** |

### F.3 Producer-specific policies

There is **no universal threshold**. Each producer and kind names a policy in
the registry ([EVIDENCE_REGISTRY.md §3](EVIDENCE_REGISTRY.md)). Each policy
defines:
- the applicable evidence kinds;
- which timestamp is the source-observed time;
- the current-until duration or rule;
- the aging interval;
- the stale boundary;
- market-hours versus off-hours behavior (`elapsed_wall_clock`,
  `freeze_at_session_close`, or `not_applicable`);
- missing-timestamp behavior (always `unknown`);
- the clock-health requirement;
- the configuration version.

A policy whose parameters are **unset** evaluates every item to `unknown`
(`policy_parameters_unset`). This lets the registry name live intraday
policies now without inventing thresholds before review.

### F.4 Evaluation algorithm (bundle builder only)

The same algorithm applies to every item, whatever its kind. A
`missing_evidence` or `stale_evidence` status item is evaluated for its own
currency (is this status report itself recent?), and it never satisfies a
requirement for the evidence it reports on (§N.3).

1. If the policy is `timeless` → `timeless`.
2. If the policy parameters are unset → `unknown`.
3. If the clock-health fact at `as_of_utc` is missing → `unknown`; if
   unhealthy → `unknown`.
4. If `source_observed_at_utc` is null → `unknown`; if it is later than
   `as_of_utc` plus the policy's future tolerance → `unknown`.
5. Compute age (applying the off-hours rule), then compare with the
   boundaries: `current` → `aging` → `stale`.

**Monotonicity.** For a fixed item and policy, the state is non-increasing
in `as_of_utc`: `current` ≥ `aging` ≥ `stale`. A consumer can never convert
`stale`, `aging` or `unknown` back to `current`. Only a **new observation**
(a new original item with a newer observed time, not a revision of the old
one) can be current.

---

## G. Confidence, strength and uncertainty

### G.1 No universal confidence score

Quality is recorded along separate dimensions (§B.9): data quality, source
reliability (`source_tier`), calculation completeness, inference support,
research status, and directional relevance (scenario relations, §H). They are
**never multiplied, averaged, or collapsed** into one number by any producer
or consumer.

### G.2 Graded strength

`low`/`moderate`/`high` may be used only with a registered rubric that
defines each grade in advance. **No rubric is registered.** Every item
therefore uses `confidence_not_available`.

### G.3 Numerical probability

`calibrated_probability` must be null unless **all** of the following hold:
- a registry authorization record names the producer and payload schema as an
  authorized calibrated model;
- that record references recorded validation evidence (a hash and document);
- the item is a forecast satisfying DECISION_RULES "Forecast Requirements"
  (timestamp, horizon, evidence, contradictory evidence, confirmation and
  invalidation criteria).

No such authorization exists. Research estimates (for example the
confirmation study's bps estimates and exact p-values) are carried inside the
research payload as **recorded study statistics**, never as a probability
about the current session.

---

## H. Directional relevance

### H.1 Scenario relations

- **Existing agents stay non-directional.** Market Evidence, News and Macro
  keep `directional_assessment = "not_performed"` and
  `trade_recommendation = "not_performed"`. Their registry entries have
  `directional_authority = none`, so their items may not carry scenario
  relations, and `inference_basis.directional_content` must be `none`.
- **Scenarios are registered definitions.** A scenario relation can reference
  only a registered scenario definition with a fixed `scenario_class`:
  - `non_directional` (for example "session regime favors reversion
    conditions" or "evidence is sufficient for a briefing");
  - `directional` (the underlying moves up or down over a horizon).
  **No scenario definition is registered.**
- **Authorization levels.** `directional_authority` in the registry is one
  of:
  - `none`;
  - `non_directional_scenario_relations` (may relate evidence to
    non-directional scenarios);
  - `authorized_directional` (may relate evidence to directional
    scenarios; the item must then satisfy DECISION_RULES Forecast
    Requirements).
  **No producer holds either of the last two today.**
- **A relation records** the scenario ID, relation, rationale category,
  producer (the item's), parent evidence, timestamp (the item's
  `effective_at_utc`), and the authorization record (§B.12).
- **A future directional component** may consume the existing agents'
  evidence. It must emit **its own** `current_inference` items, under its own
  producer ID and authorization, citing their items as parents. It can never
  alter or re-label their items.
- **Contract eligibility is out of scope for every inference producer,
  permanently.** Only the deterministic selector classifies contracts
  (§H.3). Contract ranking is a separate, future,
  separately authorized capability (§H.3). The Options Strategy Agent
  remains unauthorized.

### H.2 Inference as a machine-decision input: default deny

**Current state.**
- Every `current_inference` item has `machine_decision_eligible = false`.
- The existing Market Evidence, News and Macro agents remain
  non-directional.
- The scenario-definition, rubric, directional-authorization and
  inference-input-authorization registries are empty.
- Therefore **no current machine-decision component may consume model
  inference.**

**Permanent boundaries** (no authorization can lift these under this
family):
- No `confirmed_fact`, `deterministic_calculation` or
  `historical_research_result` may be derived from a model inference
  (§C.3).
- Inference never overrides deterministic eligibility.
- Inference never affects contract eligibility status in any way (§H.3).
- Inference cannot turn a rejected contract into a returned contract, or
  change `research_only` to `eligible`.
- Inference cannot change a frozen research label or calculation.
- Inference cannot be silently converted into a numeric probability (§G.3).
- Inference can be displayed and explained when clearly labelled.

**Future, separately authorized path.** A future scenario/setup component
(§H.4) or contract-ranking component (§H.3) may consume selected
`current_inference` items only when **all** of the following exist:
1. an authorized scenario definition in the registry;
2. a frozen input rubric naming which inference payload fields are used and
   how;
3. explicit producer grants in the registry (an inference-input
   authorization record naming each consumed producer and payload schema);
4. the required freshness policies for every consumed input;
5. conflict handling for those inputs (§I);
6. exact input evidence IDs recorded on every output;
7. DECISION_RULES Forecast Requirements, where the output is a forecast;
8. a recorded component version and configuration identity;
9. a new `current_inference` output envelope;
10. **no effect on contract eligibility, ever** (§H.3), and no effect on
    a setup's lifecycle or qualification unless that setup's own reviewed
    research and decision rules explicitly permit inference as an input
    (§H.4). The permanent boundaries above always hold.

**The output remains inference.** A component that combines several
inferences, facts and calculations emits `current_inference`. It never
becomes a fact or a calculation merely because it combines several inputs.

This path preserves the intended future use of macro and news
interpretations. It authorizes none of it today.

### H.3 Contract eligibility (permanent) versus contract ranking (future)

**Permanent boundary: contract eligibility is deterministic.**
- Deterministic contract eligibility belongs **exclusively** to the existing
  deterministic Contract Selector (`deterministic_contract_selector`,
  `spy-contract-selector-1`).
- **What the selector produces.** For each evaluated contract, exactly
  one of three outcomes, decided only by the selector:
  - **returned as `eligible`**: included in the result's
    `eligible_contracts` set (operational eligibility requires OPRA);
  - **returned as `research_only`**: included in the result's
    `research_only_contracts` set (for example `indicative` data where
    research use is allowed);
  - **rejected**: an evaluated contract rejected by a selector rule and
    counted under its bounded `RejectionReason`. The selector exposes
    **only aggregate `rejection_counts`**. It returns no per-contract record
    for a rejected contract, and this design does not create one:
    "ineligible" is not a stored per-contract status anywhere in this
    family.
- No inference, scenario ranker, contract ranker, conversational
  assistant, dashboard or notification component may:
  - turn a rejected contract into a returned contract;
  - change `research_only` to `eligible`;
  - introduce a contract absent from the selector's returned sets;
  - suppress, rewrite, or bypass the rejection counts, their reasons, or
    any selector requirement (including OPRA);
  - suppress or rewrite the returned set a contract belongs to.
- **Display.** The dashboard, assistant and contract-review UI display
  individual **returned** contracts only. Rejected contracts appear only
  through aggregate counts by `RejectionReason`. No consumer
  reconstructs, lists or materializes rejected contracts individually, for
  example by diffing the option-chain batch against the returned sets.
- No registry authorization record type exists that could grant any of
  these. Changing eligibility means changing the selector itself, through
  its own reviewed code and configuration change.

**Future, separately authorized contract ranking.** A contract-ranking
component (`future_contract_ranker`) may, only once separately authorized:
- consume selector outputs;
- rank contracts only **within the returned `eligible` set** and,
  separately, **within the returned `research_only` set**; it cannot rank,
  list or reconstruct rejected contracts;
- explain why one contract ranks above another;
- use selected `current_inference` evidence only under the §H.2 conditions;
- emit a **new** `current_inference` item containing the ranking, citing the
  selector result as a parent;
- preserve, visibly and immutably, the returned set (`eligible` or
  `research_only`) of every ranked contract, and leave the rejection counts
  untouched.

It may never mix `eligible` and `research_only` contracts into one
undifferentiated ranking. Each returned set is ranked separately and shown
separately. A ranking is not eligibility: the top-ranked research-only
contract is still research-only.

### H.4 Future setup and scenario component

A future, separately authorized scenario/setup component may consume
inference under §H.2 and may emit, as `current_inference`:
- scenario support;
- scenario contradiction;
- setup ranking;
- a setup interpretation.

It may affect a setup's lifecycle or qualification **only** if that setup's
own separately reviewed research and decision rules explicitly permit
inference as an input. No current setup definition permits it.

It can never change:
- a frozen historical research result;
- deterministic VWAP eligibility;
- deterministic regime calculations;
- deterministic contract eligibility.

---

## I. Conflict model

### I.1 Conflict types

| Type | Example |
|---|---|
| `inference_vs_observation` | A news interpretation conflicts with observed price behavior |
| `cross_domain_inference_disagreement` | Macro evidence is interpreted as risk-on while a market-structure calculation contradicts it |
| `provider_disagreement` | Two providers report different values for the same fact |
| `current_vs_research_context` | Current conditions differ from the conditions the recorded research covered |
| `freshness_mismatch` | One item needed together with another is current while the other is stale or unknown |
| `revision_disagreement` | A revised item disagrees with the version used by an earlier decision |
| `scenario_relation_disagreement` | Two authorized relations disagree about the same scenario |

### I.2 Detection and severity

- Detection is by `deterministic_rule` (registered rule ID and fixed severity
  table), `model_detected` (recorded as detected by inference; severity comes
  from the rule table for that type, never from the model), or
  `manual_review`.
- **Severity rule table (initial):**
  - `critical`: the conflict involves an item that a bundle purpose marks
    `required`, and the conflict type is `provider_disagreement` or
    `freshness_mismatch`.
  - `material`: any other conflict involving a required item, or any
    `inference_vs_observation` and `current_vs_research_context` conflict.
  - `informational`: everything else.

### I.3 No silent winner

- There is no `winner`, `preferred_item`, or `resolved_value` field.
- **Resolution supersedes the conflict-status record, not the evidence.**
  A resolution is a **new** conflict record whose `supersedes_conflict_id`
  names the prior conflict-status record. It may mark the conflict
  resolved and cite new evidence (for example a corrected provider value).
- **Involved evidence stays immutable.** A conflict or its resolution never
  deletes, rewrites, or automatically supersedes any conflicting evidence
  item. Every involved item remains stored and visible.
- **Evidence is superseded only by its own process.** An underlying item is
  superseded only through its own source-correction or revision process
  (§M). A resolution may *cite* such a revision
  (`underlying_item_revised`); it never creates one.
- `acknowledged_unresolvable` records that a person reviewed the conflict
  and it stays open as context.
- The dashboard and assistant must display every `material` or `critical`
  unresolved conflict touching what they show
  ([consumer rules](EVIDENCE_CONSUMER_RULES.md)).
- An unresolved `critical` conflict makes `machine_decision_ready = false`.

---

## J. Evidence registry

Specified in [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md): every existing and
planned producer, freshness policies, payload schemas, scenario definitions
(empty), rubrics (empty), authorization records (empty), consumer grants,
and the registry's own versioning.

## K. Consumer rules

Specified in [EVIDENCE_CONSUMER_RULES.md](EVIDENCE_CONSUMER_RULES.md):
dashboard, conversational assistant, scanner/setup ranker, contract-review
UI, and notification layer.

---

## L. Canonical identity and hashing

All identities are `prefix + SHA-256(canonical payload)` per §B.0. No record
hashes itself: its own ID field is always excluded from its payload.

### L.1 Substantive fields: always in the identity where applicable

- `source_observed_at_utc`;
- `effective_at_utc` and every other as-of field (`run_as_of_utc`,
  `evaluated_as_of_utc`, bundle `as_of_utc`);
- the market-session date;
- bar timestamps;
- publication timestamps;
- the economic observation period and source vintage;
- scheduled-event times;
- validity intervals;
- subject identities;
- producer identity and version;
- evidence kind;
- the substantive payload, including every timestamp inside it;
- source-reference identities (including their request and retrieval
  times);
- parent evidence IDs;
- configuration identity.

### L.2 Operational metadata: the only exclusions

Only metadata that does not alter meaning is excluded:
- the record's own ID;
- the database insertion time (`recorded_at_utc`, which lives on the
  storage row, not in the record);
- envelope emission and receipt times;
- retry times;
- the derived `superseded_by` relation (never stored in a record);
- audit timestamps unrelated to the evidence's effective time
  (`detected_at_utc`, `built_at_utc`, audit events);
- signatures or external attestations, if any are ever added.

**`generated_at_utc` rule.** `provenance.generated_at_utc` is excluded only
because it is purely operational. That holds because every item must carry
the substantive `effective_at_utc` (§B.3b). For an inference, the as-of state
it reasoned over is `effective_at_utc`, which is included. `generated_at_utc`
may never be the only record of an item's as-of state.

### L.3 Treatment by prefix

| Identity | Prefix | Substantive temporal fields included | Excluded (operational only) |
|---|---|---|---|
| Evidence item | `evi1_` | `effective_at_utc`, `temporal_scope`, `provenance.source_observed_at_utc`, payload timestamps, source-reference times | `item_id`, `provenance.generated_at_utc` |
| Envelope | `eve1_` | `run_as_of_utc`, and the item IDs (which carry their own times) | `envelope_id`, `emitted_at_utc`, receipt time |
| Conflict | `evc1_` | `evaluated_as_of_utc`, involved item IDs | `conflict_id`, `detected_at_utc` |
| Bundle | `evb1_` | **`as_of_utc`**, each entry's evaluated freshness (`evaluated_at_utc`, age) | `bundle_id`, `built_at_utc` |
| Registry version | `evr1_` | none: registry content has no temporal meaning. When a version comes into force is recorded in a separate append-only activation log keyed by (`registry_version_id`, `effective_from_utc`), both substantive | `registry_version_id`, publication metadata |
| Configuration | `cfg1_` | none of its own; any time-dependent parameters (windows, limits) are part of the configuration content | nothing |

### L.4 Consequences (explicit)

- **Different times, different IDs.** Two otherwise identical observations
  at different effective or source times receive different evidence IDs.
  For example, two bars with identical OHLCV at 10:00 and 10:05 are
  different items.
- **Retries, same ID.** A retry of the same observation with the same
  substantive temporal identity (same commit, configuration, inputs, as-of
  and payload) receives the same ID. The store treats a second append of an
  existing ID as an idempotent no-op: it keeps the first `recorded_at_utc`
  and records an `append_duplicate_ignored` audit event.
- **Model retries.** A model re-run at the same as-of normally yields
  different output and therefore a different ID. Both are kept.
- **Re-observation is new evidence.** Re-retrieving the same stored source at
  a later time (a new `retrieved_at`) is a new observation with a new ID,
  not a retry.
- **Bundle as-of participates.** Two bundles with the same entries but
  different `as_of_utc` have different IDs.
- **Genuine revisions** get new IDs because their content (including
  `revision.supersedes_item_id`) differs (§M).

**"Known at time T"** is defined by the storage row's `recorded_at_utc`.

---

## M. Revision and supersession

`EvidenceRevision` (inside every item):

| Field | Type | Null | Validation |
|---|---|---|---|
| `revision_number` | `int` | no | 1 for an original; predecessor's + 1 otherwise |
| `supersedes_item_id` | `ItemId` | yes | null iff `revision_number = 1`; the predecessor must have the same producer, kind and primary subject |
| `revision_reason` | enum `original`, `source_revision`, `producer_correction`, `analytical_reinterpretation`, `availability_change` | no | `original` iff `revision_number = 1` |

- **`source_revision`**: the source itself changed (a FRED vintage, a
  provider-corrected bar).
- **`producer_correction`**: the producer was wrong (a bug fix); the new item
  has a new producer version or commit.
- **`analytical_reinterpretation`**: a new inference over the same inputs.
  It never supersedes a fact or calculation.
- **`availability_change`**: previously missing evidence became available,
  or the reverse.

**What is not a revision.**
- A later observation of the same subject (the next bar, a newer article, a
  new release) is a new original item (`revision_number = 1`) with its own
  temporal identity.
- An item aging from `current` to `stale` is never a reason to revise it or
  to emit anything. Freshness is evaluated at bundle time (§C.4, §F), and
  the item stays unchanged.

Rules:
- **Nothing is deleted or edited.** `superseded_by` is a *derived* relation
  (a query over `supersedes_item_id`), never stored on the older item.
- **Point-in-time integrity.** A bundle at `as_of_utc` sees only items
  recorded at or before it. A correction recorded later never changes what an
  earlier bundle, decision record, or notification saw. This follows
  DECISION_RULES "No retroactive forecast editing".
- **Branching.** Two revisions superseding the same predecessor are allowed
  (for example two independent corrections). The bundle builder treats this
  as a `revision_disagreement` conflict; it never picks one.
- **Consumer behavior with multiple revisions.** By default a bundle includes
  the latest revision known at `as_of_utc` and marks earlier ones as
  superseded. `read_superseded` permission (audit only) shows the whole chain.
- **Retention.** Evidence items, conflicts and bundle manifests are retained
  indefinitely, including null, failed, missing and superseded records
  (CLAUDE.md "Preserve failed and null findings"). Any future retention limit
  needs its own reviewed decision.

---

## N. Bundles and queries

### N.1 Bundle purposes

| Purpose | Contents (selection rule) | Required producers (initial) |
|---|---|---|
| `premarket_briefing` | Prior-session facts and calculations, macro facts and inferences, news facts and inferences, recorded research context, provider health | clock, macro observations, news, regime (prior session) |
| `live_market_state` | Latest bars, regime calculation, snapshot, provider health, material conflicts | clock, bars, regime |
| `setup_detail` | One setup candidate's calculations, research reference, supporting and contradicting items, invalidation state | clock, bars, regime, the lane's research result |
| `contract_review` | One selector result, plus option-chain facts **only for returned contracts** and batch-level provenance (the full evaluated batch is available only to `audit_export`, never rendered as contracts) | clock, selector, option-chain facts |
| `assistant_question_context` | Items matching a bounded query (§N.2), plus required-producer checks for the question's route | depends on route |
| `notification_decision` | The notification-event item and exactly the evidence that caused it | clock, the triggering producer |

### N.2 `EvidenceQuery` (bounded)

| Field | Type | Limit |
|---|---|---|
| `producer_ids` | `list[ProducerId]` | 0–16 |
| `evidence_kinds` | `list[EvidenceKind]` | 0–6 |
| `subject_ids` | `list[str]` | 0–32 canonical subject IDs |
| `subject_types` | `list[SubjectType]` | 0–13 |
| `effective_from_utc` / `effective_to_utc` | `UtcTimestamp` | window ≤ 7 days for live purposes; ≤ 400 days for briefing and audit |
| `include_superseded` | `bool` | `true` only with `read_superseded` |
| `max_entries` | `int` | 1–2,000 |

**Not allowed:** SQL, free-text search, regular expressions, embedding or
semantic retrieval as a **machine-decision** input. The assistant may use
semantic search only to choose which *IDs* to show a person; the chosen IDs
are then fetched through a bounded query and cited (consumer rules §3).

**Filters never hide absence.** Required-producer checks run regardless of
filters, so a query cannot make a bundle look complete by excluding stale or
missing evidence.

### N.3 `machine_decision_ready`

Readiness uses the bundle builder's **evaluated freshness**, never evidence
kind alone. A `missing_evidence` or `stale_evidence` status item never
satisfies a requirement; it records why the requirement is unmet.

`true` only if:
- every required producer has an included item;
- every required entry is `machine_decision_eligible`;
- every required entry's freshness is `current` or `timeless`;
- no unresolved `critical` conflict touches an included item;
- the bundle's registry version is the one in force at `as_of_utc`.

---

## O. Audit and security

- **Append-only storage.** Items, envelopes, conflicts and bundle manifests
  are insert-only. No update or delete path exists. The storage design (a
  future migration) needs its own authorization; none is proposed here.
- **Bounded audit events.** `EvidenceAuditEvent`: `event_type` (enum
  `envelope_accepted`, `envelope_refused`, `append_duplicate_ignored`,
  `bundle_built`, `bundle_refused`, `conflict_recorded`, `consumer_access`,
  `consumer_emit`), `occurred_at_utc`, `actor` (producer or consumer ID),
  `subject_record_id` (item, envelope, conflict or bundle ID), `reason_code`
  (registered token). No free text.
- **Refusals.** The store refuses, with a sanitized reason code:
  - unknown `schema_version`;
  - unknown or unimplemented producer or version;
  - a kind, subject, or payload schema not allowed for the producer;
  - an ID mismatch;
  - a missing parent;
  - a lineage violation;
  - a prohibited-content match;
  - a non-null unauthorized probability;
  - a scenario relation without authorization.
- **Sanitized errors.** Fixed category tokens only, following the existing
  agent and serialization error conventions: no path, SQL, payload, provider
  message, or exception text.
- **No secrets, raw headers or bodies, or hidden reasoning** (§E).
- **Model runs.** Model name as returned, request-schema ID, prompt-template
  ID and hash, evidence-package hash, output hash (§B.6).
- **Exact parents.** Every derived item lists its exact parent IDs.
- **Consumer access boundaries.** Each consumer reads only through bundles
  built for its registered purposes and permissions. Every read is a
  `consumer_access` audit event.
- **Deterministic recomputation.** Any `deterministic_calculation` can be
  recomputed from its `calculation_input` digest, commit and configuration;
  a mismatch is a `producer_correction` investigation, never an in-place
  fix.
- **Holdout guard.** A product-side guard that *implements* the
  preregistration's rule ("not ingested, built, or evaluated before the
  2026-12-04 session has finished"). It changes nothing in that rule.
  - **What it blocks.** Product ingestion, construction, display, bundling,
    and inference over **SPY price evidence dated 2026-09-23 through
    2026-12-04**, until the holdout has been evaluated and recorded.
  - **What counts as SPY price evidence.** Any item whose payload or source
    data contains SPY underlying price, volume or trade values, or values
    derived from them: bars, snapshots, session calculations, regime
    features, VWAP distances, and selector inputs or results that carry an
    underlying price.
  - **How it is enforced.** Adapters refuse to emit such items (availability
    `refused`, reason `holdout_restricted`); the store refuses to append
    them; the bundle builder lists the producer as `holdout_restricted`;
    consumers never display or reason over them.
  - **What it does not block.** Synthetic fixtures, and already-authorized
    historical fixtures dated outside the holdout window.
  - **What it does not authorize.** It authorizes no activity after
    2026-12-04. Every later activity still needs its own authorization.
  - **Removal.** Deactivating or removing the guard requires the recorded
    holdout milestone **and** a separate authorization.

---

## P. Future implementation test matrix (listed, not created)

| Area | Tests |
|---|---|
| Contract validation | every contract rejects extra fields, wrong types, out-of-range sizes, naive timestamps, non-canonical decimals, NaN/Infinity |
| Canonical serialization | golden byte fixtures; key order; null retention; set-list sorting; duplicate rejection; decimal/rational/timestamp encoding |
| Deterministic IDs | same inputs → same ID; only the §L.2 operational fields are excluded; identical observations at different effective/source times → different IDs; retry with the same temporal identity → same ID; bundle `as_of_utc` changes the bundle ID; prefixes correct |
| Enum coverage | every enum value exercised; unknown values refused |
| Unknown schema refusal | unknown family version, producer version, payload schema, registry version |
| Freshness boundaries | exact boundary instants (current/aging/stale); off-hours rules; unset policy → `unknown`; monotonicity property test |
| Clock failures | missing, unhealthy, future-dated clock facts → `unknown` |
| Evidence-kind separation | every row of §C.2; model path cannot produce a fact or calculation; an aging item keeps its kind and ID at T1/T2/T3 while its evaluated freshness changes; status items never satisfy a requirement; missing evidence never becomes a zero or neutral value |
| Research references | missing reference refused; label outside the schema's enum refused; missing fixed caveat refused; holdout values refused while `sealed` |
| Revision/supersession | chains, branches (conflict raised), point-in-time bundles unaffected by later corrections |
| Conflict detection | each rule; no winner field; a resolution supersedes only the prior conflict-status record; involved evidence unchanged; critical conflict blocks readiness |
| Parent cycles | self-parent, unknown parent, later-generated parent, deep lineage taint |
| Bundle hashes | reproducible; `built_at_utc` excluded; entry order independence |
| Missing producers | required producer absent, unimplemented, holdout-restricted |
| Stale required evidence | readiness false; filters cannot hide it |
| Assistant citations | every material claim cited; citations resolve inside the bundle; kind labels rendered |
| No fabricated confidence | non-null probability refused; graded strength without rubric refused |
| Inference default deny | every `current_inference` ineligible without an inference-input authorization; a ranker refuses inference; inference can never change eligibility, research labels or probabilities; setup lifecycle changed by inference refused unless the setup's own rules permit it |
| Holdout guard | synthetic SPY evidence dated in the window refused by adapters, store, bundle builder and consumers; synthetic and non-holdout fixtures accepted; guard cannot be deactivated by configuration |
| Directional-authority enforcement | existing agents cannot emit relations; directional relation refused without authorization; policy filter on display text |
| Selector-boundary enforcement | contract classification from any producer except the selector refused; inference cannot change selector outcomes; `research_only` never appears as eligible in any consumer or ranking; eligible and research-only rankings remain separate (no mixed ranking); contracts absent from the selector's returned sets cannot be introduced by a ranker, assistant, dashboard or notification; rejected contracts cannot be introduced by inference or ranking; rejected contracts are never materialized individually by any downstream consumer (including by diffing the option-chain batch against the returned sets); rejection counts and reasons remain byte-identical from selector result to every consumer view; assistant and dashboard render each returned contract's original set unchanged next to any ranking |
| Notification-boundary enforcement | non-authorized events refused; recommendation phrasing refused; evidence IDs recorded; notification inputs cannot bypass or restate selector outcomes (a research-only contract can never be alerted as eligible; a rejected contract can never be named) |
| Sanitization | credentials, headers, URLs with credential query names, paths, exception text, prompt text all refused |
| Synthetic end-to-end | synthetic-only dashboard and chat flows (no real SPY data from the holdout window, no provider, no DuckDB) |

---

## Q. Decisions and open questions

### Q.1 Fixed by PRODUCT_VISION

- Five evidence categories: fact, calculation, research, inference,
  missing/stale (vision §5).
- The existing agents are non-directional; directional reasoning belongs only
  to separately authorized components (vision §3).
- Contracts come only from the deterministic selector; research-only
  candidates are never presented as eligible (vision §7).
- "No qualified setup" is a valid output; lanes do not substitute for each
  other (vision §4).
- Manual final decisions; no execution (vision §6).

### Q.2 Made in this design

- One family version `evidence-envelope-1`; nested contracts versioned by
  their parent record.
- Six kinds, with missing and stale as explicit status kinds. Kind is
  independent of the bundle-time freshness evaluation, and an item keeps
  its kind as it ages.
- Freshness is never stored in an item.
- Substantive temporal fields are part of every identity; only operational
  metadata is excluded.
- Model inference is default-deny for machine decisions: not eligible
  today, with a separately authorized future path (§H.2).
- Lineage taint: no fact, calculation or research result descends from
  inference (permanent).
- `unknown` freshness is gated like `stale`.
- Separate quality dimensions; `confidence_not_available` by default; no
  rubric or calibrated-probability authorization registered.
- Scenario relations require a registered scenario and authorization record;
  none exist.
- Content-addressed IDs with explicit exclusions; `recorded_at_utc` defines
  point-in-time knowledge.
- Conflicts have no winner; a resolution supersedes the prior
  conflict-status record, never the evidence.
- A product-side holdout guard on SPY price evidence through 2026-12-04.

### Q.3 Future decisions (each needs review)

- Live intraday freshness parameters for bars, snapshot and option chain
  (registry lists them as unset).
- The clock-health policy thresholds for the product.
- Storage: a DuckDB migration for append-only evidence tables, or a separate
  store.
- The machine-readable registry file format and location.
- The first scenario definitions, and whether any non-directional relation
  producer is authorized.
- Strength rubrics, if any.
- The setup-card contract and the first setup definitions (roadmap priority
  3).
- The notification-event taxonomy (roadmap priority 6).
- Whether to authorize a future scenario/setup component to consume
  inference, and under which scenario definitions, rubrics and grants
  (§H.2, §H.4).
- Whether to authorize a future contract-ranking component, which could
  only rank within the returned eligible set and, separately, the returned
  research-only set (§H.3). Contract
  eligibility itself is not an open question: it stays deterministic.

### Q.4 Blockers

- **The sealed holdout.** Real SPY price evidence dated 2026-09-23 →
  2026-12-04 cannot be ingested, built, displayed, bundled or reasoned over
  before the holdout is evaluated and recorded (§O). Until then,
  implementation and end-to-end testing must use synthetic fixtures or
  already-authorized historical fixtures outside the holdout window.
- **No exchange calendar.** Market-hours-aware freshness and session subjects
  cannot distinguish holidays or early closes; policies that need them stay
  unset.
- **No scheduled-catalyst source.** `scheduled_catalyst` subjects and the
  regime engine's `catalyst_state` have no approved source.
- **SOURCE_POLICY uncertainty gap.** There is still no stored uncertainty
  field for news. `uncertainty_codes` can carry fixed codes, but a news
  verification status needs its own design.
- **Not reviewed.** This design, the registry and the consumer rules must be
  reviewed before any implementation authorization.

### Q.5 Authorization required for offline implementation

An explicit instruction that:
1. names this design, the registry and the consumer rules at a reviewed
   commit;
2. authorizes **offline** implementation only: Pydantic contracts, canonical
   serialization, ID functions, validators, freshness evaluation, and
   in-memory or temporary-file tests;
3. confirms no DuckDB migration, no write to the real database, no provider
   or network request, no model call, no holdout-window SPY data, and no
   dashboard, assistant, SMS, or notification implementation;
4. lists which producer adapters (if any) may be written, and states that
   they read only already-stored, pre-holdout, or synthetic data;
5. restates that no directional, calibrated-probability, notification, or
   Options Strategy Agent authorization is granted.
