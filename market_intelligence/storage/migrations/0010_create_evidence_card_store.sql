-- Offline evidence, registry and setup-card store
-- (docs/EVIDENCE_CARD_STORAGE_DESIGN.md §5; docs/REGISTRY_LOADING_DESIGN.md §6).
--
-- Authoritative tables are insert-only: the repository layer
-- (market_intelligence/evidence_store/) exposes append and read operations
-- only, never UPDATE, DELETE, REPLACE or upsert. Every authoritative row
-- carries the commit that wrote it (commit_seq), that commit's observed UTC
-- time (recorded_at_utc) and a row hash over every other column; most also
-- carry the full canonical record (record_json) and its content hash.
-- store_commits chains every commit by digest.
--
-- Projection tables (evidence_item_*, evidence_envelope_items,
-- evidence_conflict_items, evidence_bundle_*) are derived lookup aids,
-- rebuildable from the authoritative rows, and never an authority.
--
-- Conventions follow the existing migrations: no DuckDB foreign keys,
-- CHECK-enumerated values, deterministic primary keys, UTC TIMESTAMP columns.
-- No table holds an order, quantity, position size, stop, target, entry or
-- exit price, brokerage or account field, display status, freshness column
-- on items, key material, or provider response body.
--
-- This migration was implemented offline and tested only on temporary
-- databases. Applying it to the real project database needs its own separate
-- authorization and a verified backup first.

CREATE TABLE store_instance (
    store_instance_id VARCHAR PRIMARY KEY
        CHECK (regexp_full_match(store_instance_id, '^[0-9a-f]{32}$')),
    initialized_at_utc TIMESTAMP NOT NULL,
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL CHECK (commit_seq = 1),
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64)
);

CREATE TABLE store_commits (
    commit_seq BIGINT PRIMARY KEY CHECK (commit_seq >= 1),
    committed_at_utc TIMESTAMP NOT NULL,
    writer_id VARCHAR NOT NULL CHECK (writer_id IN (
        'evidence_ingest', 'conflict_detector', 'manual_conflict_review',
        'bundle_builder', 'card_builder', 'registry_operator',
        'recovery_operator', 'checkpoint_operator', 'store_service')),
    operation VARCHAR NOT NULL CHECK (operation IN (
        'initialize_store', 'append_envelope', 'append_conflict', 'append_bundle',
        'append_card', 'register_registry_version', 'append_activation',
        'record_recovery', 'rotate_checkpoint_key', 'record_audit_events')),
    row_count INTEGER NOT NULL CHECK (row_count >= 1),
    rows_sha256 VARCHAR NOT NULL CHECK (length(rows_sha256) = 64),
    prev_commit_digest VARCHAR,
    commit_digest VARCHAR NOT NULL UNIQUE CHECK (length(commit_digest) = 64),
    CHECK ((commit_seq = 1) = (prev_commit_digest IS NULL))
);

CREATE TABLE registry_versions (
    registry_version_id VARCHAR PRIMARY KEY
        CHECK (regexp_full_match(registry_version_id, '^(evr1|sdr1)_[0-9a-f]{64}$')),
    registry_kind VARCHAR NOT NULL
        CHECK (registry_kind IN ('evidence_registry', 'setup_definition_registry')),
    registry_label VARCHAR NOT NULL,
    file_format VARCHAR NOT NULL
        CHECK (file_format IN ('evidence-registry-file-1', 'setup-definition-registry-file-1')),
    content_sha256 VARCHAR NOT NULL CHECK (length(content_sha256) = 64),
    source_path VARCHAR NOT NULL,
    source_file_sha256 VARCHAR NOT NULL CHECK (length(source_file_sha256) = 64),
    source_commit_sha VARCHAR NOT NULL
        CHECK (regexp_full_match(source_commit_sha, '^[0-9a-f]{40}$')),
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64),
    UNIQUE (registry_kind, registry_label)
);

CREATE TABLE registry_activations (
    activation_id VARCHAR PRIMARY KEY
        CHECK (regexp_full_match(activation_id, '^rga1_[0-9a-f]{64}$')),
    registry_kind VARCHAR NOT NULL
        CHECK (registry_kind IN ('evidence_registry', 'setup_definition_registry')),
    registry_version_id VARCHAR NOT NULL,
    effective_from_utc TIMESTAMP NOT NULL,
    activation_reason VARCHAR NOT NULL
        CHECK (activation_reason IN ('initial_activation', 'version_upgrade', 'rollback')),
    supersedes_activation_id VARCHAR UNIQUE,
    authorization_ref VARCHAR NOT NULL CHECK (
        regexp_full_match(authorization_ref, '^project-state:[1-9][0-9]{0,4}@[0-9a-f]{40}$')),
    activated_at_utc TIMESTAMP NOT NULL,
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64),
    UNIQUE (registry_kind, effective_from_utc),
    CHECK (effective_from_utc >= activated_at_utc),
    CHECK ((activation_reason = 'initial_activation') = (supersedes_activation_id IS NULL))
);

