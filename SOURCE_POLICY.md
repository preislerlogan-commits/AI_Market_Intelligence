# Source Policy

This document defines the hierarchy and sourcing requirements for all
information — market data, news, and commentary — used by this project for
research and forecasting.

## Source Hierarchy

Sources should be preferred in the following order, from most to least
authoritative:

1. **Primary government or exchange sources** — e.g. SEC filings and EDGAR,
   Federal Reserve / FRED, BLS, BEA, Treasury, exchange-published data
   (NYSE, Nasdaq, CBOE).
2. **Licensed/direct market-data APIs** — e.g. Alpaca, and other data
   providers with direct, licensed feeds rather than scraped or
   third-hand data.
3. **Original company filings and releases** — e.g. 10-K/10-Q/8-K filings,
   earnings releases, investor presentations, and official press releases
   issued directly by the company.
4. **Reputable financial reporting** — established financial news
   organizations with editorial standards and correction practices (e.g.
   Reuters, Bloomberg, Wall Street Journal, Associated Press).
5. **Secondary commentary** — analyst notes, opinion pieces, social media,
   forums, and other commentary. Lowest priority; treat as opinion, not
   fact, and corroborate against higher-tier sources before use.

Lower-tier sources may be used for context, sentiment, or hypothesis
generation, but should not be treated as fact without corroboration from a
higher-tier source.

## Required Fields for Every Sourced Item

Every piece of information pulled into this system from an external source
must record:

- **Timestamp** — when the information was retrieved/observed.
- **URL or provider name** — where the information came from, specific
  enough to be re-checked.
- **Publication time vs. event time** — when the source published the
  information versus when the underlying event actually occurred (these
  are often different, and the gap matters for market-moving information).
- **Explicit uncertainty** — a note on how confident/verified the
  information is (e.g. "confirmed by primary source" vs. "single secondary
  source, unconfirmed").

Information missing these fields should not be treated as reliable input
to forecasts or decisions.

## Retained provenance vs. model-facing evidence excerpts

These are two different things and must not be conflated:

- **Retained provenance** — what storage and audit records must keep. This
  is the binding requirement above, and it is **not weakened** by anything in
  this section. For the news currently ingested, `news_articles` (migration
  `0004`) retains the provider name, the article URL (`article_url`), the
  retrieval timestamp (`retrieved_at`), and the provider's publication/update
  timestamps kept distinct from retrieval time. Ingestion runs are recorded
  in `ingestion_runs` and `orchestration_*`. The SPY option-chain snapshot
  storage added in migration `0009` (applied to the real database
  2026-09-14, with one authorized live ingestion stored; see
  [DATA_CATALOG.md](DATA_CATALOG.md)) follows the
  same rule with a normalized, three-table design: each stored retrieval's
  `option_chain_snapshot_batches` row records the provider, the
  **explicitly requested feed (`opra` or `indicative`, stored verbatim and
  never merged across feeds)**, the bounded request window it was retrieved
  under, and the UTC retrieval instant — recorded even for a retrieval that
  returned zero contracts; each `option_chain_snapshots` row is an
  **immutable** point-in-time observation that keeps the same retrieval
  instant, kept distinct from the provider's quote/trade timestamps; and
  each `option_chain_snapshot_batch_items` row is the truthful record of
  which batch a given observation belongs to (a normalized membership row
  per contract per batch, rather than a mutable pointer on the observation
  itself), so batch membership stays reconstructable even when the same
  observation is re-ingested into a later batch. `indicative`-feed data is a
  delayed/derived feed and must be labelled as such — it is not licensed
  live OPRA data.

- **Model-facing evidence excerpts** — the bounded payloads the agents send
  to the model. These are deliberately narrower than the retained record: the
  News Analyst's model-facing evidence omits article URLs entirely (URLs live
  only in the snapshot's separate `audit_provenance` field, which is never
  sent to the model), and other agents send only the specific stored fields
  each analysis needs. Omitting a field from a model-facing excerpt is a
  data-minimization and prompt-safety choice; it does **not** remove that
  field from the retained provenance record, and it must never be read as
  permission to store less.

## Known gap: no formal uncertainty-rating field

The "Explicit uncertainty" requirement above is **not yet implemented as a
stored field.** `news_articles` has no confidence/verification-status column,
and the agents do not assign one — a News Analyst `claim_summary` is required
by instruction to be framed as provider-reported, but that framing is not a
structured uncertainty rating and is not validated as one. This is an open
gap against the requirement, recorded here honestly; it is not something the
current system already solves. Any future uncertainty-rating design must add
it as retained provenance, not only as model-facing text.
