"""Offline demo entry point for the Evidence-to-Dashboard vertical slice.

``python -m market_intelligence.integration.demo`` builds every scenario in a
fresh temporary workspace, reads each one back from its temporary store,
validates it through the dashboard's presentation adapter, prints a short
summary and removes the workspace. ``--dashboard`` then shows the same
read-back scenarios in the existing Streamlit dashboard (localhost only).

It refuses the real database, the repository's data directory, any
non-temporary location and settings that carry provider credentials. It
contacts no provider or model, writes nothing outside the temporary workspace
and leaves no evidence, checkpoint, key or database behind.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from market_intelligence.dashboard.adapter import present
from market_intelligence.dashboard.validation import DashboardInputError
from market_intelligence.dashboard.views import DashboardModel
from market_intelligence.integration.scenarios import SCENARIOS, SliceScenario, run_scenario
from market_intelligence.integration.workspace import temporary_workspace


@dataclass(frozen=True)
class ScenarioSummary:
    scenario_id: str
    bundles: Mapping[str, str]
    cards: tuple[str, ...]
    refusals: tuple[str, ...]


@dataclass(frozen=True)
class SliceResult:
    """Everything the dashboard needs, held in memory after the temporary
    workspace is gone."""

    order: tuple[str, ...]
    titles: Mapping[str, str]
    models: Mapping[str, DashboardModel | str]
    summaries: Mapping[str, ScenarioSummary]


def build_slice(
    parent: Path | None = None, specs: Sequence[SliceScenario] = SCENARIOS
) -> SliceResult:
    """Run every scenario in a temporary workspace (under ``parent``, which
    must itself be temporary), present each read-back scenario, and remove
    the workspace before returning."""
    models: dict[str, DashboardModel | str] = {}
    summaries: dict[str, ScenarioSummary] = {}
    with temporary_workspace(parent) as workspace:
        for spec in specs:
            store, scenario = run_scenario(workspace, spec)
            try:
                models[spec.scenario_id] = present(scenario)
            except DashboardInputError as error:
                models[spec.scenario_id] = error.reason
            summaries[spec.scenario_id] = ScenarioSummary(
                scenario_id=spec.scenario_id,
                bundles=dict(store.bundles),
                cards=tuple(store.cards),
                refusals=tuple(store.refusals),
            )
    return SliceResult(
        order=tuple(s.scenario_id for s in specs),
        titles={s.scenario_id: s.title for s in specs},
        models=models,
        summaries=summaries,
    )


def _summary_lines(result: SliceResult) -> list[str]:
    lines = ["Offline Evidence-to-Dashboard slice (synthetic; temporary stores removed)."]
    for scenario_id in result.order:
        model = result.models[scenario_id]
        summary = result.summaries[scenario_id]
        if isinstance(model, str):
            lines.append(f"- {scenario_id}: validation failed ({model})")
            continue
        card = model.vwap_lane.cards[0] if model.vwap_lane.cards else None
        state = card.readiness_label if card else model.vwap_lane.text
        refused = (
            f"; refused before storage: {', '.join(summary.refusals)}" if summary.refusals else ""
        )
        lines.append(
            f"- {scenario_id}: {len(summary.bundles)} bundles, {len(summary.cards)} card(s); "
            f"{state}{refused}"
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="show the read-back scenarios in the local Streamlit dashboard",
    )
    args = parser.parse_args(argv)
    if args.dashboard:
        app = Path(__file__).with_name("dashboard_app.py")
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)])
    for line in _summary_lines(build_slice()):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
