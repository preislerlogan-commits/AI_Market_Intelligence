# Versioned Registry Loading and Activation — Design

**Status: REVIEWED DESIGN — accepted by the merge that introduces this
document. DESIGN ONLY. Not implemented. Not authorized.** This document
specifies how reviewed Evidence Registry versions, and future
setup-definition registries, would be written as files, parsed, identified,
registered and activated. Its companion,
[EVIDENCE_CARD_STORAGE_DESIGN.md](EVIDENCE_CARD_STORAGE_DESIGN.md), proposes
the tables they are registered into.

- **Nothing exists.** No registry file, loader, operator command, table or
  activation has been created. `registry-draft-0`
  ([EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md)) remains prose and is loaded
  nowhere.
- **The merge accepts the design only.** Nothing it describes has been
  implemented. The merge does **not** authorize migrations, database
  changes, registry files or activation, storage writers or readers,
  checkpoint creation, credentials or HMAC keys, producer adapters, setup
  definitions, evidence or card collection, dashboard or assistant access,
  machine-decision mode, notifications, or trading or execution. Any
  implementation needs a new, separate authorization (§14.4).
- **Open items stay open.** The open questions and blockers in §14.2 and
  §14.3 are unresolved.
- **The setup-definition registry stays empty.** This design makes a
  non-empty setup-definition registry **unrepresentable** in its file format
  (§10). Adding a definition needs separate research, review, authorization
  and a code change, never a file edit alone.
- **The Contract Selector's authority is permanent.** No file, field or
  activation can transfer or share contract-classification authority (§11).
- **Governing documents win**, as in the companion design.

---

## 1. Summary

| Question | Answer |
|---|---|
| File format | Strict JSON in the repository, in the existing display form (`indent=2`, sorted keys, trailing newline), wrapped with a file-format tag and the declared registry identity (§3) |
| Why not YAML or TOML? | YAML's implicit typing is ambiguous and needs a new dependency. TOML has no `null`, while unset freshness parameters must be explicit nulls. JSON is in the standard library and matches the existing file writers |
| Parsing | Duplicate keys, floats, NaN/Infinity, BOM, non-UTF-8, trailing data and unknown fields are refused; the file must equal its own canonical rendering byte for byte (§4) |
| Identity | Evidence registries keep the existing `evr1_` (SHA-256 over the whole `EvidenceRegistry` dump). Setup-definition registries get a new `sdr1_` (§5) |
| Registration versus activation | Two separate, append-only acts: *register* stores reviewed content; *activate* appends an activation record naming a registered version and an effective time (§6) |
| Effective time | `effective_from_utc` is never earlier than the activation's own commit instant, so no activation is backdated and past answers never change (§7) |
| Rollback | Activate a prior version again, with reason `rollback`. History is never rewritten (§9) |
| Startup | Fails closed when the active registry of either kind is missing, invalid, unverifiable or ambiguous (§8) |
| Runtime editing | None. Only the dry-run-first operator command can register or activate, from committed repository files. The dashboard and assistant have no path (§12) |

---

## 2. What the design must match (code facts)

| Fact | Where |
|---|---|
| `EvidenceRegistry` is strict (`extra="forbid"`, frozen), with sorted, unique tables | `evidence/registry.py` |
| `evr1_` = `prefixed_id("evr1_", registry.model_dump(mode="json"))`, over **all** fields including `registry_label` | `compute_registry_version_id` |
| Scenario definitions, strength rubrics, authorization records and inference-input authorizations are **structurally empty** (`max_length=0`) | `EvidenceRegistry` |
| No producer may hold directional authority; inference emission cannot be machine-decision eligible | `ProducerEntry._check_entry` |
| Only `deterministic_contract_selector`, deterministic only, may register the contract-classification payload | `ProducerEntry._check_entry`, `selector_boundary.py` |
| Every payload schema must declare `spy_price_content`; status payloads carry none | `PayloadSchemaEntry`, `EvidenceRegistry._check_registry` |
| Payload validation needs a **code** model for each schema; a schema without one is refused | `validation._validate_payload` (`unknown_payload_schema`) |
| `SetupDefinition` allows only the VWAP lane, `setup_evaluation.v1`, and `inference_allowed = False` | `setup_cards/definitions.py` |
| `REGISTERED_SETUP_DEFINITIONS = ()` | `setup_cards/definitions.py` |
| The registry has no effective time of its own; activation is a separate append-only log keyed by version and `effective_from_utc` | design §L.3 |

