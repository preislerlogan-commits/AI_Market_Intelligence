"""Tests for scripts/complete_macro_characterization.py.

Synthetic and offline only: the CLI consumes a local scaffold
``EvaluationRunRecord`` plus a local ``MacroAdjudicationInput`` and never opens a
database, makes a network request, or calls OpenAI / an agent runtime. These
tests assert that boundary statically and dynamically, and exercise the
run-id-match / dry-run / no-overwrite / path-refusal / classification-preserving
/ sanitization behaviour. Passing them says nothing about any real agent output.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.evaluation.contracts import (
    AgentIdentifier,
    CitationAdjudication,
    CitationClassification,
    CitationReason,
    build_run_id,
)
from market_intelligence.evaluation.fixtures.macro_characterization import (
    CHARACTERIZATION_CREATED_AT,
    COMPLETE_ADJUDICATION_INPUT,
    COMPLETE_MULTI_CLAIM,
)
from market_intelligence.evaluation.macro_characterization_input import (
    MacroAdjudicationInput,
    adjudication_input_to_json_str,
)
from market_intelligence.evaluation.macro_characterization_workflow import (
    build_macro_characterization,
)
from market_intelligence.evaluation.serialization import read_record, write_record

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "complete_macro_characterization.py"
LOCAL_DIR = REPO_ROOT / "data" / "evaluations" / "local"

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "duckdb",
    "httpx",
    "socket",
    "urllib",
    "http",
    "requests",
    "market_intelligence.data_connectors",
    "market_intelligence.model_clients",
    "market_intelligence.storage",
    "market_intelligence.agents",
    "market_intelligence.orchestration",
    "market_intelligence.market_features",
    "market_intelligence.config",
)

_SCAFFOLD = build_macro_characterization(
    COMPLETE_MULTI_CLAIM, created_at=CHARACTERIZATION_CREATED_AT
).run_record


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("complete_macro_characterization", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(module: ModuleType, capsys, argv: list[str]) -> tuple[int, dict]:
    code = module.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.fixture
def local_files():
    """Yield a factory for uniquely named paths under ``data/evaluations/local/``.

    Every path handed out is removed afterwards so the gitignored directory
    stays clean.
    """
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    def _make(name: str) -> Path:
        path = LOCAL_DIR / f"pytest-{uuid.uuid4().hex}-{name}"
        created.append(path)
        return path

    yield _make

    for path in created:
        path.unlink(missing_ok=True)


def _write_scaffold(factory) -> Path:
    path = factory("record.json")
    write_record(_SCAFFOLD, path)
    return path


def _write_adjudications(factory, model: MacroAdjudicationInput) -> Path:
    path = factory("adj.json")
    path.write_text(adjudication_input_to_json_str(model), encoding="utf-8")
    return path


def _adj(claim_id, citation_id, classification, reason) -> CitationAdjudication:
    return CitationAdjudication(
        claim_id=claim_id,
        citation_id=citation_id,
        reviewer="synthetic-reviewer",
        adjudicated_at=CHARACTERIZATION_CREATED_AT,
        classification=classification,
        reason=reason,
    )


def _symlink_or_skip(src, dst, target_is_directory=False):
    try:
        os.symlink(src, dst, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")


# ---------------------------------------------------------------------------
# Offline boundary
# ---------------------------------------------------------------------------


def test_cli_script_imports_no_forbidden_dependency():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    for name in names:
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), name


def test_importing_the_script_pulls_in_no_db_provider_or_agent_module():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{SCRIPT_PATH}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "banned = {'openai', 'duckdb', 'httpx', 'socket'}\n"
        "leaked = banned & set(sys.modules)\n"
        "assert not leaked, leaked\n"
        "agenty = [x for x in sys.modules if x.startswith('market_intelligence.') and any(\n"
        "    k in x for k in ('data_connectors', 'model_clients', 'storage', 'agents',\n"
        "                     'orchestration', 'market_features'))]\n"
        "assert not agenty, agenty\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# Dry run (default) -- completes a characterization, writes nothing
# ---------------------------------------------------------------------------


def test_dry_run_completes_and_writes_nothing(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)
    output = local_files("out.json")

    code, payload = _run(
        module,
        capsys,
        ["--record", str(record), "--adjudications", str(adj), "--output", str(output)],
    )

    assert code == 0
    assert payload["mode"] == "dry_run"
    assert payload["output_written"] is False
    assert payload["expected_pair_count"] == 5
    assert payload["adjudication_count"] == 5
    assert payload["rubric_complete"] is True
    assert not output.exists()


def test_every_classification_is_preserved_in_the_tally(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)
    output = local_files("out.json")

    code, payload = _run(
        module,
        capsys,
        ["--record", str(record), "--adjudications", str(adj), "--output", str(output)],
    )

    assert code == 0
    assert payload["classification_tally"] == {
        "supported": 2,
        "partially_supported": 1,
        "unsupported": 1,
        "unable_to_determine": 1,
    }
    # The scaffold's transcription findings survive completion untouched.
    assert payload["finding_severity_tally"] == {"info": 3, "warning": 1, "failure": 1}


# ---------------------------------------------------------------------------
# run_id must match the scaffold
# ---------------------------------------------------------------------------


def test_run_id_mismatch_is_refused(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    mismatched = MacroAdjudicationInput(
        run_id=build_run_id(
            AgentIdentifier.MACRO_ANALYST, "a-different-label", CHARACTERIZATION_CREATED_AT
        ),
        adjudications=list(COMPLETE_ADJUDICATION_INPUT.adjudications),
    )
    adj = _write_adjudications(local_files, mismatched)
    output = local_files("out.json")

    code, payload = _run(
        module,
        capsys,
        ["--record", str(record), "--adjudications", str(adj), "--output", str(output)],
    )

    assert code == 2
    assert payload == {"error": "run_id_mismatch"}
    assert not output.exists()


# ---------------------------------------------------------------------------
# Completion rejects an incomplete / malformed adjudication set
# ---------------------------------------------------------------------------


def test_missing_pair_is_refused(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    partial = MacroAdjudicationInput(
        run_id=COMPLETE_ADJUDICATION_INPUT.run_id,
        adjudications=list(COMPLETE_ADJUDICATION_INPUT.adjudications)[:-1],
    )
    adj = _write_adjudications(local_files, partial)

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(local_files("out.json")),
        ],
    )

    assert code == 2
    assert payload == {"error": "completion_failed"}


def test_duplicate_pair_is_refused(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    dupe = MacroAdjudicationInput(
        run_id=COMPLETE_ADJUDICATION_INPUT.run_id,
        adjudications=[
            *COMPLETE_ADJUDICATION_INPUT.adjudications,
            COMPLETE_ADJUDICATION_INPUT.adjudications[0],
        ],
    )
    adj = _write_adjudications(local_files, dupe)

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(local_files("out.json")),
        ],
    )

    assert code == 2
    assert payload == {"error": "completion_failed"}


def test_already_adjudicated_record_maps_to_completion_failed(local_files, capsys):
    """A --record that already carries adjudications hits the scaffold-identity
    guard and is mapped to the CLI's existing sanitized failure marker."""
    module = load_script_module()
    from market_intelligence.evaluation.macro_characterization_workflow import (
        complete_macro_characterization,
    )

    completed = complete_macro_characterization(
        _SCAFFOLD, list(COMPLETE_ADJUDICATION_INPUT.adjudications)
    )
    record = local_files("record.json")
    write_record(completed, record)
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(local_files("out.json")),
        ],
    )

    assert code == 2
    assert payload == {"error": "completion_failed"}


