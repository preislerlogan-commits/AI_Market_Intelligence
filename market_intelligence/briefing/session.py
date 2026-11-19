"""One briefing per dashboard session, held only in memory.

``BriefingSessionController`` is framework-free: the Streamlit entry point
(``live_app``) passes it ``st.session_state`` as a plain mutable mapping.

- **Status before execution.** ``status()`` reports the non-network
  preflight and whether the configured authorization is recorded. The run
  is enabled only when both pass and no run has happened in this session.
- **At most one run per session, never automatic.** ``run_once`` runs only
  when called (the run button), refuses a second call, and records the
  attempt even when the briefing refuses, so a refresh can never rerun it.
- **Memory only.** The briefing runs in its own temporary workspace, which
  ``run_briefing`` deletes before returning. Only the materialized result
  (the dashboard view model and two identifiers) is placed in the session
  mapping; nothing is written to a file or a database.
- **Clear.** ``clear`` removes the result from memory. The one-run limit
  still holds for the rest of the session.

**This implements the display handoff only. It does not authorize or
execute a live run:** ``AUTHORIZED_LIVE_RECORDS`` is empty, so the default
controller's run control stays disabled.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_intelligence.briefing.workflow import (
    AUTHORIZED_LIVE_RECORDS,
    BriefingDependencies,
    BriefingRefusal,
    BriefingRequest,
    BriefingResult,
    LiveAuthorization,
    PreflightReport,
    preflight,
    run_briefing,
)
from market_intelligence.config.settings import Settings
from market_intelligence.dashboard.views import DashboardModel

RESULT_KEY = "briefing_result"
ATTEMPTED_KEY = "briefing_attempted"
REFUSAL_KEY = "briefing_refusal"


class SessionRefusal(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SessionStatus:
    preflight: PreflightReport
    authorized: bool
    already_run: bool

    @property
    def run_enabled(self) -> bool:
        return self.authorized and self.preflight.ready and not self.already_run

    @property
    def blockers(self) -> tuple[str, ...]:
        reasons = []
        if not self.authorized:
            reasons.append("live_run_not_authorized")
        if not self.preflight.ready:
            reasons.append("preflight_not_ready")
        if self.already_run:
            reasons.append("already_run_this_session")
        return tuple(reasons)


@dataclass(frozen=True)
class BriefingSessionRecord:
    """The materialized result kept in memory: views and identifiers only,
    never a path, a store or a raw evidence record."""

    model: DashboardModel
    registry_label: str
    bundle_id: str


class BriefingSessionController:
    def __init__(
        self,
        *,
        settings: Settings,
        authorization: LiveAuthorization | None,
        build_dependencies: Callable[[Settings, Any], BriefingDependencies],
        request: BriefingRequest | None = None,
        authorized_records: frozenset[str] = AUTHORIZED_LIVE_RECORDS,
        workspace_parent: Path | None = None,
        run: Callable[..., BriefingResult] = run_briefing,
    ) -> None:
        self._settings = settings
        self._authorization = authorization
        self._build = build_dependencies
        self._request = request or BriefingRequest()
        self._authorized_records = authorized_records
        self._workspace_parent = workspace_parent
        self._run = run

    def status(self, state: MutableMapping[str, Any]) -> SessionStatus:
        return SessionStatus(
            preflight=preflight(self._settings),
            authorized=isinstance(self._authorization, LiveAuthorization)
            and self._authorization.record_id in self._authorized_records,
            already_run=bool(state.get(ATTEMPTED_KEY)),
        )

    def run_once(self, state: MutableMapping[str, Any]) -> BriefingSessionRecord | None:
        """Run the briefing once, only if the status allows it. Records the
        attempt before running, so it can never repeat in this session."""
        status = self.status(state)
        if not status.run_enabled:
            raise SessionRefusal(status.blockers[0])
        state[ATTEMPTED_KEY] = True
        try:
            deps = self._build(self._settings, self._authorization)
            result = self._run(self._request, deps, workspace_parent=self._workspace_parent)
        except BriefingRefusal as refusal:
            state[REFUSAL_KEY] = refusal.reason
            return None
        record = BriefingSessionRecord(
            model=result.model, registry_label=result.registry_label, bundle_id=result.bundle_id
        )
        state[RESULT_KEY] = record
        return record

    @staticmethod
    def result(state: MutableMapping[str, Any]) -> BriefingSessionRecord | None:
        return state.get(RESULT_KEY)

    @staticmethod
    def clear(state: MutableMapping[str, Any]) -> None:
        """Remove the result (and any refusal) from memory. The one-run limit
        for this session is kept."""
        state.pop(RESULT_KEY, None)
        state.pop(REFUSAL_KEY, None)


def default_controller() -> BriefingSessionController:
    """The production controller: real settings (credentials are reported as
    booleans only), no authorization, and the live dependency builder, which
    refuses before building anything while no authorization is recorded."""
    from datetime import UTC, datetime

    from market_intelligence.briefing.workflow import live_dependencies

    return BriefingSessionController(
        settings=Settings(),
        authorization=None,
        build_dependencies=lambda settings, authorization: live_dependencies(
            settings, authorization, clock=lambda: datetime.now(UTC)
        ),
    )
