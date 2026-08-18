CREATE TABLE schema_migrations (
    version             INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    applied_at_utc      TEXT NOT NULL,
    checksum_sha256     TEXT NOT NULL
);

CREATE TABLE definition_registry (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    definition_id       TEXT NOT NULL,
    definition_version  TEXT NOT NULL,
    definition_type     TEXT NOT NULL
        CHECK (definition_type IN (
            'CONTEXT_EXTRACTION', 'EVIDENCE_ASSEMBLY', 'HYPOTHESIS', 'CONFIDENCE',
            'DAILY_REASONING', 'MEDICAL_REVIEW', 'PERSONAL_PATTERN'
        )),
    status              TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'DEPRECATED')),
    definition_sha256   TEXT,
    registered_at_utc   TEXT NOT NULL,
    notes               TEXT,

    UNIQUE (definition_id, definition_version)
);

CREATE TABLE pipeline_runs (
    run_id                  TEXT PRIMARY KEY,
    mode                    TEXT NOT NULL
        CHECK (mode IN ('FULL_REBUILD', 'INCREMENTAL', 'MANUAL_TEST', 'REPLAY')),
    status                  TEXT NOT NULL
        CHECK (status IN ('RUNNING', 'PASS', 'FAIL', 'PARTIAL')),
    source_l3_path          TEXT NOT NULL,
    source_l4_path          TEXT NOT NULL,
    source_l5_path          TEXT NOT NULL,
    started_at_utc          TEXT NOT NULL,
    finished_at_utc         TEXT,
    details_json            TEXT
);

CREATE TABLE processing_checkpoints (
    pipeline_name               TEXT PRIMARY KEY,
    last_l5_analytic_id         INTEGER NOT NULL DEFAULT 0
        CHECK (last_l5_analytic_id >= 0),
    last_l3_feature_id          INTEGER NOT NULL DEFAULT 0
        CHECK (last_l3_feature_id >= 0),
    last_l4_baseline_id         INTEGER NOT NULL DEFAULT 0
        CHECK (last_l4_baseline_id >= 0),
    last_successful_run_id      TEXT,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (last_successful_run_id) REFERENCES pipeline_runs(run_id)
);

CREATE TABLE reasoning_issues (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT,
    stage                   TEXT NOT NULL,
    issue_code              TEXT NOT NULL,
    severity                TEXT NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ERROR')),
    message                 TEXT NOT NULL,
    created_at_utc          TEXT NOT NULL,

    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
);

CREATE INDEX idx_definition_registry_type ON definition_registry(definition_type);
CREATE INDEX idx_pipeline_runs_started ON pipeline_runs(started_at_utc);
