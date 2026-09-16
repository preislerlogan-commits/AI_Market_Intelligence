"""Deterministic SPY options-contract eligibility selector -- Phase 1 step e
(see ``docs/OPTIONS_DECISION_WORKFLOW.md``, "Deterministic Contract
Selector").

This package runs **without any AI model**, entirely offline, and before any
future strategy agent. It consumes exactly: SPY; one already-validated
scenario-horizon bucket from the upstream deterministic regime engine
(``market_intelligence.market_features.spy_regime_contracts.ScenarioHorizon``);
one bounded option-chain batch with one retrieval instant; the underlying
price and as-of time; and bounded selector configuration. There is no field
anywhere in this package for news text, model output, a credential, a
database path, or brokerage data.

- ``contracts`` -- strict Pydantic v2 shapes (``extra="forbid"``, bounded
  collections, timezone-aware timestamps, finite numerics): the input
  (``ContractSelectorInput``), the per-contract quote shape
  (``OptionContractQuote``, deliberately independent of
  ``data_connectors.alpaca_options_chain.OptionChainSnapshot`` -- this
  package never imports a data connector), the centralized, provisional,
  documented filter configuration (``SelectorConfig``), and the output
  (``ContractSelectorResult``).
- ``selector`` -- the pure, deterministic filtering function
  (``select_eligible_contracts``): a fixed, published, centralized-threshold
  filter order (expiration/DTE; option type when a directional side is
  explicitly supplied; strike/moneyness; delta range; required IV and
  Greeks; positive bid/ask; non-crossed quote; maximum absolute/percentage
  spread; minimum quote size where available; snapshot freshness; feed
  provenance), producing eligible contracts in deterministic order, bounded
  rejection counts by a fixed reason enum, and one of three statuses
  (``eligible`` / ``no_eligible_contracts`` / ``indeterminate``). No
  recommendation, ranking, score, prediction, or trade action is produced
  anywhere in this package -- there is simply no field for one.
- ``serialization`` -- a symlink-refusing / no-overwrite / atomic / bounded
  local JSON round trip, mirroring
  ``market_intelligence.evaluation.spy_vwap_reversion_serialization``.

**Open interest is unavailable from the current option-chain endpoint and is
never invented, inferred, or replaced with volume anywhere in this
package** -- see ``DATA_CATALOG.md`` and ``docs/OPTIONS_DECISION_WORKFLOW.md``.
An `indicative`-feed contract may be processed for offline design/testing
(``ContractSelectorResult.feed_is_live_opra`` is ``False`` in that case) but
must never be described as live OPRA data or used to support an execution
claim.

**Implemented and tested offline only.** No real selector run has been
performed against the real local database or the one stored SPY
option-chain batch, every filter threshold in ``SelectorConfig`` is a
provisional hypothesis (not tuned against the step-d VWAP-reversion
evaluation or the single stored option batch), and no contract
recommendation, usefulness, pricing-accuracy, execution, or profitability
claim is made anywhere in this package. The Options Strategy Agent (Phase 1
step f) does not exist and is not built by this package.
"""

from __future__ import annotations
