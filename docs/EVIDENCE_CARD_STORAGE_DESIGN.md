# Evidence and Setup-Card Storage — Design

**Status: REVIEWED DESIGN — accepted by the merge that introduces this
document. DESIGN ONLY. Not implemented. Not authorized.** This document
specifies how the reviewed Evidence Envelope records and `setup-card-1`
cards would be persisted. Its companion,
[REGISTRY_LOADING_DESIGN.md](REGISTRY_LOADING_DESIGN.md), proposes how
registry versions are loaded and activated.

- **The merge accepts the design only.** Nothing it describes has been
  implemented. The merge does **not** authorize migrations, database
  changes, registry files or activation, storage writers or readers,
  checkpoint creation, credentials or HMAC keys, producer adapters, setup
  definitions, evidence or card collection, dashboard or assistant access,
  machine-decision mode, notifications, or trading or execution. Any
  implementation needs a new, separate authorization (§16.4).
- **Nothing exists.** No migration, table, repository, store, loader,
  checkpoint, registry file or consumer has been created. The real
  database, its migrations, providers, models and holdout data were not
  touched by this design.
- **Open items stay open.** The open questions and blockers in §16.2 and
  §16.3 are unresolved. The **[STRICTER]** invariants remain proposed
  implementation requirements that need explicit acceptance when an
  implementation is reviewed.
- **The production setup-definition registry stays empty.** Storage never
  makes a card possible. With no registered setup definition, the store
  refuses every card of either kind (§6.6).
- **Governing documents win.** [DECISION_RULES.md](../DECISION_RULES.md),
  [SOURCE_POLICY.md](../SOURCE_POLICY.md), the reviewed
  [Evidence Envelope design](EVIDENCE_ENVELOPE_DESIGN.md),
  [registry](EVIDENCE_REGISTRY.md) and
  [consumer rules](EVIDENCE_CONSUMER_RULES.md), the
  [setup-card contract](SETUP_CARD_CONTRACT.md), frozen research protocols
  and [PROJECT_STATE.md](../PROJECT_STATE.md) always govern. Where this
  design seems to conflict with them, they win.
- **It changes no contract.** Every stored record is exactly a record the
  merged offline code already defines. `evidence-envelope-1` and
  `setup-card-1` are unchanged, including every identity rule. Storage adds
  only wrapper fields (such as the commit sequence and recorded time)
  outside every identity.
- **Invariants stricter than the merged code** are marked
  **[STRICTER]** where they appear and collected in §16.1. Each needs
  explicit acceptance during implementation review.

---

## 1. Summary

| Question | Answer |
|---|---|
| Where is the authoritative store? | **New insert-only tables in the existing project DuckDB database**, under one future migration (§14). Not append-only files, and not a second database |
| What about files? | Reviewed registry *content* is authored as version-controlled files (companion design). Runtime records and activations live only in DuckDB. Backups and optional exports are verification artifacts, never a second authority |
| What is authoritative? | Each sealed item, envelope, conflict, bundle and card with its storage wrapper; registry versions and activations; the commit log; recovery events; audit events (§3.2) |
| What is derived? | Lookup projections (subjects, parents, revision links, envelope membership, conflict involvement, bundle entries, requirement outcomes), rebuildable from authoritative rows (§3.2) |
| What orders history? | The integer **commit sequence** is the authoritative total order. The observed UTC commit time is kept separately; it never regresses but may repeat (§6.1, §8.1) |
| What defines "known at time T"? | Every row of every commit whose recorded UTC commit time is at or before T. Because commit times never regress, this is always a prefix of the commit sequence (§8.1) |
| How are duplicates treated? | Same ID with identical identity bytes: an idempotent no-op that keeps the first row. Same ID with different identity bytes: an integrity stop (§7) |
| How is a bundle reproduced? | From the records visible at its `as_of_utc`, the registry version it names, its stored query and consumer context, and the builder identity stored in its wrapper; the recomputed `evb1_` must match (§8.5) |
| How is recovery done? | By restoring a verified backup **exactly**, then appending a separate recovery event. Nothing is re-appended or re-timed; commits lost after the backup are never recreated as if they existed (§10.5) |
| Can an older, internally valid database be swapped in? | Not undetected. The hash chain alone cannot catch it, so a minimal **external anti-rollback checkpoint** witnesses the highest durable commit already seen. It holds no evidence and is not an authority (§10.6) |
| When does the store stop reads? | Only on an integrity stop (§10.3). An ordinary refusal, including a holdout refusal, never disables unrelated reads |

---

## 2. What the design must match (code facts)

| Fact | Where | Consequence for storage |
|---|---|---|
| IDs are `prefix + SHA-256(canonical JSON)` with compact, sorted, NaN-free JSON | `evidence/canonical.py` | Stored IDs are recomputed on every verification; the store never assigns an ID |
| Item identity excludes only `provenance.generated_at_utc` | `evidence/contracts.py` `ITEM_EXCLUDED_PATHS` | The full record is stored; two appends of one ID may differ only there |
| Envelope identity is over its item IDs; `emitted_at_utc` is excluded | `envelope_identity_payload` | Envelopes are stored as a header plus item IDs |
| Conflict identity excludes `detected_at_utc`; a resolution names `supersedes_conflict_id` and may not change the type or involved items | `conflict_identity_payload`, `validate_conflict` | Conflict status forms chains keyed by type and involved items |
| Conflict severity is checked with `expected_severity(conflict_type, involves_required_item)`, where the caller supplies the required set | `validation.expected_severity`, `validate_conflict` | Storage must fix that input independently of any bundle (§6.3) |
| Bundle identity excludes only `built_at_utc`; freshness is inside each entry | `bundle_identity_payload`, `EvidenceBundleEntry` | Freshness is stored only inside the bundle record |
| `build_bundle` takes records with `recorded_at_utc`, ignores those after `as_of`, keeps the earliest duplicate | `evidence/bundle.py` | The store supplies exactly those records and never pre-filters by freshness, kind or revision |
| Requirement winners use substantive ordering only; ties become `AmbiguousRequirement` | `requirement_rank_key`, `_select_for_requirement` | Storage order never selects anything |
| `evr1_` covers the **whole** `EvidenceRegistry` dump, including `registry_label` | `compute_registry_version_id` | Registry identity is computed from content |
| Unknown payload schemas are refused before holdout evaluation | `holdout.carries_spy_price_evidence`, `validation._validate_payload` | Unknown schemas fail closed |
| The holdout guard has no switch | `evidence/holdout.py` | The store calls it; nothing disables it |
| Card identity excludes only `provenance.generated_at_utc`; `validate_setup_card` re-derives every bundle-dependent value from a `CardContext` that includes `setup_definitions` | `setup_cards/contracts.py`, `setup_cards/validation.py` | Card verification needs the exact setup definitions used (§5.7) |
| The in-memory history validator refuses duplicates, missing predecessors and branches; its chain key is (setup, lane, subject) or (no-setup, lane, session date) | `supersession.card_identity_key`, `validate_card_history` | Storage adds one root per chain and cycle refusal (§7.4) |
| Display status is computed at render time | `supersession.display_status` | No display-status column exists |
| `REGISTERED_SETUP_DEFINITIONS = ()` | `setup_cards/definitions.py` | Every card write fails in production |
| DuckDB 1.5.4, one read-write process, no triggers | `pyproject.toml` | Enforcement is by the repository layer, constraints and verification |
| The migration runner requires unique, ascending file versions and an applied history that is an exact prefix of the files; it does **not** require numeric contiguity | `storage/database.py` `_load_migrations`, `_diff_migration_history` | See §14 for the numbering consequence |

---

## 3. Architecture and authority

### 3.1 Why DuckDB tables

| Option | Strengths | Weaknesses | Verdict |
|---|---|---|---|
| **New DuckDB tables** in the existing database | Atomic multi-record transactions; the existing migration runner, health check, path-safety boundary and backup routine; constraints; indexed point-in-time queries | No triggers, so append-only is enforced by code and verification; one read-write process at a time | **Recommended** |
| Append-only files (JSONL segments) | Naturally append-only; easy to hash-chain | No atomic multi-file transaction; crash recovery to design from scratch; full scans for point-in-time queries; a second technology to secure | Rejected as the authority |
| Hybrid (files authoritative, DuckDB as an index) | Human-readable history | Two sources that can disagree | Rejected |
| Separate new database file | Isolation | A second migration history and backup unit, for no benefit | Rejected |

**Decision (proposed).** One authority: insert-only tables in
`data/market_intelligence.duckdb`. Registry files are reviewed source
content that is *registered* into DuckDB (companion design). Backups and
exports verify; they never outrank DuckDB, and a disagreement is an
integrity finding resolved by a person. The external anti-rollback
checkpoint (§10.6) is a witness to a high-water mark only, never a source of
domain records.

### 3.2 Authoritative records versus derived projections

| Authoritative (insert-only) | Holds |
|---|---|
| `store_commits` | one row per committed write transaction: the total order and the hash chain |
| `registry_versions` | the registered content of each registry version |
| `registry_activations` | the append-only activation log |
| `evidence_items` | each sealed `EvidenceItem` |
| `evidence_envelopes` | each envelope header with its item IDs |
| `evidence_conflicts` | each conflict or conflict-status record |
| `evidence_bundles` | each sealed `EvidenceBundleManifest` with its builder wrapper |
| `setup_cards` | each sealed `SetupCard` with its registry wrapper |
| `setup_card_chains` | one row per card chain, naming its root |
| `store_instance` | one row: the database identity (§5.11) |
| `store_recovery_events` | one row per restoration from backup |
| `store_checkpoint_key_rotations` | one row per checkpoint HMAC key rotation (§10.6); key IDs only, never keys |
| `store_audit_events` | bounded audit events |

| Derived (rebuildable) | Built from |
|---|---|
| `evidence_item_subjects`, `evidence_item_parents`, `evidence_item_revisions` | item records |
| `evidence_envelope_items` | envelope headers |
| `evidence_conflict_items` | conflict records |
| `evidence_bundle_entries`, `evidence_bundle_requirement_outcomes` | bundle records and the registry versions they name |

Rules:
- **Copied key columns never decide.** They exist only to index;
  verification refuses a row whose key columns differ from its record.
- **`superseded_by` is never stored**; it stays a query over successors
  (design §M).
- **Derived tables have no authority.** A mismatch is fixed by rebuilding
  the projection, never by changing an authoritative row.

### 3.3 Trust boundaries and writers

