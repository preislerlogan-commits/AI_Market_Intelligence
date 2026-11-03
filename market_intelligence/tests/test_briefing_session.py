"""The live briefing dashboard handoff, with fakes only: status before
execution, the disabled run control while unauthorized, one run per session,
persistence across rerenders, cleanup, clearing, and refusal of real
database or file persistence. No live dependency is ever built or run."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from market_intelligence.briefing import fakes as F
from market_intelligence.briefing import live_app
from market_intelligence.briefing import session as S
from market_intelligence.briefing import workflow as W
from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.dashboard import labels as L

APP = Path(live_app.__file__).with_name("live_dashboard_app.py")
RECORD = "test-only-record"


def _settings(ready: bool = True) -> Settings:
    keys = dict(
        alpaca_api_key=SecretStr("k" * 20) if ready else None,
        alpaca_api_secret=SecretStr("s" * 20) if ready else None,
        fred_api_key=SecretStr("f" * 32) if ready else None,
        openai_api_key=SecretStr("o" * 40) if ready else None,
    )
    return Settings(_env_file=None, **keys)


def _offline_deps(settings, authorization) -> W.BriefingDependencies:
    return W.BriefingDependencies(
        sources=F.FakeSources(),
        model_client=F.FakeModelClient(),
        clock=F.fake_clock,
        clock_health=F.fake_clock_health,
    )


class Counted:
    """Counts briefing runs and dependency builds across rerenders."""

    def __init__(self, deps=_offline_deps) -> None:
        self.builds = 0
        self.runs = 0
        self._deps = deps

    def build(self, settings, authorization):
        self.builds += 1
        return self._deps(settings, authorization)

    def run(self, *args, **kwargs):
        self.runs += 1
        return W.run_briefing(*args, **kwargs)


def _controller(
    counted: Counted,
    *,
    authorized: bool = True,
    ready: bool = True,
    workspace_parent=None,
) -> S.BriefingSessionController:
    return S.BriefingSessionController(
        settings=_settings(ready),
        authorization=W.LiveAuthorization(RECORD),
        build_dependencies=counted.build,
        # Test-only: the real recorded set is empty.
        authorized_records=frozenset({RECORD}) if authorized else W.AUTHORIZED_LIVE_RECORDS,
        workspace_parent=workspace_parent,
        run=counted.run,
    )


# --- The controller ------------------------------------------------------------------------------


def test_the_run_control_stays_disabled_without_a_recorded_authorization():
    counted = Counted()
    state: dict = {}
    controller = _controller(counted, authorized=False)
    status = controller.status(state)
    assert not status.authorized and not status.run_enabled
    assert status.blockers == ("live_run_not_authorized",)
    with pytest.raises(S.SessionRefusal, match="live_run_not_authorized"):
        controller.run_once(state)
    assert counted.builds == counted.runs == 0 and state == {}
    assert W.AUTHORIZED_LIVE_RECORDS == frozenset()
    unready = _controller(counted, ready=False).status({})
    assert unready.blockers == ("preflight_not_ready",) and not unready.run_enabled


def test_one_run_per_session_with_the_result_kept_in_memory_and_cleaned_up(tmp_path):
    counted = Counted()
    state: dict = {}
    controller = _controller(counted, workspace_parent=tmp_path)
    record = controller.run_once(state)
    assert record is not None and record.registry_label.startswith("synthetic-")
    assert record.model.briefing is not None
    assert list(tmp_path.iterdir()) == []  # the temporary store is already gone
    # Only the materialized result and the attempt flag are held in memory.
    assert set(state) == {S.ATTEMPTED_KEY, S.RESULT_KEY}
    assert isinstance(state[S.RESULT_KEY], S.BriefingSessionRecord)
    assert not any(isinstance(v, Path) for v in vars(state[S.RESULT_KEY]).values())
    with pytest.raises(S.SessionRefusal, match="already_run_this_session"):
        controller.run_once(state)
    assert counted.runs == 1


def test_clearing_removes_the_result_but_keeps_the_one_run_limit(tmp_path):
    counted = Counted()
    state: dict = {}
    controller = _controller(counted, workspace_parent=tmp_path)
    controller.run_once(state)
    controller.clear(state)
    assert controller.result(state) is None and S.RESULT_KEY not in state
    assert controller.status(state).already_run and not controller.status(state).run_enabled


def test_a_refused_run_is_recorded_and_never_retried(tmp_path):
    def refusing(settings, authorization):
        raise W.BriefingRefusal("credentials_missing")

    counted = Counted(deps=refusing)
    state: dict = {}
    controller = _controller(counted, workspace_parent=tmp_path)
    assert controller.run_once(state) is None
    assert state[S.REFUSAL_KEY] == "credentials_missing" and S.RESULT_KEY not in state
    with pytest.raises(S.SessionRefusal, match="already_run_this_session"):
        controller.run_once(state)
    assert counted.builds == 1


def test_real_database_and_repository_persistence_is_refused():
    data = REPO_ROOT / "data"
    before = sorted(p.name for p in data.iterdir()) if data.exists() else []
    counted = Counted()
    state: dict = {}
    controller = _controller(counted, workspace_parent=data)
    assert controller.run_once(state) is None
    assert state[S.REFUSAL_KEY] == "repository_directory_refused"
    after = sorted(p.name for p in data.iterdir()) if data.exists() else []
    assert after == before  # nothing was written to the repository's data directory


def test_the_handoff_writes_no_result_file():
    for module in (S, live_app):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("open(", "write_text", "write_bytes", "to_csv", "to_json", "duckdb"):
            assert forbidden not in source, (module.__name__, forbidden)


# --- The Streamlit entry point -------------------------------------------------------------------


def _app(monkeypatch, controller_factory):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(live_app, "controller_factory", controller_factory)
    return AppTest.from_file(str(APP), default_timeout=240).run()


def _run_button(at):
    (button,) = [b for b in at.button if b.label == "Run the authorized briefing once"]
    return button


def test_unauthorized_page_shows_status_and_keeps_the_run_disabled(monkeypatch):
    counted = Counted()
    at = _app(monkeypatch, lambda: _controller(counted, authorized=False))
    assert not at.exception and _run_button(at).disabled
    text = " ".join(str(e.value) for e in (*at.info, *at.warning, *at.caption, *at.markdown))
    assert live_app.NOT_AUTHORIZED_NOTICE in text and live_app.LIVE_ASSISTANT_UNAVAILABLE in text
    assert "Authorization:** not authorized" in text
    assert counted.builds == counted.runs == 0


def test_one_run_persists_across_rerenders_and_can_be_cleared(monkeypatch, tmp_path):
    counted = Counted()
    at = _app(monkeypatch, lambda: _controller(counted, workspace_parent=tmp_path))
    assert not _run_button(at).disabled and counted.runs == 0  # nothing runs on load
    _run_button(at).click().run()
    assert counted.runs == 1 and list(tmp_path.iterdir()) == []
    for _ in range(2):  # refreshes never rerun it
        at.run()
    assert counted.runs == 1 and _run_button(at).disabled
    text = " ".join(str(e.value) for e in (*at.markdown, *at.warning))
    assert "Macro conditions" in text and L.BRIEFING_LABEL in text
    at.radio[0].set_value("market_overview").run()
    assert any(L.BRIEFING_EXCLUDED in i.value for i in at.info)
    (clear,) = [b for b in at.button if b.label == "Clear the briefing from memory"]
    clear.click().run()
    assert "No briefing result is held in this session." in " ".join(c.value for c in at.caption)
    assert _run_button(at).disabled and counted.runs == 1
    assert not at.text_input  # no assistant or free-form input
