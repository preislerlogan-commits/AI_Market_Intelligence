"""The one-time macro-and-news briefing workflow, with fake sources and fake
model responses only: the complete flow, preflight and authorization,
citation preservation, abstention, cleanup and boundary refusals. No
provider, model, real database or network is contacted."""

from __future__ import annotations

import ast
import socket
import tempfile
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest
from pydantic import SecretStr

from market_intelligence.assistant import explain as A
from market_intelligence.briefing import fakes as F
from market_intelligence.briefing import registry as BR
from market_intelligence.briefing import workflow as W
from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.dashboard import labels as L
from market_intelligence.evidence.enums import EvidenceKind
from market_intelligence.evidence_adapters.clock import ClockHealthReading

PACKAGE = Path(W.__file__).parent
BRIEFING_PRODUCERS = BR.BRIEFING_PRODUCERS


def _deps(**changes) -> W.BriefingDependencies:
    fields = dict(
        sources=F.FakeSources(),
        model_client=F.FakeModelClient(),
        clock=F.fake_clock,
        clock_health=F.fake_clock_health,
    )
    fields.update(changes)
    return W.BriefingDependencies(**fields)


def _run(tmp_path=None, **changes) -> W.BriefingResult:
    return W.run_briefing(W.BriefingRequest(), _deps(**changes), workspace_parent=tmp_path)


def _refusal(tmp_path=None, **changes) -> str:
    with pytest.raises(W.BriefingRefusal) as excinfo:
        _run(tmp_path, **changes)
    return excinfo.value.reason


@pytest.fixture(scope="module")
def result() -> W.BriefingResult:
    return _run()


# --- The complete flow ---------------------------------------------------------------------------


def test_the_complete_flow_produces_a_labelled_non_directional_briefing(result):
    assert result.registry_label == BR.SYNTHETIC_LABEL
    assert result.agent_kinds == {
        "macro_agent": ("current_inference",),
        "news_agent": ("current_inference", "current_inference"),
    }
    assert {i.provenance.producer_id for i in result.items.values()} <= BRIEFING_PRODUCERS
    briefing = result.model.briefing
    assert briefing.label == L.BRIEFING_LABEL
    assert len(briefing.macro) == 3 and len(briefing.news) == 2
    assert len(briefing.interpretations) == 3 and briefing.assistant_unavailable is None
    assert result.model.vwap_lane.cards == () and result.model.cards == {}
    assert briefing.bundle.strip.readiness_label.startswith("BRIEFING EVIDENCE")
    for answer in result.answers.values():
        assert answer.answerable
        text = " ".join(s.text for s in (*answer.statements, *answer.inference))
        assert L.forbidden_terms_in(text) == []


def test_analyst_citations_resolve_to_the_stored_fred_and_news_evidence(result):
    items = result.items
    for item in items.values():
        if item.evidence_kind is not EvidenceKind.CURRENT_INFERENCE:
            continue
        assert item.payload["cited_evidence_keys"]
        parents = [items[p] for p in item.provenance.parent_evidence_ids]  # all in the bundle
        expected = {"macro_agent": "fred_macro_observations", "news_agent": "alpaca_news"}
        assert {p.provenance.producer_id for p in parents} == {expected[item.payload["agent"]]}
    macro = next(i for i in items.values() if i.payload.get("agent") == "macro_agent")
    (observation,) = [items[p] for p in macro.provenance.parent_evidence_ids]
    assert observation.payload["series_id"] == "FEDFUNDS"
    assert observation.payload["observation_date"] == "2026-12-01"
    for answer in result.answers.values():
        for statement in (*answer.statements, *answer.inference):
            assert set(statement.evidence_citation.cited_item_ids) <= set(items)


def test_identities_are_deterministic(result):
    again = _run()
    assert again.bundle_id == result.bundle_id and set(again.items) == set(result.items)


# --- Abstentions and stale inputs ----------------------------------------------------------------