| Writer | May append | Never |
|---|---|---|
| `evidence_ingest` | envelopes and their items, from a registered producer adapter | conflicts, bundles, cards, registry records |
| `conflict_detector` | conflict and conflict-status records from a registered `conflict_detector` producer | items, bundles, cards |
| `manual_conflict_review` | `acknowledged_unresolvable` status records, once separately authorized | anything else |
| `bundle_builder` | bundles built by `build_bundle` | items, conflicts, cards |
| `card_builder` | cards built by the setup-card builder | anything else |
| `registry_operator` | registry versions and activations, through the dry-run-first operator command | evidence, conflicts, bundles, cards |
| `recovery_operator` | the recovery event after a restoration (§10.5) | anything else |
| `checkpoint_operator` | the key-rotation record (§10.6), through a dry-run-first operator command | anything else |
| `store_service` | audit events only (refusals, consumer access, reconciliation), each in its own commit | domain records of any kind |

- **Consumers are not writers.** They receive no writer identity and no
  connection (§12).
- **No path edits a row.** The repository layer exposes append and read
  methods only: no update, delete, upsert or truncate.

---

## 4. Proposed storage contracts and enums

Storage-layer contracts, outside the `evidence-envelope-1` and
`setup-card-1` families. All `STRICT_FROZEN`.

### 4.1 Contracts

| Contract | Fields | Notes |
|---|---|---|
| `RecordedItem`, `RecordedConflict` | the record and `recorded_at_utc` | **exist** in `evidence/bundle.py`; reused unchanged as `build_bundle` inputs |
| `StoreRowMeta` | `commit_seq`, `recorded_at_utc`, `record_sha256`, `row_sha256` | carried by every authoritative row (§5) |
| `RecordedEnvelope` | header fields, `item_ids`, `StoreRowMeta` | items fetched by ID |
| `RecordedBundle` | `bundle`, `builder_id`, `builder_version`, `builder_code_commit_sha`, `builder_configuration_identity`, `visible_through_commit_seq`, `StoreRowMeta` | builder fields support reproduction but are **outside** `evb1_` (§8.5) |
| `RecordedCard` | `card`, `setup_definition_registry_version_id`, `StoreRowMeta` | the exact `sdr1_` version used to validate the card (§5.7) |
| `StoreCommit` | `commit_seq`, `committed_at_utc`, `writer_id`, `operation`, `row_count`, `rows_sha256`, `prev_commit_digest`, `commit_digest` | §5.1 |
| `RecoveryEvent` | §5.10 columns | |
| `StoreAuditEvent` | `event_seq`, `occurred_at_utc`, `event_type`, `actor`, `subject_record_id` (nullable), `reason_code`, `commit_seq` | no free text; always written in a commit |
| `StoreCheckpoint` | `checkpoint_schema_version`, `store_instance_id`, `commit_seq`, `commit_digest`, `created_at_utc`, `authentication` (`{algorithm, key_id, mac}`, or null only in explicitly selected unauthenticated development or test mode) | the external anti-rollback witness (§10.6); **not** stored in DuckDB and not an authority |
| `CheckpointKeyRotation` | §5.12 columns | key IDs and algorithm only |
| `IntegrityReport` | `checked_at_utc`, `high_water_commit_seq`, `findings` (`{finding, record_kind, record_id?}`) | IDs and tokens only |

### 4.2 Bounded enums

| Enum | Values |
|---|---|
| `StoreRecordKind` | `evidence_item`, `evidence_envelope`, `evidence_conflict`, `evidence_bundle`, `setup_card`, `registry_version`, `registry_activation`, `recovery_event` |
| `StoreWriterId` | `evidence_ingest`, `conflict_detector`, `manual_conflict_review`, `bundle_builder`, `card_builder`, `registry_operator`, `recovery_operator`, `checkpoint_operator`, `store_service` |
| `StoreOperation` | `append_envelope`, `append_conflict`, `append_bundle`, `append_card`, `register_registry_version`, `append_activation`, `record_recovery`, `rotate_checkpoint_key`, `record_audit_events` |
| `StoreState` | `serving`, `checkpoint_reconciliation_required`, `checkpoint_key_rotation_in_progress`, `recovery_in_progress`, `not_serviceable`, `read_refused` |
| `CheckpointMode` | `production_authenticated`, `offline_development_unauthenticated` |
| `CheckpointAuthAlgorithm` | `hmac_sha256` |
| `SeverityRuleVersion` | `conflict-severity-rules-1` (the fixed table of design §I.2, implemented today by `expected_severity`) |
| `RecoveryKind` | `backup_restoration` |
| `StoreAuditEventType` | the design §O events plus `conflict_refused`, `card_recorded`, `card_refused`, `registry_version_registered`, `registry_activated`, `registry_activation_refused`, `holdout_write_refused`, `holdout_read_refused`, `integrity_stop`, `store_restored`, `store_open_refused`, `checkpoint_reissue_required`, `checkpoint_reconciliation_entered`, `checkpoint_reconciled`, `checkpoint_key_rotated`. **`checkpoint_reissue_required`** is an authoritative recovery event stating that an external checkpoint must be issued for the named recovery commit; it never claims the checkpoint exists. Reissuance is proven complete only by an authenticated checkpoint equal to the database tip; there is no `checkpoint_reissued` event |
| `IntegrityFinding` (**stop** unless marked) | `identity_mismatch`, `record_hash_mismatch`, `row_hash_mismatch`, `commit_chain_mismatch`, `commit_sequence_gap`, `commit_time_regression`, `impossible_duplicate_identity`, `persisted_holdout_violation`, `invalid_registry_activation`, `unknown_record_schema`, `authoritative_chain_ambiguity` (a branch, second root, cycle or missing predecessor in a conflict, card or activation chain), `missing_parent`, `card_bundle_missing`, `card_registry_version_missing`, `checkpoint_missing`, `checkpoint_invalid`, `checkpoint_authentication_failed`, `checkpoint_algorithm_unsupported`, `checkpoint_store_mismatch`, `database_behind_checkpoint`, `checkpoint_commit_mismatch`, `checkpoint_recovery_mismatch`, `conflict_severity_irreproducible`; **not a stop:** `key_column_mismatch`, `projection_mismatch`, `bundle_reproduction_mismatch` |
| `StoreRefusalReason` | the existing validator tokens (for example `unknown_producer`, `unknown_payload_schema`, `holdout_restricted`, `missing_parent`, `card_bundle_mismatch`, `setup_definition_not_registered`) plus `writer_not_permitted`, `store_clock_regressed`, `bundle_as_of_not_past`, `bundle_not_reproducible`, `bundle_registry_not_in_force`, `conflict_chain_already_started`, `conflict_status_not_tip`, `card_chain_already_started`, `card_predecessor_not_tip`, `card_bundle_not_stored`, `record_too_large`, `store_read_refused`, `active_registry_missing`, `active_registry_ambiguous`, `active_registry_invalid`, `conflict_as_of_not_past`, `conflict_registry_version_missing`, `conflict_registry_activation_ambiguous`, `conflict_requirements_not_reproducible`, `conflict_severity_mismatch`, `severity_rule_version_unknown`, `checkpoint_reconciliation_required`, `checkpoint_key_rotation_in_progress`, `checkpoint_key_missing`, `checkpoint_key_id_unknown`, `conflict_evaluation_stale`, `workflow_incomplete` |

---

## 5. Proposed tables

Conventions follow the existing migrations and the shadow-recorder
proposal: no DuckDB foreign keys, `CHECK`-enumerated values, deterministic
primary keys, UTC `TIMESTAMP` columns (microseconds, normalized to UTC),
and no raw payload outside the canonical record column.

**Common columns on every authoritative table except `store_commits`:**

| Column | Type | Rule |
|---|---|---|
| `record_json` | `VARCHAR NOT NULL` | the full sealed record (or header) as canonical compact JSON, operational fields kept |
| `record_sha256` | `VARCHAR NOT NULL` | SHA-256 of `record_json` bytes (the **content hash**) |
| `commit_seq` | `BIGINT NOT NULL` | the commit that wrote the row (the authoritative order) |
| `recorded_at_utc` | `TIMESTAMP NOT NULL` | equals that commit's `committed_at_utc` |
| `row_sha256` | `VARCHAR NOT NULL` | SHA-256 of the canonical JSON of **every other column of the row**, wrapper fields included (the **row hash**) |

### 5.1 `store_commits`

| Column | Type | Rule |
|---|---|---|
| `commit_seq` | `BIGINT` | primary key; contiguous from 1; the **authoritative total order** |
| `committed_at_utc` | `TIMESTAMP NOT NULL` | observed UTC wall-clock time; **not less than** the previous commit's; may equal it |
| `writer_id` | `VARCHAR NOT NULL` | `CHECK` in `StoreWriterId` |
| `operation` | `VARCHAR NOT NULL` | `CHECK` in `StoreOperation` |
| `row_count` | `INTEGER NOT NULL` | `CHECK (row_count >= 1)`; authoritative rows written, duplicates excluded |
| `rows_sha256` | `VARCHAR NOT NULL` | SHA-256 of the sorted, newline-joined `row_sha256` values written |
| `prev_commit_digest` | `VARCHAR` | null only for `commit_seq = 1` |
| `commit_digest` | `VARCHAR NOT NULL UNIQUE` | SHA-256 of canonical `{commit_seq, committed_at_utc, writer_id, operation, row_count, rows_sha256, prev_commit_digest}` |

A commit is referenced from outside the store only as the pair
(`commit_seq`, `commit_digest`) (§10.5).

### 5.2 `registry_versions` and `registry_activations`

Specified in the companion design §6. Summary: `registry_versions` keys on
`registry_version_id` (`evr1_…` or `sdr1_…`) with unique
`(registry_kind, registry_label)`; `registry_activations` keys on
`activation_id` with unique `(registry_kind, effective_from_utc)` and
unique `supersedes_activation_id`.

### 5.3 `evidence_items`

| Column | Type | Rule |
|---|---|---|
| `item_id` | `VARCHAR` | primary key; `CHECK (regexp_full_match(item_id, '^evi1_[0-9a-f]{64}$'))` |
| `schema_version` | `VARCHAR NOT NULL` | `CHECK (schema_version = 'evidence-envelope-1')` |
| `evidence_kind` | `VARCHAR NOT NULL` | `CHECK` in the six `EvidenceKind` values |
| `producer_id`, `producer_version`, `payload_schema_id`, `primary_subject_id`, `configuration_identity` | `VARCHAR NOT NULL` | copied |
| `effective_at_utc` | `TIMESTAMP NOT NULL` | copied |
| `source_observed_at_utc` | `TIMESTAMP` | copied |
| `revision_number` | `INTEGER NOT NULL` | `CHECK (revision_number >= 1)` |
| `supersedes_item_id` | `VARCHAR` | `CHECK ((revision_number = 1) = (supersedes_item_id IS NULL))`; **not unique**: branched revisions are legal evidence and surface as ambiguity (design §M) |
| `machine_decision_eligible` | `BOOLEAN NOT NULL` | copied (derived at emission) |
| `validated_registry_version_id` | `VARCHAR NOT NULL` | wrapper: the evidence registry in force when the item was accepted |
| common columns | | |