---

## 3. File format

### 3.1 Location

| Registry kind | Path (repository-relative) |
|---|---|
| Evidence registry | `registries/evidence/<registry_label>.json` |
| Setup-definition registry | `registries/setup_definitions/<registry_label>.json` |

- The file name must equal the content's `registry_label`
  (`registry_label_filename_mismatch`).
- The loader resolves paths only inside this repository's `registries/`
  directory, refuses symlinks, and never reads outside the repository
  (CLAUDE.md "No access outside this repository").
- Each file is reviewed through a normal pull request. A companion Markdown
  note may explain a version's rationale; it is never parsed.

### 3.2 Evidence-registry file

```json
{
  "file_format": "evidence-registry-file-1",
  "registry": {
    "authorization_records": [],
    "clock_policy": { "…": "…" },
    "component_tokens": [],
    "consumer_grants": [],
    "freshness_policies": [ "…" ],
    "inference_input_authorizations": [],
    "limitation_codes": [],
    "payload_schemas": [ "…" ],
    "producers": [ "…" ],
    "registry_label": "evidence-registry-1",
    "scenario_definitions": [],
    "selection_rules": [],
    "strength_rubrics": [],
    "uncertainty_codes": []
  },
  "registry_version_id": "evr1_<64 hex>"
}
```

(Synthetic and abbreviated; `…` marks omitted content.)

- `registry` is exactly `EvidenceRegistry.model_dump(mode="json")`.
- `registry_version_id` is **declared** so reviewers see the identity in
  the diff. The loader recomputes it and refuses a mismatch
  (`registry_id_mismatch`); the declared value never overrides the
  computed one.
- Unset freshness or clock parameters are explicit `null`s, never omitted.

### 3.3 Setup-definition-registry file

```json
{
  "file_format": "setup-definition-registry-file-1",
  "registry": {
    "definitions": [],
    "registry_label": "setup-definitions-empty-0"
  },
  "registry_version_id": "sdr1_<64 hex>"
}
```

`registry` is a new strict contract, `SetupDefinitionRegistry`
(`registry_label`, `definitions`), whose `definitions` list has
`max_length=0` in this version (§10).

### 3.4 Canonical file bytes

A file is accepted only if its bytes equal

```
json.dumps(wrapper_dump, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
```

encoded as UTF-8, where `wrapper_dump` is the parsed wrapper re-dumped in
JSON mode. So:
- there is one valid rendering per version, and review diffs are stable;
- hidden differences (key order, spacing, escapes, number spellings) are
  impossible;
- set-like lists stay sorted, because the models already require it.

---

## 4. Strict parsing

The loader runs these steps in order, refusing with the first failing
token. Every token is bounded; no message includes file content.

