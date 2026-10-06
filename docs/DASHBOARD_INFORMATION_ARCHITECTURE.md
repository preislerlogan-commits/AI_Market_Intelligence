# Dashboard Information Architecture

**Status: REVIEWED DESIGN — accepted by the merge that introduces this
document (2026-09-29). DESIGN ONLY.** This is
roadmap priority 3 ([PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md)): the dashboard's
pages, hierarchy, states and trust vocabulary. The card itself is specified in
[SETUP_CARD_CONTRACT.md](SETUP_CARD_CONTRACT.md).

- **Merge accepts the design only.** The merge that introduced this document
  accepted the design and authorizes no dashboard, setup-card implementation,
  assistant registry grant, storage, adapter, ranking, scenario,
  notification, contract-selector change or trading capability.
- **Nothing is implemented or authorized.** No dashboard code, framework,
  hosting, authentication, storage, refresh loop, or consumer exists or is
  authorized by this document. `market_intelligence/dashboard/` remains an
  empty placeholder.
- **`no_qualified_setup` correction (2026-09-29).** §4.3 and §6.2 were
  revised with the [setup-card contract](SETUP_CARD_CONTRACT.md) §4.8:
  `no_qualified_setup` appears only after an authorized deterministic lane
  evaluation concluded `not_qualified`; unauthorized, unavailable and
  evidence-blocked lanes are availability states. The correction keeps this
  document's reviewed-design status and is accepted through the
  setup-card implementation merge (PROJECT_STATE item 61). It authorizes no
  dashboard.
- **Governing documents win.** [DECISION_RULES.md](../DECISION_RULES.md),
  [SOURCE_POLICY.md](../SOURCE_POLICY.md), the reviewed
  [Evidence Envelope design](EVIDENCE_ENVELOPE_DESIGN.md),
  [evidence registry](EVIDENCE_REGISTRY.md),
  [consumer rules](EVIDENCE_CONSUMER_RULES.md), frozen research protocols and
  [PROJECT_STATE.md](../PROJECT_STATE.md) always govern. Where this design
  seems to conflict with them, they win.
- **Frontend framework is not chosen.** README lists the dashboard directory
  as "a future Streamlit application", but that is a placeholder note, not a
  reviewed decision. This design is framework-neutral (§12).
- **Offline synthetic prototype (2026-10-06; PROJECT_STATE item 64).** It is
  reviewed and implemented through its merge, and uses deterministic
  synthetic fixtures only.
  - A local, read-only Streamlit prototype of §2-§8 now exists in
    `market_intelligence/dashboard/`, so that directory is no longer an empty
    placeholder.
  - It renders committed synthetic bundles and cards only, through a
    fail-closed presentation adapter.
  - No real evidence, database, provider, registry activation or model is
    connected. It persists nothing and has no execution control. The
    assistant remains a disabled placeholder.
  - Streamlit was chosen for this prototype only. Production framework,
    hosting and authentication remain open (§12.3).
  - Everything else in §12.5 remains unauthorized.

**The dashboard presents evidence; it never trades.** It has no order entry,
no position sizing, no brokerage link, and no recommendation framing. Every
view carries the manual-decision statement.

---

## 1. What the dashboard is for

The dashboard is the main interface of the SPY market-intelligence copilot
([vision §3](PRODUCT_VISION.md)). It helps the user:

1. prepare before the open (Pre-Market Brief);
2. monitor the live session (Market Overview, Live Scanner);
3. inspect one setup's full evidence (Setup Detail);
4. review deterministic contract candidates for manual review (Contract
   Review);
5. understand research and system health honestly (Research and System
   Status);
6. ask the assistant to explain cited evidence (Assistant workspace).

**What exists today, honestly.** The Evidence Envelope core exists offline
only. No producer adapter, evidence store, populated registry, setup
definition, scenario definition, ranking rubric, trend-day research, or live
data feed exists. Until separately authorized components exist, every page
must render its empty or "not authorized" state truthfully (§6).

---

## 2. Navigation