CREATE TABLE evidence_items (
    item_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(item_id, '^evi1_[0-9a-f]{64}$')),
    schema_version VARCHAR NOT NULL CHECK (schema_version = 'evidence-envelope-1'),
    evidence_kind VARCHAR NOT NULL CHECK (evidence_kind IN (
        'confirmed_fact', 'deterministic_calculation', 'historical_research_result',
        'current_inference', 'missing_evidence', 'stale_evidence')),
    producer_id VARCHAR NOT NULL,
    producer_version VARCHAR NOT NULL,
    payload_schema_id VARCHAR NOT NULL,
    primary_subject_id VARCHAR NOT NULL,
    configuration_identity VARCHAR NOT NULL,
    effective_at_utc TIMESTAMP NOT NULL,
    source_observed_at_utc TIMESTAMP,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    supersedes_item_id VARCHAR,
    machine_decision_eligible BOOLEAN NOT NULL,
    validated_registry_version_id VARCHAR NOT NULL,
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64),
    CHECK ((revision_number = 1) = (supersedes_item_id IS NULL))
);

CREATE TABLE evidence_envelopes (
    envelope_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(envelope_id, '^eve1_[0-9a-f]{64}$')),
    producer_id VARCHAR NOT NULL,
    producer_version VARCHAR NOT NULL,
    producer_run_key VARCHAR NOT NULL,
    run_as_of_utc TIMESTAMP NOT NULL,
    registry_version_id VARCHAR NOT NULL,
    availability_state VARCHAR NOT NULL CHECK (availability_state IN (
        'available', 'partial', 'unavailable', 'unsupported', 'refused', 'error')),
    item_count INTEGER NOT NULL CHECK (item_count BETWEEN 0 AND 512),
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64)
);

CREATE TABLE evidence_conflicts (
    conflict_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(conflict_id, '^evc1_[0-9a-f]{64}$')),
    conflict_chain_key VARCHAR NOT NULL CHECK (length(conflict_chain_key) = 64),
    conflict_type VARCHAR NOT NULL CHECK (conflict_type IN (
        'inference_vs_observation', 'cross_domain_inference_disagreement',
        'provider_disagreement', 'current_vs_research_context', 'freshness_mismatch',
        'revision_disagreement', 'scenario_relation_disagreement')),
    severity VARCHAR NOT NULL CHECK (severity IN ('informational', 'material', 'critical')),
    status VARCHAR NOT NULL
        CHECK (status IN ('unresolved', 'resolved', 'acknowledged_unresolvable')),
    detection_method VARCHAR NOT NULL
        CHECK (detection_method IN ('deterministic_rule', 'model_detected', 'manual_review')),
    detector_producer_id VARCHAR NOT NULL,
    detector_version VARCHAR NOT NULL,
    evaluated_as_of_utc TIMESTAMP NOT NULL,
    supersedes_conflict_id VARCHAR UNIQUE,
    severity_registry_version_id VARCHAR NOT NULL,
    matched_requirement_ids VARCHAR NOT NULL,
    involves_required_item BOOLEAN NOT NULL,
    severity_rule_version VARCHAR NOT NULL
        CHECK (severity_rule_version IN ('conflict-severity-rules-1')),
    validated_registry_version_id VARCHAR NOT NULL,
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64)
);

CREATE TABLE evidence_bundles (
    bundle_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(bundle_id, '^evb1_[0-9a-f]{64}$')),
    purpose VARCHAR NOT NULL CHECK (purpose IN (
        'premarket_briefing', 'live_market_state', 'setup_detail', 'contract_review',
        'assistant_question_context', 'notification_decision')),
    as_of_utc TIMESTAMP NOT NULL,
    registry_version_id VARCHAR NOT NULL,
    selection_rule_id VARCHAR NOT NULL,
    consumer_id VARCHAR NOT NULL,
    machine_decision_ready BOOLEAN NOT NULL,
    entry_count INTEGER NOT NULL CHECK (entry_count BETWEEN 0 AND 2000),
    builder_id VARCHAR NOT NULL CHECK (builder_id = 'evidence_bundle_builder'),
    builder_version VARCHAR NOT NULL,
    builder_code_commit_sha VARCHAR NOT NULL
        CHECK (regexp_full_match(builder_code_commit_sha, '^[0-9a-f]{40}$')),
    builder_configuration_identity VARCHAR NOT NULL,
    visible_through_commit_seq BIGINT NOT NULL CHECK (visible_through_commit_seq >= 0),
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64),
    CHECK (recorded_at_utc > as_of_utc)
);

