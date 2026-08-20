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
