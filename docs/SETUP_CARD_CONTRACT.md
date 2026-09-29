# Setup-Card Contract — `setup-card-1`

**Status: REVIEWED DESIGN — accepted by the merge that introduces this
document (2026-09-29). DESIGN ONLY.** This
specifies the versioned setup-card contract behind the dashboard's Live
Scanner, Setup Detail and Contract Review
([DASHBOARD_INFORMATION_ARCHITECTURE.md](DASHBOARD_INFORMATION_ARCHITECTURE.md)),
implementing the card content of [vision §7](PRODUCT_VISION.md).

- **Merge accepts the design only.** The merge that introduced this document
  accepted the design and authorizes no dashboard, setup-card implementation,
  assistant registry grant, storage, adapter, ranking, scenario,
  notification, contract-selector change or trading capability.
- **Implementation status (2026-09-29).** An offline core of this contract
  is reviewed and merged in `market_intelligence/setup_cards/` under a
  separate authorization (PROJECT_STATE item 61): contracts, identity,
  validation, supersession and a pure builder over in-memory Evidence
  Envelope objects.
  No setup definition is registered, so the production builder produces
  **no card of either kind**. No card storage, dashboard, assistant grant or
  other consumer exists or is authorized.
- **`no_qualified_setup` correction (2026-09-29).** The `no_qualified_setup`
  rules (§1, §3, §4.8, §5.2, §9.2, §9.5, §10) were revised to match the
  implementation: the card presents only a cited, authorized deterministic
  `not_qualified` lane conclusion, and the former `lane_not_authorized`,
  `lane_not_researched` and `evidence_not_ready` card reasons are dashboard
  availability states outside this contract. The correction keeps this
  document's reviewed-design status and is accepted through the merge of
  the implementation (PROJECT_STATE item 61), which enforces every §4.8
  condition: blocked evidence yields the `evidence_blocked` availability
  state and no card.
- **Governing documents win.** [DECISION_RULES.md](../DECISION_RULES.md),
  the reviewed [Evidence Envelope design](EVIDENCE_ENVELOPE_DESIGN.md),
  [registry](EVIDENCE_REGISTRY.md),
  [consumer rules](EVIDENCE_CONSUMER_RULES.md), frozen research protocols and
  [PROJECT_STATE.md](../PROJECT_STATE.md) always govern.

**A setup card is an evidence-backed presentation object. It is not a trading
signal, a recommendation, or an order.** It has no order, quantity,
position-size, price-target, brokerage, or execution field, and none may be
added under this version.

---

## 1. What a card is

- **One card, one decision context.** Every card is built from exactly one
  Evidence Bundle (usually purpose `setup_detail`) and cites only entries of
  that bundle.
- **Two kinds.** A `setup` card describes one setup candidate in one lane. A
  `no_qualified_setup` card presents a cited, authorized deterministic
  lane-level evaluation that concluded `not_qualified` at an evaluation
  time, and its reason (§4.8).
