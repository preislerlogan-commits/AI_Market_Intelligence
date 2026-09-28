# Evidence Consumer Rules

**Status: DESIGN ONLY, drafted and revised before review; awaiting review
(2026-09-28).** These
rules govern every future consumer of the Evidence Envelope designed in
[EVIDENCE_ENVELOPE_DESIGN.md](EVIDENCE_ENVELOPE_DESIGN.md), using the
producers and grants in [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md).

- **Nothing is implemented or authorized.** No dashboard, assistant, setup
  ranker, contract-review UI or notification layer exists, and none is
  authorized by these rules.
- **Governing documents win.** DECISION_RULES.md, SOURCE_POLICY.md, frozen
  research protocols and PROJECT_STATE.md boundaries override these rules.

## 1. Rules for every consumer

1. **Read through bundles only.** A consumer reads evidence only through an
   `EvidenceBundleManifest` built for one of its registered purposes. It
   never queries storage directly, never uses SQL, and never reads provider
   responses.
2. **Never mutate or relabel.** A consumer never edits, deletes, re-kinds,
   re-scores, or re-dates an item, conflict or bundle. It may only emit
   **new** items under its own producer identity, citing parents.
3. **Freshness comes from the bundle.** A consumer displays or gates on the
   bundle's `EvidenceFreshness` only. It never computes its own freshness
   and never converts `stale`, `aging` or `unknown` to `current`.
4. **Show the kind.** Anything shown to a person carries its kind label:
   - **Fact** (as recorded from a named source);
   - **Calculation**;
   - **Research result** (with study, label and caveats);
   - **Inference** (with the producer named);
   - **Missing**;
   - **Stale evidence notice** (for a `stale_evidence` status item).

   Evaluated freshness is shown separately, as a badge (rule 11).
5. **Absence is visible.** Missing required producers and stale or unknown
   required evidence are shown, never hidden or filled in.
6. **Conflicts are visible.** Every `material` or `critical` unresolved
   conflict touching displayed evidence is shown, with both sides. No
   consumer picks a winner.
7. **No fabricated numbers.** No consumer presents a probability,
   confidence percentage, or graded strength that the item does not carry
   under design §G. Default wording is "confidence not available".
8. **No execution.** No consumer places, modifies, routes, or schedules an
   order, or links to a brokerage action. Final trading decisions are
   manual (DECISION_RULES).
9. **Point in time.** A consumer output that records a decision context
   (a setup card, an alert, an answer) records its bundle ID. Later
   corrections never change what that bundle contained.
10. **Holdout.** No consumer displays, bundles, retrieves, reasons over,
    or tests with real SPY price evidence dated 2026-09-23 through
    2026-12-04 before the holdout is evaluated and recorded (design §O).
    Synthetic fixtures and already-authorized fixtures outside that window
    are unaffected.
11. **Kind is not freshness.** A consumer never treats an item's kind as
    its freshness. An aged `confirmed_fact` is still a fact, shown with its
    evaluated `stale` freshness; it is not relabelled "stale evidence".

## 2. Dashboard

- **May display** facts, calculations, research results and labelled
  inferences from its purposes (`premarket_briefing`, `live_market_state`,
  `setup_detail`, `contract_review`).
- **Must show freshness** for every displayed item. Show `aging` with its
  age; show `stale` and `unknown` distinctly and never styled like
  `current`.
- **Must show conflicts** (rule 1.6) next to the evidence they involve.
- **Must never relabel evidence.** For example, it must not show a regime
  label as a "signal", an inference as a fact, or `research_only` contracts
  as eligible.
- **Returned contracts only.** It displays individual contracts only from
  the selector's returned `eligible` and `research_only` sets. Rejected
  contracts appear only as aggregate counts by `RejectionReason`, unchanged
  (design §H.3).
- **Keeps live state and research apart.** Current system state (live
  facts, calculations and inferences) is visually separate from historical
  research (recorded study results with their caveats and `holdout_state`).
- **Setup cards** follow vision §7. Every card:
  - labels each input by kind;
  - cites research with its caveats;
  - shows the lane and lifecycle state;
  - carries the manual-decision label.
- **No qualified setup.** When no setup qualifies, the dashboard shows
  "no qualified setup" as a first-class result.
- **Shadow status.** Shows the shadow-research status (currently Stage 2
  `design_complete_test_pending`), not implied readiness.

## 3. Conversational assistant

- **Bounded retrieval.** Retrieves evidence **by ID or by a bounded
  `EvidenceQuery`** into an `assistant_question_context` bundle. Semantic
  search may only choose *which IDs to show a person*. Those IDs are then
  fetched through a bounded query and cited. Semantic retrieval is never a
  machine-decision input.
- **Cites every material claim** with an `EvidenceCitation` whose IDs all
  resolve inside the answer's bundle. A claim with no citation is phrased
  as the assistant's own inference and labelled as such, or omitted.
- **Labels inference.** Anything interpretive is marked as inference,
  naming its producer. The assistant's own explanations are
  `current_inference` items from `future_explanation_layer`.
- **States missing and stale evidence** that bears on the question, using
  `missing_evidence_notice` / `stale_evidence_notice` citations.
- **May summarize, never mutate.** A summary is new inference. It cannot
  change a cited item's kind, value, time, or caveats.
- **May compare scenarios** only by describing the cited evidence for and
  against each. Relations to registered scenarios require an authorization
  record (none exists). The assistant never states a guaranteed direction.
  Anything framed as a forecast must meet DECISION_RULES "Forecast
  Requirements".
