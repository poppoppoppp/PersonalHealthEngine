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
        CHECK (
            definition_type IN (
                'NORMALIZER',
                'QUALITY_RULE',
                'RESOLUTION_RULE',
                'FEATURE'
            )
        ),
    status              TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (
            status IN (
                'ACTIVE',
                'DEPRECATED'
            )
        ),
    definition_sha256   TEXT,
    registered_at_utc   TEXT NOT NULL,
    notes               TEXT,

    UNIQUE (
        definition_id,
        definition_version
    )
);

CREATE TABLE pipeline_runs (
    run_id                  TEXT PRIMARY KEY,
    mode                    TEXT NOT NULL
        CHECK (
            mode IN (
                'FULL_REBUILD',
                'INCREMENTAL',
                'MANUAL_TEST'
            )
        ),
    status                  TEXT NOT NULL
        CHECK (
            status IN (
                'RUNNING',
                'PASS',
                'FAIL',
                'PARTIAL'
            )
        ),
    source_l2_path          TEXT NOT NULL,
    source_schema_version   INTEGER,
    started_at_utc          TEXT NOT NULL,
    finished_at_utc         TEXT,
    details_json            TEXT
);

CREATE TABLE processing_checkpoints (
    pipeline_name               TEXT PRIMARY KEY,
    last_l2_observation_id      INTEGER NOT NULL DEFAULT 0
        CHECK (last_l2_observation_id >= 0),
    last_successful_run_id      TEXT,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (
        last_successful_run_id
    )
    REFERENCES pipeline_runs(run_id)
);

CREATE TABLE normalization_issues (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT,
    stage                   TEXT NOT NULL,
    dataset                 TEXT,
    l2_logical_record_id    INTEGER,
    l2_raw_version_id       INTEGER,
    issue_code              TEXT NOT NULL,
    severity                TEXT NOT NULL
        CHECK (
            severity IN (
                'INFO',
                'WARN',
                'ERROR'
            )
        ),
    message                 TEXT NOT NULL,
    created_at_utc          TEXT NOT NULL,

    FOREIGN KEY (run_id)
    REFERENCES pipeline_runs(run_id)
);

CREATE INDEX idx_definition_registry_type
ON definition_registry(definition_type);

CREATE INDEX idx_pipeline_runs_started
ON pipeline_runs(started_at_utc);

CREATE INDEX idx_normalization_issues_run
ON normalization_issues(run_id);

CREATE INDEX idx_normalization_issues_dataset
ON normalization_issues(dataset);