| # | Step | Refusal token |
|---|---|---|
| 1 | Path inside `registries/<kind>/`, no symlink, file exists | `registry_file_not_found`, `registry_path_outside_repository` |
| 2 | Size ≤ 1 MiB | `registry_file_too_large` |
| 3 | UTF-8 without a BOM | `registry_file_not_utf8` |
| 4 | JSON parse with duplicate keys refused, `parse_float` and `parse_constant` refusing floats, NaN and Infinity, no trailing data, nesting depth ≤ 32 | `registry_json_invalid`, `registry_duplicate_key`, `registry_float_not_allowed` |
| 5 | `file_format` is a known literal | `registry_file_format_unknown` |
| 6 | The wrapper and its `registry` validate strictly (unknown fields refused, strict types, the models' own invariants) | `registry_schema_invalid` |
| 7 | The bytes equal the canonical rendering (§3.4) | `registry_not_canonical_form` |
| 8 | The recomputed ID equals `registry_version_id` | `registry_id_mismatch` |
| 9 | The file name equals `registry_label` | `registry_label_filename_mismatch` |
| 10 | Code compatibility (§8.3) | the §8.3 tokens |

Pydantic `ValidationError` text is discarded and replaced by
`registry_schema_invalid`, because it can quote input values.

---

## 5. Identity and hashing

| Registry kind | Identity | Computed over |
|---|---|---|
| Evidence registry | `evr1_<sha256>` | `EvidenceRegistry.model_dump(mode="json")`, canonical compact JSON (existing `compute_registry_version_id`, unchanged) |
| Setup-definition registry | `sdr1_<sha256>` (**new prefix**, proposed) | `SetupDefinitionRegistry.model_dump(mode="json")`, the same canonical encoding |
| Activation | `rga1_<sha256>` (**new prefix**, proposed) | the activation content except its own ID and the operational `activated_at_utc` (§6.2) |

- **Deterministic.** The same content always yields the same ID, on any
  machine, whatever the file's history.
- **The label is content.** Because `evr1_` covers `registry_label`, two
  files that differ only in label are different versions. A label may never
  be reused for different content (`registry_label_reused`).
- **The file wrapper is not in the identity.** `file_format` and the
  declared ID are checked but not hashed. The file's own SHA-256 is recorded
  at registration as provenance.
- **Registry content has no time.** When it applies is decided only by the
  activation log (design §L.3).

---

## 6. Registration and activation records

Both are written by the `registry_operator` writer through the
dry-run-first command (§12), in separate transactions, into the tables of
the companion design.

### 6.1 `registry_versions` (register = store reviewed content)

| Column | Type | Rule |
|---|---|---|
| `registry_version_id` | `VARCHAR` | primary key; `evr1_` or `sdr1_` pattern matching `registry_kind` |
| `registry_kind` | `VARCHAR NOT NULL` | `CHECK` in `evidence_registry`, `setup_definition_registry` |
| `registry_label` | `VARCHAR NOT NULL` | unique together with `registry_kind` |
| `file_format` | `VARCHAR NOT NULL` | `CHECK` in the known formats |
| `content_json` | `VARCHAR NOT NULL` | canonical compact JSON of the content (the identity form) |
| `content_sha256` | `VARCHAR NOT NULL` | equals the ID's hex part |
| `source_path` | `VARCHAR NOT NULL` | repository-relative, `^registries/(evidence|setup_definitions)/[a-z0-9][a-z0-9._-]{0,63}\.json$` |
| `source_file_sha256` | `VARCHAR NOT NULL` | SHA-256 of the file bytes |
| `source_commit_sha` | `VARCHAR NOT NULL` | full 40-hex commit containing exactly those bytes at that path |
| `recorded_at_utc`, `commit_seq` | | as in the companion design |

Registration rules:
- The working tree must be clean and the file committed at
  `source_commit_sha` with identical bytes (`registry_not_committed`).
- Registering an already registered ID is an idempotent no-op.
- Registering does **not** make a version active.

### 6.2 `registry_activations` (activate = append to the log)

| Column | Type | Rule |
|---|---|---|
| `activation_id` | `VARCHAR` | primary key; `rga1_` + SHA-256 of the fields below except `activated_at_utc` |
| `registry_kind` | `VARCHAR NOT NULL` | as above |
| `registry_version_id` | `VARCHAR NOT NULL` | must be registered, of the same kind |
| `effective_from_utc` | `TIMESTAMP NOT NULL` | unique together with `registry_kind`; `CHECK (effective_from_utc >= activated_at_utc)` |
| `activation_reason` | `VARCHAR NOT NULL` | `CHECK` in `initial_activation`, `version_upgrade`, `rollback` |
| `supersedes_activation_id` | `VARCHAR UNIQUE` | null only for the first activation of a kind; otherwise the current tip of that kind |
| `authorization_ref` | `VARCHAR NOT NULL` | a bounded token naming the recorded authorization for this activation (for example a PROJECT_STATE item token); `^[a-z][a-z0-9_]{0,63}$` |
| `activated_at_utc` | `TIMESTAMP NOT NULL` | the commit instant (equals `recorded_at_utc`) |
| `commit_seq` | `BIGINT NOT NULL` | |

Rules:
- **Linear log per kind.** Each new activation supersedes the current tip
  (`activation_not_tip`); the unique `supersedes_activation_id` makes a
  branch impossible.
- **Reasons are checked.** `initial_activation` only for the first record
  of a kind; `rollback` only to a version that was active before;
  `version_upgrade` otherwise.
- **Same version again** is allowed only as `rollback` (a version is never
  "re-upgraded" to itself while active: `activation_no_change`).
- Activation writes no evidence, bundle or card.
- **Before appending**, every activation runs the checks of §8.3 and, when
  triggered, the unresolved-conflict stability check of §8.4.
- **Completion.** Registration and activation each end with one
  authoritative commit (the row plus its `registry_version_registered` or
  `registry_activated` audit event). Each is complete only after the
  anti-rollback checkpoint covers that commit (storage design §6.8). A
  checkpoint failure is completed through reconciliation, never by
  re-appending the row.

---

## 7. Effective-time and activation-time semantics

- **Two times.** `activated_at_utc` is when the activation was recorded.
  `effective_from_utc` is when the version governs. The first is
  operational; the second is substantive and part of the activation ID.
- **No backdating.** `effective_from_utc ≥ activated_at_utc`
  (`activation_backdated`). An activation therefore never changes which
  version governed any instant already in the past, and every stored bundle
  keeps a valid "registry in force at `as_of_utc`".
- **Future activation** is allowed (for example the next session open). Until
  it takes effect, the previous version stays in force.
- **In force at T:** among activations of that kind with
  `effective_from_utc ≤ T`, the one with the greatest `effective_from_utc`.
  The uniqueness of `(registry_kind, effective_from_utc)` means there is at
  most one. None → no registry is in force at T.
- **What uses it.**
  - Envelope writes require the envelope's `registry_version_id` to be the
    evidence registry in force at the write's commit instant (otherwise
    `registry_version_mismatch`, and the producer re-emits).
  - Bundles require the one in force at their `as_of_utc`.
  - Cards are validated with the setup-definition registry in force at
    their bundle's `as_of_utc`, and the card's storage row records that
    exact `sdr1_` version. Reconstruction and verification load that
    recorded version, never the one active now (storage design §5.7).
  - Past bundles are reproduced with the version they name, never with
    today's.