- **May not invent probabilities** or confidence (rule 1.7).
- **May not bypass the selector.** It discusses only contracts in a
  `contract_selector_result` item. It never proposes a contract, strike, or
  expiration outside the returned sets. It never presents `research_only`
  as eligible, and always states which returned set each contract is in,
  including next to any future ranking. It never names, lists or
  reconstructs rejected contracts; it may cite only the aggregate rejection
  counts by `RejectionReason` (design §H.3).
- **May not turn "no qualified setup" into a recommendation.** It may
  explain why nothing qualified. It may not suggest a substitute trade, a
  looser criterion, or the other lane as a fallback (vision §4).
- **Existing agents stay non-directional.** It presents their items as
  non-directional inference and never re-frames them as directional.

## 4. Scanner / setup ranker (future, unauthorized)

- **Machine-decision mode.** Builds bundles with
  `machine_decision_mode = true`, and accepts only items with
  `machine_decision_eligible = true`.
- **No inference today.** `current_inference` is not an input: it is
  default-deny, and no inference-input authorization exists. A future,
  separately authorized scenario/setup component could consume selected
  inference only under all the conditions in design §H.2. Its output would
  be `current_inference`. It could affect a setup's lifecycle or
  qualification only where that setup's own reviewed research and decision
  rules explicitly permit inference (design §H.4). It could never change
  frozen research results, deterministic VWAP eligibility, regime
  calculations, or contract eligibility.
- **Rejects unknown schemas.** An unknown `schema_version`, producer
  version, payload schema, or registry version is refused, never skipped
  silently.
- **Rejects stale required evidence.** If `machine_decision_ready = false`
  (a missing required producer, a stale/unknown required entry, or an
  unresolved critical conflict), the ranker emits a `missing_evidence` or
  "no qualified setup" calculation. It never emits a degraded ranking.
- **Records its exact inputs.** Every output lists its exact input item IDs
  and the bundle ID.
- **Emits new evidence only.** Ranking is a new `deterministic_calculation`
  (or, if ever authorized, a new `current_inference`). It never edits
  upstream evidence.
- **Lanes are separate.** A failed reversion setup never produces a trend
  setup, and the reverse is equally true. "No qualified setup" is an
  explicit calculation about the `market_session` subject.
- **Setup qualification** uses only registered, frozen criteria for its
  lane. No live VWAP setup definition is registered, because live shadow
  collection is unauthorized.

## 5. Contract-review UI

- **Selector output only.** Shows only `deterministic_contract_selector`
  items and their input facts.
- **Eligible and research-only stay separate.** The two sets are separate
  sections, and `indicative`-feed data is labelled "indicative
  (delayed/derived), not live OPRA".
- **Selector outcomes are permanent and visible.** The UI displays
  individual **returned** contracts only, each shown in its returned set
  (`eligible` or `research_only`). Rejected contracts appear only through
  the aggregate counts by `RejectionReason`, shown unchanged. The UI never
  changes, hides or re-derives these outcomes, and never materializes
  rejected contracts individually (for example from the option-chain batch)
  (design §H.3).
- **Explains rejection and ranking.** Rejections use the selector's
  `RejectionReason` counts. Today any ordering is the selector's
  deterministic order and is labelled as such. A future, separately
  authorized `future_contract_ranker` ranking would be shown **within**
  the returned eligible set and, separately, within the returned
  research-only set, labelled as inference, with the returned set beside
  each contract; the two sets are never merged into one list, and rejected
  contracts are never ranked.
  The Options Strategy Agent remains unauthorized.
- **Never places orders.** No order, ticket, or brokerage deep link.
- **Manual-decision label** on every view.

## 6. Notification layer (future, unauthorized)

- **Authorized events only.** Accepts only notification-event items of a
  registered payload schema from a producer holding a notification
  authorization record. None exists.
- **Severity, deduplication and throttling.** Obeys the future
  notification taxonomy's severity, deduplication keys, throttling and
  quiet periods (roadmap priority 6). Without that taxonomy, it sends
  nothing.
- **No recommendation language.** Alert text is built from fixed templates
  keyed by event type. It must pass the non-directional content policy, and
  never says buy, sell, enter, exit, or "setup to trade". Every alert points
  back to the dashboard.
- **Cannot bypass selector outcomes.** A notification never describes a
  contract as belonging to any set other than the one the selector
  returned it in, never names a contract absent from the returned sets
  (including any rejected contract), never presents a research-only
  contract as eligible, and never alters rejection counts (design §H.3).
- **Records the cause.** Each dispatch records the event item ID, the
  `notification_decision` bundle ID, and the exact evidence IDs that caused
  it.
- **SMS needs its own review.** Any SMS provider is new external
  infrastructure that needs its own security and credential review.

## 7. Citation and display vocabulary

| Kind | Required display label | Required accompanying detail |
|---|---|---|
| `confirmed_fact` | "Fact" | source/provider and observed time; for news, "provider-reported, unverified" on headline text |
| `deterministic_calculation` | "Calculation" | producer and as-of time |
| `historical_research_result` | "Research result" | study, sample, label, fixed caveats, holdout state |
| `current_inference` | "Inference" | producer, model or rule, and "not evaluated" unless an evaluation record exists |
| `missing_evidence` | "Missing" | what is missing and why (reason code) |
| `stale_evidence` status item | "Stale evidence notice" | which item or producer was stale, its last observed time, and the policy |
| any kind with evaluated freshness `aging`, `stale` or `unknown` | the kind's own label **plus** a freshness badge ("Aging", "Stale", "Freshness unknown") | age and policy |
