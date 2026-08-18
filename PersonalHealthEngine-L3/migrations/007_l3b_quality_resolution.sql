CREATE TABLE quality_assessments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_fact_id         INTEGER NOT NULL,
    metric                  TEXT NOT NULL,
    quality_dimension       TEXT NOT NULL,
    result                  TEXT NOT NULL,
    reason_code             TEXT NOT NULL,
    definition_id           TEXT NOT NULL,
    definition_version      TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'CURRENT',
    details_json            TEXT,
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,

    FOREIGN KEY (subject_fact_id)
    REFERENCES fact_registry(id),

    CHECK (result IN ('PASS', 'FLAGGED', 'UNKNOWN')),
    CHECK (status IN ('CURRENT', 'STALE', 'FAILED'))
);

CREATE UNIQUE INDEX uq_quality_current_dimension
ON quality_assessments (
    subject_fact_id,
    quality_dimension,
    definition_id,
    definition_version
)
WHERE status = 'CURRENT';

CREATE INDEX idx_quality_metric_status
ON quality_assessments(metric, status);

CREATE TABLE quality_assessment_inputs (
    assessment_id           INTEGER NOT NULL,
    fact_id                 INTEGER NOT NULL,
    input_role              TEXT NOT NULL DEFAULT 'SUBJECT',
    created_at_utc          TEXT NOT NULL,

    PRIMARY KEY (assessment_id, fact_id, input_role),

    FOREIGN KEY (assessment_id)
    REFERENCES quality_assessments(id)
    ON DELETE CASCADE,

    FOREIGN KEY (fact_id)
    REFERENCES fact_registry(id),

    CHECK (input_role IN ('SUBJECT', 'CONTEXT'))
);

CREATE INDEX idx_quality_inputs_fact
ON quality_assessment_inputs(fact_id);

CREATE TABLE source_resolution_decisions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    metric                  TEXT NOT NULL,
    component               TEXT NOT NULL,
    grouping_key            TEXT NOT NULL,
    decision                TEXT NOT NULL,
    outcome                 TEXT NOT NULL,
    reason_code             TEXT NOT NULL,
    definition_id           TEXT NOT NULL,
    definition_version      TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'CURRENT',
    details_json            TEXT,
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,

    CHECK (
        decision IN (
            'SINGLE_SOURCE',
            'AGREE',
            'COMPLEMENTARY',
            'CONFLICT',
            'RESOLVED',
            'UNRESOLVED'
        )
    ),
    CHECK (outcome IN ('SELECTED', 'COEXIST', 'UNRESOLVED', 'REJECTED')),
    CHECK (status IN ('CURRENT', 'STALE', 'FAILED'))
);

CREATE UNIQUE INDEX uq_resolution_current_group
ON source_resolution_decisions (
    metric,
    component,
    grouping_key,
    definition_id,
    definition_version
)
WHERE status = 'CURRENT';

CREATE INDEX idx_resolution_metric_status
ON source_resolution_decisions(metric, status);

CREATE TABLE source_resolution_inputs (
    decision_id             INTEGER NOT NULL,
    fact_id                 INTEGER NOT NULL,
    membership_role         TEXT NOT NULL,
    created_at_utc          TEXT NOT NULL,

    PRIMARY KEY (decision_id, fact_id),

    FOREIGN KEY (decision_id)
    REFERENCES source_resolution_decisions(id)
    ON DELETE CASCADE,

    FOREIGN KEY (fact_id)
    REFERENCES fact_registry(id),

    CHECK (
        membership_role IN (
            'CANDIDATE',
            'SELECTED',
            'RETAINED',
            'CONFLICTING'
        )
    )
);

CREATE INDEX idx_resolution_inputs_fact
ON source_resolution_inputs(fact_id);