Primary navigation (left rail on desktop, bottom bar plus menu on mobile):

| Order | Page | Primary question it answers |
|---|---|---|
| 1 | Market Overview | What is the market state right now, and can I trust the evidence? |
| 2 | Pre-Market Brief | What should I know before the open? |
| 3 | Live Scanner | Has any setup qualified in a researched lane? |
| 4 | Setup Detail | What is the full evidence for this one setup? |
| 5 | Contract Review | Which contracts did the deterministic selector return? |
| 6 | Research and System Status | What is researched, what is live, what is healthy, what is authorized? |
| 7 | Assistant workspace | Explain this evidence to me, with citations. |

**Setup Detail and Contract Review are reached from context**, not browsed
blindly: they always open for one card or one selector result, and they show
the bundle they were built from.

### 2.1 The primary drill path

```
Market Overview ──► Live Scanner (lane tab) ──► Setup Detail (one card)
       │                                         │        │
       │                                         │        ├─► Evidence drawer (one item,
       │                                         │        │     its kind, freshness,
       │                                         │        │     provenance, conflicts)
       │                                         │        └─► Contract Review (the card's
       │                                         │              selector result only)
       └────────────── Assistant workspace ◄─────┴─ "Explain this card" (same bundle ID)
```

- **The bundle travels with the user.** Every hop carries the bundle ID and
  as-of time of the view it came from. A page never silently swaps to a newer
  bundle; when a newer one exists, it shows a "Newer evidence available" notice
  and the user chooses to move (§6.3).
- **Back is always safe.** Returning to an earlier page shows that page's own
  bundle, unchanged.

---

## 3. Global status indicators

A persistent status strip sits above every page:

| Indicator | Content | Source |
|---|---|---|
| As-of | Bundle `as_of_utc`, shown in America/New_York and UTC | bundle manifest |
| Clock health | `healthy`, or the bounded `clock_health_reason` (for example `no_clock_fact`, `ambiguous_clock_facts`) | bundle freshness entries |
| Evidence readiness | `Ready for manual review` or `Not ready for manual review`, with the count of blocking reasons. Never phrased as "ready to trade" | the bundle's state: missing and ambiguous requirements, freshness, clock health, critical conflicts (not the bundle's `machine_decision_ready` flag) |
| Missing / stale | Counts of missing requirements and stale, aging or unknown-freshness entries | bundle |
| Conflicts | Count of unresolved material and critical conflicts; critical shown as a blocking banner | bundle conflicts |
| Registry | Short registry version ID | bundle `registry_version_id` |
| Holdout guard | "SPY price evidence dated 2026-09-23 → 2026-12-04 is restricted until the holdout is recorded" whenever relevant | holdout guard (design §O) |
| Scope | "Decision support only. Manual execution. No orders are placed." | fixed text |

**The strip never shows a single confidence score, and never uses
buy/sell colors.**

---

## 4. Pages

For each page: purpose, information hierarchy (top to bottom), what it may
consume from the Evidence Envelope, and what it must never do.

### 4.1 Market Overview

**Purpose.** The current SPY market state and whether the evidence behind it
is trustworthy right now.

**Hierarchy.**
1. Blocking states first: critical conflicts, missing required evidence,
   ambiguous requirements, ambiguous or unknown clock, holdout restriction.
2. Current SPY state: latest bar facts and session calculations, each with its
   kind label and freshness.
3. Regime calculation (`spy-regime-engine-1`), labelled **Calculation**,
   with the explicit note that a regime label is not a signal and not a
   qualified setup.
4. Macro and news context: facts (publication metadata, observations) and
   the agents' **non-directional** inferences, labelled **Inference**.
5. Unresolved conflicts touching displayed evidence, both sides shown.
6. "What is missing or stale": every missing requirement (by requirement ID
   and bounded reason) and every stale, aging or unknown-freshness entry.

**Consumes.** A `live_market_state` bundle (facts, calculations, labelled
inferences, conflicts, freshness).

**Never.** Hides stale evidence, restyles aging evidence as current,
presents the regime label as a directional call, or merges agent inferences
into facts.

