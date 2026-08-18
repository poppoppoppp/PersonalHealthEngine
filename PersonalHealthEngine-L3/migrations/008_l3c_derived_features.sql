CREATE TABLE derived_features (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_name                TEXT NOT NULL,
    scope_type                  TEXT NOT NULL,
    scope_key                   TEXT NOT NULL,
    local_date                  TEXT NOT NULL,
    value_num                   REAL,
    value_code                  TEXT,
    unit                        TEXT,
    sample_count                INTEGER NOT NULL,
    provider                    TEXT NOT NULL,
    source_sid                  TEXT NOT NULL,
    source_class                TEXT NOT NULL,
    timezone_name               TEXT,
    timezone_offset_seconds     INTEGER,
    coverage_status             TEXT NOT NULL,
    definition_id               TEXT NOT NULL,
    definition_version          TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'CURRENT',
    attributes_json             TEXT,
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    CHECK (scope_type IN ('DAILY', 'SOURCE_EPISODE')),
    CHECK (sample_count >= 1),
    CHECK (
        coverage_status IN (
            'COMPLETE',
            'OBSERVED_ONLY',
            'VENDOR_BUCKET_WIDTH_UNRESOLVED',
            'VENDOR_INFERENCE'
        )
    ),
    CHECK (status IN ('CURRENT', 'STALE', 'FAILED')),
    CHECK (value_num IS NOT NULL OR value_code IS NOT NULL)
);

CREATE UNIQUE INDEX uq_derived_feature_current_scope
ON derived_features (
    feature_name,
    scope_type,
    scope_key,
    definition_id,
    definition_version
)
WHERE status = 'CURRENT';

CREATE INDEX idx_derived_features_date
ON derived_features(local_date, feature_name, status);

CREATE TABLE derived_feature_fact_inputs (
    feature_id                  INTEGER NOT NULL,
    fact_id                     INTEGER NOT NULL,
    input_role                  TEXT NOT NULL DEFAULT 'VALUE_INPUT',
    created_at_utc              TEXT NOT NULL,

    PRIMARY KEY (feature_id, fact_id, input_role),

    FOREIGN KEY (feature_id)
    REFERENCES derived_features(id)
    ON DELETE CASCADE,

    FOREIGN KEY (fact_id)
    REFERENCES fact_registry(id),

    CHECK (input_role IN ('VALUE_INPUT', 'EPISODE_CONTEXT'))
);

CREATE INDEX idx_derived_fact_inputs_fact
ON derived_feature_fact_inputs(fact_id);

CREATE TABLE derived_feature_quality_inputs (
    feature_id                  INTEGER NOT NULL,
    assessment_id               INTEGER NOT NULL,
    created_at_utc              TEXT NOT NULL,

    PRIMARY KEY (feature_id, assessment_id),

    FOREIGN KEY (feature_id)
    REFERENCES derived_features(id)
    ON DELETE CASCADE,

    FOREIGN KEY (assessment_id)
    REFERENCES quality_assessments(id)
);

CREATE INDEX idx_derived_quality_inputs_assessment
ON derived_feature_quality_inputs(assessment_id);

CREATE TABLE derived_feature_resolution_inputs (
    feature_id                  INTEGER NOT NULL,
    decision_id                 INTEGER NOT NULL,
    created_at_utc              TEXT NOT NULL,

    PRIMARY KEY (feature_id, decision_id),

    FOREIGN KEY (feature_id)
    REFERENCES derived_features(id)
    ON DELETE CASCADE,

    FOREIGN KEY (decision_id)
    REFERENCES source_resolution_decisions(id)
);

CREATE INDEX idx_derived_resolution_inputs_decision
ON derived_feature_resolution_inputs(decision_id);