Example (synthetic):

| Activation | Version | `activated_at_utc` | `effective_from_utc` | In force |
|---|---|---|---|---|
| A1 `initial_activation` | V1 | 2027-01-04 13:00Z | 2027-01-04 14:00Z | from 14:00Z on Jan 4 |
| A2 `version_upgrade` | V2 | 2027-01-08 22:00Z | 2027-01-11 14:00Z | from Jan 11 14:00Z |
| A3 `rollback` | V1 | 2027-01-11 15:10Z | 2027-01-11 15:10Z | from Jan 11 15:10Z |

A bundle at 2027-01-11 15:00Z names V2 for ever. A bundle at 15:20Z names
V1. Nothing recorded later changes either answer.

---

## 8. Fail-closed startup and validation

### 8.1 Startup

Before the store serves any read or write (the health check and
verification excepted), the storage design's anti-rollback checkpoint is
checked first (storage design §10.6). Then, for **each** registry kind:

| Check | Refusal |
|---|---|
| the activation records verify (record hashes, the store commit chain, one linear log per kind, no backdating) | `active_registry_invalid` |
| exactly one version is in force now | `active_registry_missing` (none) / `active_registry_ambiguous` (more than one, which the constraints should make impossible) |
| its `registry_versions` row exists, and `content_json` re-parses strictly and re-hashes to its ID | `active_registry_invalid` |
| code compatibility (§8.3) holds for the running build | the §8.3 token |
| the setup-definition registry in force has no definitions, and equals `REGISTERED_SETUP_DEFINITIONS` | `setup_definitions_not_authorized` |