CREATE TABLE setup_cards (
    card_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(card_id, '^scd1_[0-9a-f]{64}$')),
    schema_version VARCHAR NOT NULL CHECK (schema_version = 'setup-card-1'),
    card_kind VARCHAR NOT NULL CHECK (card_kind IN ('setup', 'no_qualified_setup')),
    lane VARCHAR NOT NULL CHECK (lane IN ('vwap_reversion', 'trend_continuation')),
    chain_key_sha256 VARCHAR NOT NULL CHECK (length(chain_key_sha256) = 64),
    setup_subject_id VARCHAR,
    session_date DATE NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    supersedes_card_id VARCHAR UNIQUE,
    revision_reason VARCHAR NOT NULL CHECK (revision_reason IN (
        'original', 'newer_bundle', 'evidence_correction', 'lifecycle_change', 'expiration')),
    decision_context_bundle_id VARCHAR NOT NULL,
    registry_version_id VARCHAR NOT NULL,
    setup_definition_registry_version_id VARCHAR NOT NULL
        CHECK (regexp_full_match(setup_definition_registry_version_id, '^sdr1_[0-9a-f]{64}$')),
    effective_at_utc TIMESTAMP NOT NULL,
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64),
    CHECK ((card_kind = 'setup') = (setup_subject_id IS NOT NULL)),
    CHECK ((revision_number = 1) = (supersedes_card_id IS NULL))
);

CREATE TABLE setup_card_chains (
    chain_key_sha256 VARCHAR PRIMARY KEY CHECK (length(chain_key_sha256) = 64),
    card_kind VARCHAR NOT NULL CHECK (card_kind IN ('setup', 'no_qualified_setup')),
    lane VARCHAR NOT NULL CHECK (lane IN ('vwap_reversion', 'trend_continuation')),
    setup_subject_id VARCHAR,
    session_date DATE,
    root_card_id VARCHAR NOT NULL UNIQUE,
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64)
);

CREATE TABLE store_recovery_events (
    recovery_seq BIGINT PRIMARY KEY CHECK (recovery_seq >= 1),
    recovery_kind VARCHAR NOT NULL CHECK (recovery_kind = 'backup_restoration'),
    restored_high_water_commit_seq BIGINT NOT NULL,
    restored_high_water_commit_digest VARCHAR NOT NULL,
    backup_file_sha256 VARCHAR NOT NULL CHECK (length(backup_file_sha256) = 64),
    superseded_checkpoint_commit_seq BIGINT NOT NULL,
    superseded_checkpoint_commit_digest VARCHAR NOT NULL,
    superseded_checkpoint_sha256 VARCHAR NOT NULL CHECK (length(superseded_checkpoint_sha256) = 64),
    recovery_authorization_ref VARCHAR NOT NULL CHECK (
        regexp_full_match(recovery_authorization_ref, '^project-state:[1-9][0-9]{0,4}@[0-9a-f]{40}$')),
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64)
);

CREATE TABLE store_checkpoint_key_rotations (
    rotation_seq BIGINT PRIMARY KEY CHECK (rotation_seq >= 1),
    previous_key_id VARCHAR NOT NULL CHECK (regexp_full_match(previous_key_id, '^[a-z][a-z0-9_]{0,63}$')),
    new_key_id VARCHAR NOT NULL CHECK (regexp_full_match(new_key_id, '^[a-z][a-z0-9_]{0,63}$')),
    algorithm VARCHAR NOT NULL CHECK (algorithm = 'hmac_sha256'),
    authorization_ref VARCHAR NOT NULL CHECK (
        regexp_full_match(authorization_ref, '^project-state:[1-9][0-9]{0,4}@[0-9a-f]{40}$')),
    record_json VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL CHECK (length(record_sha256) = 64),
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64),
    CHECK (new_key_id <> previous_key_id)
);