- **No card without a conclusion.** `no_qualified_setup` never means "the
  system could not evaluate the lane". A lane with no registered setup
  definition, no authorized evaluation, or evidence that is not ready
  produces **no card**; the dashboard shows an availability state instead
  ([dashboard §6.2](DASHBOARD_INFORMATION_ARCHITECTURE.md#62-no_qualified_setup-and-lane-availability)).
- **Presentation, not evidence.** A card is not an `EvidenceItem`, is not
  emitted into the evidence store, and is never a machine-decision input.
  Whether cards are ever persisted is an open decision (§11).
- **Immutable.** A card never changes. A later bundle, correction, lifecycle
  change or expiration produces a **new card** that supersedes it (§6).

---

## 2. Conventions

The same conventions as the Evidence Envelope family (design §B.0):

- Pydantic v2, `extra="forbid"`, `frozen=True`, `strict=True`.
- Bounded enums for every behavior-changing field.
- Canonical serialization and SHA-256 identity per design §B.0, with only
  operational fields excluded.
- Shared primitive types (`ItemId`, `BundleId`, `ConflictId`, `UtcTimestamp`,
  `VersionLabel`, `CommitSha`, `ConfigIdentity`, `Token`) and the envelope's
  `EvidenceCitation`, `MissingProducer` and `AmbiguousRequirement` shapes are
  reused unchanged.
- The top-level record carries `schema_version: Literal["setup-card-1"]`.

---

## 3. `SetupCard` fields

| Field | Type | Null | Rule | In identity |
|---|---|---|---|---|
| `schema_version` | `Literal["setup-card-1"]` | no | exact | yes |
| `card_id` | `^scd1_[0-9a-f]{64}$` | no | recomputed and must match | (identity) |
| `card_kind` | `CardKind` enum: `setup`, `no_qualified_setup` | no | — | yes |
| `underlying` | `Literal["instrument:us_equity:SPY"]` | no | SPY only | yes |
| `lane` | `Lane` enum: `vwap_reversion`, `trend_continuation` | no | one lane per card | yes |
| `setup_subject_id` | canonical `setup:` subject ID | yes | required iff `card_kind = setup`; its lane segment must equal `lane` | yes |
| `setup_definition_id` | `VersionLabel` | yes | required iff `card_kind = setup`; must be a registered setup definition (**none is registered**) | yes |
| `session_date` | `SessionDate` | no | New York date of `effective_at_utc` | yes |
| `effective_at_utc` | `UtcTimestamp` | no | equals the bundle's `as_of_utc` | yes |
| `observed_at_utc` | `UtcTimestamp` | yes | earliest `source_observed_at_utc` among cited required entries; null only if none carries one | yes |
| `decision_context_bundle_id` | `BundleId` | no | the one bundle this card is built from | yes |
| `registry_version_id` | `RegistryVersionId` | no | equals the bundle's | yes |
| `evidence_readiness` | `EvidenceReadiness` enum (§5.1) | no | derived from the bundle's state (§4.4) | yes |
| `readiness_blockers` | `list[ReadinessBlocker]` | no | sorted, unique; non-empty iff `evidence_readiness = blocked` | yes |
| `qualification` | `SetupQualification` enum (§5.2) | no | derived; see rules | yes |
| `qualification_item_id` | `ItemId` | yes | the cited `deterministic_calculation` that decided qualification; required iff `qualification` is `qualified` or `not_qualified` | yes |
| `lifecycle_state` | `SetupLifecycle` enum (§5.3) | yes | required iff `card_kind = setup`; temporal progression only, never qualification | yes |
| `no_setup_reason` | `NoSetupReason` enum | yes | required iff `card_kind = no_qualified_setup`; `criteria_not_met` or `no_candidate_evaluated`, exactly the cited lane evaluation's reason (§4.8) | yes |
| `deterministic_rank` | `DeterministicRank` | yes | **must be null**: no ranking rubric is registered | yes |
| `interpretation` | `CardInterpretation` | yes | **must be null**: no inference-input authorization exists | yes |
| `supporting_citations` | `list[EvidenceCitation]` | no | 0–32; role `supports_claim` | yes |
| `contradicting_citations` | `list[EvidenceCitation]` | no | 0–32; role `contradicts_claim` | yes |
| `context_citations` | `list[EvidenceCitation]` | no | 0–32; role `context` (for example research) | yes |
| `missing_requirements` | `list[MissingProducer]` | no | **exactly** the bundle's `missing_required_producers` | yes |
| `ambiguous_requirements` | `list[AmbiguousRequirement]` | no | **exactly** the bundle's `ambiguous_requirements` | yes |
| `non_current_entries` | `list[CardFreshnessNote]` | no | every cited or required entry whose freshness is not `current`/`timeless` | yes |
| `conflicts` | `list[CardConflictNote]` | no | every recorded conflict in the bundle touching a cited or required item | yes |
| `research_references` | `list[CardResearchNote]` | no | one per cited `historical_research_result` item | yes |
| `scenario_relations` | `list[...]` | no | **must be empty**: no scenario definition or authorization exists | yes |
| `selector_availability` | `SelectorAvailability` enum (§5.6): `not_requested`, `available`, `unavailable` | no | always `not_requested` for a `no_qualified_setup` card; see §4.6 | yes |
| `contracts` | `CardContractSection` | yes | present iff `selector_availability = available`; see §4.6 | yes |
| `manual_decision_statement` | `Literal[...]` | no | exactly: "Decision support only. You make every trading decision manually. No order is placed." | yes |
| `expires_at_utc` | `UtcTimestamp` | yes | set only by a registered setup-definition rule; null otherwise | yes |
| `manual_review_ready` | `bool` | no | derived (§4.4); equals `evidence_readiness = ready`; separate from the bundle's `machine_decision_ready` | yes |
| `card_revision` | `CardRevision` | no | §6 | yes |
| `provenance` | `CardProvenance` | no | §3.1 | yes, except `generated_at_utc` |

### 3.1 Nested contracts

- **`ReadinessBlocker`** (enum): `missing_required_evidence`,
  `stale_required_evidence`, `unknown_freshness`, `ambiguous_requirement`,
  `ambiguous_clock_facts`, `unresolved_critical_conflict`,
  `holdout_restricted`, `ineligible_required_input`.
- **`NoSetupReason`** (enum, as carried by a card): `criteria_not_met`,
  `no_candidate_evaluated`. Both are deterministic lane conclusions. A lane
  that is not authorized, not researched, or whose evidence is not ready has
  no conclusion, so it has no card and no reason (§4.8).
- **`CardFreshnessNote`**: `item_id`, `state` (`aging`, `stale`, `unknown`),
  `reason` (the bundle's `FreshnessReason`), `required` (bool).
- **`CardConflictNote`**: `conflict_id`, `conflict_type`, `severity`,
  `status` (the current status record's), `involved_item_ids`.
- **`CardResearchNote`**: `item_id`, `study_id`, `result_kind`,
  `primary_label`, `secondary_labels`, `research_status`, `holdout_state`,
  `fixed_caveats` — all copied from the cited item's research reference.
- **`CardContractSection`** (present only when `selector_availability =
  available`): `selector_result_item_id` (required), `selector_outcome`
  (required; exactly one of the selector's four outcomes, §5.5), `feed`
  (`opra`, `indicative`), `eligible_contracts` (OSI symbols),
  `research_only_contracts` (OSI symbols), `rejection_counts`
  (`dict[RejectionReason, int]`).
- **`DeterministicRank`** (reserved): `rubric_id`, `position`, `of_count`,
  `lane`. Must be null in this version.
- **`CardInterpretation`** (reserved): inference citations plus an
  `authorization_record_id`. Must be null in this version.
- **`CardRevision`**: `revision_number` (≥ 1), `supersedes_card_id`
  (nullable), `revision_reason` (`original`, `newer_bundle`,
  `evidence_correction`, `lifecycle_change`, `expiration`).
- **`CardProvenance`**: `card_builder_id`, `card_builder_version`,
  `code_commit_sha`, `code_tree_clean`, `configuration_identity`,
  `generated_at_utc` (operational; excluded from identity).

---

## 4. Validation rules

### 4.1 Citations resolve inside the bundle

- Every citation's `bundle_id` equals `decision_context_bundle_id`, and every
  cited item is an entry of that bundle (the envelope's
  `validate_citations`).
- `cited_kinds` match the bundle entries, so the renderer always knows each
  item's kind.
- The card embeds **no uncited evidence**: every item ID anywhere on the card
  (`qualification_item_id`, research notes, the selector reference, freshness
  and conflict notes) is a bundle entry, and every value shown is read from a
  cited item.

### 4.2 Kinds stay distinguishable

- Supporting and contradicting citations may cite facts, calculations,
  research results, and status items, never `current_inference`: relating an
  inference to a setup needs a scenario or inference-input authorization, and
  none exists. Inference may appear only in `context_citations`, labelled as
  inference, and never as the qualification basis.
- `qualification_item_id` must cite a `deterministic_calculation` from the
  registered setup-definition producer. It can never be an inference, fact
  or research result.

### 4.3 Missing and stale cannot disappear

- `missing_requirements` and `ambiguous_requirements` must equal the
  bundle's, byte for byte.
- `non_current_entries` must list every cited or required entry whose
  bundle freshness is `aging`, `stale` or `unknown`.
- `conflicts` must include every recorded conflict in the bundle that touches
  a cited or required item.

### 4.4 Evidence readiness and `manual_review_ready`

- **Definition.** `manual_review_ready = true` means exactly: **the card
  contains enough current, non-conflicting evidence to be presented to the
  user for manual review.**
- **It never means** that the setup should be traded, that the setup is
  qualified, that a contract is eligible, that the system recommends action,
  or that the evidence is predictively validated. Qualification (§5.2) and the
  selector outcome (§5.5) are separate fields, and a `not_qualified` setup
  card can be `manual_review_ready`. A `no_qualified_setup` card is presented
  only when evidence readiness is `ready` (§4.8).
- **Derivation.** `evidence_readiness` is derived from the card's bundle
  state: missing and ambiguous requirements, the freshness of every cited or
  required entry, clock health (including `ambiguous_clock_facts`),
  unresolved critical conflicts touching cited or required items, holdout
  restriction, and eligibility of required inputs. It is `ready` only if no
  blocker applies; otherwise `blocked`, with every applicable
  `readiness_blockers` value.
- `manual_review_ready` equals `evidence_readiness = ready`. When it is
  `false`, `readiness_blockers` must be non-empty and bounded (§3.1).
- **An unresolved critical conflict always blocks** (`unresolved_critical_conflict`).
- **Separate from the bundle's `machine_decision_ready`.** That flag answers
  whether a machine consumer may use the bundle. `manual_review_ready`
  answers only whether this card may be presented for a person's manual
  review. Neither is derived from, copied into, or substituted for the
  other.

### 4.5 Inference cannot change deterministic outcomes

- `qualification` and `lifecycle_state` derive only from the setup's
  registered, reviewed deterministic rules, and independently of each other
  (§5.2, §5.3).
- Inference may change them only if that setup definition's reviewed rules
  explicitly allow inference as an input (design §H.4). No setup definition
  exists, so in this version inference can never change them.
- Inference can never change contract eligibility (design §H.3).

### 4.6 Contracts stay the selector's

- **Availability is separate from outcome.** `selector_availability` is
  `not_requested` (no selector run was requested for this card), `available`
  (a valid selector result is cited), or `unavailable` (a run was needed but
  no valid result exists, for example the producer is missing or its result
  is stale or refused).
- When `available`: `contracts` is present, cites exactly one selector result
  item from `deterministic_contract_selector`, and carries exactly one of the
  selector's four outcomes.
- When `not_requested` or `unavailable`: `contracts` is null, so the selector
  outcome, the result reference and every contract list are absent.
- **An unavailable selector is never shown as `no_eligible_contracts`.**
  `no_eligible_contracts` is a real deterministic selector result, not an
  infrastructure state.
- Returned eligible and research-only contracts may appear only when a valid
  selector result is referenced.
- `eligible_contracts`, `research_only_contracts` and `rejection_counts` must
  equal the selector result exactly (the envelope's `validate_contract_view`
  semantics): no omission, addition, duplicate, status change, or count
  change.
- **Rejected contracts are never listed**, individually or reconstructed;
  only `rejection_counts` appear.
- A `no_qualified_setup` card has `selector_availability = not_requested`
  and no contract section.

### 4.7 No execution surface

The contract has no field for orders, quantities, position size, stops,
targets, prices to enter, brokerage identifiers or execution. A validator
refuses any such field name, and `extra="forbid"` refuses unknown fields.

### 4.8 `no_qualified_setup` requires an authorized lane conclusion

A `no_qualified_setup` card is allowed only when **all** of these hold:

1. the lane has a registered setup definition;
2. that definition's authorized deterministic producer evaluated the lane;
3. the bundle contains, and the card cites, **exactly one** current
   lane-level evaluation (no setup subject) concluding `not_qualified`,
   as the card's `qualification_item_id`;
4. that evaluation's lifecycle is `evaluation_complete`, `invalidated` or
   `expired`;
5. the card's `no_setup_reason` (`criteria_not_met` or
   `no_candidate_evaluated`) and `expires_at_utc` exactly match the cited
   evaluation;
6. `selector_availability` is `not_requested`, with no contract section;
7. evidence readiness is `ready`.

Otherwise no card is produced:

| Situation | Result |
|---|---|
| No registered setup definition for the lane | no card; dashboard shows `not_authorized` |
| Evaluation missing, unauthorized, or from an unregistered producer | no card; dashboard shows `unavailable` |
| Evaluation concluded `not_evaluated`, `indeterminate` or `qualified` | no `no_qualified_setup` card |
| Stale, missing, ambiguous or critically conflicted required evidence | no new conclusion card; dashboard shows `evidence_blocked` |
| More than one lane-level conclusion | no card; dashboard shows `unavailable`; never resolved by picking one |

The availability states are dashboard states outside this contract; they
have no `scd1_` identity
([dashboard §6.2](DASHBOARD_INFORMATION_ARCHITECTURE.md#62-no_qualified_setup-and-lane-availability)).

---

## 5. State models (accepted design; not implemented or authorized)

Seven separate vocabularies. **They are never collapsed into one generic
"status".**

### 5.1 Evidence readiness

`ready`, `blocked` (with `readiness_blockers`).

| From | To | Allowed? |
|---|---|---|
| (new card) | `ready` / `blocked` | yes, derived from the bundle |
| `blocked` | `ready` | only in a **new card** built from a newer bundle |
| `ready` | `blocked` | only in a **new card** |
| any | any, within the same card | **forbidden** (cards are immutable) |

`manual_review_ready` is the boolean form of this vocabulary (§4.4).

### 5.2 Setup qualification

`qualified`, `not_qualified`, `indeterminate`, `not_evaluated`.

**Qualification answers: "what did the authorized deterministic setup rules
conclude?"**

- `qualified` / `not_qualified` require a cited deterministic qualification
  calculation.
- `indeterminate`: the calculation ran but could not decide (for example
  incomplete session).
- `not_evaluated`: no qualification calculation has concluded. **Today it is
  the only possible value**, because no live setup definition is registered.

| Forbidden | Why |
|---|---|
| any → `qualified` without a cited deterministic calculation | qualification is deterministic |
| any → `qualified` because of inference | inference cannot qualify a setup (§4.5) |
| `trend_continuation` → anything but `not_evaluated` | no trend research or definition exists |
| a `no_qualified_setup` card with anything but a cited `not_qualified` | the card presents a completed lane conclusion only (§4.8) |

### 5.3 Setup lifecycle

`observed`, `developing`, `evaluation_complete`, `invalidated`, `expired`
(setup cards only).

**Lifecycle answers: "where is this observation in time and processing?"**
It describes temporal progression only and never says whether the setup
qualified.

- `observed`: a setup candidate was recorded; evaluation has not started.
- `developing`: evaluation is under way; conditions are still forming.
- `evaluation_complete`: the authorized deterministic rules have finished
  evaluating. Its qualification may be `qualified`, `not_qualified` or
  `indeterminate`.
- `invalidated`: a registered invalidation rule ended the setup. Terminal.
- `expired`: `expires_at_utc` passed. Terminal.

**Relationship to qualification.**
- Qualification never rewrites lifecycle, and lifecycle never implies
  qualification.
- While lifecycle is `observed` or `developing`, qualification is
  `not_evaluated`.
- `evaluation_complete` requires qualification `qualified`, `not_qualified`
  or `indeterminate`.
- `invalidated` and `expired` keep whatever qualification the setup already
  had; reaching a terminal state never changes it.
- Today, with no registered live setup definition, qualification stays
  `not_evaluated`, so no card can reach `evaluation_complete`.
- A `no_qualified_setup` card has no `lifecycle_state` of its own; the lane
  evaluation it cites must be at `evaluation_complete`, `invalidated` or
  `expired` (§4.8).

| From | To | Allowed? | Condition |
|---|---|---|---|
| (new setup) | `observed` | yes | first card for the setup |
| (new setup) | `developing` | yes | first card, evaluation already under way |
| `observed` | `developing` | yes | evaluation started |
| `observed` / `developing` | `evaluation_complete` | yes | the authorized rules concluded (qualification `qualified`, `not_qualified` or `indeterminate`) |
| `observed` / `developing` / `evaluation_complete` | `invalidated` | yes | registered invalidation rule met |
| `observed` / `developing` / `evaluation_complete` | `expired` | yes | `expires_at_utc` passed |
| `developing` / `evaluation_complete` | `observed` | **forbidden** | lifecycle never moves backward |
| `evaluation_complete` | `developing` | **forbidden** | a completed evaluation never reopens; a new observation is a new setup |
| `invalidated` / `expired` | any state | **forbidden** | terminal; no transition returns a terminal setup to an active state |
| any | a different lane | **forbidden** | lanes never substitute for each other |
| any | any, because qualification changed | **forbidden** | qualification never drives lifecycle |

Lifecycle changes always create a new card revision (`lifecycle_change`).

### 5.4 Display status (render-time, never stored in the card)

`current_view`, `outdated_view`, `superseded`, `historical`.

- Computed by the dashboard when rendering, like freshness: a card's age
  since `effective_at_utc` is compared with a view policy (**unset**; live
  refresh cadence is an open decision). While unset, a card older than its
  bundle's freshness allows is shown as `outdated_view`.
- `superseded`: a newer card revision exists. `historical`: the user opened a
  past card on purpose.

| Forbidden | Why |
|---|---|
| `superseded` → `current_view` | a superseded card never becomes current again |
| `outdated_view` → `current_view` for the same card | only a new card built from newer evidence is current |

### 5.5 Contract-selector outcome

Exactly the selector's own four `SelectorStatus` values: `eligible`,
`no_eligible_contracts`, `research_only`, `indeterminate`. Nothing is added
to this vocabulary. It exists only inside a `contracts` section, which exists
only when `selector_availability = available`.

| Forbidden | Why |
|---|---|
| `research_only` → `eligible` on any card | only a new selector run on OPRA data can produce `eligible` |
| any outcome changed by inference, ranking, assistant or dashboard | eligibility is the selector's, permanently |
| an infrastructure problem recorded as `no_eligible_contracts` | that is a real selector result, not an availability state |

### 5.6 Selector availability

`not_requested`, `available`, `unavailable`.

| Availability | `contracts` section | Selector outcome and result reference |
|---|---|---|
| `not_requested` | null | null |
| `available` | present | exactly one real outcome and one cited selector result |
| `unavailable` | null | null |

| Forbidden | Why |
|---|---|
| `unavailable` shown or stored as `no_eligible_contracts` | availability and outcome are different questions |
| any contract list while availability is not `available` | contracts appear only from a valid, referenced selector result |
| `available` without a cited selector result item | an outcome must come from a real result |

### 5.7 Research status

The envelope's `ResearchStatus` (`exploratory`, `confirmed`,
`shadow_pending`, `unsupported`, `not_applicable`) plus `holdout_state`
(`sealed`, `recorded`, `not_applicable`), copied from recorded results.

| Forbidden | Why |
|---|---|
| `shadow_pending` → `confirmed` on a card | only a new recorded research result can change status |
| `holdout_state` → `recorded` before the holdout result is recorded | frozen protocol |
| any research status set from live evidence or inference | research is historical and recorded |

---

## 6. Identity and supersession

- **Identity.** `card_id = "scd1_" + SHA-256(canonical card)`, excluding only
  `card_id` and `provenance.generated_at_utc`. `effective_at_utc`, the bundle
  ID and every citation are in the identity.
- **Retries.** The same builder, configuration and bundle produce the same
  card ID.
- **Supersession.** A new card for the same setup subject (or the same lane
  and session for `no_qualified_setup`) carries `supersedes_card_id` and
  `revision_number` + 1. The older card is never edited or deleted.
- **Point in time.** A card records exactly what its bundle contained. A
  later correction to evidence creates a new card; it never changes what an
  earlier card showed (DECISION_RULES "No retroactive forecast editing").
- **Branches.** Two cards superseding the same card are refused; the builder
  must build from one bundle at a time.

---

## 7. Relationship to the Evidence Envelope

- **Inputs.** A card is built by a future, unauthorized card builder from one
  bundle. It reads entries, freshness, missing and ambiguous requirements,
  conflicts, and cited items only.
- **Outputs.** The card is presentation. It is not appended to the evidence
  store and cannot be cited by evidence items.
- **Holdout.** A card can never cite SPY price evidence restricted by the
  holdout guard; the bundle already excludes it, and the card reports the
  requirement as `holdout_restricted`.
- **Qualification, ranking and interpretation** are themselves evidence
  items (deterministic calculations or authorized inferences) produced
  elsewhere. The card only cites them.

---

## 8. Forbidden content

- Order, quantity, position size, stop, target, entry or exit price.
- Brokerage, account, or execution identifiers.
- Recommendation wording, guaranteed-direction wording, or any probability
  or confidence number not produced by an authorized calibrated model (none
  exists).
- Uncited values, rejected-contract lists, or merged eligible and
  research-only lists.
- Free text used for decisions. Display text, if ever added, follows the
  envelope's `DisplayText` rules and the non-directional content policy.

---

## 9. Synthetic examples

All examples are **synthetic and abbreviated**. Identifiers such as
`evi1_<syn-bar-1>` stand for full 64-hex IDs; they are placeholders, not
valid IDs. Dates are in January 2027, outside the sealed holdout window. No
market value appears.

### 9.1 A qualified VWAP-reversion card (future, illustrative)

Illustrates the shape once a live VWAP setup definition is separately
authorized. **Today no such definition is registered, so this card cannot be
produced.**

```yaml
schema_version: setup-card-1
card_id: scd1_<syn-card-1>
card_kind: setup
underlying: instrument:us_equity:SPY
lane: vwap_reversion
setup_subject_id: setup:vwap_reversion:<syn_definition>:SPY:20270112T150500Z
setup_definition_id: <syn-definition-1>
session_date: 2027-01-12
effective_at_utc: 2027-01-12T15:06:00.000000Z
decision_context_bundle_id: evb1_<syn-bundle-1>
evidence_readiness: ready
readiness_blockers: []
qualification: qualified             # what the authorized rules concluded
qualification_item_id: evi1_<syn-qualification-calc>
lifecycle_state: evaluation_complete # where the setup is in time and processing
deterministic_rank: null            # no ranking rubric registered
interpretation: null                # no inference authorization
supporting_citations:
  - {role: supports_claim, cited_item_ids: [evi1_<syn-bar-1>, evi1_<syn-vwap-calc>],
     cited_kinds: [confirmed_fact, deterministic_calculation]}
contradicting_citations:
  - {role: contradicts_claim, cited_item_ids: [evi1_<syn-regime-calc>],
     cited_kinds: [deterministic_calculation]}
context_citations:
  - {role: context, cited_item_ids: [evi1_<syn-confirmation-result>],
     cited_kinds: [historical_research_result]}
missing_requirements: []
ambiguous_requirements: []
non_current_entries: []
conflicts: []
research_references:
  - {study_id: spy_vwap_reversion, result_kind: confirmation_result,
     primary_label: supported_for_further_shadow_research,
     research_status: shadow_pending, holdout_state: sealed,
     fixed_caveats: [underlying_setup_only_no_options_or_pnl,
                     research_result_not_validation, "..."]}
scenario_relations: []
selector_availability: available
contracts: {selector_result_item_id: evi1_<syn-selector-result>, "...": "see 9.6"}
manual_decision_statement: "Decision support only. You make every trading decision manually. No order is placed."
manual_review_ready: true           # evidence complete enough to present; not a recommendation
```

### 9.2 Trend lane, unavailable (a dashboard state, not a card)

**This is not a setup card.** It is a dashboard availability state outside
the card contract, with no `scd1_` identity and no card fields. No card of
either kind is produced, because no trend setup definition is registered
and no authorized evaluation exists (§4.8).

```yaml
# dashboard lane state, not a setup-card-1 object
lane: trend_continuation
lane_state: not_authorized
```

Displayed as "Trend lane unavailable — no researched or registered setup
definition exists".

### 9.3 Blocked by stale evidence

```yaml
card_kind: setup
lane: vwap_reversion
lifecycle_state: developing
qualification: not_evaluated
evidence_readiness: blocked
readiness_blockers: [stale_required_evidence]
non_current_entries:
  - {item_id: evi1_<syn-bar-1>, state: stale, reason: beyond_stale_boundary, required: true}
missing_requirements:
  - {requirement_id: req.bars.spy, producer_id: <syn-bars-producer>, reason: only_stale_items}
selector_availability: unavailable   # not shown as no_eligible_contracts
contracts: null
manual_review_ready: false
```

### 9.4 Blocked by a critical conflict

```yaml
card_kind: setup
lane: vwap_reversion
lifecycle_state: developing
qualification: not_evaluated
evidence_readiness: blocked
readiness_blockers: [unresolved_critical_conflict]
conflicts:
  - {conflict_id: evc1_<syn-conflict-1>, conflict_type: provider_disagreement,
     severity: critical, status: unresolved,
     involved_item_ids: [evi1_<syn-bar-1>, evi1_<syn-bar-2>]}
selector_availability: not_requested
contracts: null
manual_review_ready: false
```

Both sides of the conflict are shown; no winner is chosen.

### 9.5 `no_qualified_setup` (future, illustrative)

Illustrative only: no setup definition is registered today, so this card
cannot be produced. It is allowed because the bundle contains exactly one
lane-level evaluation from the registered producer, cited below, with
lifecycle `evaluation_complete`, qualification `not_qualified` and reason
`criteria_not_met`, and evidence is ready (§4.8).

```yaml
card_kind: no_qualified_setup
lane: vwap_reversion
no_setup_reason: criteria_not_met   # exactly the cited evaluation's reason
qualification: not_qualified
qualification_item_id: evi1_<syn-qualification-calc>  # the lane evaluation
expires_at_utc: null                # exactly the cited evaluation's expiry
evidence_readiness: ready
readiness_blockers: []
selector_availability: not_requested
contracts: null
manual_review_ready: true           # the result can be presented; nothing qualified
```

Displayed with neutral styling: "No qualified setup in the VWAP-reversion
lane at 10:06 ET. This is a valid result."

### 9.6 Contract review with separate sets

Two synthetic selector results are shown, because a single selector run
returns either an OPRA eligible set or an indicative research-only set.

```yaml
# OPRA selector run
selector_availability: available
contracts:
  selector_result_item_id: evi1_<syn-selector-opra>
  selector_outcome: eligible
  feed: opra
  eligible_contracts: [<SYN-OSI-A>, <SYN-OSI-B>]
  research_only_contracts: []
  rejection_counts: {spread_too_wide: 1, delta_outside_range: 2, "...": 0}
---
# Indicative selector run (research mode)
selector_availability: available
contracts:
  selector_result_item_id: evi1_<syn-selector-indicative>
  selector_outcome: research_only
  feed: indicative
  eligible_contracts: []
  research_only_contracts: [<SYN-OSI-C>]
  rejection_counts: {spread_too_wide: 3, "...": 0}
```

Rendered as two separate sections: "Eligible (OPRA)" and "Research only —
not eligible (indicative)". Rejected contracts appear only as the counts.

If no valid selector result exists, the card instead carries
`selector_availability: unavailable` and `contracts: null`, rendered as
"Contract selector unavailable" — never as "No eligible contracts".

### 9.7 Conversational explanation citing the card's bundle

```yaml
assistant_answer:
  bundle_id: evb1_<syn-bundle-1>          # the same bundle as card 9.1
  claims:
    - text: "[Fact] The latest stored 5-minute bar was recorded at 10:05 ET."
      citation: {bundle_id: evb1_<syn-bundle-1>, claim_index: 0,
                 cited_item_ids: [evi1_<syn-bar-1>], role: supports_claim,
                 cited_kinds: [confirmed_fact]}
    - text: "[Research] The confirmation study labelled this setup
             supported for further shadow research; this is not validation."
      citation: {bundle_id: evb1_<syn-bundle-1>, claim_index: 1,
                 cited_item_ids: [evi1_<syn-confirmation-result>], role: context,
                 cited_kinds: [historical_research_result]}
    - text: "[Calculation] The regime calculation contradicts a reversion
             reading in this bundle."
      citation: {bundle_id: evb1_<syn-bundle-1>, claim_index: 2,
                 cited_item_ids: [evi1_<syn-regime-calc>], role: contradicts_claim,
                 cited_kinds: [deterministic_calculation]}
  closing: "Decision support only. You make every trading decision manually."
```

The answer cites only entries of the card's bundle, labels each claim's
kind, gives no direction or probability, and names no contract beyond the
selector's returned sets.

---

## 10. Future implementation test matrix (listed, not created)

- strict contract validation; forbidden execution-field names refused;
- canonical identity; retries give the same `card_id`; supersession chain;
- citations resolve inside the bundle; uncited IDs refused;
- missing, ambiguous, stale and conflict lists equal the bundle's;
- critical conflict and ambiguity block readiness;
- qualification only from a cited deterministic calculation; inference
  cannot qualify;
- `deterministic_rank`, `interpretation` and `scenario_relations` must be
  empty;
- contract section equals the selector result exactly; no rejected contract
  listed; research-only never shown as eligible;
- `no_qualified_setup` cards have `selector_availability = not_requested`,
  no contract section, and render neutrally;
- a `no_qualified_setup` card requires exactly one cited lane-level
  `not_qualified` evaluation from the registered producer, at
  `evaluation_complete`, `invalidated` or `expired`, whose reason and expiry
  the card repeats; only `criteria_not_met` and `no_candidate_evaluated`
  are accepted;
- no card of either kind for: no registered definition, a missing or
  unauthorized evaluation, `not_evaluated`, or stale, missing, ambiguous or
  critically conflicted required evidence; these are availability states;
- selector availability and outcome stay separate: `unavailable` and
  `not_requested` carry no outcome, reference or contract list, and are
  never shown as `no_eligible_contracts`;
- `manual_review_ready` equals `evidence_readiness = ready`, needs
  blockers when false, and is never derived from `machine_decision_ready`;
- lifecycle and qualification stay independent: `evaluation_complete`
  requires a concluded qualification, earlier states require
  `not_evaluated`, and terminal states never return to active;
- trend lane can only be `not_evaluated`;
- every forbidden transition in §5 refused;
- holdout-restricted requirements reported, never cited;
- synthetic end-to-end rendering with no real SPY holdout data.

---

## 11. Decisions and open questions

### 11.1 Decided by existing reviewed documents

- Card content (vision §7); manual-decision label; lanes; `no_qualified_setup`.
- Evidence kinds, citations, freshness, ambiguity and conflicts (Evidence
  Envelope design, as implemented offline).
- Selector eligibility and rejected-contract rules (design §H.3).
- No execution surface (DECISION_RULES).

### 11.2 Decided by this design (accepted by its merge)

- The `setup-card-1` contract, its field list and validation rules (§3–§4).
- The seven state vocabularies and their transition tables (§5), including
  the separate selector-availability vocabulary.
- The `manual_review_ready` field and its strict definition (§4.4).
- The `no_qualified_setup` conclusion rules (§4.8), with lane availability
  kept outside the card contract (corrected 2026-09-29; accepted through the
  implementation merge).
- Card identity, supersession and retry rules (§6).
- The `scd1_` identity prefix.
- The fixed manual-decision statement text.

### 11.3 Future decisions (not decided here)

- Whether and where cards are persisted.
- Setup definitions (including any live VWAP definition) and invalidation and
  expiration rules.
- Ranking rubrics and any inference-input authorization.
- Scenario definitions.
- Trend-day rules (requires its own research program first).
- The view policy for display status and the live refresh cadence.

### 11.4 Blockers

- No setup definition is registered, so no real `setup` card can reach
  `qualified` and no `no_qualified_setup` card can be produced.
- VWAP live qualification waits on the shadow-research stages (Stage 3 not
  authorized) and the sealed holdout.
- No producer adapters or evidence store exist to build real bundles.

### 11.5 Remaining unauthorized

The card builder, card storage, dashboard, assistant, ranking, scenario
relations, notifications, trend-day detection, the Options Strategy Agent,
and any trading or execution capability.