Every failure refuses to open the store for evidence, bundle and card
operations (`store_open_refused`). There is no fallback to a default, a
"latest file", an in-code registry, or the previous version. Two kinds of
failure differ in what follows:

- **Integrity stop (read-refused mode, companion design §10.3):** an
  activation record that fails verification, an ambiguous in-force answer,
  or registered content that no longer re-hashes to its ID
  (`invalid_registry_activation`, `authoritative_chain_ambiguity`). These
  mean authoritative data is wrong and need human review.
- **Not yet serviceable:** no activation exists yet
  (`active_registry_missing`), or the running build lacks a payload model.
  Nothing is corrupt; the store serves only the health check and
  verification until an operator registers and activates a valid version or
  deploys a compatible build.

The runtime loads registry content from the verified `registry_versions`
row, not from the working tree, so a later edit to a file cannot change a
running or past registry. A file whose bytes differ from the registered
content under the same label is refused at registration
(`registry_label_reused`).

### 8.2 What the in-force registry governs

- Producers, versions, kinds, subjects and payload schemas accepted at
  write.
- Freshness and clock policies, selection rules and consumer grants used by
  bundles at their `as_of_utc`.
- Nothing retroactively: stored items keep the eligibility derived at
  emission (design §B.3).

### 8.3 Checks at registration and activation

Checks that concern history compare the proposed version against the
**complete prior activation history** of its kind: every version ever
activated, including superseded and rolled-back ones, not only the version
in force.

| Check | Token |
|---|---|
| every payload schema in the registry has a payload model in the running build | `payload_model_missing` |
| no payload schema or freshness policy referenced by any stored item is removed | `registry_drops_referenced_schema`, `registry_drops_referenced_policy` |
| **price-declaration monotonicity:** once any activated version has registered a payload schema ID with `spy_price_content = true`, no later version may register that ID as `false` | `holdout_declaration_weakened` |
| a payload schema ID first activated with `false` may not later be registered as `true`, because that would reclassify evidence already stored under it; a material change in price-content meaning, in either direction, needs a **new payload-schema ID** | `holdout_declaration_changed` |
| the structurally empty tables are empty, no producer has directional authority, and no inference rule is machine-decision eligible (already enforced by the model) | `registry_schema_invalid` |
| contract classification only by the deterministic selector (already enforced by the model) | `registry_schema_invalid` |
| every consumer grant names a registered `ConsumerId` (already enforced by the model) | `registry_schema_invalid` |
| setup-definition registry: no definitions | `setup_definitions_not_authorized` |

**Grant changes are a human review gate.** The dry run prints every
consumer-grant difference from the version in force (for the initial
activation, every grant). Code cannot judge whether an authorization covers
a grant, so the recorded authorization named by `authorization_ref` must
list each grant change explicitly, and the operator must not activate a
change it does not list. Today no grant widening is authorized; in
particular the proposed read-only assistant `setup_detail` grant remains
unapproved.

### 8.4 Unresolved-conflict stability check (evidence registry)