No freshness column, no `superseded_by` column.

### 5.4 `evidence_envelopes`

| Column | Type | Rule |
|---|---|---|
| `envelope_id` | `VARCHAR` | primary key; `eve1_` pattern |
| `producer_id`, `producer_version` | `VARCHAR NOT NULL` | copied |
| `producer_run_key` | `VARCHAR NOT NULL` | copied; **not unique** (design §L.4) |
| `run_as_of_utc` | `TIMESTAMP NOT NULL` | copied |
| `registry_version_id` | `VARCHAR NOT NULL` | copied |
| `availability_state` | `VARCHAR NOT NULL` | `CHECK` in `AvailabilityState` |
| `item_count` | `INTEGER NOT NULL` | `CHECK (item_count BETWEEN 0 AND 512)` |
| common columns | | `record_json` is the envelope identity payload plus `emitted_at_utc` |

### 5.5 `evidence_conflicts`

| Column | Type | Rule |
|---|---|---|
| `conflict_id` | `VARCHAR` | primary key; `evc1_` pattern |
| `conflict_chain_key` | `VARCHAR NOT NULL` | SHA-256 of canonical `{conflict_type, involved_item_ids}` (a resolution may not change either) |
| `conflict_type`, `severity`, `status`, `detection_method` | `VARCHAR NOT NULL` | `CHECK` in their enums |
| `detector_producer_id`, `detector_version` | `VARCHAR NOT NULL` | copied |
| `evaluated_as_of_utc` | `TIMESTAMP NOT NULL` | copied; also the time at which severity is derived |
| `supersedes_conflict_id` | `VARCHAR UNIQUE` | null only for a chain root; **unique**, so at most one direct successor |
| `severity_registry_version_id` | `VARCHAR NOT NULL` | wrapper: the exact Evidence Registry version in force at `evaluated_as_of_utc`, used for the severity derivation (§6.3) |
| `matched_requirement_ids` | `VARCHAR NOT NULL` | wrapper: canonical JSON list of `"<purpose>:<requirement_id>"` strings, sorted and unique (possibly empty) |
| `involves_required_item` | `BOOLEAN NOT NULL` | wrapper: `matched_requirement_ids` is non-empty |
| `severity_rule_version` | `VARCHAR NOT NULL` | wrapper; `CHECK` in `SeverityRuleVersion` |
| `validated_registry_version_id` | `VARCHAR NOT NULL` | wrapper: the registry in force when the record was accepted |
| common columns | | every wrapper field above, and the copied `severity`, is covered by `row_sha256` |

There is no separate conflict-chains table. A root is a row with a null
`supersedes_conflict_id`. "One root per `conflict_chain_key`" is enforced by
the writer inside the write transaction and re-checked by verification,
because DuckDB has no partial unique index (§7.3).

### 5.6 `evidence_bundles`

| Column | Type | Rule |
|---|---|---|
| `bundle_id` | `VARCHAR` | primary key; `evb1_` pattern |
| `purpose` | `VARCHAR NOT NULL` | `CHECK` in `BundlePurpose` |
| `as_of_utc` | `TIMESTAMP NOT NULL` | copied |
| `registry_version_id` | `VARCHAR NOT NULL` | copied |
| `selection_rule_id`, `consumer_id` | `VARCHAR NOT NULL` | copied |
| `machine_decision_ready` | `BOOLEAN NOT NULL` | copied |
| `entry_count` | `INTEGER NOT NULL` | `CHECK (entry_count BETWEEN 0 AND 2000)` |
| `builder_id` | `VARCHAR NOT NULL` | wrapper; the registered builder (`evidence_bundle_builder`) |
| `builder_version` | `VARCHAR NOT NULL` | wrapper |
| `builder_code_commit_sha` | `VARCHAR NOT NULL` | wrapper; full 40-hex commit |
| `builder_configuration_identity` | `VARCHAR NOT NULL` | wrapper; `cfg1_…` or `cfg_none` |
| `visible_through_commit_seq` | `BIGINT NOT NULL` | wrapper; the last commit whose `committed_at_utc ≤ as_of_utc` when the bundle was built |
| common columns | | plus `CHECK (recorded_at_utc > as_of_utc)` (§8.4) |

### 5.7 `setup_cards` and `setup_card_chains`

`setup_cards`:

