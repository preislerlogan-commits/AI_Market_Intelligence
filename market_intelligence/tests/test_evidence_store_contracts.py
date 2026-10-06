"""Store contracts, identities and registry files (synthetic, temporary files only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.evidence.registry import compute_registry_version_id
from market_intelligence.evidence_store.contracts import (
    AuditEvent,
    CheckpointKeyRotation,
    RecoveryRecord,
    RegistryActivationContent,
    StoreCommitContent,
    compute_activation_id,
    compute_commit_digest,
    compute_row_sha256,
    seal_activation,
    seal_commit,
)
from market_intelligence.evidence_store.enums import (
    STOP_FINDINGS,
    ActivationReason,
    CheckpointAuthAlgorithm,
    IntegrityFinding,
    RecoveryKind,
    RegistryKind,
    StoreAuditEventType,
    StoreOperation,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import StoreRefusal
from market_intelligence.evidence_store.registry_files import (
    SetupDefinitionRegistry,
    compute_setup_registry_version_id,
    load_registry_content,
    load_registry_file,
    parse_registry_bytes,
    render_canonical_file,
    strict_parse,
)
from market_intelligence.tests import evidence_store_fixtures as fx
from market_intelligence.tests import setup_card_fixtures as s

NOW = datetime(2027, 1, 12, 13, 0, tzinfo=UTC)
EVR = "evr1_" + "1" * 64


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


# --- Contracts -----------------------------------------------------------------------------------


def test_commit_digest_is_deterministic_and_chained():
    first = seal_commit(
        StoreCommitContent(
            commit_seq=1,
            committed_at_utc=NOW,
            writer_id=StoreWriterId.STORE_SERVICE,
            operation=StoreOperation.INITIALIZE_STORE,
            row_count=2,
            rows_sha256="a" * 64,
            prev_commit_digest=None,
        )
    )
    assert first.commit_digest == compute_commit_digest(first)
    # Golden: the digest is a pure function of the canonical content.
    assert (
        first.commit_digest
        == seal_commit(
            StoreCommitContent(**{n: getattr(first, n) for n in StoreCommitContent.model_fields})
        ).commit_digest
    )
    with pytest.raises(ValidationError):
        StoreCommitContent(
            commit_seq=2,
            committed_at_utc=NOW,
            writer_id=StoreWriterId.STORE_SERVICE,
            operation=StoreOperation.INITIALIZE_STORE,
            row_count=1,
            rows_sha256="a" * 64,
            prev_commit_digest=None,  # only commit 1 has no predecessor
        )
    with pytest.raises(ValidationError):
        StoreCommitContent(
            commit_seq=1,
            committed_at_utc=NOW,
            writer_id="trading_bot",
            operation=StoreOperation.INITIALIZE_STORE,
            row_count=1,
            rows_sha256="a" * 64,
            prev_commit_digest=None,
        )


def test_activation_identity_excludes_activation_time_and_refuses_backdating():
    content = RegistryActivationContent(
        registry_kind=RegistryKind.EVIDENCE_REGISTRY,
        registry_version_id=EVR,
        effective_from_utc=NOW,
        activation_reason=ActivationReason.INITIAL_ACTIVATION,
        supersedes_activation_id=None,
        authorization_ref=fx.AUTH,
    )
    activation = seal_activation(content, NOW)
    later = seal_activation(content, NOW - timedelta(minutes=5))
    assert activation.activation_id == later.activation_id == compute_activation_id(content)
    assert activation.activation_id.startswith("rga1_")
    with pytest.raises(ValidationError):
        seal_activation(content, NOW + timedelta(seconds=1))  # effective before recorded
    with pytest.raises(ValidationError):  # only an initial activation has no predecessor
        RegistryActivationContent(**{**content.model_dump(), "activation_reason": "rollback"})


@pytest.mark.parametrize(
    "ref",
    [
        "project-state:63",
        "project-state:0@" + "a" * 40,
        "project-state:63@" + "A" * 40,
        "PROJECT_STATE_ITEM_63",
        "project-state:63@" + "a" * 39,
    ],
)
def test_authorization_ref_syntax_is_bounded(ref):
    with pytest.raises(ValidationError):
        CheckpointKeyRotation(
            previous_key_id="key_one",
            new_key_id="key_two",
            algorithm=CheckpointAuthAlgorithm.HMAC_SHA256,
            authorization_ref=ref,
        )


def test_rotation_records_key_ids_only():
    rotation = CheckpointKeyRotation(
        previous_key_id="key_one",
        new_key_id="key_two",
        algorithm=CheckpointAuthAlgorithm.HMAC_SHA256,
        authorization_ref=fx.AUTH,
    )
    assert set(rotation.model_dump()) == {
        "previous_key_id",
        "new_key_id",
        "algorithm",
        "authorization_ref",
    }
    with pytest.raises(ValidationError):
        CheckpointKeyRotation(**{**rotation.model_dump(), "new_key_id": "key_one"})


def test_holdout_audit_events_never_carry_a_record_id():
    with pytest.raises(ValidationError):
        AuditEvent(
            event_type=StoreAuditEventType.HOLDOUT_WRITE_REFUSED,
            actor="evidence_ingest",
            subject_record_id="evi1_" + "0" * 64,
            reason_code="holdout_restricted",
        )
    with pytest.raises(ValidationError):  # bounded tokens only
        AuditEvent(
            event_type=StoreAuditEventType.ENVELOPE_REFUSED,
            actor="evidence_ingest",
            reason_code="Traceback (most recent call last)",
        )


def test_recovery_record_preserves_superseded_checkpoint_and_authorization():
    record = RecoveryRecord(
        recovery_kind=RecoveryKind.BACKUP_RESTORATION,
        restored_high_water_commit_seq=3,
        restored_high_water_commit_digest="b" * 64,
        backup_file_sha256="c" * 64,
        backup_verification_sha256="d" * 64,
        replaced_high_water_commit_seq=None,
        replaced_high_water_commit_digest=None,
        superseded_checkpoint_commit_seq=5,
        superseded_checkpoint_commit_digest="e" * 64,
        superseded_checkpoint_created_at_utc=NOW,
        superseded_checkpoint_sha256="f" * 64,
        recovery_authorization_ref=fx.AUTH,
    )
    assert record.superseded_checkpoint_commit_seq == 5
    with pytest.raises(ValidationError):
        RecoveryRecord(**{**record.model_dump(), "replaced_high_water_commit_seq": 4})


def test_row_hash_covers_every_column_but_itself():
    columns = {"a": 1, "b": NOW, "row_sha256": "ignored"}
    assert compute_row_sha256(columns) == compute_row_sha256({**columns, "row_sha256": "x"})
    assert compute_row_sha256(columns) != compute_row_sha256({**columns, "a": 2})


def test_stop_and_non_stop_findings_are_partitioned():
    # Only a rebuildable projection is not a stop (implementation review).
    assert IntegrityFinding.PROJECTION_MISMATCH not in STOP_FINDINGS
    assert STOP_FINDINGS == frozenset(IntegrityFinding) - {IntegrityFinding.PROJECTION_MISMATCH}
    for finding in (
        IntegrityFinding.KEY_COLUMN_MISMATCH,
        IntegrityFinding.BUNDLE_REPRODUCTION_MISMATCH,
        IntegrityFinding.IDENTITY_MISMATCH,
        IntegrityFinding.ROW_HASH_MISMATCH,
        IntegrityFinding.COMMIT_CHAIN_MISMATCH,
        IntegrityFinding.IMPOSSIBLE_DUPLICATE_IDENTITY,
        IntegrityFinding.PERSISTED_HOLDOUT_VIOLATION,
        IntegrityFinding.INVALID_REGISTRY_ACTIVATION,
        IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY,
    ):
        assert finding in STOP_FINDINGS


# --- Registry files ------------------------------------------------------------------------------


def _evidence_file(tmp_path, registry=None):
    return fx.write_registry_file(
        tmp_path / "registries",
        RegistryKind.EVIDENCE_REGISTRY,
        registry or fx.no_machine_decision_registry(),
    )


def test_canonical_registry_file_loads_with_deterministic_identity(tmp_path):
    registry = fx.no_machine_decision_registry()
    path = _evidence_file(tmp_path, registry)
    loaded = load_registry_file(
        RegistryKind.EVIDENCE_REGISTRY, path, registries_root=tmp_path / "registries"
    )
    assert loaded.registry_version_id == compute_registry_version_id(registry)
    assert loaded.source_path == f"registries/evidence/{registry.registry_label}.json"
    assert load_registry_content(RegistryKind.EVIDENCE_REGISTRY, loaded.content_json) == registry


def test_label_change_is_a_new_identity():
    one = fx.no_machine_decision_registry()
    two = fx.no_machine_decision_registry(registry_label="synthetic-test-2")
    assert compute_registry_version_id(one) != compute_registry_version_id(two)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda b: b"\xef\xbb\xbf" + b, "registry_file_not_utf8"),
        (
            lambda b: b.replace(b'"file_format"', b'"file_format": "x", "file_format"', 1),
            "registry_duplicate_key",
        ),
        (
            lambda b: b.replace(
                b'  "registry_version_id"', b'  "x": 1.5, "registry_version_id"', 1
            ),
            "registry_float_not_allowed",
        ),
        (lambda b: b.replace(b"\n}\n", b',\n  "x": NaN\n}\n', 1), "registry_float_not_allowed"),
        (lambda b: b + b"{}", "registry_json_invalid"),
        (lambda b: b"\xff\xfe" + b, "registry_file_not_utf8"),
        (lambda b: b.replace(b"  ", b"   "), "registry_not_canonical_form"),
    ],
)
def test_strict_parsing_refusals(tmp_path, mutate, reason):
    raw = _evidence_file(tmp_path).read_bytes()
    assert (
        _reason(
            parse_registry_bytes,
            RegistryKind.EVIDENCE_REGISTRY,
            mutate(raw),
            file_name="synthetic-test.json",
        )
        == reason
    )


def test_unknown_field_and_unknown_format_are_refused(tmp_path):
    raw = _evidence_file(tmp_path).read_bytes()
    data = json.loads(raw)
    data["registry"]["unexpected_field"] = []
    bad = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    assert (
        _reason(
            parse_registry_bytes,
            RegistryKind.EVIDENCE_REGISTRY,
            bad,
            file_name="synthetic-test.json",
        )
        == "registry_schema_invalid"
    )
    data = json.loads(raw)
    data["file_format"] = "evidence-registry-file-9"
    bad = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    assert (
        _reason(
            parse_registry_bytes,
            RegistryKind.EVIDENCE_REGISTRY,
            bad,
            file_name="synthetic-test.json",
        )
        == "registry_file_format_unknown"
    )


def test_declared_identity_and_filename_must_agree(tmp_path):
    raw = _evidence_file(tmp_path).read_bytes()
    data = json.loads(raw)
    data["registry_version_id"] = "evr1_" + "0" * 64
    bad = (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    assert (
        _reason(
            parse_registry_bytes,
            RegistryKind.EVIDENCE_REGISTRY,
            bad,
            file_name="synthetic-test.json",
        )
        == "registry_id_mismatch"
    )
    assert (
        _reason(parse_registry_bytes, RegistryKind.EVIDENCE_REGISTRY, raw, file_name="other.json")
        == "registry_label_filename_mismatch"
    )


def test_size_and_nesting_limits():
    assert _reason(strict_parse, b" " * (1024 * 1024 + 1)) == "registry_file_too_large"
    assert _reason(strict_parse, b"[" * 40 + b"]" * 40) == "registry_json_invalid"


def test_paths_are_contained_and_symlinks_refused(tmp_path):
    root = tmp_path / "registries"
    path = _evidence_file(tmp_path)
    outside = tmp_path / "synthetic-test.json"
    outside.write_bytes(path.read_bytes())
    assert _reason(
        load_registry_file, RegistryKind.EVIDENCE_REGISTRY, outside, registries_root=root
    ) == ("registry_path_outside_repository")
    traversal = root / "evidence" / ".." / ".." / "synthetic-test.json"
    assert _reason(
        load_registry_file, RegistryKind.EVIDENCE_REGISTRY, traversal, registries_root=root
    ) == ("registry_path_outside_repository")
    # Wrong kind folder.
    assert _reason(
        load_registry_file, RegistryKind.SETUP_DEFINITION_REGISTRY, path, registries_root=root
    ) == ("registry_path_outside_repository")
    link = root / "evidence" / "linked.json"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    assert _reason(
        load_registry_file, RegistryKind.EVIDENCE_REGISTRY, link, registries_root=root
    ) == ("registry_path_outside_repository")


def test_setup_definition_registry_is_structurally_empty(tmp_path):
    with pytest.raises(ValidationError):
        SetupDefinitionRegistry(
            registry_label="one-definition", definitions=[s.SYNTHETIC_DEFINITION]
        )
    path = fx.write_registry_file(
        tmp_path / "registries", RegistryKind.SETUP_DEFINITION_REGISTRY, fx.EMPTY_SETUP_REGISTRY
    )
    loaded = load_registry_file(
        RegistryKind.SETUP_DEFINITION_REGISTRY, path, registries_root=tmp_path / "registries"
    )
    assert loaded.registry_version_id == compute_setup_registry_version_id(fx.EMPTY_SETUP_REGISTRY)
    assert loaded.registry_version_id.startswith("sdr1_")
    # A hand-written file adding a definition is refused before registration.
    data = json.loads(path.read_bytes())
    data["registry"]["definitions"] = [s.SYNTHETIC_DEFINITION.model_dump(mode="json")]
    bad = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    assert (
        _reason(
            parse_registry_bytes, RegistryKind.SETUP_DEFINITION_REGISTRY, bad, file_name=path.name
        )
        == "registry_schema_invalid"
    )


def test_canonical_rendering_round_trips(tmp_path):
    path = _evidence_file(tmp_path)
    loaded = parse_registry_bytes(
        RegistryKind.EVIDENCE_REGISTRY, path.read_bytes(), file_name=path.name
    )
    from market_intelligence.evidence_store.registry_files import EvidenceRegistryFile

    wrapper = EvidenceRegistryFile(
        file_format="evidence-registry-file-1",
        registry=loaded.evidence_registry,
        registry_version_id=loaded.registry_version_id,
    )
    assert render_canonical_file(wrapper) == path.read_bytes()


def test_no_real_registry_file_is_committed():
    from market_intelligence.evidence_store.registry_files import DEFAULT_REGISTRIES_ROOT

    assert (
        not any(DEFAULT_REGISTRIES_ROOT.rglob("*.json"))
        if DEFAULT_REGISTRIES_ROOT.exists()
        else True
    )