**Why.** Under `evidence-envelope-1`, a conflict-status successor must carry
a resolution, so an unresolved conflict's stored severity cannot be updated
append-only (storage design §6.3.1). A registry activation that changed
what an unresolved conflict's severity derives from would therefore leave a
**stale** stored severity in front of machine-decision consumers. This check
refuses such an activation instead.

**When it runs.** Before activating any Evidence Registry version, rollbacks
included, that differs from the version in force in any of these ways:
- it authorizes a machine-decision bundle purpose (a purpose becomes both
  granted to a consumer with `machine_decision_mode_allowed = true` and
  covered by a selection rule);
- it adds, removes or changes a requirement of an authorized
  machine-decision purpose;
- it changes which consumers may use machine-decision mode.

**What it does.** Inside the activation's own write transaction (so no
conflict can be appended between check and activation), for every conflict
chain whose current tip has status `unresolved`:
1. recompute the matched requirement IDs under the **proposed** registry,
   using the derivation of storage design §6.3.1, steps 3–4;
2. recompute `involves_required_item`;
3. recompute severity with the tip's recorded `severity_rule_version`, or
   with the proposed activation's accepted rule version if one is
   declared;
4. compare all three with the tip's stored wrapper values.

**Refusal.** If any unresolved conflict's matched requirement set,
`involves_required_item` or severity would change, the activation is
refused with `unresolved_conflict_requires_contract_amendment`.
- Ordinary output shows only that token and the number of affected
  unresolved conflicts. It shows no conflict ID, item ID, subject or
  evidence value. Identifying the affected conflicts is an operator task
  through the separately authorized audit path.
- Nothing is written except the bounded refusal audit event.
- Such an activation stays blocked until a separately reviewed Evidence
  Envelope amendment defines how an unresolved conflict can receive a new
  append-only evaluation or status record without falsely marking it
  resolved.

**Immediate effect.** A triggering activation must take effect at its own
commit instant: its `effective_from_utc` is set to that commit's time, and
a future-dated one is refused
(`machine_decision_activation_must_take_effect_immediately`). This closes
the window in which a conflict recorded between activation and effective
time could become stale. The storage design separately refuses a new
unresolved conflict whose derivation has already changed by the time it is
recorded (`conflict_evaluation_stale`).

**Allowed.** A registry change that alters no unresolved conflict's derived
inputs proceeds if every other activation rule passes. A change that
triggers nothing above does not run the check at all.

**Stated plainly.**
- No machine-decision mode is authorized today: no consumer grant has
  `machine_decision_mode_allowed = true`, and the setup ranker and
  notification layer are unauthorized.
- The registry tables required to be empty stay empty.
- This rule authorizes **no** machine decision. It only prevents a future
  activation from silently making a stored unresolved-conflict severity
  stale.
- Historical conflict records are never rewritten, whatever the outcome.

---

## 9. Rollback

- **Rollback is a new activation** of a previously active version, with
  reason `rollback` and a new `effective_from_utc` at or after its own
  recording.
- **Nothing is rewritten.** The superseded activation, the rolled-back
  version's content, and every bundle built under it remain stored and
  reproducible.
- **Evidence written under the rolled-back version stays.** Items keep the
  `validated_registry_version_id` they were accepted under. If the restored
  version lacks a schema or policy those items reference, the activation is
  refused (§8.3), so rollback cannot make stored evidence unreadable.
- **A bad version is never deleted.** It stays registered and visible in
  the activation history, like any null or failed finding.

---

## 10. The setup-definition registry stays empty

- `SetupDefinitionRegistry.definitions` has `max_length=0` in this design,
  so a file with any definition fails strict parsing
  (`registry_schema_invalid`) before registration.
- Startup requires the in-force setup-definition registry to equal
  `REGISTERED_SETUP_DEFINITIONS`, which is `()`.
- The only version this design expects to exist is an empty one (for
  example `setup-definitions-empty-0`), activated so that "no definitions"
  is an explicit, versioned, auditable state rather than an absence.