def test_a_pre_model_abstention_is_recorded_as_missing_evidence():
    result = _run(sources=F.FakeSources(with_news=False))
    assert result.agent_kinds["news_agent"] == ("missing_evidence",)
    news = result.answers[A.QuestionId.NEWS_CONTEXT.value]
    assert news.inference == ()
    assert {s.label for s in news.statements} == {A.StatementLabel.MISSING_EVIDENCE}
    missing = {r.producer_id for r in result.model.briefing.missing}
    assert missing == {"news_agent", "alpaca_news"}  # the abstention and the absent articles


def test_stale_macro_evidence_yields_an_abstention_not_an_interpretation():
    result = _run(sources=F.FakeSources(stale_macro=True))
    assert result.agent_kinds["macro_agent"] == ("missing_evidence",)
    statements = result.answers[A.QuestionId.MISSING_OR_STALE.value].statements
    assert any("series_stale" in s.text for s in statements)


# --- Fail closed ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"model_client": F.FakeModelClient("post_model_abstention")},
            "post_model_abstention_not_representable",
        ),
        ({"model_client": F.FakeModelClient("directional")}, "analyst_policy_violation"),
        (
            {
                "clock_health": lambda: ClockHealthReading(
                    measured_at_utc=F.FAKE_NOW - timedelta(seconds=30),
                    offset_ms=5000,  # unhealthy: freshness cannot be trusted
                    last_sync_at_utc=F.FAKE_NOW - timedelta(minutes=5),
                )
            },
            "stale_evidence_cited",
        ),
    ],
)
def test_unrepresentable_directional_or_untrusted_outputs_fail_closed(tmp_path, changes, reason):
    assert _refusal(tmp_path, **changes) == reason
    assert list(tmp_path.iterdir()) == []  # the workspace was still removed


@pytest.mark.parametrize(
    ("parent", "reason"),
    [
        (REPO_ROOT / "data", "repository_directory_refused"),
        (REPO_ROOT, "repository_directory_refused"),
        (Path.home(), "non_temporary_directory_refused"),
    ],
)
def test_non_temporary_or_repository_locations_are_refused(parent, reason):
    assert _refusal(parent) == reason


