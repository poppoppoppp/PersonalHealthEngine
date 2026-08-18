CREATE TABLE personal_context (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    context_date            TEXT NOT NULL,
    context_type            TEXT NOT NULL,
    body_part               TEXT,
    severity                TEXT,
    raw_text                TEXT,
    source                  TEXT NOT NULL CHECK (source = 'USER_REPORTED'),
    status                  TEXT NOT NULL DEFAULT 'CURRENT' CHECK (status IN ('CURRENT', 'SUPERSEDED')),
    supersedes_id           INTEGER,
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,

    FOREIGN KEY (supersedes_id) REFERENCES personal_context(id)
);

CREATE INDEX idx_context_date ON personal_context(context_date, status);

CREATE TABLE context_revisions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id              INTEGER NOT NULL,
    revision_kind           TEXT NOT NULL CHECK (revision_kind IN ('CORRECTION', 'DELETION')),
    prior_json              TEXT,
    new_json                TEXT,
    created_at_utc          TEXT NOT NULL,

    FOREIGN KEY (context_id) REFERENCES personal_context(id)
);

CREATE TABLE user_feedback (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type            TEXT NOT NULL CHECK (subject_type IN ('DAILY_REASONING', 'HYPOTHESIS')),
    subject_id              INTEGER NOT NULL,
    feedback_status         TEXT NOT NULL CHECK (feedback_status IN ('CONFIRMED', 'REJECTED', 'CORRECTED')),
    correction_text         TEXT,
    source                  TEXT NOT NULL CHECK (source = 'USER_FEEDBACK'),
    created_at_utc          TEXT NOT NULL
);

CREATE INDEX idx_feedback_subject ON user_feedback(subject_type, subject_id);

CREATE TABLE evidence_bundles (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date           TEXT NOT NULL,
    bundle_json             TEXT NOT NULL,
    bundle_sha256           TEXT NOT NULL,
    evidence_definition_id  TEXT NOT NULL,
    evidence_definition_version TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'CURRENT' CHECK (status IN ('CURRENT', 'STALE')),
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_evidence_bundle_current
ON evidence_bundles (analysis_date, evidence_definition_id, evidence_definition_version)
WHERE status = 'CURRENT';

CREATE TABLE hypotheses (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date           TEXT NOT NULL,
    evidence_bundle_id      INTEGER NOT NULL,
    hypothesis_type         TEXT NOT NULL,
    rank                    INTEGER NOT NULL,
    supporting_json         TEXT,
    counter_json            TEXT,
    missing_json            TEXT,
    confidence              TEXT NOT NULL CHECK (confidence IN ('VERY_LOW', 'LOW', 'MODERATE', 'HIGH')),
    reasoning_summary       TEXT,
    origin                  TEXT NOT NULL CHECK (origin IN ('CANDIDATE', 'MODEL')),
    status                  TEXT NOT NULL DEFAULT 'CURRENT' CHECK (status IN ('CURRENT', 'STALE')),
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,

    FOREIGN KEY (evidence_bundle_id) REFERENCES evidence_bundles(id)
);

CREATE INDEX idx_hypotheses_date ON hypotheses(analysis_date, status);

CREATE TABLE daily_reasoning (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date           TEXT NOT NULL,
    evidence_bundle_id      INTEGER NOT NULL,
    overall_state           TEXT NOT NULL CHECK (overall_state IN ('STABLE', 'MILD_CHANGE', 'NOTABLE_CHANGE', 'INSUFFICIENT_EVIDENCE')),
    primary_hypothesis_type TEXT,
    secondary_hypothesis_type TEXT,
    confidence              TEXT NOT NULL CHECK (confidence IN ('VERY_LOW', 'LOW', 'MODERATE', 'HIGH')),
    recommended_actions_json TEXT,
    medical_review_state    TEXT NOT NULL CHECK (medical_review_state IN ('REQUIRED', 'PERFORMED', 'BYPASSED', 'UNAVAILABLE')),
    reasoning_model         TEXT,
    medical_model           TEXT,
    reasoning_summary       TEXT,
    definition_id           TEXT NOT NULL,
    definition_version      TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'CURRENT' CHECK (status IN ('CURRENT', 'STALE')),
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,

    FOREIGN KEY (evidence_bundle_id) REFERENCES evidence_bundles(id)
);

CREATE UNIQUE INDEX uq_daily_reasoning_current
ON daily_reasoning (analysis_date, definition_id, definition_version)
WHERE status = 'CURRENT';

CREATE TABLE qa_sessions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text           TEXT NOT NULL,
    asked_at_utc            TEXT NOT NULL,
    evidence_bundle_id      INTEGER,
    question_bundle_sha256  TEXT NOT NULL,
    answer_json             TEXT,
    answer_text             TEXT,
    medical_review_state    TEXT NOT NULL CHECK (medical_review_state IN ('REQUIRED', 'PERFORMED', 'BYPASSED', 'UNAVAILABLE')),
    reasoning_model         TEXT,
    status                  TEXT NOT NULL DEFAULT 'CURRENT' CHECK (status IN ('CURRENT', 'FAILED')),
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,

    FOREIGN KEY (evidence_bundle_id) REFERENCES evidence_bundles(id)
);

CREATE TABLE model_invocations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    adapter_kind            TEXT NOT NULL CHECK (adapter_kind IN ('REASONING', 'MEDICAL')),
    model_id                TEXT NOT NULL,
    request_sha256          TEXT NOT NULL,
    response_sha256         TEXT,
    status                  TEXT NOT NULL CHECK (status IN ('PASS', 'INVALID', 'TIMEOUT', 'ERROR', 'UNAVAILABLE')),
    token_input             INTEGER,
    token_output            INTEGER,
    duration_ms             INTEGER,
    error_code              TEXT,
    created_at_utc          TEXT NOT NULL
);

CREATE TABLE medical_reviews (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type            TEXT NOT NULL CHECK (subject_type IN ('DAILY_REASONING', 'QA')),
    subject_id              INTEGER NOT NULL,
    review_state            TEXT NOT NULL CHECK (review_state IN ('REQUIRED', 'PERFORMED', 'BYPASSED', 'UNAVAILABLE')),
    trigger_reason          TEXT,
    findings_json           TEXT,
    reviewer_model          TEXT,
    created_at_utc          TEXT NOT NULL
);

CREATE TABLE personal_patterns (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key             TEXT NOT NULL UNIQUE,
    trigger_context_type    TEXT NOT NULL,
    outcome_signal          TEXT NOT NULL,
    support_count           INTEGER NOT NULL CHECK (support_count >= 0),
    total_count             INTEGER NOT NULL CHECK (total_count >= 0),
    maturity                TEXT NOT NULL CHECK (maturity IN ('OBSERVING', 'ESTABLISHED')),
    first_seen_date         TEXT,
    last_seen_date          TEXT,
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL
);

CREATE TABLE reasoning_provenance (
    subject_type            TEXT NOT NULL CHECK (subject_type IN ('EVIDENCE_BUNDLE', 'DAILY_REASONING', 'QA')),
    subject_id              INTEGER NOT NULL,
    upstream_layer          TEXT NOT NULL CHECK (upstream_layer IN ('L3', 'L4', 'L5')),
    upstream_type           TEXT NOT NULL,
    upstream_id             INTEGER NOT NULL,
    created_at_utc          TEXT NOT NULL,

    PRIMARY KEY (subject_type, subject_id, upstream_layer, upstream_type, upstream_id)
);