- **Adding a definition** would require, in order: its own research and
  preregistration where the research protocols demand it, a reviewed
  definition, an explicit authorization, and a reviewed code change lifting
  `max_length=0` and the in-code constant. A file edit alone can never add
  one.
- With the registry empty, the storage design refuses every card of either
  kind (companion §6.6).

---

## 11. The Contract Selector's authority cannot move

- `ProducerEntry` refuses the contract-classification payload for any
  producer other than `deterministic_contract_selector`, and refuses a
  non-deterministic path for the selector.
- The file format has no field for contract-eligibility authority, and
  unknown fields are refused, so none can be smuggled in.
- `authorization_records` and `inference_input_authorizations` are
  structurally empty; even when a future version opens them, "contract
  eligibility" is not a grantable record type
  ([EVIDENCE_REGISTRY.md §5](EVIDENCE_REGISTRY.md)).
- A future `future_contract_ranker` entry could rank only within the
  selector's returned sets (design §H.3). No registry content can widen,
  replace or share eligibility.

---

## 12. No runtime editing

- **Only one path writes.** A future operator command,
  `scripts/manage_registry.py`, dry-run by default:
  - `register --kind … --file registries/…` validates (§4, §8.3) and prints
    the computed ID, the file SHA-256 and the commit; `--execute` stores it.
  - `activate --kind … --version … --effective-from … --reason …
    --authorization-ref …` prints the resulting in-force timeline and the
    §8.4 result (triggered or not; if refused, the token and a count only);
    `--execute` appends the activation.
  - A command reports success only after the anti-rollback checkpoint covers
    its final commit (storage design §6.8).
- **Human operator only.** It runs manually, from a clean working tree. It
  is not scheduled and not callable by any agent at runtime.
- **No other surface.** The dashboard, assistant, notification layer,
  rankers and producer adapters have no method to register, activate,
  edit, or read-and-rewrite a registry. Registry changes are never made
  from a UI, a chat message or a model output.
- **Output is sanitized.** The command prints IDs, labels, times, reasons
  and refusal tokens only.

---

## 13. Proposed contracts and enums

| Contract / enum | Content |
|---|---|
| `EvidenceRegistryFile` | `file_format: Literal["evidence-registry-file-1"]`, `registry: EvidenceRegistry`, `registry_version_id: RegistryVersionId` |
| `SetupDefinitionRegistry` | `registry_label: VersionLabel`, `definitions: list[SetupDefinition]` with `max_length=0` |
| `SetupDefinitionRegistryFile` | `file_format: Literal["setup-definition-registry-file-1"]`, `registry: SetupDefinitionRegistry`, `registry_version_id` (`^sdr1_[0-9a-f]{64}$`) |
| `RegistryVersionRecord` | the §6.1 columns |
| `RegistryActivation` | the §6.2 columns |
| `RegistryKind` | `evidence_registry`, `setup_definition_registry` |
| `ActivationReason` | `initial_activation`, `version_upgrade`, `rollback` |
| `RegistryRefusalReason` | every token in §4, §6, §8, including `unresolved_conflict_requires_contract_amendment` and `machine_decision_activation_must_take_effect_immediately` (§8.4) |

All strict and frozen.

---

## 14. Test matrix, open questions, blockers and authorization

### 14.1 Future implementation test matrix (listed, not created)

