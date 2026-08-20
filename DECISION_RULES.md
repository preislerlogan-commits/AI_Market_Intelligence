# Decision Rules

This document defines the boundaries within which this system provides
decision support. These rules are binding on all code, prompts, and agents
operating in this repository.

## Execution Boundaries

- **AI does not autonomously execute trades.** No component of this system
  may place, modify, or cancel a brokerage order. All execution is manual,
  performed by the user.
- **Robinhood credentials are prohibited.** No Robinhood API keys, session
  tokens, or login credentials may be stored, requested, or used anywhere
  in this repository. Robinhood is used manually by the user, entirely
  outside this system.
- **Manual user approval is required for every trade.** Every trade,
  without exception, must be reviewed and manually approved and entered by
  the user. The system may inform this decision; it may never make it.

## Forecast Requirements

Every forecast produced by this system must record:

- **Timestamp** — when the forecast was made.
- **Horizon** — the time window the forecast applies to.
- **Evidence** — the supporting evidence for the forecast.
- **Contradictory evidence** — evidence considered that argues against the
  forecast, not just evidence that supports it.
- **Confirmation criteria** — what outcome would confirm the forecast was
  correct.
- **Invalidation criteria** — what outcome would show the forecast was
  wrong.

## Analytical Integrity

- **Separate underlying-direction accuracy from options-contract
  profitability.** Whether the underlying moved in the predicted direction
  is a distinct question from whether a specific options contract would
  have been profitable (given premium, theta, IV changes, spread, etc.).
  These must be tracked and reported separately, never conflated.
- **No retroactive forecast editing.** Once recorded, a forecast's original
  content, timestamp, and reasoning must not be edited after the fact.
  Corrections or updates must be recorded as new, separate entries that
  reference the original.
- **No averaging down based only on AI conviction.** AI-expressed
  confidence is not a valid justification, on its own, for increasing
  position size on a losing position. Any such decision remains manual and
  must be justified independently by the user.
- **No fabricated probabilities.** Numeric probabilities or confidence
  scores must not be presented unless they are derived from a defined,
  recorded methodology. Unsupported numeric confidence must not be
  invented to sound precise.
