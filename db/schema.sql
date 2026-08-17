-- Studio Decision Ledger — analytical schema
--
-- Bitemporal by construction. Two independent time axes:
--
--   business time  — when a fact is true in the world (valid_from / valid_to)
--   system time    — when we came to know it (`revision`, monotonic)
--
-- Evidence tables are APPEND-ONLY. A correction to a fact inserts a new row
-- version carrying the same natural key and a higher `revision`; it never
-- updates or deletes the prior version. This is what makes SPEC invariant 2
-- enforceable at the storage layer rather than by convention.
--
-- A decision snapshot pins `max_revision`. Every point-in-time read filters
-- `revision <= max_revision` and keeps the latest surviving version per key,
-- which is why a replay can reproduce an outcome that current data would no
-- longer produce.
--
-- No table here is ever mutated. There are no ALTER UPDATE / DELETE paths in
-- the application. If you find yourself reaching for one, the invariant is
-- being broken.

CREATE DATABASE IF NOT EXISTS sdl;

-- ---------------------------------------------------------------------------
-- Evidence tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sdl.title_licenses
(
    license_id      String,
    title_id        String,
    territory_code  LowCardinality(String),          -- ISO-3166 alpha-2
    rights_scope    LowCardinality(String),          -- SVOD | AVOD | FAST | TVOD
    valid_from      DateTime64(3, 'UTC'),
    valid_to        DateTime64(3, 'UTC'),
    status          LowCardinality(String),          -- ACTIVE | SUSPENDED | TERMINATED
    revision        UInt64,
    recorded_at     DateTime64(3, 'UTC'),
    amendment_note  String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (title_id, territory_code, license_id, revision);

CREATE TABLE IF NOT EXISTS sdl.clearances
(
    clearance_id    String,
    title_id        String,
    asset_ref       String,                          -- e.g. cue title, stock shot id
    clearance_kind  LowCardinality(String),          -- MUSIC_SYNC | MUSIC_MASTER | STOCK_FOOTAGE | TALENT
    territory_code  LowCardinality(String),
    valid_from      DateTime64(3, 'UTC'),
    valid_to        DateTime64(3, 'UTC'),
    status          LowCardinality(String),          -- ACTIVE | EXPIRED | REVOKED
    revision        UInt64,
    recorded_at     DateTime64(3, 'UTC'),
    amendment_note  String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (title_id, territory_code, clearance_id, revision);

CREATE TABLE IF NOT EXISTS sdl.ratings
(
    rating_id       String,
    title_id        String,
    territory_code  LowCardinality(String),
    rating_code     LowCardinality(String),          -- territory-specific certificate
    issued_at       DateTime64(3, 'UTC'),
    expires_at      DateTime64(3, 'UTC'),
    status          LowCardinality(String),          -- VALID | EXPIRED | WITHDRAWN
    revision        UInt64,
    recorded_at     DateTime64(3, 'UTC'),
    amendment_note  String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (title_id, territory_code, rating_id, revision);

CREATE TABLE IF NOT EXISTS sdl.deliveries
(
    delivery_id             String,
    title_id                String,
    master_version          String,
    approved_at             Nullable(DateTime64(3, 'UTC')),
    captions_state          LowCardinality(String),  -- APPROVED | PENDING | ABSENT
    audio_description_state LowCardinality(String),  -- APPROVED | PENDING | ABSENT
    revision                UInt64,
    recorded_at             DateTime64(3, 'UTC'),
    amendment_note          String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (title_id, delivery_id, revision);

CREATE TABLE IF NOT EXISTS sdl.continuity_exceptions
(
    exception_id    String,
    title_id        String,
    scene_ref       String,
    severity        LowCardinality(String),          -- BLOCKING | ADVISORY
    state           LowCardinality(String),          -- OPEN | RESOLVED | WAIVED
    resolution_ref  String DEFAULT '',
    revision        UInt64,
    recorded_at     DateTime64(3, 'UTC'),
    amendment_note  String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (title_id, exception_id, revision);

-- ---------------------------------------------------------------------------
-- Policy
-- ---------------------------------------------------------------------------

-- A policy revision is immutable. `payload_sha256` is computed over the
-- canonical JSON of `rules_payload` and is what a decision record binds to;
-- the verifier recomputes it and refuses certification on mismatch.
CREATE TABLE IF NOT EXISTS sdl.policy_revisions
(
    policy_revision String,
    rules_payload   String,                          -- canonical JSON
    payload_sha256  String,
    effective_at    DateTime64(3, 'UTC'),
    recorded_at     DateTime64(3, 'UTC')
)
ENGINE = MergeTree
ORDER BY (policy_revision);

-- ---------------------------------------------------------------------------
-- Decision layer
-- ---------------------------------------------------------------------------

-- `max_revision` is the pin. `facts_json` is the manifest: one entry per
-- retrieval, carrying table_name, canonical_query, result_hash, row_count.
-- `source_manifest_hash` is the SHA-256 over that canonical manifest.
CREATE TABLE IF NOT EXISTS sdl.decision_snapshots
(
    snapshot_id          String,
    captured_at          DateTime64(3, 'UTC'),
    max_revision         UInt64,
    source_manifest_hash String,
    facts_json           String
)
ENGINE = MergeTree
ORDER BY (snapshot_id);

-- Append-only. A correction is a new decision_id referencing `supersedes`;
-- the superseded record keeps its original outcome, inputs and evidence.
--
-- `model_rationale` is an ARTIFACT, not evidence (SPEC invariant 5). It is
-- deliberately stored apart from the rule hits so that no query can mistake
-- model text for a determinant of the outcome.
CREATE TABLE IF NOT EXISTS sdl.decision_records
(
    decision_id               String,
    title_id                  String,
    territory_code            LowCardinality(String),
    effective_at              DateTime64(3, 'UTC'),

    policy_revision           String,
    policy_sha256             String,
    snapshot_id               String,

    outcome                   LowCardinality(String), -- AVAILABLE | HOLD | ESCALATE
    rule_hits                 Array(String),
    query_evidence            String,                 -- canonical JSON, per-retrieval bindings

    model_rationale           String DEFAULT '',
    model_config              String DEFAULT '',
    prompt_template_revision  String DEFAULT '',

    decided_at                DateTime64(3, 'UTC'),
    supersedes                String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (decision_id);

-- Verification attempts are themselves append-only history: a decision that
-- certified C2 last month and cannot be certified today is a finding worth
-- keeping, not a row to overwrite.
CREATE TABLE IF NOT EXISTS sdl.verification_attempts
(
    attempt_id       String,
    decision_id      String,
    attempted_at     DateTime64(3, 'UTC'),
    capability_class LowCardinality(String),          -- C2 | C3_BOUNDARY | NOT_CERTIFIED
    failed_requirement String DEFAULT '',             -- first failure, empty when certified
    detail           String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (decision_id, attempted_at);

-- Generation provenance recorded against an asset. `generation_kind` of NONE
-- is an assertion that the asset is not generated, and is deliberately a row
-- rather than an absent one: the evaluator distinguishes "recorded as not
-- generated" from "nothing on file", and only the latter is unknowable.
CREATE TABLE IF NOT EXISTS sdl.synthetic_content
(
    record_id                   String,
    title_id                    String,
    asset_ref                   String,                   -- e.g. scene or shot reference
    generation_kind             LowCardinality(String),   -- SYNTHETIC | ASSISTED | NONE
    tool_ref                    String,                   -- tool or model that produced it
    disclosure_obligation_ref   String,                   -- obligation recorded in policy
    revision                    UInt64,
    recorded_at                 DateTime64(3, 'UTC'),
    amendment_note              String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (title_id, record_id, revision);

-- Performer consent for use of likeness or voice. Not territory-partitioned in
-- the ORDER BY on purpose: consents are retrieved across territories so the
-- evaluator can tell a consent that does not cover this request (HOLD) from no
-- consent at all (ESCALATE).
CREATE TABLE IF NOT EXISTS sdl.performer_consents
(
    consent_id      String,
    title_id        String,
    performer_ref   String,
    consent_scope   LowCardinality(String),           -- likeness | voice | both
    territory_code  LowCardinality(String),
    valid_from      DateTime64(3, 'UTC'),
    valid_to        DateTime64(3, 'UTC'),
    status          LowCardinality(String),           -- ACTIVE | WITHDRAWN | EXPIRED
    revision        UInt64,
    recorded_at     DateTime64(3, 'UTC'),
    amendment_note  String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (title_id, consent_id, revision);