| Column | Type | Rule |
|---|---|---|
| `card_id` | `VARCHAR` | primary key; `scd1_` pattern |
| `schema_version` | `VARCHAR NOT NULL` | `CHECK (schema_version = 'setup-card-1')` |
| `card_kind` | `VARCHAR NOT NULL` | `CHECK` in `setup`, `no_qualified_setup` |
| `lane` | `VARCHAR NOT NULL` | `CHECK` in `vwap_reversion`, `trend_continuation` |
| `chain_key_sha256` | `VARCHAR NOT NULL` | SHA-256 of the canonical `card_identity_key(card)` from the accepted card rules |
| `setup_subject_id` | `VARCHAR` | `CHECK ((card_kind = 'setup') = (setup_subject_id IS NOT NULL))` |
| `session_date` | `DATE NOT NULL` | copied |
| `revision_number` | `INTEGER NOT NULL` | `CHECK (revision_number >= 1)` |
| `supersedes_card_id` | `VARCHAR UNIQUE` | `CHECK ((revision_number = 1) = (supersedes_card_id IS NULL))` |
| `revision_reason` | `VARCHAR NOT NULL` | `CHECK` in `CardRevisionReason` |
| `decision_context_bundle_id` | `VARCHAR NOT NULL` | must name a stored bundle |
| `registry_version_id` | `VARCHAR NOT NULL` | copied; the **Evidence Registry** identity, bound through the bundle (must equal the bundle's) |
| `setup_definition_registry_version_id` | `VARCHAR NOT NULL` | wrapper, **authoritative**: the exact `sdr1_` version used to validate the card; `CHECK (regexp_full_match(…, '^sdr1_[0-9a-f]{64}$'))`; included in `row_sha256` |
| `effective_at_utc` | `TIMESTAMP NOT NULL` | copied; equals the bundle `as_of_utc` |
| common columns | | |

- **Binding.** At write, `setup_definition_registry_version_id` must equal
  the setup-definition registry in force at the bundle's `as_of_utc`.
  After that, it is a fixed fact of the card row.
- **Reconstruction and verification load that exact version** from
  `registry_versions`, never whichever version is active now. A missing
  version is `card_registry_version_missing` (an integrity stop).
- **The current format stays structurally empty** (companion design §10),
  so the only version that can exist has no definitions, and production
  storage refuses every card of either kind.

No display-status column, and no column for orders, quantity, position
size, stops, targets, entry or exit prices, brokerage or accounts (§15.2).

`setup_card_chains`:

| Column | Type | Rule |
|---|---|---|
| `chain_key_sha256` | `VARCHAR` | primary key: **exactly one root per chain** |
| `card_kind`, `lane` | `VARCHAR NOT NULL` | as above |
| `setup_subject_id` | `VARCHAR` | for setup chains |
| `session_date` | `DATE` | for `no_qualified_setup` chains |
| `root_card_id` | `VARCHAR NOT NULL UNIQUE` | the revision-1 card |
| common columns | | |

### 5.8 `store_audit_events`

| Column | Type | Rule |
|---|---|---|
| `event_seq` | `BIGINT` | primary key |
| `occurred_at_utc` | `TIMESTAMP NOT NULL` | |
| `event_type` | `VARCHAR NOT NULL` | `CHECK` in `StoreAuditEventType` |
| `actor` | `VARCHAR NOT NULL` | a `StoreWriterId` or a registered `ConsumerId` |
| `subject_record_id` | `VARCHAR` | a record ID or null; **always null** for holdout events (§9) |
| `reason_code` | `VARCHAR NOT NULL` | `CHECK (regexp_full_match(reason_code, '^[a-z][a-z0-9_]{0,63}$'))` |
| `commit_seq` | `BIGINT NOT NULL` | the commit that wrote it (its own `store_service` commit, or the commit it accompanies) |
| `reference_commit_seq` | `BIGINT` | for workflow events only: the commit the event refers to (for `checkpoint_reconciled`, the verified tip it reconciled through); null otherwise |
| `row_sha256` | `VARCHAR NOT NULL` | row hash |

No free text, payload values, SQL, paths or exception text.

### 5.9 Derived projections

| Table | Key | Columns |
|---|---|---|
| `evidence_item_subjects` | (`item_id`, `position`) | `subject_type`, `subject_id` |
| `evidence_item_parents` | (`item_id`, `parent_item_id`) | — |
| `evidence_item_revisions` | (`predecessor_item_id`, `successor_item_id`) | `successor_commit_seq` |
| `evidence_envelope_items` | (`envelope_id`, `item_id`) | — |
| `evidence_conflict_items` | (`conflict_id`, `item_id`) | — |
| `evidence_bundle_entries` | (`bundle_id`, `item_id`) | `evidence_kind`, `producer_id`, `required`, `freshness_state`, `freshness_reason`, `age_seconds`, `clock_health_reason`, `availability_state`, `machine_decision_eligible`, `superseded_as_of`, `latest_revision_item_id` |
| `evidence_bundle_requirement_outcomes` | (`bundle_id`, `requirement_id`) | `outcome` (`satisfied`, `missing`, `ambiguous`), `missing_reason`, `ambiguity_reason`, `satisfying_item_id`, `competing_item_count` |

There is deliberately **no per-contract projection**. Contract sets exist
only inside the sealed selector item and card records, exactly as the
selector returned them; rejected contracts exist only as aggregate counts
(design §H.3).

### 5.10 `store_recovery_events`

| Column | Type | Rule |
|---|---|---|
| `recovery_seq` | `BIGINT` | primary key |
| `recovery_kind` | `VARCHAR NOT NULL` | `CHECK (recovery_kind = 'backup_restoration')` |
| `restored_high_water_commit_seq` | `BIGINT NOT NULL` | the last commit present in the restored backup |
| `restored_high_water_commit_digest` | `VARCHAR NOT NULL` | its `commit_digest` |
| `backup_file_sha256` | `VARCHAR NOT NULL` | hash of the backup file restored |
| `backup_verification_sha256` | `VARCHAR NOT NULL` | hash of the canonical integrity report that verified the backup |
| `replaced_high_water_commit_seq` | `BIGINT` | the last commit of the replaced database, if it could still be read; else null |
| `replaced_high_water_commit_digest` | `VARCHAR` | its digest, if readable; else null |
| `superseded_checkpoint_commit_seq` | `BIGINT NOT NULL` | the `commit_seq` named by the checkpoint in force before the restoration (§10.6) |
| `superseded_checkpoint_commit_digest` | `VARCHAR NOT NULL` | its `commit_digest` |
| `superseded_checkpoint_created_at_utc` | `TIMESTAMP NOT NULL` | its creation time |
| `superseded_checkpoint_sha256` | `VARCHAR NOT NULL` | SHA-256 of the superseded checkpoint file's exact bytes |
| `recovery_authorization_ref` | `VARCHAR NOT NULL` | a bounded token naming the explicit recovery authorization; `^[a-z][a-z0-9_]{0,63}$` |
| common columns | | written in the first commit after restoration |

The recovery commit holds exactly this row and two audit events,
`store_restored` and `checkpoint_reissue_required`, both with
`reference_commit_seq` set to the recovery commit itself. Neither event
claims that a new checkpoint exists. The authenticated external checkpoint,
once written and verified equal to the database tip, is the only proof that
reissuance completed (§10.6).

### 5.11 `store_instance`

| Column | Type | Rule |
|---|---|---|
| `store_instance_id` | `VARCHAR` | primary key; 32 lowercase hex characters, generated randomly once when the store is initialized; the **database identity** |
| `initialized_at_utc` | `TIMESTAMP NOT NULL` | the first commit's time |
| common columns | | written in `commit_seq = 1`; exactly one row ever |

A restored backup of the same store keeps the same identity. A different
database file carries a different identity, so a checkpoint cannot be
satisfied by an unrelated store.

### 5.12 `store_checkpoint_key_rotations`

| Column | Type | Rule |
|---|---|---|
| `rotation_seq` | `BIGINT` | primary key |
| `previous_key_id` | `VARCHAR NOT NULL` | `^[a-z][a-z0-9_]{0,63}$` |
| `new_key_id` | `VARCHAR NOT NULL` | same pattern; `CHECK (new_key_id <> previous_key_id)` |
| `algorithm` | `VARCHAR NOT NULL` | `CHECK` in `CheckpointAuthAlgorithm` |
| `authorization_ref` | `VARCHAR NOT NULL` | bounded token naming the rotation authorization |
| common columns | | |

No column can hold key material. The latest row names the key that must
authenticate every later checkpoint (§10.6).

---

## 6. Write paths and transaction boundaries

### 6.1 Common rules for every write

1. **Single writer.** One process holds the read-write connection and an
   exclusive store lock (the existing fail-closed overlap-lock pattern). A
   second writer is refused.
2. **Store must be serving.** Writes are refused in read-refused mode
   (`store_read_refused`) or when the active registries do not load
   (companion design §8).
3. **Validate, then write,** inside one transaction, against records read
   in that transaction.
4. **Commit order.** The transaction takes `commit_seq` = previous + 1 and
   `committed_at_utc` = the observed UTC wall clock. If that time is
   **earlier** than the previous commit's, the write is refused
   (`store_clock_regressed`). An **equal** time is accepted. The store never
   invents or nudges a timestamp to make it unique.
5. **One commit, one time.** Every row the transaction writes carries that
   `commit_seq` and `recorded_at_utc = committed_at_utc`.
6. **All or nothing.** Rows, projections, the commit row and the success
   audit event commit together; any failure rolls all of them back.
7. **Refusals are recorded separately,** as `store_service` audit commits
   after the rollback. A refusal is never an integrity stop (§10.3).
8. **Size bounds** from the contracts are enforced before any write
   (`record_too_large`).
9. **Checkpoint after every commit, never before.** After **every**
   authoritative commit, and only once it is durable, the anti-rollback
   checkpoint is advanced to it (§10.6). If that advancement fails, the
   committed transaction stands and the store enters
   `checkpoint_reconciliation_required`, which refuses further
   authoritative writes until reconciled.
10. **Only in `serving` state.** Every authoritative write, including audit
    commits, requires the store state `serving`.

### 6.2 Envelope append (`evidence_ingest`)

1. Re-seal the envelope and check its ID.
2. Require its `registry_version_id` to be the evidence registry in force at
   this commit's time (`validate_envelope`).
3. For each item: `validate_item` (including the **holdout guard**), then
   `validate_lineage` with parents and predecessors already stored or
   earlier in the same envelope.
4. Insert new items, the header, projections, the commit row and
   `envelope_accepted`. Existing items are duplicates (§7.1).

A holdout refusal at step 3 is `holdout_write_refused` (§9).

### 6.3 Conflict append (`conflict_detector`, `manual_conflict_review`)

1. Re-seal and check the ID; compute `conflict_chain_key`.
2. Every involved and resolution item must already be stored.
3. **Chain rules (§7.3).** A record with no resolution is a chain root and
   is refused if the chain key already has a root
   (`conflict_chain_already_started`). A record with a resolution must name,
   as `supersedes_conflict_id`, the current tip of its chain
   (`conflict_status_not_tip`), and share its chain key.
4. **Severity (frozen derivation, §6.3.1).** Derive and check the
   severity wrapper; refuse on any failure listed there.
5. Insert, project, commit, `conflict_recorded`.

#### 6.3.1 Conflict-severity derivation — frozen, **[STRICTER]**

1. `evaluated_as_of_utc` must be strictly earlier than this commit's time
   (`conflict_as_of_not_past`), so the registry in force at it can no
   longer change (activations are never backdated).
2. `R` := the Evidence Registry version in force at `evaluated_as_of_utc`.
   Refuse if no version is in force or the stored version is missing
   (`conflict_registry_version_missing`), or if the activation answer is
   ambiguous (`conflict_registry_activation_ambiguous`).
3. **Authorized machine-decision purposes in `R`:** a bundle purpose `P`
   qualifies only if `R` holds a consumer grant with
   `machine_decision_mode_allowed = true` that lists `P`, **and** `R` has a
   selection rule for `P`. Requirements that belong only to dashboard
   display, assistant context, historical review, research-only review or
   an unauthorized purpose never count.
4. `matched_requirement_ids` := every `"<P>:<requirement_id>"` such that a
   requirement of `P`'s selection rule in `R` matches
   (`BundleRequirement.matches`) at least one involved item. Sorted,
   unique.
5. `involves_required_item` := `matched_requirement_ids` is non-empty.
6. `severity` := the fixed table of `severity_rule_version`
   (`conflict-severity-rules-1` = `expected_severity(conflict_type,
   involves_required_item)`, design §I.2). An unknown rule version is
   refused (`severity_rule_version_unknown`).
7. The record's own `severity` must equal the derived value, and the
   wrapper must equal the derivation exactly
   (`conflict_requirements_not_reproducible`, `conflict_severity_mismatch`).
   `validate_conflict` is called with the required set derived here, never
   with any bundle's.

**Consequences.**
- **Never bundle-specific.** No bundle's required marking changes a stored
  severity. Consumer readiness stays bundle-specific: `build_bundle` still
  gates on the stored severity of the conflicts touching its own entries.
- **Today.** No registry grants machine-decision mode to any consumer
  (`setup_ranker` and `notification_layer` are unauthorized), so no purpose
  qualifies, `involves_required_item` is false for every conflict, and
  severity follows from conflict type alone: `material` for
  `inference_vs_observation` and `current_vs_research_context`,
  `informational` otherwise, and never `critical`.
- **Reverification** recomputes steps 2–7 from the recorded
  `severity_registry_version_id`, the involved evidence IDs, the authorized
  machine-decision purposes of that version, and the fixed table. Any
  disagreement found in authoritative storage, or a missing version, an
  ambiguous activation or an unknown rule version, is an integrity stop
  (`conflict_severity_irreproducible`).
- **No rewriting.** A later registry change never alters an existing
  conflict record. A conflict evaluated later under another registry
  version is a **new** append-only conflict-status record in the same chain,
  with its own derivation.
- **No stale-at-birth record.** An `unresolved` conflict record is also
  derived under the Evidence Registry in force at its own commit time. If
  that derivation differs from the one at its `evaluated_as_of_utc` (the
  conflict was evaluated before a registry change and recorded after it),
  the write is refused (`conflict_evaluation_stale`) and the detector must
  re-evaluate at a later `evaluated_as_of_utc`.
- **Merged-contract limit, and how it is contained.** Under
  `evidence-envelope-1`, a successor status record must carry a resolution
  (status `resolved` or `acknowledged_unresolvable`). A later evaluation
  that is still `unresolved` therefore cannot be appended to update a
  severity. To keep this from producing a stale severity, the registry
  design refuses any activation that would change an unresolved conflict's
  matched requirements, `involves_required_item` or severity
  (`unresolved_conflict_requires_contract_amendment`; companion design
  §8.4). Lifting that block needs an Evidence Envelope amendment
  (§16.3).

The involved evidence is never touched. It stays stored and visible.

### 6.4 Bundle append (`bundle_builder`)

1. Refuse unless `as_of_utc` is **strictly earlier** than this commit's
   `committed_at_utc` (`bundle_as_of_not_past`). If the clock has not yet
   moved past `as_of_utc`, the builder retries later; no time is invented.
2. Require `registry_version_id` to be the evidence registry in force at
   `as_of_utc` (`bundle_registry_not_in_force`).
3. **Rebuild before storing.** Read the records visible at `as_of_utc`
   (§8.2), call `build_bundle` with the manifest's purpose, query and
   consumer context, and require the same `bundle_id`
   (`bundle_not_reproducible`).
4. Record the builder wrapper fields and `visible_through_commit_seq`.
5. Insert the bundle, entries, requirement projections, the commit row and
   `bundle_built`.

### 6.5 Card append (`card_builder`)

1. Re-seal and check the `scd1_` ID; compute `chain_key_sha256`.
2. The bundle must be stored (`card_bundle_not_stored`) and the card's
   `registry_version_id` must equal the bundle's.
3. Determine the setup-definition registry in force at the bundle's
   `as_of_utc`; that exact `sdr1_` version is the one bound to the card.
4. Build the `CardContext` from storage only: the stored bundle, its
   entries' items, the conflicts visible at its `as_of_utc`, the evidence
   registry version it names, and the definitions of that `sdr1_` version.
5. Run `validate_setup_card(card, ctx)`. For a `no_qualified_setup` card,
   also require `lane_qualification(ctx, lane, cited)` to be `available`.
6. **Chain rules (§7.4).** A revision-1 card must start a new chain (a
   second root is refused: `card_chain_already_started`); a later revision
   must supersede the chain's current tip (`card_predecessor_not_tip`) and
   pass `validate_supersession`.
7. Insert the card row (with `setup_definition_registry_version_id`), the
   chain row for a root, the commit row and `card_recorded`.

### 6.6 Why storage cannot create a card

- The store accepts only sealed cards from the card builder; it cannot
  construct, complete or edit one.
- With the in-force setup-definition registry structurally empty, step 5
  fails for every card with `setup_definition_not_registered` or
  `lane_qualification_not_available`: **no card of either kind can be
  stored.**
- `not_authorized`, `unavailable` and `evidence_blocked` lane states are
  render-time dashboard states. They are never persisted as cards, or at
  all.

### 6.7 Registry registration and activation (`registry_operator`)

Separate transactions, specified in the companion design §5–§8.

### 6.8 Workflow completion: checkpoint covers the final commit

**A write or operator workflow is complete only after the anti-rollback
checkpoint covers its final authoritative commit:** the checkpoint has been
written for that commit, and re-reading it shows it equals the database
tip. Until then the workflow is reported incomplete (`workflow_incomplete`),
the store accepts no unrelated authoritative write, and normal consumer
service does not resume.

| Workflow | Final authoritative commit | How a retry recognizes it (never re-appended) |
|---|---|---|
| Ordinary write | its own commit | the record IDs it wrote |
| Reconciliation (§10.6) | the `checkpoint_reconciled` audit commit | the tip commit is exactly that event, with `reference_commit_seq` = tip − 1 |
| Recovery (§10.6) | the single recovery commit (recovery row, `store_restored` and `checkpoint_reissue_required` events); completion is proven by the authenticated checkpoint equal to that commit, not by any further event | a `store_recovery_events` row with the same backup hash and superseded-checkpoint hash |
| Key rotation (§10.6) | the rotation commit (rotation row and `checkpoint_key_rotated` event) | a rotation row with the same key IDs and authorization reference |
| Registry registration (companion §6.1) | the registration commit | the `registry_versions` row with that ID |
| Registry activation (companion §6.2) | the activation commit | the `registry_activations` row with that `activation_id` |
| Shutdown | none: shutdown writes no audit event in this design | a clean shutdown releases the store lock only when the checkpoint equals the tip, reconciling first if needed. A future shutdown event would follow this same rule |

If the checkpoint write after a final commit fails, the store enters
`checkpoint_reconciliation_required`, and reconciliation (§10.6) completes
the workflow without repeating its final commit.

---

## 7. Duplicates and chains

### 7.1 Same ID

- **Identical identity bytes:** an idempotent no-op. The first row,
  `commit_seq`, `recorded_at_utc` and operational fields are kept, and
  `append_duplicate_ignored` is recorded. This matches `_known_items`.
- **Different identity bytes under the same ID:** only possible through
  corruption or a hash collision. The write is refused and the store takes
  an integrity stop (`impossible_duplicate_identity`).

### 7.2 Same meaning, different ID

Not a duplicate. Observations at different times, a retry by a different
build, or a model re-run are different records (design §L.4). All are kept;
the store never merges or picks one.

### 7.3 Conflict-status chains — frozen, **[STRICTER]**

The merged `validate_conflict` checks one resolution against one given
prior; it does not forbid branches or second roots. Storage freezes these
invariants:

| Rule | Enforcement |
|---|---|
| One root per chain (chain key = type + involved items) | a second unresolved original for a chain key is refused (`conflict_chain_already_started`); a re-detection of an open chain is not stored |
| At most one direct successor per status record | unique `supersedes_conflict_id` |
| No branches | the successor must supersede the current tip (`conflict_status_not_tip`) |
| No missing predecessor | the superseded record must be stored, in the same chain |
| No cycles | a predecessor must have a lower `commit_seq`, so a cycle cannot be written; verification re-checks |
| Idempotent retries | identical identity bytes → no-op (§7.1) |
| Conflicting content under one identity | integrity stop (§7.1) |
| Evidence untouched | no conflict write changes, supersedes or hides an involved item |

A later "second opinion" supersedes the current tip, never the original.
Each status record carries its own severity derivation (§6.3.1); a later
registry never rewrites an earlier record.

### 7.4 Card chains — frozen, **[STRICTER]**

The merged `validate_card_history` refuses duplicates, missing
predecessors and branches, and `validate_supersession` refuses lifecycle
regression and reopening terminal states. Storage adds one root per chain
and cycle refusal, and enforces all of them at write:

| Rule | Enforcement |
|---|---|
| Deterministic chain identity | `chain_key_sha256` = SHA-256 of the canonical `card_identity_key`: (setup, lane, setup subject) or (no-setup, lane, session date) |
| Exactly one root per chain | `setup_card_chains` primary key (`card_chain_already_started`) |
| No branches | unique `supersedes_card_id`; successor must supersede the tip (`card_predecessor_not_tip`) |
| No missing predecessor | the predecessor must be stored, in the same chain |
| No cycles | predecessors have lower `commit_seq`; verification re-checks |
| No lifecycle regression | `validate_supersession` (`lifecycle_transition_forbidden`) |
| No reopening a terminal state | `validate_supersession`; a terminal card is followed only by the same terminal state |
| Earlier cards never mutate | insert-only; verified by row hashes |

### 7.5 Other chains

| Chain | Branch allowed? |
|---|---|
| Evidence revisions (`supersedes_item_id`) | **yes**: a branch is legal evidence, reported as `branched_revisions` ambiguity (design §M) |
| Registry activations | **no** (companion design §6) |

---

## 8. Point-in-time reads

### 8.1 Order and visibility

- **Total order:** `commit_seq`. Every transaction-exact question ("what
  had been committed when commit N committed?") uses it:
  `visible_through(N)` = every row with `commit_seq ≤ N`.
- **Time visibility:** a query "as of T" sees every row of every commit
  whose `committed_at_utc ≤ T`, **including all commits that share the
  timestamp T**. Because commit times never regress, this set is exactly
  `visible_through(N_T)`, where `N_T` is the greatest `commit_seq` with
  `committed_at_utc ≤ T`. Time visibility is therefore always a
  commit-sequence prefix.
- **"Known at T"** (design §L.4) is this time visibility, via each row's
  `recorded_at_utc`, which is how `build_bundle` already filters.
- **In force at T** (registries): the activation with the greatest
  `effective_from_utc ≤ T` (companion design §7).

### 8.2 Records visible at T

```
records_as_of(T):
  1. refuse unless T < committed_at_utc of the transaction doing the read
     (bundle_as_of_not_past): a later commit could otherwise share time T
  2. N_T := greatest commit_seq with committed_at_utc ≤ T
  3. registry := the evidence registry in force at T, loaded by ID and
     verified by hash
  4. items := evidence_items rows with commit_seq ≤ N_T
  5. conflicts := evidence_conflicts rows with commit_seq ≤ N_T
  6. verify each row (re-parse strictly, recompute the ID, record_sha256 and
     row_sha256); any mismatch is an integrity stop (§10.3)
  7. check each item with is_holdout_restricted(item, registry); a hit is
     a persisted_holdout_violation, an integrity stop (§9)
  8. return RecordedItem / RecordedConflict lists, sorted by ID only for
     deterministic serialization
```

The read never filters by kind, freshness, revision, supersession,
producer or query; those belong to `build_bundle`, so a live build and a
reproduction see identical inputs. ID order never selects anything.

### 8.3 Revision and supersession as of T

- **Successors of X at T:** revision links whose successor has
  `commit_seq ≤ N_T`.
- **Latest revision at T:** walk successors; a branch yields none (the
  builder reports it).
- **Conflict status at T:** the tip of the conflict chain among records with
  `commit_seq ≤ N_T`.

### 8.4 Why a stored bundle cannot drift

A bundle is stored only when `as_of_utc < committed_at_utc` of its own
commit (§6.4). Every later commit has `committed_at_utc ≥` that time, so
it is strictly after `as_of_utc` and never visible at it. The bundle's
`visible_through_commit_seq` records `N_T` exactly, so verification can
also check the prefix by sequence. Corrections recorded later appear only
in later bundles (design §M; DECISION_RULES "No retroactive forecast
editing").

### 8.5 Exact bundle reproduction

```
reproduce(bundle_id):
  1. load and verify the bundle row and wrapper
  2. load the registry version it names; verify its hash; check it was in
     force at as_of_utc
  3. require N_T(as_of_utc) == visible_through_commit_seq
  4. (items, conflicts) := records_as_of(as_of_utc)
  5. rebuilt := build_bundle(registry, purpose, as_of_utc, consumer_context,
                             query, items, conflicts, built_at=built_at_utc)
  6. equal iff rebuilt.bundle_id == bundle_id
```

- **Builder identity lives in the wrapper.** `builder_id`,
  `builder_version`, `builder_code_commit_sha` and
  `builder_configuration_identity` are authoritative storage facts that
  support verification and reconstruction. They are **not** part of
  `evb1_`, and `evidence-envelope-1` is unchanged.
- **A mismatch under a different builder** is reported as
  `bundle_reproduction_mismatch` (not an integrity stop), investigated as a
  builder change, and never "fixed" by rewriting the stored bundle.
- **Changing bundle identity** to include the builder would be a future
  `evidence-envelope-2` design question; it is not proposed here.

### 8.6 Cards

| Read | Algorithm |
|---|---|
| Card by ID | load and verify the row; return the card with its wrapper |
| Chain history | all rows with the same `chain_key_sha256`, checked against §7.4 |
| Chain tip at T | chain rows with `commit_seq ≤ N_T`, then the one with no visible successor |
| Card reconstruction | the stored bundle it names, the evidence registry that bundle names, and the **exact `sdr1_` version on the card row**, never the active one |
| Display status | computed at render time by `display_status(card, history=chain history visible now, now, view_max_age_seconds)`; never stored; fails closed to `outdated_view` while the view policy is unset |

---

## 9. Holdout enforcement

The guard implements, and never changes, the preregistration rule and
design §O. It has no switch.

### 9.1 Three distinct cases

| Case | When | Behavior | Store state |
|---|---|---|---|
| `holdout_write_refused` | a proposed write contains SPY price evidence dated 2026-09-23 → 2026-12-04, or descends from it | rejected **before persistence**: no record row. One audit event with only bounded metadata: `event_type`, `actor`, `occurred_at_utc`, `reason_code = holdout_restricted`, and a null `subject_record_id` | **remains readable and writable** |
| `holdout_read_refused` | a direct record read (for example an `audit_export` read by ID, subject or window) targets restricted scope | rejected before any row is returned, without exposing an ID, count, date or value; one bounded audit event as above | **remains readable** |
| `persisted_holdout_violation` | restricted material is found in authoritative storage, or an authoritative read path returns it | an **integrity stop** (§10.3) | **read-refused mode** |

Bundle building is not a refusal: `build_bundle` already excludes
restricted items and reports the requirement as `holdout_restricted`, as
the reviewed design requires.

### 9.2 Boundaries

| Boundary | Enforcement |
|---|---|
| Write (items) | `validate_item` → `enforce_holdout_guard`; `validate_lineage` refuses restricted ancestors |
| Write (bundles, cards) | the store's rebuild (§6.4) and `validate_setup_card` (`holdout_restricted_citation`) |
| Read | request-level scope check (`holdout_read_refused`); row-level re-check on every authoritative read path (`persisted_holdout_violation`) |
| Registry | a payload schema's `spy_price_content` can never change once activated; a new meaning needs a new schema ID (companion design §8.3) |
| Unknown schema | refused before any holdout evaluation |

### 9.3 No leakage

A restricted item's ID is a hash of holdout content, so no holdout event,
error or log carries a record ID, envelope ID, subject, date, count or
value. A normal holdout refusal never disables unrelated reads.

---

## 10. Integrity, corruption and recovery

### 10.1 Hash chain

Each commit's `commit_digest` covers its sequence, time, writer, operation,
row count, the hash of its rows' `row_sha256` values, and the previous
digest. Deleting, inserting or editing any authoritative row breaks a
content hash, a row hash, a commit's row set, or the chain.

### 10.2 Verification scan

A read-only verification command (future) re-parses every authoritative
row and reports only IDs and tokens (§4.2 `IntegrityFinding`): identities,
content and row hashes, commit sequence contiguity, non-regressing commit
times, the chain and each commit's row set, key columns, parents and
predecessors, every conflict, card and activation chain, bundle-to-card
and card-to-registry-version links, the holdout guard, projections, the
anti-rollback checkpoint (§10.6), and a sample of (or every) bundle
reproduction.

### 10.3 Integrity stop versus ordinary refusal

**Integrity stop → read-refused mode** (bundle building, card reads and
all writes stop; only the health check and verification run):

- canonical identity mismatch;
- content-hash or row-hash mismatch;
- commit-chain mismatch (including a sequence gap or a commit-time
  regression);
- impossible duplicate identity;
- persisted holdout violation;
- invalid authoritative registry activation;
- unrecoverable authoritative-chain ambiguity (a second root, branch,
  cycle or missing predecessor found in a stored conflict, card or
  activation chain);
- an unknown schema in a stored authoritative row, or a card's bound
  `sdr1_` version or bundle missing;
- an anti-rollback checkpoint failure: missing for an established store,
  invalid, failing HMAC authentication, using an unsupported algorithm, for
  another store, ahead of the database, or naming a commit whose digest
  differs (§10.6);
- an irreproducible conflict-severity derivation in a stored record
  (§6.3.1).

`checkpoint_reconciliation_required` and a missing checkpoint key or unknown
key ID are **not** integrity stops. Nothing stored is known to be wrong, so
the store refuses writes (or does not open) until the condition is
cleared (§10.6).

**Not a stop** (the store keeps serving; the finding is reported):

- `key_column_mismatch` and `projection_mismatch`: projections and key
  copies are rebuilt from authoritative rows, recorded as an audit event;
- `bundle_reproduction_mismatch`: investigated as a builder change.

**Ordinary refusals never stop the store.** Invalid input, an unauthorized
operation or writer, a clock regression, a chain-rule refusal at write,
and `holdout_write_refused` or `holdout_read_refused` fail only the request
they concern. They write nothing authoritative and never disable unrelated
reads.

Authoritative data is never auto-repaired. Leaving read-refused mode is a
human decision after review, normally a restoration (§10.5).

### 10.4 Partial writes and crashes

- A DuckDB transaction is atomic; a crash before `COMMIT` leaves nothing,
  and DuckDB replays or discards its write-ahead log on reopen.
- Rows and their commit row are written in one transaction, so a row
  without its commit (or the reverse) can only come from an out-of-band
  edit, and is a commit-chain mismatch.
- A writer that crashed mid-validation wrote nothing and may retry;
  retries are idempotent (§7.1).

### 10.5 Backups, restoration and recovery

- **Backups** are byte copies of the database file, taken while the store
  lock is held and no transaction is open. A backup is trusted only after a
  full verification scan of the copy. Its manifest records the file SHA-256
  and the high-water (`commit_seq`, `commit_digest`).
- **Restoration preserves every authoritative row exactly.** It replaces
  the database file with the verified backup, byte for byte. Every commit
  sequence, original commit time, recorded time, content hash, row hash,
  previous-commit digest, identity and canonical record is kept as it was.
  **Nothing is re-appended, re-timed or re-hashed.**
- **Restoring behind the checkpoint needs explicit authorization.** A
  verified backup whose high-water mark is older than the anti-rollback
  checkpoint is exactly the rollback §10.6 detects. It may be restored only
  under an explicit, recorded recovery authorization. The checkpoint is
  never moved backward silently.
- **The restoration is recorded separately.** Before any other write, the
  `recovery_operator` appends one recovery commit, the first after the
  restored high-water mark. It holds one `store_recovery_events` row
  (§5.10) and the `store_restored` and `checkpoint_reissue_required` audit
  events, and like every commit it continues the chain from the restored
  high-water digest. The full sequence is in §10.6.
- **Lost commits stay lost.** Commits made after the latest verified
  backup are gone. They **cannot be recreated as if they originally
  existed**: no row may be written with an old `commit_seq`, an old commit
  time or an old recorded time. New writes after restoration are new
  commits with their own observed times. Evidence that producers emit again
  is new knowledge from that later commit, never a claim that it was known
  before.
- **Sequence numbers after a restoration.** Because the sequence continues
  from the restored high-water mark, a number that belonged to a lost
  commit can be reused. Outside the store, a commit is therefore always
  referenced by (`commit_seq`, `commit_digest`), and the recovery event
  records the replaced database's high-water pair when it could be read.
- **Import from another authority** (for example a verified export) is
  **not** designed here. Any such reconstruction needs its own separately
  designed and authorized import process that preserves original
  identities, commit sequences, times and hashes.
- **Optional exports** (open question O-5) are hash-chained, append-only
  copies for off-machine verification, never read back as an authority.

### 10.6 Valid-prefix rollback detection: the external checkpoint

**The gap.** The internal hash chain proves that every row is consistent
with the rows before it. It **cannot** detect that the whole DuckDB file was
replaced by an older copy that is itself internally valid: an older
database is a valid prefix of the newer one, and its chain verifies
perfectly. Without an outside witness, silently losing the newest commits
would look healthy.

**The checkpoint.** A minimal, external anti-rollback witness,
`StoreCheckpoint`, stored **outside** the database file. It holds only:

| Field | Content |
|---|---|
| `checkpoint_schema_version` | `Literal["store-checkpoint-1"]` |
| `store_instance_id` | the database identity (§5.11) |
| `commit_seq` | the highest `commit_seq` verified as durably committed |
| `commit_digest` | that commit's `commit_digest` |
| `created_at_utc` | when this checkpoint was written |
| `authentication` | `{algorithm, key_id, mac}`: `hmac_sha256` over the canonical JSON of every other field. Null **only** in explicitly selected unauthenticated development or test mode |

It contains **no** evidence, prices, outcomes, record IDs, bundle contents,
card contents, registry content or paths. It is canonical JSON under the
same strict rules as registry files (unknown fields, duplicate keys and
floats refused).

**Authority.**
- The DuckDB tables remain the **sole authority** for every domain record.
- The checkpoint is only a trusted witness to the **minimum durable
  high-water mark previously observed**. It never supplies, restores or
  overrides a record.
- It must never name a commit that is not present in the database.

**Location (frozen).** A separately configured **checkpoint state
directory**, outside the database directory:
- it must resolve to a real directory that is neither inside nor equal to
  the database's directory, is not a symlink, and is not tracked by Git;
- the checkpoint path must resolve inside it, is never a symlink, and is
  never logged in full;
- the setting is configuration, not evidence. If the directory lies outside
  this repository, that location needs the user's explicit authorization
  (CLAUDE.md "No access outside this repository").

**Authentication (frozen).**

| Mode | When | Rule |
|---|---|---|
| `production_authenticated` | the real store, and the default | every checkpoint carries `hmac_sha256` authentication with a `key_id`; the key comes only from an approved credential source (for example `.env`, like other credentials) |
| `offline_development_unauthenticated` | only when **explicitly selected** for an offline development or test store | the checkpoint has null `authentication`; refused for the real database path |

- **Key handling.** The HMAC key is never stored in the database, the
  checkpoint, a registry, any configuration committed to Git, or a log. The
  checkpoint and the rotation records carry only the algorithm and
  `key_id`.
- **No silent fallback.** A missing production key never switches the store
  to unauthenticated mode.
- **Fail closed** in production mode:

| Condition | Result |
|---|---|
| production key missing from the credential source | store does not open (`checkpoint_key_missing`) |
| checkpoint `key_id` not the configured one (or, during rotation, not the old or new one) | store does not open (`checkpoint_key_id_unknown`) |
| unsupported `algorithm` | integrity stop (`checkpoint_algorithm_unsupported`) |
| HMAC verification fails | integrity stop (`checkpoint_authentication_failed`) |
| an unauthenticated checkpoint found in production mode | integrity stop (`checkpoint_invalid`) |

**Writing it.**
1. Advance the checkpoint after **every** authoritative commit, and only
   once that DuckDB transaction has committed durably (§6.1 rule 9), to
   its (`commit_seq`, `commit_digest`).
2. Never move it backward, except through an authorized recovery (below).
3. Write atomically: a new file with an exclusive create in the state
   directory, flushed and synced, then an atomic rename over the previous
   checkpoint, then a directory sync. A partial write can never replace a
   good checkpoint; a leftover temporary file is ignored and removed.

**If the checkpoint write fails after a successful commit.**
- The committed database transaction is **preserved**. It is never rolled
  back, rewritten or duplicated.
- The store enters the bounded state `checkpoint_reconciliation_required`.
- Every further authoritative write is refused
  (`checkpoint_reconciliation_required`), including audit commits. The only
  exception is reconciliation's own `checkpoint_reconciled` commit (step 5
  below).
- Reads are permitted only after confirming that the existing checkpoint is
  valid (parses, authenticates, names this store) and that its exact
  (`commit_seq`, `commit_digest`) is a verified prefix of the database (the
  chain verifies from commit 1 through it). Until then no read is served.
- Consumer reads that must record a `consumer_access` audit event wait
  until reconciliation completes, because that event is itself an
  authoritative write. Stored records may be read by the verification and
  operator commands meanwhile.

**Reconciliation.** Every checkpoint write below is authenticated with the
key named by the latest `store_checkpoint_key_rotations` row, or the
configured initial key if there is none.
1. Authenticate and validate the existing checkpoint.
2. Verify that its exact (`commit_seq`, `commit_digest`) is a prefix of the
   database.
3. Verify the complete commit chain from that checkpoint through the
   current database tip `T`.
4. Advance the checkpoint to `T`.
5. Commit one bounded `checkpoint_reconciled` audit event, with
   `reference_commit_seq = T`, as commit `T + 1`. **Skip this step** if the
   database shows the event already exists: the tip commit is exactly a
   `checkpoint_reconciled` event whose `reference_commit_seq` is the tip
   minus 1, and it was verified in step 3.
6. Advance the checkpoint again, to include the audit-event commit.
7. Verify that the checkpoint now equals the database tip.
8. Only then leave `checkpoint_reconciliation_required` and resume
   authoritative writes and normal consumer service.

Failure handling:
- A failure at steps 1–3 is an integrity stop.
- If either checkpoint write (step 4 or step 6) fails, the store stays in
  `checkpoint_reconciliation_required` and accepts no unrelated
  authoritative write.
- A retry, or a restart, runs the sequence again from step 1. Checkpoint
  writes are idempotent (they name a verified commit). The audit event is
  never duplicated: step 5 finds the committed event and is skipped.

The same reconciliation runs at startup whenever the database is found
ahead of a valid checkpoint.

**Key rotation.** Through the `checkpoint_operator` command, dry-run by
default, with an authorization reference. Both the old and new keys are
available from the credential source during rotation.
1. The store must be `serving`. It enters
   `checkpoint_key_rotation_in_progress`, which refuses every other write.
2. Verify the existing checkpoint with the old key, and verify the
   **complete** database chain.
3. Append one `store_checkpoint_key_rotations` row (§5.12) and a
   `checkpoint_key_rotated` audit event, holding only the previous key ID,
   the new key ID, the algorithm and the authorization reference. No secret
   key is recorded anywhere.
4. Write the replacement checkpoint for that commit, authenticated with the
   **new** key.
5. Verify that the checkpoint equals the database tip (the rotation commit
   is the workflow's final commit, §6.8).
6. Only then return to `serving`.

If step 2 fails, nothing is written: an integrity finding stops the store,
otherwise it returns to `serving` with the old key. If step 4 fails after
step 3 committed, the store enters `checkpoint_reconciliation_required`.
Reconciliation authenticates with the new key named by the committed
rotation row, never re-appends that row, and completes the workflow. Writes
resume only when the rotation has finished successfully.

**At startup** (before any read or write other than the health check):

| Situation | Result |
|---|---|
| brand-new, empty store (no commits) and no checkpoint | allowed **only during initialization**; the first checkpoint is written after commit 1 |
| non-empty store and no checkpoint | integrity stop (`checkpoint_missing`) |
| checkpoint fails strict parsing, or is unauthenticated in production mode | integrity stop (`checkpoint_invalid`) |
| production key missing, or `key_id` unknown | store does not open (`checkpoint_key_missing`, `checkpoint_key_id_unknown`) |
| unsupported algorithm, or HMAC fails | integrity stop (`checkpoint_algorithm_unsupported`, `checkpoint_authentication_failed`) |
| `store_instance_id` differs from the database's | integrity stop (`checkpoint_store_mismatch`) |
| the database ends before the checkpoint's `commit_seq` (including an empty database with a checkpoint present) | integrity stop (`database_behind_checkpoint`): a valid-prefix rollback |
| the database's commit at that `commit_seq` has a different `commit_digest` | integrity stop (`checkpoint_commit_mismatch`) |
| the database contains the exact (`commit_seq`, `commit_digest`) and may extend beyond it | **safe**: reconcile (steps 1–8 below) |
| the tip is a recovery commit (recovery row with `checkpoint_reissue_required`), and the checkpoint's bytes and its (`commit_seq`, `commit_digest`) match the superseded-checkpoint metadata in that row | **recovery pending** (`recovery_in_progress`): the recovery committed but its checkpoint was not yet written; complete it (restart behavior below) |
| the tip is a recovery commit, and the checkpoint already names exactly that commit | **recovery complete**: no database write; verify and serve |
| the tip is a recovery commit, and the checkpoint matches neither the superseded metadata nor the recovery commit | integrity stop (`checkpoint_recovery_mismatch`) |

A database ahead of its checkpoint is the normal result of a crash
between a durable commit and the checkpoint write, and is never an error.

**Authorized recovery behind the checkpoint.** An explicit, recorded
recovery authorization is required; without it the store stays in
read-refused mode. Throughout, the store is in `recovery_in_progress`, the
only state that tolerates a checkpoint ahead of the database, and only with
that authorization reference. It accepts no unrelated authoritative write.
1. Verify the selected backup, the recovery authorization and the existing
   checkpoint (parsed and authenticated, so its metadata can be preserved).
2. Restore the verified database byte for byte (§10.5).
3. Verify the restored database's identity (`store_instance_id`), hashes
   and complete commit chain.
4. Commit **one** recovery transaction containing: the authoritative
   recovery row; the prior checkpoint's bounded metadata (`commit_seq`,
   `commit_digest`, creation time) and its file hash; the recovery
   authorization reference (all in the row, §5.10); and the `store_restored`
   and `checkpoint_reissue_required` audit events.
5. Write the authenticated external checkpoint for that recovery commit.
6. Verify that the checkpoint's exact (`commit_seq`, `commit_digest`) equals
   the database tip.
7. Only once that equality is proven does recovery finish and normal service
   resume.

The authenticated checkpoint is the proof that reissuance completed. No
completion event is recorded: one would be a further commit needing a
further checkpoint, an extra cycle this design avoids.

**Restart and retry after the recovery commit.** If step 5 or 6 fails, or
the process stops after step 4:
- the database remains authoritative and unchanged, and the store stays in
  `recovery_in_progress`, accepting no unrelated authoritative write;
- at startup, the store finds that the tip is the latest recovery commit
  carrying `checkpoint_reissue_required`;
- it verifies that the existing checkpoint matches the superseded-checkpoint
  metadata and file hash stored in that recovery row;
- it verifies the restored database and the complete commit chain;
- it writes the missing checkpoint for the recovery commit (idempotent: it
  names a verified commit);
- it verifies that the checkpoint equals the database tip, and only then
  resumes service;
- it never re-appends the recovery row or either recovery audit event.

If the checkpoint already exists and names the recovery commit exactly,
recovery is complete and nothing is written. If a checkpoint exists that
matches neither the superseded metadata nor the recovery commit, startup
takes an integrity stop (`checkpoint_recovery_mismatch`).

**Configuration, not evidence.** The concrete state-directory path and
the credential-source entry names are set at implementation time and
recorded in that authorization. They are never stored as evidence.

**Limits, stated honestly.** An actor who controls the database, the
checkpoint state directory **and** the credential source can still defeat
this mechanism, for example by rolling all three back together or forging
a checkpoint with the key. It reliably catches accidental restores, file
swaps, and tampering by anyone without the key.

---

## 11. Sanitization and leakage

- **Reason tokens only.** Every refusal is an `EvidenceValidationError`
  with a bounded token (`evidence/errors.py`), or a store token from
  `StoreRefusalReason`.
- **No payload in logs.** Logs and audit events never contain payloads,
  display text, research estimates or outcome values, contract sets or
  record JSON. They may contain record IDs (except for holdout events),
  writer and consumer IDs, purposes and tokens.
- **DuckDB errors are wrapped.** A constraint violation becomes a store
  token; the DuckDB message, which can quote values, is discarded.
- **Integrity reports** list IDs and tokens only.
- **Research outcome values**, if ever stored as evidence, follow the same
  rules and are readable only through a granted bundle.

---

## 12. Consumer access

1. **No connection, no SQL.** Consumers call a future bundle service with
   an `EvidenceConsumerContext` and an `EvidenceQuery` (consumer rules §1).
2. **Grants decide.** `build_bundle` checks the consumer's registered grant,
   purpose and permissions in the registry in force; storage never bypasses
   it.
3. **Cards through their purpose.** A card is returned only to a consumer
   whose grant includes its bundle's purpose (`setup_detail`). No consumer
   is implemented, and the proposed read-only assistant `setup_detail`
   grant remains an **unapproved** future registry change.
4. **Audited reads.** Every bundle build and card read records a
   `consumer_access` event.
5. **One writer.** DuckDB allows no concurrent read-only process while the
   writer is open, so a future dashboard would read through the single
   store service or a verified read-only snapshot (open question O-4).
6. **No consumer writes.** A consumer that emits inference does so as a
   registered producer through `evidence_ingest`, under its own
   authorization.

---

## 13. Indexes

Indexes serve lookups only; none is part of an identity or used to choose
a winner. Adding or dropping a secondary index changes no record, ID or
result.

| Table | Index | Serves |
|---|---|---|
| `store_commits` | (`committed_at_utc`, `commit_seq`) | `N_T` lookup |
| `evidence_items` | (`producer_id`, `payload_schema_id`, `primary_subject_id`, `effective_at_utc`); (`commit_seq`); (`supersedes_item_id`) | requirement candidates; visibility; successor walks |
| `evidence_item_subjects` | (`subject_id`, `item_id`) | subject queries |
| `evidence_item_parents` | (`parent_item_id`) | lineage and taint checks |
| `evidence_conflicts` | (`conflict_chain_key`, `commit_seq`); (`commit_seq`) | chain tips; visibility |
| `evidence_conflict_items` | (`item_id`) | conflicts touching included items |
| `evidence_bundles` | (`purpose`, `as_of_utc`) | audit lookups |
| `setup_cards` | (`chain_key_sha256`, `revision_number`); (`decision_context_bundle_id`) | chain history; card by bundle |
| `registry_activations` | (`registry_kind`, `effective_from_utc`) | in-force lookup |

---

## 14. Migration number

- **No migration is created by this design.** The real database is at
  `0009`, and the shadow-recorder design proposes
  `0010_create_spy_vwap_shadow_tables.sql` (not created).
- **What the runner permits (verified 2026-09-29 against
  `storage/database.py`, using its pure functions on synthetic temporary
  files; no database opened).** File versions need only be unique and
  ascending, so a directory with `0009` and `0011` but no `0010` loads, and
  applying `0011` passes the history check. But if a `0010` file is added
  **after** `0011` is applied, the applied history is no longer a prefix of
  the files, and the database becomes permanently unhealthy.
- **Consequence.** `0011` is therefore **not** a safe expectation while
  `0010` is only reserved: using it first would make the shadow recorder's
  reserved number unusable. This storage migration will use **the next
  valid migration number, fixed by the implementing authorization**. That
  is `0011` only if `0010` has by then been created and applied; otherwise
  the shadow-recorder reservation must be resolved first.
- Applying it later would need a verified backup first, migration tests on
  temporary databases, and a health-check update for the new tables.

---

## 15. Future implementation test matrix (listed, not created)

All synthetic, on temporary DuckDB files inside a test data directory. No
real database, provider, model or holdout data.

### 15.1 Evidence storage

| Area | Tests |
|---|---|
| Migration | creates exactly the §5 tables; `CHECK` constraints refuse bad enum values, malformed IDs and revision mismatches; health check reports the new tables; no existing table changes; numbering follows §14 |
| Round trip | every record kind stores and reloads byte-identical canonical JSON; recomputed IDs, content hashes and row hashes match |
| Commit order | `commit_seq` contiguous; equal commit times accepted; an earlier time refused (`store_clock_regressed`) without inventing a time; one transaction shares one time |
| Visibility | time visibility includes every commit sharing time T; it equals the `commit_seq` prefix `N_T`; transaction-exact reads by sequence |
| Duplicates | identical identity bytes → no-op keeping the first row; forged same-ID different identity → integrity stop |
| Append-only | no update, delete or upsert method exists |
| Atomicity | a failure after each insert leaves no row, projection, commit or success event; the refusal is recorded separately |
| Lineage and revisions | missing parent, later parent, inference ancestor, holdout ancestor refused; branched revisions stored and reported as ambiguity; no ID tie-break anywhere |
| Conflict chains | second root refused; successor of a non-tip refused; unique successor; cycle impossible; identical retry is a no-op; involved items unchanged |
| Conflict severity | only requirements of authorized machine-decision purposes in the registry in force at `evaluated_as_of_utc` match; dashboard, assistant, historical, research-only and unauthorized purposes never do; matched IDs sorted and unique; boolean and severity reproduce from the recorded registry version and rule version; missing version, ambiguous activation, irreproducible matches, mismatched boolean or severity, and unknown rule version refused at write and stop the store when found stored; a later registry never rewrites a record; a bundle's required set never changes a severity; with today's grants every conflict has `involves_required_item = false` |
| Unknown schemas | refused at write; a stored unknown schema is an integrity stop |
| Point in time | later records invisible; stored bundles unaffected by later corrections; `visible_through_commit_seq` equals `N_T` |
| Bundle reproduction | every stored bundle rebuilds to the same `evb1_`; builder wrapper fields do not enter the ID; a different builder is reported, never rewritten |
| Holdout | `holdout_write_refused` stores nothing, logs no ID, and leaves the store serving; `holdout_read_refused` exposes nothing and leaves the store serving; an injected restricted row is a `persisted_holdout_violation` and enters read-refused mode |
| Integrity stops | each §10.3 stop condition enters read-refused mode; each ordinary refusal does not; projection and key-column mismatches are rebuilt without a stop |
| Recovery | restoring a verified backup keeps every row, sequence, time and hash byte-identical; the recovery event is the next commit and chains from the restored digest; no lost commit can be re-inserted with an old sequence or time |
| Anti-rollback checkpoint | an older, internally valid database copy with a newer checkpoint → `database_behind_checkpoint`; digest mismatch at the checkpoint's sequence → stop; checkpoint for another `store_instance_id` → stop; database ahead after a simulated crash → chain verified and checkpoint advanced; missing checkpoint allowed only for an empty store during initialization; a truncated or partial checkpoint write never replaces a good one; symlinked or out-of-directory path refused; the checkpoint contains no record ID or evidence field; restoring behind the checkpoint without authorization stays read-refused; with authorization, the recovery event preserves the old checkpoint and a new one is issued only after verification |
| Checkpoint policy | checkpoint advanced after every commit; commit succeeds but checkpoint write fails → transaction preserved, state `checkpoint_reconciliation_required`; writes (including audit commits) refused during reconciliation; reads served only after the existing checkpoint is confirmed valid and a verified prefix, and consumer reads needing an access event wait; reconciliation with the database ahead succeeds and returns to `serving`; missing production key, unknown key ID, failed HMAC and unsupported algorithm each fail closed with their tokens; an explicitly selected unauthenticated development mode works on a temporary store and is refused for the real database path; a missing production key never falls back to unauthenticated mode; state directory inside the database directory, symlinked or Git-tracked refused; no key material in database, checkpoint, logs or rotation rows |
| Key rotation | successful rotation verifies the old checkpoint and full chain, records only key IDs, algorithm and authorization, and issues a new-key checkpoint; writes refused until it completes; failed verification writes nothing; failure after the rotation commit enters reconciliation and completes with the new key; the rotation's final commit is checkpointed before `serving` |
| Reconciliation sequence | the first checkpoint advancement succeeds but the one after the `checkpoint_reconciled` commit fails → state stays `checkpoint_reconciliation_required`; restart after the reconciliation event committed but before its checkpoint → completes without a second event; repeated reconciliation never duplicates the event; writes and consumer service stay blocked until the checkpoint equals the tip |
| Workflow completion | recovery's final commit, rotation's final commit, registration and activation commits are each checkpointed before the workflow reports complete; a checkpoint failure after any of them completes through reconciliation without repeating the commit; a failed checkpoint after a recovery commit is completed through the recovery restart path; clean shutdown waits for checkpoint = tip |
| Recovery checkpoint reissue | recovery commit and checkpoint write both succeed → service resumes only after checkpoint = tip; recovery commit succeeds but checkpoint write fails → store stays `recovery_in_progress` and refuses unrelated writes; restart completes the missing checkpoint without duplicating the recovery row, `store_restored` or `checkpoint_reissue_required`; an existing checkpoint that already names the recovery commit completes recovery with no database write; a checkpoint matching neither the superseded metadata nor the recovery commit → `checkpoint_recovery_mismatch` integrity stop; service stays blocked until checkpoint = tip; no `checkpoint_reissued` event exists at any point, before or after the external checkpoint is written |
| Stale conflicts | an unresolved conflict evaluated before a registry change and recorded after it, whose derivation differs, is refused (`conflict_evaluation_stale`) |
| Sanitization | no refusal, log or audit event contains payload, path, SQL or exception text; DuckDB constraint text never surfaces |
| Consumers | no connection exposed; ungranted purpose or permission refused; every read audited |

### 15.2 Setup-card storage

| Area | Tests |
|---|---|
| Production registry | with the empty setup-definition registry in force, every card of either kind is refused |
| Registry binding | the card row stores the `sdr1_` in force at the bundle's `as_of_utc`; verification loads that exact version even after a later activation; a missing bound version is an integrity stop |
| Synthetic definitions | with a clearly named synthetic definitions registry on a temporary store (test-only, never the production format), a valid card stores and reloads with the same `scd1_` |
| Bundle binding | unstored bundle, different evidence registry, or a value that disagrees with the stored bundle refused |
| No-setup cards | `not_authorized`, `unavailable` and `evidence_blocked` lanes store nothing |
| Card chains | second root, branch, non-tip predecessor, missing predecessor, lifecycle regression and terminal reopening refused; cycles impossible |
| Immutability | earlier cards byte-identical after later revisions |
| Display status | no stored column; computed at render; superseded never current; unset view policy → `outdated_view` |
| Selector sets | stored contract section equals the stored selector result exactly; no rejected contract materialized; no per-contract table |
| Execution fields | no column of `setup_cards` or `setup_card_chains` matches the card contract's `FORBIDDEN_FIELD_FRAGMENTS` (evidence tables are outside the card contract; for example `evidence_bundles.entry_count` is a bundle size, not an entry price) |

---

## 16. Decisions, open questions and blockers

### 16.1 Proposed invariants, including those stricter than merged code

| Invariant | Merged code today | Status |
|---|---|---|
| Linear conflict-status chains: one root per chain key, one successor, no branch, cycle or missing predecessor | `validate_conflict` checks one resolution against a supplied prior only | **[STRICTER]**; needs explicit acceptance at implementation review |
| Frozen conflict-severity derivation (§6.3.1): only authorized machine-decision purposes in the registry in force at `evaluated_as_of_utc`, fully recorded and reproducible, never a bundle's required set | `expected_severity` takes a caller-supplied flag | **[STRICTER]**; needs explicit acceptance |
| One root per card chain; cycle refusal | `validate_card_history` refuses branches, duplicates and missing predecessors, but not a second root | **[STRICTER]**; needs explicit acceptance |
| Store-side rebuild of every bundle before storing it; bundles only for a past `as_of_utc` | `build_bundle` is pure; no store exists | proposed |
| `payload_schema` price declaration immutable once activated | the registry model has no history | proposed (companion design §8.3) |
| Builder identity and `sdr1_` binding in storage wrappers, outside every identity | no wrapper exists | proposed; contracts unchanged |
| Commit sequence as the total order; non-regressing, possibly equal commit times | no store exists | proposed |
| Exact restoration plus a separate recovery event; lost commits never recreated | no store exists | proposed |
| External anti-rollback checkpoint as a non-authoritative high-water witness; restoring behind it needs explicit recovery authorization | no store exists | proposed |
| Checkpoint advanced after every commit, in a separate state directory, HMAC-authenticated in production with no silent fallback; reconciliation and key-rotation states refuse writes | no store exists | proposed (frozen policy) |

### 16.2 Open questions

| # | Question |
|---|---|
| O-1 | Should envelopes be stored as headers (proposed) or only as run records? |
| O-2 | Resolved into a blocker (§16.3): unresolved re-evaluation needs an Evidence Envelope amendment |
| O-3 | Should a re-detected open conflict be silently not stored (proposed) or recorded as an audit event only? |
| O-4 | How will future consumers read while DuckDB's single writer runs (one store service, or verified read-only snapshots)? |
| O-5 | Is an off-machine, hash-chained export wanted, and where would it live? |
| O-6 | Retention: everything is kept forever (design §M); any limit needs its own decision. |
| O-7 | Which already-stored, pre-holdout data may the first producer adapters read? |
| O-8 | How is the shadow recorder's reserved `0010` resolved before this storage migration is numbered (§14)? |
| O-9 | Which concrete state-directory path and credential-source entry names will the implementation use, and is the directory inside or outside the repository (outside needs explicit authorization)? |

### 16.3 Blockers

- No clock-health producer or policy, so non-timeless freshness evaluates
  to `unknown`.
- No producer adapters.
- The empty setup-definition registry, by design.
- The sealed holdout (2026-09-23 → 2026-12-04).
- Migration numbering depends on the reserved `0010` (§14).
- **Future-contract blocker: unresolved conflict re-evaluation.**
  `evidence-envelope-1` needs an amendment before an unresolved conflict's
  severity can be updated append-only, because every successor
  conflict-status record must carry a resolution. Until a separately
  reviewed amendment defines a new append-only evaluation or status record
  that does not falsely mark a conflict resolved, any registry activation
  that would change an unresolved conflict's derivation is refused
  (companion design §8.4).

### 16.4 Authorization a later implementation would require

An explicit instruction that:
1. names this design and the companion registry-loading design at a
   reviewed commit;
2. explicitly accepts or rejects each **[STRICTER]** invariant in §16.1;
3. authorizes the storage repository, the migration (with its final
   number), the verification, recovery, reconciliation and key-rotation
   commands, the HMAC-authenticated anti-rollback checkpoint with its
   configured state directory and credential source, and synthetic tests
   on temporary databases;
4. states separately whether the migration may be **applied to the real
   database** (with a verified backup first) or only tested;
5. confirms no provider or network request, no model call, no
   holdout-window SPY data, and no producer adapter unless separately
   listed;
6. confirms the production setup-definition registry stays empty and grants
   no dashboard, assistant, `setup_detail` grant, ranking, notification,
   Options Strategy Agent, trading or execution authority.