### 4.2 Pre-Market Brief

**Purpose.** What to know before the open, without forecasting.

**Hierarchy.**
1. Readiness and freshness of the briefing's own evidence.
2. Prior-session state: the prior session's calculations and facts.
3. Overnight and scheduled context: scheduled catalysts **only when an
   approved calendar source exists** (none does today; the slot shows "No
   approved catalyst source").
4. Macro and news evidence, with agent inferences labelled.
5. Conditional scenarios: a slot reserved for **registered** scenario
   definitions with authorized relations. None exist, so it shows "Scenario
   analysis not authorized". When authorized, each scenario is labelled
   **Inference**, cites its evidence, and never states a guaranteed
   direction.
6. Important levels: shown **only** when supplied by an authorized
   deterministic producer; otherwise the slot is absent, not guessed.
7. Research context: recorded study results with caveats, visually separate
   from live evidence.

**Consumes.** A `premarket_briefing` bundle.

**Never.** Produces or implies a directional forecast, invents levels, or
fills an empty scenario slot with free text.

### 4.3 Live Scanner

**Purpose.** Show, per researched lane, whether any setup has qualified.

**Hierarchy.**
1. Lane tabs, each with its own status. Lanes are never merged.
   - **VWAP reversion.** The lane exists as research
     (`supported_for_further_shadow_research`, research status
     `shadow_pending`). **Live qualification is not authorized**: no live
     setup definition is registered and shadow collection (Stage 3) is not
     authorized. The tab shows "Live qualification not authorized — research
     context only" until that changes. No setup card of either kind is
     shown.
   - **Trend continuation.** **Unavailable**: no trend-day research,
     definition or detector exists. The tab shows "Not researched — no trend
     signal exists", with no setup card. It never borrows reversion evidence.
2. Within an authorized lane (future): setup cards grouped by lifecycle
   (`observed`, `developing`, `evaluation_complete`, `invalidated`,
   `expired`), with qualification (`qualified`, `not_qualified`,
   `indeterminate`, `not_evaluated`) shown as its own separate label. Today
   no lane is authorized, so no card of either kind is shown.
3. Ranking (future, separately authorized): only a deterministic ranking
   under a registered rubric, and only within one lane. None exists; cards
   are ordered by lifecycle group, then decision time.
4. `no_qualified_setup`: a normal, first-class result card per lane (never
   an error or an empty table), rendered only after an authorized
   deterministic lane evaluation concluded `not_qualified`. A lane that is
   not authorized, unavailable or evidence-blocked shows that availability
   state instead (§6.2), never this card.

**Consumes.** `live_market_state` for context; one `setup_detail` bundle per
card.

**Never.** Lets one lane substitute for another (a failed reversion setup is
never shown as a trend setup, or the reverse), shows a lane as active when it
is unauthorized, or ranks across lanes.

### 4.4 Setup Detail

**Purpose.** Everything behind one setup card.

**Hierarchy.**
1. Card header: lane, lifecycle state, qualification, evidence readiness
   ("Ready for manual review" or not, with blockers), card as-of time,
   bundle ID, supersession status, and the manual-decision statement. Each
   is a separate label.
2. Blocking reasons, if any (missing, stale, unknown freshness, ambiguity,
   critical conflict).
3. Supporting evidence, then contradicting evidence, each item with its kind
   label, freshness badge and producer.
4. Missing and stale requirements (never hidden).
5. Historical research reference: study, sample, label, fixed caveats,
   holdout state, visually separated from live evidence.
6. Inference interpretation (only when separately authorized), labelled
   **Inference**, never mixed into supporting evidence.
7. Conflicts, with both sides.
8. Timing and provenance: effective and observed times, producers, versions,
   registry version.
9. Link to Contract Review for the card's selector result.

**Evidence drawer.** Opening any cited item shows: kind, payload summary,
effective and observed times, freshness with reason, producer and version,
source references (no secrets, paths or raw bodies), parents, revision
chain, and conflicts.

**Consumes.** One `setup_detail` bundle; the setup card (`setup-card-1`).

**Never.** Shows an uncited value, hides a missing requirement, or presents
inference as a reason the setup qualified (unless the setup's reviewed rules
allow inference, which none do).

### 4.5 Contract Review

**Purpose.** Show exactly what the deterministic Contract Selector returned
for one setup, for manual review.

**Hierarchy.**
1. Selector availability ("Available", "Not requested", or "Contract
   selector unavailable"). Only when available: the selector's outcome
   (`eligible`, `no_eligible_contracts`, `research_only`, `indeterminate`),
   feed (`opra` vs `indicative`), selector as-of and freshness. Always: the
   manual-decision statement. "Contract selector unavailable" is never shown
   as "No eligible contracts".
2. **Eligible** contracts, as returned by the selector (OPRA required).
3. **Research-only** contracts, in a separate section headed "Research only —
   not eligible", with the indicative-feed warning.
4. Rejection summary: aggregate counts by `RejectionReason` only, copied
   unchanged.
5. Future ranking (only if separately authorized): shown within each
   returned set, labelled **Inference**, beside the selector's status. Never
   merges the two sets.

**Consumes.** A `contract_review` bundle; the card's selector-result
reference.

**Never.** Shows a contract the selector did not return, lists or
reconstructs a rejected contract, shows research-only as eligible, offers an
order ticket, quantity, sizing, or a brokerage deep link.

### 4.6 Research and System Status

**Purpose.** Separate what is researched from what is live, and show health
and authorization honestly.

**Hierarchy.**
1. **Research** (historical, visually distinct from live state):
   - SPY VWAP reversion: confirmation label
     `supported_for_further_shadow_research` with its fixed caveats;
     holdout `sealed`; shadow research Stage 2 `design_complete_test_pending`;
     Stage 3 not authorized.
   - Trend continuation: not researched.
2. **System health:** producer availability, registry version, clock health,
   freshness policy status (including "live thresholds unset"), provider
   health facts, and holdout guard status.
3. **Authorization status:** a fixed list of what is and is not authorized
   (from PROJECT_STATE), including every unimplemented component.

**Consumes.** Research result items and health facts through registered
bundle purposes; authorization text from reviewed documents (static, not
evidence). A dedicated `system_status` bundle purpose is an open question
(§12).

**Never.** Presents a research label as validation, profitability or a live
signal, or implies an unimplemented component exists.

### 4.7 Assistant workspace

**Purpose.** Conversational explanation of cited evidence (consumer rules
§3).

**Layout.** A conversation pane beside a citation pane. Every answer is bound
to one `assistant_question_context` bundle, shown in the pane header.

**Rules.**
- Evidence is retrieved by ID or bounded query only.
- Every material claim is cited, and each citation resolves inside the
  answer's bundle.
- Fact, calculation, research and inference are labelled in both the answer
  text and the citation pane.
- Missing and stale evidence relevant to the question is stated.
- No contract is discussed unless it is in the selector's returned sets; no
  rejected contract is named.
- No invented probability, no guaranteed direction, no recommendation.
- "Explain this card" opens the assistant bound to the card's own bundle, so
  the explanation and the card cite the same evidence (§11;
  [card example 9.7](SETUP_CARD_CONTRACT.md)). **This needs a registry
  change that is not approved:** today the assistant's consumer grant covers
  only `assistant_question_context`, so reading the card's `setup_detail`
  bundle (read-only) remains an unapproved future grant, part of the
  dashboard-to-chat handoff (roadmap priority 5). This merge does not grant
  it.

**Consumes.** An `assistant_question_context` bundle. The assistant itself
(retrieval, model, explanation layer) is not implemented or authorized.

---

## 5. Layout priorities

### 5.1 Desktop

- **Three zones:** status strip (top), page content (center), and a
  collapsible context panel (right) for the evidence drawer or the assistant.
- **Blocking states always render above the fold**, before any value.
- **Evidence tables** show kind and freshness columns before value columns.
- **Contract Review** uses two stacked tables (eligible, then research-only),
  never side-by-side columns that could be read as one list.

### 5.2 Responsive and mobile

- **Single column.** Order: status strip → blocking states → primary content
  → secondary content.
- **Status strip collapses** to a compact bar showing readiness, clock and
  conflict counts; tapping expands it.
- **Evidence drawer and assistant** open as full-screen sheets that keep the
  bundle ID in their header.
- **Contract Review on mobile** is read-only list sections with the same
  separation and headings as desktop.
- **Nothing is dropped on mobile** that affects trust: kind labels, freshness
  badges, conflicts, missing items and the manual-decision statement always
  remain visible.

---

## 6. States

### 6.1 Page and panel states

| State | When | What the user sees |
|---|---|---|
| Empty | The bundle has no entries for this panel | A plain statement of what is absent and why (for example "No approved catalyst source") |
| Loading | A bundle is being built | Skeleton rows; no values; the previous bundle is not shown as current |
| Not authorized | The component or lane is not authorized | "Not authorized", with the authorization it would need |
| Unavailable | The producer is not implemented | "Not implemented" |
| Missing | A requirement has no satisfying item | The requirement ID and its bounded reason |
| Stale / aging / unknown | Evidence freshness is not current | The badge, the age, and the policy; value dimmed but readable |
| Ambiguous | Competing evidence or clock facts | "Ambiguous evidence — no winner", with every competing ID |
| Conflict | An unresolved conflict touches shown evidence | Both sides; critical conflicts also raise the blocking banner |
| Holdout restricted | Evidence falls in the holdout window | "Restricted until the holdout is recorded" |
| Error | A bundle or card failed validation | The sanitized reason token only; no stack traces, paths or raw text |

### 6.2 `no_qualified_setup` and lane availability

Only the last row is a card. The other three are dashboard availability
states outside the [setup-card contract](SETUP_CARD_CONTRACT.md) (§4.8),
with no `scd1_` identity.

| Lane state | Meaning | Card |
|---|---|---|
| `not_authorized` | No authorized setup definition or producer for the lane | none |
| `unavailable` | The required evaluation is absent or unusable | none |
| `evidence_blocked` | Required evidence is stale, missing, ambiguous or critically conflicted | no new conclusion card |
| `no_qualified_setup` | An authorized deterministic evaluation completed and concluded `not_qualified` | allowed |

A `no_qualified_setup` card shows the lane, the evaluation time, and the
cited evaluation's reason (`criteria_not_met` or `no_candidate_evaluated`).
It uses neutral styling, never error styling. Blocked or unavailable
evidence is never shown as "no qualified setup".

### 6.3 Newer evidence

A view never updates its content in place. When a newer bundle or a
superseding card exists, a notice offers to open it. The older view stays
readable and labelled "Superseded" or "Historical".

---

## 7. What must never be combined or visually confused

| Never | Why |
|---|---|
| Live evidence and historical research in one panel | Research is context, not current state |
| Inference styled like a fact or calculation | Evidence kinds must stay distinguishable |
| Eligible and research-only contracts in one list | Research-only is never eligible |
| Any list of rejected contracts | Only aggregate `RejectionReason` counts may appear |
| Two lanes in one ranking | Lanes never substitute for each other |
| A regime label styled as a signal | A regime label is a calculation, not a setup |
| Green/red buy-or-sell styling or directional arrows implying a call | No recommendation framing |
| One combined confidence or quality score | Quality dimensions stay separate |
| Stale or unknown-freshness values styled like current values | Freshness is always visible |
| `no_qualified_setup` styled as an error | It is a valid result |
| An order button, quantity field, or brokerage link | Execution is manual and outside the system |
| "Contract selector unavailable" and "No eligible contracts" | Availability is infrastructure; no eligible contracts is a real selector result |
| Lifecycle and qualification in one label | "Where the setup is in time" and "what the rules concluded" are different questions |
| "Ready for manual review" and any recommendation wording | Readiness only means the evidence can be presented |

---

## 8. Trust vocabulary (display labels)

Every label is text first, with an icon or pattern second and color third.
Color is never the only carrier of meaning, and no palette maps to buy/sell.

| Label | Meaning | Visual treatment |
|---|---|---|
| **Fact** | `confirmed_fact`: recorded as reported by a named source | Solid outline chip "FACT"; news headlines add "provider-reported, unverified" |
| **Calculation** | `deterministic_calculation` | Solid outline chip "CALC" with the producer name |
| **Research result** | `historical_research_result` | Distinct panel background and "RESEARCH" chip with study, label and caveats |
| **Inference** | `current_inference` | Dashed outline chip "INFERENCE" with the producer; text in a visibly different style |
| **Missing** | Required evidence absent | Hollow dashed chip "MISSING" with the reason |
| **Stale** | Freshness `stale`, or a `stale_evidence` notice | "STALE" chip with age and policy |
| **Aging** | Freshness `aging` | "AGING" chip with age |
| **Freshness unknown** | Freshness `unknown` (including clock problems) | "FRESHNESS UNKNOWN" chip with the reason |
| **Material conflict** | Unresolved material conflict | "CONFLICT" chip beside both involved items |
| **Critical conflict** | Unresolved critical conflict | Blocking banner at the top of the page, plus chips |
| Research-only contract | Returned in the research-only set | Section header "Research only — not eligible" and an "INDICATIVE" chip |
| **No eligible contracts** | A real selector result: the selector ran and returned none | Neutral text "The selector returned no eligible contracts" |
| **Contract selector unavailable** | No valid selector result exists | "Contract selector unavailable", with the reason; never the no-eligible wording |
| **Ready for manual review** | `manual_review_ready`: enough current, non-conflicting evidence to present | "READY FOR MANUAL REVIEW" chip; never "ready to trade", never implies qualification or eligibility |
| **Manual decision required** | Every card and contract view | Persistent statement: "Decision support only. You make every trading decision manually." |

**Forbidden wording.** "Buy", "sell", "enter", "exit", "signal", "will rise",
"will fall", "guaranteed", "high-probability", "strong buy", "recommended
contract", and any percentage confidence not produced by an authorized,
calibrated model (none exists).

---

## 9. Accessibility requirements

- **Target:** WCAG 2.2 AA.
- **Not color alone:** every state has text and an icon or pattern.
- **Contrast:** text and chips meet AA contrast in light and dark themes.
- **Keyboard:** every control and drill-down is reachable and operable by
  keyboard, with a visible focus indicator and logical focus order.
- **Screen readers:** chips expose their full meaning (for example "Evidence
  kind: inference, from Macro Analyst"); tables use header cells; the status
  strip is a labelled landmark.
- **Live updates:** status changes are announced politely; nothing steals
  focus; no automatic in-place value changes (§6.3).
- **Time:** timestamps show America/New_York and UTC with explicit zone
  labels; relative ages ("aging, 7 min") also give the absolute time.
- **Motion:** no animation carries meaning; reduced-motion preferences are
  respected.
- **Language:** plain wording; bounded reason tokens are shown with a
  human-readable label and the token itself.

---

## 10. What each page may consume

| Page | Bundle purpose | Evidence kinds displayed | Notes |
|---|---|---|---|
| Market Overview | `live_market_state` | fact, calculation, inference (labelled), missing, stale | Conflicts and freshness always |
| Pre-Market Brief | `premarket_briefing` | fact, calculation, research, inference (labelled), missing, stale | Scenarios only when authorized |
| Live Scanner | `live_market_state`, `setup_detail` | via setup cards | Lanes separate |
| Setup Detail | `setup_detail` | all kinds, labelled | Card cites only this bundle |
| Contract Review | `contract_review` | selector calculation; option-chain facts for returned contracts only | Rejected contracts as counts only |
| Research and System Status | registered purposes (research and health facts) plus static authorization text | research, facts | Possible future `system_status` purpose (§12) |
| Assistant workspace | `assistant_question_context` | all kinds, labelled | Citations resolve inside the bundle |

The dashboard's registered permissions are read-only (registry §6). It never
emits evidence and never edits an item, conflict, bundle or card.

---

## 11. Synthetic walkthrough

All identifiers and values below are synthetic and abbreviated; they are not
market data. The session date is 2027-01-12, outside the sealed holdout
window. Full card examples are in [SETUP_CARD_CONTRACT.md §9](SETUP_CARD_CONTRACT.md).

1. **Market Overview** shows bundle `evb1_…a1` (as of 10:05 ET). Clock
   healthy. Regime calculation present. One material conflict between a
   news-agent inference and a bar calculation, both sides shown. No missing
   requirements.
2. **Live Scanner, VWAP lane** shows "Live qualification not authorized —
   research context only" today. In a future where a live setup definition is
   authorized, it would show a `qualified` card `scd1_…c1`. **Trend lane**
   shows "Not researched — no trend signal exists".
3. **Setup Detail** for `scd1_…c1` opens with its own bundle `evb1_…b1`:
   supporting and contradicting citations, the research reference with fixed
   caveats, and "Decision support only".
4. **Contract Review** opens the card's selector result: two eligible
   contracts; research-only section empty; rejection counts
   `spread_too_wide: 1`.
5. **Assistant**: "Explain this card" answers with citations to the same
   `evb1_…b1` entries the card cites.

---

## 12. Decisions and open questions

### 12.1 Decided by existing reviewed documents

- Dashboard is the main interface; manual final decisions; no execution
  ([vision](PRODUCT_VISION.md) §3, §6).
- Setup-card content (vision §7) and consumer rules for the dashboard,
  assistant and contract review ([consumer rules](EVIDENCE_CONSUMER_RULES.md)).
- Evidence kinds, freshness, conflicts, ambiguity and bundle semantics
  ([design](EVIDENCE_ENVELOPE_DESIGN.md), as implemented offline).
- Contract eligibility is the deterministic selector's; rejected contracts
  only as aggregate counts (design §H.3).
- Lanes are separate; `no_qualified_setup` is a valid result (vision §4).

### 12.2 Decided by this design (accepted by its merge)

- The seven pages, navigation and drill path (§2, §4).
- The global status strip (§3).
- Page states and the newer-evidence rule (§6).
- The never-combine list (§7) and trust vocabulary (§8).
- Accessibility target WCAG 2.2 AA (§9).
- The page-to-bundle-purpose mapping (§10).
- The `setup-card-1` contract, its `manual_review_ready` field, and its
  seven separate state vocabularies, including selector availability
  ([SETUP_CARD_CONTRACT.md](SETUP_CARD_CONTRACT.md)).
- **Not decided:** a read-only assistant grant for the card's
  `setup_detail` bundle, so an explanation can cite the same bundle as the
  card (§4.7), remains an unapproved future registry change (roadmap
  priority 5).

### 12.3 Future implementation decisions (not decided here)

- Frontend framework (README's "future Streamlit application" is a
  placeholder, not a decision).
- Production storage, authentication and hosting.
- Live refresh cadence.
- Live freshness thresholds and clock thresholds (registry: unset).
- Whether to add a `system_status` bundle purpose.
- Notification taxonomy and any SMS provider.
- Scenario definitions, trend-day rules, ranking rubrics and model choice.

### 12.4 Blockers

- **No producer adapters or evidence store**, so no real bundle can be
  built yet.
- **No setup definitions**, so no live setup card can be qualified; VWAP
  live qualification also waits on the shadow-research stages.
- **Sealed holdout**: real SPY price evidence dated 2026-09-23 → 2026-12-04
  cannot be ingested, displayed, bundled or reasoned over until recorded.
- **No exchange calendar or catalyst source**, so market-hours freshness and
  scheduled catalysts stay unavailable.

### 12.5 Remaining unauthorized

Dashboard implementation, assistant retrieval and explanation, setup and
contract ranking, scenario relations, notifications and SMS, trend-day
detection, the Options Strategy Agent, storage, adapters, and any trading or
execution capability.