def test_unexpected_pair_is_refused(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    unexpected = MacroAdjudicationInput(
        run_id=COMPLETE_ADJUDICATION_INPUT.run_id,
        adjudications=[
            *list(COMPLETE_ADJUDICATION_INPUT.adjudications)[:-1],
            _adj(
                "claim-single-match",
                "cite-b",
                CitationClassification.SUPPORTED,
                CitationReason.VALUE_MATCHES_EVIDENCE,
            ),
        ],
    )
    adj = _write_adjudications(local_files, unexpected)

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(local_files("out.json")),
        ],
    )

    assert code == 2
    assert payload == {"error": "completion_failed"}


# ---------------------------------------------------------------------------
# --write output round trip + no overwrite
# ---------------------------------------------------------------------------


def test_write_produces_a_completed_record_that_round_trips(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)
    output = local_files("out.json")

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(output),
            "--write",
        ],
    )

    assert code == 0
    assert payload["mode"] == "write"
    assert payload["output_written"] is True

    completed = read_record(output)
    assert completed.run_id == _SCAFFOLD.run_id
    assert completed.agent == "macro_analyst"
    assert len(completed.adjudications) == 5
    assert {a.classification for a in completed.adjudications} == {
        CitationClassification.SUPPORTED,
        CitationClassification.PARTIALLY_SUPPORTED,
        CitationClassification.UNSUPPORTED,
        CitationClassification.UNABLE_TO_DETERMINE,
    }
    # The scaffold's findings are carried through unchanged.
    assert len(completed.findings) == len(_SCAFFOLD.findings)


