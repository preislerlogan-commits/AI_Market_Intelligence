# Product Roadmap — AI Market Intelligence

**Status: roadmap only.** This document orders the work toward the
[Product Vision](PRODUCT_VISION.md).

- **Nothing is authorized by it.** Every item needs its own separately
  reviewed design and authorization.
- **It never overrides research.** It never overrides a frozen research
  protocol, [DECISION_RULES.md](../DECISION_RULES.md), or a recorded
  authorization boundary.
- **Current status.** See [PROJECT_STATE.md](../PROJECT_STATE.md).

## Parallel workstreams

Workstream A is frozen and paused on the sealed holdout. Workstreams B–H can
progress **independently** of it, as design work, while the holdout stays
sealed. They must never touch holdout data, the frozen VWAP research
documents, or the shadow sample.

### A. Frozen VWAP research track (paused on the holdout)

1. Preserve the sealed holdout (2026-09-23 → 2026-12-04).
2. Complete the one-time holdout evaluation under its own authorization, and
   record it immutably.
3. Execute the separately authorized IEX latency test
   ([plan](SPY_VWAP_IEX_LATENCY_TEST_PLAN.md)), no earlier than 2026-12-07.
4. Close Stage 2 only if the result is `latency_feasible`.
5. Implement and run the shadow recorder
   ([design](SPY_VWAP_SHADOW_RECORDER_DESIGN.md)) only through later,
   separate authorizations (protocol §K stages 3–8).

### B. Product platform track (independent)

- **Shared Evidence Envelope:** one versioned contract that every producer
  emits.
- **Evidence registry:** which producers exist, their versions, and their
  evidence kinds.
- **Freshness and availability states**, and a representation of conflicts.
- **Provenance and citations**, down to the stored record.
- **Audit logging** and configuration identity.

### C. Dashboard track

- the information architecture
- a pre-market page and a live market-state page
- the scanner, setup detail, and contract review
- research and shadow status
- system health and settings

### D. Conversational-assistant track

- evidence retrieval over structured outputs only, with question routing
- explanation templates that distinguish fact, calculation, research, and
  inference
- scenario comparison, source attribution, and uncertainty and
  missing-evidence handling
- context handoff from the dashboard to chat
- bounded conversation memory

### E. Macro and news track

- standardized agent outputs, in the Evidence Envelope
- event importance, and a scheduled catalyst calendar
- freshness and conflicting-source handling (see
  [SOURCE_POLICY.md](../SOURCE_POLICY.md))
- no unsupported directional claims; the existing agents stay
  non-directional

### F. Trend-day research track (discovery only)

- discovery questions
- candidate evidence categories
- breadth requirements (breadth context is not currently evaluated)
- structure and acceptance requirements, and catalyst alignment
- explicit separation from VWAP reversion
- a future preregistration before any confirmatory claim

**No trend signal is invented or frozen by this roadmap.**

### G. Contract-review track

- the existing deterministic eligibility, together with liquidity and
  spread evidence
- expiration and strike context
- research-only versus operationally eligible status. OPRA is required for
  operational eligibility, per the existing selector boundary.
- a clear explanation of why each contract ranked where it did
- **no order placement**

### H. Notification track

- an event taxonomy with severity
- deduplication, throttling, and quiet periods
- invalidation alerts and system-health alerts
- **no recommendation-style alert until it is separately authorized**
- any SMS provider integration is new external infrastructure, needing its
  own security and credential review

## Near-term priorities (independent of the holdout)

1. Freeze this product vision.
2. Design the shared **Evidence Envelope** used by every agent and engine,
   together with the evidence registry. **Design phase reviewed and
   merged (2026-09-28):** [EVIDENCE_ENVELOPE_DESIGN.md](EVIDENCE_ENVELOPE_DESIGN.md),
   [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md),
   [EVIDENCE_CONSUMER_RULES.md](EVIDENCE_CONSUMER_RULES.md). **Offline
   core reviewed and merged (2026-09-28):**
   `market_intelligence/evidence/`, under the §Q.5 authorization only.
   Storage, adapters, a registry file and every consumer remain
   unauthorized.
3. Design the dashboard information architecture and the setup-card
   contract ([vision §7](PRODUCT_VISION.md)). **Design phase reviewed and
   merged (2026-09-29):**
   [DASHBOARD_INFORMATION_ARCHITECTURE.md](DASHBOARD_INFORMATION_ARCHITECTURE.md),
   [SETUP_CARD_CONTRACT.md](SETUP_CARD_CONTRACT.md). **Setup-card offline
   core reviewed and merged (2026-09-29):**
   `market_intelligence/setup_cards/`, under its own authorization. The
   production setup-definition registry is empty, so no production card
   can be created. Dashboard implementation remains unauthorized, as do
   card storage and every consumer.
4. Standardize the Macro, News, and Market Evidence outputs into the
   envelope.
5. Design the context handoff between the dashboard and chat.
6. Design the notification taxonomy and throttling.
7. Draft a separate trend-day **discovery** plan.
8. Implement these components only through separately reviewed stages.

## Standing boundaries

These hold for every workstream:
- no autonomous trading, order routing, or position management
- no Robinhood credentials, and no brokerage execution
- the Options Strategy Agent is not authorized
- Stage 3 and shadow collection are not authorized
- no holdout access
- frozen research documents are never modified by product work
