"""The declared development/test installation includes the dashboard dependency.

A fresh ``pip install -e ".[dev]"`` must be enough to run the complete suite,
including the Streamlit AppTest render tests, while a normal library
installation stays free of Streamlit.
"""

from __future__ import annotations

import tomllib

from market_intelligence.config.settings import REPO_ROOT

STREAMLIT = "streamlit>=1.40,<2"


def _project() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_dev_installation_includes_streamlit_with_the_accepted_range():
    extras = _project()["optional-dependencies"]
    assert STREAMLIT in extras["dev"]
    assert extras["dashboard"] == [STREAMLIT]  # the same range in both places


def test_normal_installation_does_not_require_streamlit():
    assert not any(d.lower().startswith("streamlit") for d in _project()["dependencies"])