def test_write_refuses_to_overwrite_an_existing_output(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)
    output = local_files("out.json")
    output.write_text("sentinel", encoding="utf-8")

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(output),
            "--write",
        ],
    )

    assert code == 1
    assert payload == {"error": "write_failed"}
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_write_does_not_create_a_missing_directory(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)
    output = LOCAL_DIR / f"missing-{uuid.uuid4().hex}" / "out.json"

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(output),
            "--write",
        ],
    )

    assert code == 1
    assert payload == {"error": "write_failed"}
    assert not output.parent.exists()


# ---------------------------------------------------------------------------
# Path allowlist: all three of --record / --adjudications / --output must
# resolve strictly inside data/evaluations/local/
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "rec.json",  # repository root
        "docs/rec.json",
        "market_intelligence/tests/rec.json",
        "market_intelligence/evaluation/fixtures/rec.json",
        "data/evaluations/rec.json",  # parent of local/, not local/ itself
    ],
)
def test_record_path_outside_the_allowlist_is_refused(local_files, relative, capsys):
    module = load_script_module()
    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(REPO_ROOT / relative),
            "--adjudications",
            str(local_files("adj.json")),
            "--output",
            str(local_files("out.json")),
        ],
    )
    assert code == 2
    assert payload == {"error": "record_path_refused"}


@pytest.mark.parametrize(
    "relative",
    [
        "rec.json",
        "docs/adj.json",
        "data/evaluations/adj.json",
    ],
)
def test_adjudications_path_outside_the_allowlist_is_refused(local_files, relative, capsys):
    module = load_script_module()
    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(local_files("record.json")),
            "--adjudications",
            str(REPO_ROOT / relative),
            "--output",
            str(local_files("out.json")),
        ],
    )
    assert code == 2
    assert payload == {"error": "adjudications_path_refused"}


@pytest.mark.parametrize(
    "relative",
    [
        "rec.json",
        "docs/out.json",
        "market_intelligence/evaluation/fixtures/out.json",
        "data/evaluations/out.json",
    ],
)
def test_output_path_outside_the_allowlist_is_refused(local_files, relative, capsys):
    module = load_script_module()
    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(local_files("record.json")),
            "--adjudications",
            str(local_files("adj.json")),
            "--output",
            str(REPO_ROOT / relative),
            "--write",
        ],
    )
    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not (REPO_ROOT / relative).exists()


def test_paths_outside_the_repository_are_refused(tmp_path, local_files, capsys):
    module = load_script_module()
    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(tmp_path / "rec.json"),
            "--adjudications",
            str(local_files("adj.json")),
            "--output",
            str(local_files("out.json")),
        ],
    )
    assert code == 2
    assert payload == {"error": "record_path_refused"}