def test_the_temporary_workspace_is_always_removed(tmp_path):
    _run(tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- Preflight and authorization -----------------------------------------------------------------


def _settings(**keys) -> Settings:
    blank = {name: None for name in ("alpaca_api_key", "alpaca_api_secret", "fred_api_key")}
    return Settings(_env_file=None, **{**blank, "openai_api_key": None, **keys})


def test_preflight_reports_configuration_without_exposing_secrets():
    report = W.preflight(_settings())
    assert not report.ready
    assert report.missing == ("alpaca_api_key_and_secret", "fred_api_key", "openai_api_key")
    secret = "sk-test-not-a-real-key-123456789"
    ready = W.preflight(
        _settings(
            alpaca_api_key=SecretStr("k" * 20),
            alpaca_api_secret=SecretStr("s" * 20),
            fred_api_key=SecretStr("f" * 32),
            openai_api_key=SecretStr(secret),
        )
    )
    assert ready.ready and ready.missing == ()
    assert secret not in repr(ready) and "kkkk" not in repr(ready)


def test_live_runs_are_refused_without_a_recorded_authorization(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no client may be built before authorization")

    from market_intelligence.data_connectors import alpaca_news, fred_macro_data

    monkeypatch.setattr(alpaca_news.AlpacaNewsClient, "__init__", refuse)
    monkeypatch.setattr(fred_macro_data.FredMacroDataClient, "__init__", refuse)
    configured = _settings(fred_api_key=SecretStr("f" * 32))
    for authorization in (None, W.LiveAuthorization(record_id="not-recorded")):
        with pytest.raises(W.BriefingRefusal, match="live_run_not_authorized"):
            W.live_dependencies(
                configured,
                authorization,
                clock=F.fake_clock,
                clock_health=F.fake_clock_health,
            )
    assert W.AUTHORIZED_LIVE_RECORDS == frozenset()
    assert _refusal(live=True) == "live_run_not_authorized"


def test_a_real_client_without_live_authorization_is_refused():
    from market_intelligence.model_clients.openai_structured import OpenAIStructuredClient

    client = OpenAIStructuredClient(settings=_settings())
    assert _refusal(model_client=client) == "live_client_requires_authorization"


# --- Boundaries ----------------------------------------------------------------------------------

_FORBIDDEN_IMPORTS = (
    "market_intelligence.contract_selection",
    "market_intelligence.setup_cards",
    "market_intelligence.agents.market_evidence_agent",
    "market_intelligence.data_connectors.alpaca_bars",
    "market_intelligence.data_connectors.alpaca_options_chain",
    "market_intelligence.market_features.spy_",
    "market_intelligence.evaluation",
    "market_intelligence.trade_journal",
    "market_intelligence.orchestration",
    "market_intelligence.storage.bar_repository",
    "market_intelligence.storage.option_chain_snapshot_repository",
    "market_intelligence.evidence_adapters.market_data",
    "market_intelligence.evidence_adapters.market_calculations",
    "market_intelligence.evidence_adapters.options",
    "market_intelligence.evidence_adapters.contract_selector",
)


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_no_price_strategy_selection_or_execution_component_is_imported(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith(_FORBIDDEN_IMPORTS), (module, name)


def test_the_registry_admits_no_spy_price_evidence():
    for label in (BR.SYNTHETIC_LABEL, BR.LIVE_LABEL):
        registry = BR.make_briefing_registry(label)
        assert {p.producer_id for p in registry.producers} == BRIEFING_PRODUCERS
        assert not any(s.spy_price_content for s in registry.payload_schemas)
        assert not any(
            rule.machine_decision_eligible for p in registry.producers for rule in p.emission_rules
        )


def test_no_network_real_database_or_selection_access(tmp_path, monkeypatch):
    real_connect = duckdb.connect
    temp_root = str(Path(tempfile.gettempdir()).resolve())

    def temporary_only(database=":memory:", *args, **kwargs):
        assert str(Path(database).resolve()).startswith(temp_root), "non-temporary database"
        return real_connect(database, *args, **kwargs)

    def refuse(*args, **kwargs):
        raise AssertionError("forbidden access")

    from market_intelligence.contract_selection import selector
    from market_intelligence.setup_cards import builder

    monkeypatch.setattr(duckdb, "connect", temporary_only)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(selector, "select_eligible_contracts", refuse)
    monkeypatch.setattr(builder, "build_setup_card", refuse)
    monkeypatch.setattr(builder, "build_no_qualified_setup_card", refuse)
    assert _run(tmp_path).agent_kinds["macro_agent"] == ("current_inference",)


# --- Dashboard -----------------------------------------------------------------------------------


def test_the_briefing_renders_in_the_existing_dashboard():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(PACKAGE / "dashboard_app.py"), default_timeout=240).run()
    assert not at.exception and not at.error
    at.sidebar.radio[0].set_value("premarket_brief").run()
    text = " ".join(str(e.value) for e in (*at.markdown, *at.warning, *at.info, *at.caption))
    for section in ("Macro conditions", "Relevant news", "INFERENCE: analyst interpretations"):
        assert section in text
    assert L.BRIEFING_LABEL in text and L.forbidden_terms_in(text) == []
    at.sidebar.radio[0].set_value("assistant").run()
    (questions,) = [r for r in at.radio if r.label == "Supported briefing questions"]
    assert len(questions.options) == 4 and not at.text_input
    at.sidebar.radio[0].set_value("market_overview").run()
    assert any(L.BRIEFING_EXCLUDED in i.value for i in at.info)