CREATE TABLE store_audit_events (
    event_seq BIGINT PRIMARY KEY CHECK (event_seq >= 1),
    occurred_at_utc TIMESTAMP NOT NULL,
    event_type VARCHAR NOT NULL CHECK (event_type IN (
        'envelope_accepted', 'envelope_refused', 'append_duplicate_ignored', 'bundle_built',
        'bundle_refused', 'conflict_recorded', 'consumer_access', 'consumer_emit',
        'store_initialized', 'conflict_refused', 'card_recorded', 'card_refused',
        'registry_version_registered', 'registry_activated', 'registry_activation_refused',
        'holdout_write_refused', 'holdout_read_refused', 'integrity_stop', 'store_restored',
        'store_open_refused', 'checkpoint_reissue_required',
        'checkpoint_reconciliation_entered', 'checkpoint_reconciled',
        'checkpoint_key_rotated')),
    actor VARCHAR NOT NULL CHECK (regexp_full_match(actor, '^[a-z][a-z0-9_]{0,63}$')),
    subject_record_id VARCHAR,
    reason_code VARCHAR NOT NULL CHECK (regexp_full_match(reason_code, '^[a-z][a-z0-9_]{0,63}$')),
    reference_commit_seq BIGINT,
    commit_seq BIGINT NOT NULL,
    recorded_at_utc TIMESTAMP NOT NULL,
    row_sha256 VARCHAR NOT NULL CHECK (length(row_sha256) = 64),
    CHECK (event_type NOT IN ('holdout_write_refused', 'holdout_read_refused')
           OR subject_record_id IS NULL)
);

-- Derived projections (rebuildable; never an authority).

CREATE TABLE evidence_item_subjects (
    item_id VARCHAR NOT NULL,
    position INTEGER NOT NULL,
    subject_type VARCHAR NOT NULL,
    subject_id VARCHAR NOT NULL,
    PRIMARY KEY (item_id, position)
);

CREATE TABLE evidence_item_parents (
    item_id VARCHAR NOT NULL,
    parent_item_id VARCHAR NOT NULL,
    PRIMARY KEY (item_id, parent_item_id)
);

CREATE TABLE evidence_item_revisions (
    predecessor_item_id VARCHAR NOT NULL,
    successor_item_id VARCHAR NOT NULL,
    successor_commit_seq BIGINT NOT NULL,
    PRIMARY KEY (predecessor_item_id, successor_item_id)
);

CREATE TABLE evidence_envelope_items (
    envelope_id VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    PRIMARY KEY (envelope_id, item_id)
);

CREATE TABLE evidence_conflict_items (
    conflict_id VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    PRIMARY KEY (conflict_id, item_id)
);

CREATE TABLE evidence_bundle_entries (
    bundle_id VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    evidence_kind VARCHAR NOT NULL,
    producer_id VARCHAR NOT NULL,
    required BOOLEAN NOT NULL,
    freshness_state VARCHAR NOT NULL,
    freshness_reason VARCHAR NOT NULL,
    age_seconds BIGINT,
    clock_health_reason VARCHAR NOT NULL,
    availability_state VARCHAR NOT NULL,
    machine_decision_eligible BOOLEAN NOT NULL,
    superseded_as_of BOOLEAN NOT NULL,
    latest_revision_item_id VARCHAR,
    PRIMARY KEY (bundle_id, item_id)
);

CREATE TABLE evidence_bundle_requirement_outcomes (
    bundle_id VARCHAR NOT NULL,
    requirement_id VARCHAR NOT NULL,
    outcome VARCHAR NOT NULL CHECK (outcome IN ('satisfied', 'missing', 'ambiguous')),
    missing_reason VARCHAR,
    ambiguity_reason VARCHAR,
    satisfying_item_id VARCHAR,
    competing_item_count INTEGER NOT NULL,
    PRIMARY KEY (bundle_id, requirement_id)
);

CREATE INDEX idx_store_commits_time ON store_commits (committed_at_utc, commit_seq);
CREATE INDEX idx_evidence_items_requirement
    ON evidence_items (producer_id, payload_schema_id, primary_subject_id, effective_at_utc);
CREATE INDEX idx_evidence_items_commit ON evidence_items (commit_seq);
CREATE INDEX idx_evidence_items_supersedes ON evidence_items (supersedes_item_id);
CREATE INDEX idx_evidence_conflicts_chain ON evidence_conflicts (conflict_chain_key, commit_seq);
CREATE INDEX idx_setup_cards_chain ON setup_cards (chain_key_sha256, revision_number);
CREATE INDEX idx_registry_activations_time
    ON registry_activations (registry_kind, effective_from_utc);