def test_dotdot_traversal_escaping_the_local_dir_is_refused(local_files, capsys):
    module = load_script_module()
    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(local_files("record.json")),
            "--adjudications",
            str(local_files("adj.json")),
            "--output",
            str(LOCAL_DIR / ".." / "out.json"),
            "--write",
        ],
    )
    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not (REPO_ROOT / "data" / "evaluations" / "out.json").exists()


def test_symlink_escaping_the_local_dir_is_refused(tmp_path, capsys):
    module = load_script_module()
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    link = LOCAL_DIR / f"pytest-escape-{uuid.uuid4().hex}"
    _symlink_or_skip(tmp_path, link, target_is_directory=True)
    try:
        code, payload = _run(
            module,
            capsys,
            [
                "--record",
                str(link / "record.json"),
                "--adjudications",
                str(LOCAL_DIR / "adj.json"),
                "--output",
                str(LOCAL_DIR / "out.json"),
            ],
        )
        assert code == 2
        assert payload == {"error": "record_path_refused"}
    finally:
        link.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Sanitized errors / output
# ---------------------------------------------------------------------------


def test_malformed_record_yields_a_sanitized_error(local_files, capsys):
    module = load_script_module()
    record = local_files("record.json")
    record.write_text("not json", encoding="utf-8")
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(local_files("out.json")),
        ],
    )
    assert code == 2
    assert payload == {"error": "invalid_record"}


def test_malformed_adjudications_yields_a_sanitized_error(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    adj = local_files("adj.json")
    adj.write_text(json.dumps({"run_id": "not-a-run-id"}), encoding="utf-8")

    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(local_files("out.json")),
        ],
    )
    assert code == 2
    assert payload == {"error": "invalid_adjudications"}


def test_missing_record_file_yields_a_sanitized_error(local_files, capsys):
    module = load_script_module()
    code, payload = _run(
        module,
        capsys,
        [
            "--record",
            str(local_files("record.json")),
            "--adjudications",
            str(_write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)),
            "--output",
            str(local_files("out.json")),
        ],
    )
    assert code == 2
    assert payload == {"error": "invalid_record"}


def test_no_output_or_error_leaks_ids_notes_paths_or_record_text(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    adj = _write_adjudications(local_files, COMPLETE_ADJUDICATION_INPUT)
    output = local_files("out.json")

    code, out_text = None, None
    for argv in (
        ["--record", str(record), "--adjudications", str(adj), "--output", str(output)],
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(output),
            "--write",
        ],
    ):
        code = module.main(argv)
        out_text = capsys.readouterr().out
        assert code == 0
        for banned in (
            "claim-",
            "cite-",
            "SYNTH",
            "synthetic-reviewer",
            "reviewer_note",
            "2031-",
            "evalrun-",
            str(REPO_ROOT),
            "evaluations/local",
            "evaluations\\local",
        ):
            assert banned not in out_text, banned


def test_completion_failed_error_is_sanitized(local_files, capsys):
    module = load_script_module()
    record = _write_scaffold(local_files)
    partial = MacroAdjudicationInput(
        run_id=COMPLETE_ADJUDICATION_INPUT.run_id,
        adjudications=list(COMPLETE_ADJUDICATION_INPUT.adjudications)[:-1],
    )
    adj = _write_adjudications(local_files, partial)

    code = module.main(
        [
            "--record",
            str(record),
            "--adjudications",
            str(adj),
            "--output",
            str(local_files("out.json")),
        ]
    )
    out_text = capsys.readouterr().out
    assert code == 2
    for banned in ("claim-", "cite-", "SYNTH", str(REPO_ROOT), "evaluations/local"):
        assert banned not in out_text, banned


def test_all_three_paths_are_required():
    module = load_script_module()
    with pytest.raises(SystemExit):
        module.main(["--record", "r.json", "--adjudications", "a.json"])
