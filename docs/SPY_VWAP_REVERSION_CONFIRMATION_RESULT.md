# SPY VWAP-Extension/Reversion — Confirmation Result

**Status: completed and independently verified (2026-09-25).** This records
the single preregistered confirmation run defined by
[SPY_VWAP_REVERSION_PREREGISTRATION.md](SPY_VWAP_REVERSION_PREREGISTRATION.md)
(including dated clarification C1). It is a research result about the
underlying SPY setup only. It is **not** validation, not an options edge, not
a recommendation, and not a trading signal. The prospective holdout remains
sealed.

## Sample

| Item | Value |
|---|---|
| Sample | `confirmation`, 2026-02-23 → 2026-08-14 (inclusive) |
| Complete sessions | 121 (gate ≥ 100); 0 incomplete |
| Weekdays excluded | 4 (`no_regular_session_bars`): 2026-04-03, 2026-05-25, 2026-06-19, 2026-07-03, the market holidays in the window |
| Other exclusions | 0 off-grid, 0 invalid price/volume, 0 OHLC-inconsistent, 0 incomplete, 0 contract-rejected |
| Prior-day context | available for 117 sessions; none for the 4 sessions that follow those holidays (expected) |
| Eligible decision points | 5,244 above-VWAP; 4,193 below-VWAP |
| Data | Alpaca `SPY` / `5Min` / `iex` / `raw` / `USD`; seven bounded ingestion runs (see `PROJECT_STATE.md` Completed Work Log item 54) |

No discovery-sample date (2026-08-17 → 2026-09-22) and no holdout date
(2026-09-23 onward) entered the input, the evaluation record, or the result.

## Primary result — above-VWAP, all eligible points (confirmatory)

Each primary horizon's session-level signed return toward VWAP, in bps of the
signal close. Values are shown to 4 decimal places. **Every decision used the
exact stored fractions, not these displayed decimals.**

| Horizon | Estimate | 95% interval | Raw p | Holm p | Sessions | Observations | Status |
|---|---:|---|---:|---:|---:|---:|---|
| `intraday_30m` | 4.0522 | [2.3883, 5.8168] | 2/10001 | 6/10001 | 121 | 4,851 | positive |
| `intraday_2h` | 12.8834 | [7.1608, 19.0576] | 2/10001 | 6/10001 | 121 | 3,672 | positive |
| `to_session_close` | 16.9561 | [9.0180, 25.0790] | 2/10001 | 6/10001 | 121 | 5,177 | positive |

Each horizon cleared all three preregistered conditions: an estimate of at
least +1.0 bps, an interval lower bound above 0, and a Holm-adjusted p below
0.05.

**Primary label: `supported_for_further_shadow_research`.**

This label permits **proposing** a separately reviewed shadow-research stage
for the underlying setup. It does not authorize recommendations, options or
contract selection, a selector run, execution, or the Options Strategy Agent.

## Secondary result — below-VWAP (confirmatory, lower tier)

Per-horizon status uses the unadjusted 95% interval only; no p-value is
defined for these cells.

| Horizon | Estimate | 95% interval | Sessions | Observations | Status |
|---|---:|---|---:|---:|---|
| `intraday_30m` | 6.5284 | [4.2679, 8.8572] | 117 | 3,860 | positive |
| `intraday_2h` | 18.0900 | [12.2374, 24.0618] | 117 | 2,861 | positive |
| `to_session_close` | 20.3960 | [11.6964, 29.3471] | 117 | 4,139 | positive |

**Close-minus-30m paired contrast:** 13.8676 bps, 95% interval
[6.4955, 21.3878], raw p 2/10001 (unadjusted), 117 paired sessions, status
`materially_different`.

**Secondary label: `below_horizon_dependent`.**

This label means the **size** of the below-VWAP move toward VWAP changes with
horizon: the paired close-minus-30m contrast is materially different. It does
**not** mean the direction reverses. All three below-VWAP horizons were
positive, i.e. toward VWAP.

## Exploratory results

The one-way regime, time-of-day, and extension-bucket breakdowns (§6–§7) are
exploratory only: 70 subgroup cells passed their 40-session / 50-observation
gate and 14 were `insufficient_sample`. They make no claim, and they cannot
change or qualify the primary or secondary label. The descriptive secondary
outcomes (touch rate, MFE/MAE, floored percentage retraced, session
quantiles, above-minus-below asymmetry) all passed their cell gates and are
likewise descriptive only.

## Provenance and verification

| Identity | Value |
|---|---|
| Input SHA-256 | `509db0ac94a98511245e1871edeb985b41f31909f909fa742b3018a93b5dc455` |
| Evaluation-record SHA-256 | `e5a2f8649a1b944f274fd5222e178fb5c0a9f447a46dd5b903eb6e265bbcd248` |
| Confirmation-result SHA-256 | `c8d838f441005a4122cda93f4572e8f1b6eca429b130ed545c1fdcb9ad69877b` |
| Code commit (operator-supplied) | `cd587f132b914208ef95c892a74bea68f4bd35ef` |
| Base preregistration | `f77d30f8e6e90a6b77eeca11fd11c3da9c9540c1` |
| Clarification C1 | `1ff654de6dbbd54aeecb471010d1a612ca5abda6` |
| Schemas | `spy-vwap-reversion-evaluation-1`, `spy-vwap-reversion-confirmation-1` |

The three artifacts are local and gitignored under `data/evaluations/local/`;
they are not committed.

An independent read-only audit found:

- all three hashes match the exact file bytes, and all three files are
  canonical;
- re-evaluating the input reproduced the evaluation record byte for byte;
- `verify_confirmation_result` passed;
- recomputing the analysis from the stored record reproduced the result
  byte for byte;
- the configuration snapshot equals the preregistered configuration, and the
  regime and sample thresholds are the frozen defaults.

## Caveats

The result's eight fixed notes, verbatim:

- `underlying_setup_only_no_options_or_pnl`
- `research_result_not_validation`
- `not_a_recommendation_or_trading_action`
- `labels_never_mean_validated_profitable_accurate_or_tradeable`
- `observation_level_points_overlap_no_inference`
- `thresholds_frozen_unmodified`
- `next_session_unavailable_no_exchange_calendar`
- `options_strategy_agent_not_authorized`

Also:

- **IEX data only.** Findings apply to this IEX measurement and do not
  transfer automatically to consolidated data.
- **Context not evaluated.** Relative-volume, catalyst, and breadth context
  were not evaluated.
- **p-value resolution floor.** Every raw p-value is 2/10001, the smallest
  the fixed 10,000-replicate method can produce. The true value may be
  smaller; the method cannot show it.
- **Code commit not attested.** The code SHA is operator-supplied provenance,
  not externally attested. Recomputation with that checkout is consistent
  with it but does not prove which code produced the files.
- **Holdout sealed.** The prospective holdout (2026-09-23 → 2026-12-04)
  remains sealed and must not be opened, ingested, or evaluated early (§4,
  C1.1).
- **No trading economics measured.** No options returns, transaction costs,
  or P&L were measured.