| Area | Tests |
|---|---|
| Parsing | duplicate key, float, NaN, Infinity, BOM, non-UTF-8, trailing data, excessive depth or size, unknown field at every level, unknown `file_format` all refused with their tokens |
| Canonical form | a re-indented, re-ordered or re-escaped file is refused; the canonical rendering round-trips byte for byte |
| Identity | golden `evr1_` and `sdr1_` fixtures; label change → new ID; declared ID mismatch refused; the same content on two paths gives one ID |
| Paths | outside `registries/`, symlink, parent traversal, or name ≠ label refused |
| Registration | uncommitted or dirty file refused; re-registering is a no-op; a label reused with different content refused |
| Activation | first activation must be `initial_activation`; backdated refused; duplicate effective time refused; non-tip supersession refused; unregistered version refused |
| In force | boundary instants; future activation not yet in force; rollback timeline as in §7 |
| Startup | invalid, hash-mismatched or ambiguous active registry → integrity stop (read-refused); no activation yet or no payload model → store not serviceable, no stop; no fallback in any case |
| Compatibility | missing payload model; dropped referenced schema or policy — each refused; the dry run lists every grant difference |
| Unresolved-conflict check | first machine-decision authorization with no unresolved conflicts is allowed; authorization refused when an unresolved conflict would become required; a requirement change refused when it changes an unresolved conflict's matched requirement IDs; a consumer-grant change refused when it changes an unresolved conflict's severity; activation allowed when every unresolved derivation is unchanged; a rollback that triggers the check is checked too; a future-dated triggering activation refused; no historical conflict record is rewritten in any case; refusal output holds only the bounded token and a count; today's empty machine-decision grants and empty required tables are unchanged by any of these tests |
| Completion | registration and activation report success only after the checkpoint covers their commit; a checkpoint failure completes through reconciliation without repeating the row |
| Price declarations | a schema ID declared `true` in any earlier activated version (including a superseded or rolled-back one) and `false` in the proposal is refused; `false` → `true` is refused; a new schema ID with either value is accepted |
| Card binding | a card's recorded `sdr1_` is loaded for verification after a later activation; the active version is never substituted |
| Setup definitions | any definition in a file refused; in-force registry must equal `()`; storage refuses every card |
| Selector authority | a file giving the classification payload to another producer, or a non-deterministic selector path, is refused |
| No runtime editing | no consumer module imports the operator path or a writer; the command is dry-run by default |
| Sanitization | no refusal message contains file content or a validation message |
| Point in time | bundles built before and after an activation reproduce with their own versions |

### 14.2 Open questions

| # | Question |
|---|---|
| R-1 | Is `registries/` at the repository root the right location, or should it sit under `market_intelligence/`? |
| R-2 | Should the first machine-readable evidence registry translate `registry-draft-0` exactly, or wait for the live freshness and clock parameters? (Proposed: translate exactly, with unset parameters as `null`, since unset evaluates to `unknown`.) |
| R-3 | What form should `authorization_ref` take: a PROJECT_STATE item token, or a commit SHA of the authorizing record? |
| R-4 | Should activation require a second confirmation step (a separate `--confirm` token), beyond dry-run-first? |
| R-5 | Is a registry *retirement* flow ever needed? (This design never removes a schema or policy that stored evidence references.) |
| R-6 | The new `sdr1_` and `rga1_` prefixes need review, since prefixes are part of every identity. |

### 14.3 Blockers

- No evidence registry has been authorized as machine-readable content.
- `clock_policy.v0` and the live intraday freshness parameters are unset.
- The storage tables of the companion design do not exist.
- **Future-contract blocker.** Any activation that would change an
  unresolved conflict's derived severity inputs stays blocked (§8.4) until a
  reviewed Evidence Envelope amendment allows an append-only unresolved
  re-evaluation.

### 14.4 Authorization a later implementation would require

An explicit instruction that:
1. names this design and the storage design at a reviewed commit;
2. authorizes the file contracts, the strict loader, the operator command
   and synthetic tests on temporary files and databases;
3. states separately whether any **real** registry file may be committed,
   which version, and whether any activation may be recorded in the real
   database;
4. confirms the setup-definition registry stays empty and that no consumer
   grant is widened (including the proposed assistant `setup_detail` grant);
5. confirms no provider, model or holdout access, and no dashboard,
   assistant, ranking, notification, Options Strategy Agent, trading or
   execution authority.
