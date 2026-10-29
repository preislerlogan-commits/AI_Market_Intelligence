"""Deterministic fakes for the briefing workflow: synthetic FRED and news
sources (the real connector dataclasses, synthetic values) and a fake
structured-output model client. Used by tests and the offline dashboard
entry point. No provider, model or network is ever contacted.

The fake model builds each analyst's structured response from the evidence
package it is handed, so its citations always name evidence that exists. Its
modes exist to prove the workflow fails closed: ``directional`` returns
directional wording and ``post_model_abstention`` returns a zero-claim
"insufficient" news response after a (fake) model call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from market_intelligence.agents.macro_analyst import MacroAnalystModelAnalysis, MacroClaimDraft
from market_intelligence.agents.news_analyst import EventClaim, NewsAnalystModelAnalysis
from market_intelligence.data_connectors.alpaca_news import NewsItem
from market_intelligence.data_connectors.fred_macro_data import FredObservation, FredSeriesMetadata
from market_intelligence.evidence_adapters.clock import ClockHealthReading
from market_intelligence.model_clients.openai_structured import StructuredOutputResult

# Synthetic as-of, outside the sealed SPY holdout window (no price evidence is
# read in any case).
FAKE_NOW = datetime(2027, 1, 12, 14, 0, tzinfo=UTC)


def fake_clock() -> datetime:
    return FAKE_NOW


def fake_clock_health() -> ClockHealthReading:
    """A synthetic clock-health reading taken just before the as-of time."""
    return ClockHealthReading(
        measured_at_utc=FAKE_NOW - timedelta(seconds=30),
        offset_ms=5,
        last_sync_at_utc=FAKE_NOW - timedelta(minutes=5),
    )


class FakeSources:
    """Synthetic retrieval. ``with_news=False`` returns no articles;
    ``stale_macro=True`` returns observations far older than the freshness
    window."""

    def __init__(self, *, with_news: bool = True, stale_macro: bool = False) -> None:
        self.with_news = with_news
        self.stale_macro = stale_macro
        self.calls: list[tuple[str, ...]] = []

    def news(self, symbol: str, limit: int) -> list[NewsItem]:
        self.calls.append(("news", symbol, str(limit)))
        if not self.with_news:
            return []
        return [
            NewsItem(
                provider="alpaca",
                provider_article_id=article_id,
                headline=headline,
                source="synthetic_wire",
                url=f"https://example.com/synthetic/{article_id}",
                summary=summary,
                created_at=created,
                updated_at=None,
                related_symbols=("SPY",),
                retrieved_at="2027-01-12T13:59:00Z",
            )
            for article_id, headline, summary, created in (
                (
                    "51000001",
                    "Synthetic: Fed holds its policy rate steady",
                    "Synthetic provider summary of a policy-rate decision.",
                    "2027-01-12T12:00:00Z",
                ),
                (
                    "51000002",
                    "Synthetic: Fed officials discuss the policy rate path",
                    None,
                    "2027-01-12T12:30:00Z",
                ),
            )
        ]

    def fred_observations(self, series_id: str, observation_start: str) -> list[FredObservation]:
        self.calls.append(("fred_observations", series_id, observation_start))
        dates = ("2024-06-01", "2024-07-01") if self.stale_macro else ("2026-11-01", "2026-12-01")
        vintage = "2024-08-02" if self.stale_macro else "2027-01-05"
        return [
            FredObservation(
                provider="fred",
                series_id=series_id,
                observation_date=observed,
                value=Decimal(value),
                is_missing=False,
                realtime_start=vintage,
                realtime_end="9999-12-31",
                retrieved_at="2027-01-12T13:55:00Z",
            )
            for observed, value in zip(dates, ("4.33", "4.33"), strict=True)
        ]

    def fred_metadata(self, series_id: str) -> FredSeriesMetadata:
        self.calls.append(("fred_metadata", series_id))
        return FredSeriesMetadata(
            provider="fred",
            series_id=series_id,
            title="Synthetic Federal Funds Effective Rate",
            observation_start="1954-07-01",
            observation_end="2026-12-01",
            frequency="Monthly",
            frequency_short="M",
            units="Percent",
            units_short="%",
            seasonal_adjustment="Not Seasonally Adjusted",
            seasonal_adjustment_short="NSA",
            last_updated="2027-01-05T15:16:01Z",
            popularity=90,
            notes=None,
            retrieved_at_utc="2027-01-12T13:55:00Z",
        )


class FakeModelClient:
    """A stand-in for the structured-output client. Never calls a model."""

    def __init__(self, mode: str = "completed") -> None:
        if mode not in ("completed", "directional", "post_model_abstention"):
            raise ValueError("unknown fake mode")
        self.mode = mode
        self.calls: list[str] = []

    def generate(self, *, instructions: str, evidence: dict[str, Any], output_model):
        self.calls.append(output_model.__name__)
        if output_model is MacroAnalystModelAnalysis:
            parsed = self._macro(evidence)
        elif output_model is NewsAnalystModelAnalysis:
            parsed = self._news(evidence)
        else:
            raise AssertionError("unexpected output model")
        return StructuredOutputResult(
            status="completed",
            parsed=parsed,
            model="fake-model-1",
            response_id="resp_fake_briefing_1",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            incomplete_reason=None,
        )

    def _macro(self, evidence: dict[str, Any]) -> MacroAnalystModelAnalysis:
        tail = " Markets will rise from here." if self.mode == "directional" else ""
        claims = [
            MacroClaimDraft(
                series_id=fact["series_id"],
                claim_summary=(
                    f"The stored monthly observation dated {fact['observation_date']} reports "
                    f"the latest {fact['series_id']} value, per official FRED metadata.{tail}"
                ),
                evidence_ids=[evidence_id],
                economic_category="policy_rate",
                transmission_channels=["rates"],
                conditional_mechanism=(
                    "Short-term policy rates can in general relate to broad borrowing costs."
                ),
            )
            for evidence_id, fact in evidence["series"].items()
        ]
        return MacroAnalystModelAnalysis(
            evidence_quality="sufficient", macro_claims=claims, limitations=[]
        )

    def _news(self, evidence: dict[str, Any]) -> NewsAnalystModelAnalysis:
        if self.mode == "post_model_abstention":
            return NewsAnalystModelAnalysis(
                evidence_quality="insufficient",
                event_claims=[],
                limitations=["No supplied article was sufficiently relevant."],
            )
        claims = [
            EventClaim(
                event_type="monetary_policy",
                claim_summary="The provider reports a Federal Reserve policy-rate item.",
                evidence_ids=[evidence_id],
                content_basis=article["content_scope"],
                transmission_channels=["rates"],
                relevance="broad_market",
                relevance_rationale=(
                    "The cited article reports a Fed policy-rate item, which can matter for "
                    "broad equity markets through the rates channel."
                ),
                conditional_mechanism=None,
            )
            for evidence_id, article in evidence["articles"].items()
        ]
        return NewsAnalystModelAnalysis(
            evidence_quality="sufficient", event_claims=claims, limitations=[]
        )
