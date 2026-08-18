CREATE TABLE analytics_series (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_key                  TEXT NOT NULL,
    l4_series_id                INTEGER NOT NULL,
    feature_name                TEXT NOT NULL,
    scope_type                  TEXT NOT NULL,
    provider                    TEXT NOT NULL,
    source_sid                  TEXT NOT NULL,
    source_class                TEXT NOT NULL,
    timezone_name               TEXT,
    timezone_offset_seconds     INTEGER,
    unit                        TEXT NOT NULL,
    observation_semantics       TEXT NOT NULL
        CHECK (observation_semantics IN ('DAILY_VALUE', 'SOURCE_EPISODE_VALUE')),
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE')),
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    UNIQUE (series_key)
);

CREATE INDEX idx_analytics_series_feature ON analytics_series(feature_name);

CREATE TABLE deviation_analytics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id                   INTEGER NOT NULL,
    window_days                 INTEGER NOT NULL CHECK (window_days > 0),
    feature_date                TEXT NOT NULL,
    l3_feature_id               INTEGER NOT NULL,
    l4_baseline_id              INTEGER NOT NULL,
    baseline_maturity           TEXT NOT NULL,
    baseline_median             REAL,
    baseline_mad                REAL,
    current_value               REAL,
    absolute_deviation          REAL,
    relative_deviation          REAL,
    relative_deviation_applicable INTEGER NOT NULL CHECK (relative_deviation_applicable IN (0, 1)),
    robust_standardized_deviation REAL,
    robust_z_unavailable_reason TEXT,
    quantile_position           TEXT,
    deviation_side              TEXT,
    deviation_class             TEXT NOT NULL,
    evidence_status             TEXT NOT NULL,
    attributes_json             TEXT,
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE', 'FAILED')),
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (series_id) REFERENCES analytics_series(id)
);

CREATE UNIQUE INDEX uq_deviation_current
ON deviation_analytics (series_id, window_days, feature_date, l3_feature_id)
WHERE status = 'CURRENT';

CREATE INDEX idx_deviation_date ON deviation_analytics(feature_date, window_days, status);

CREATE TABLE persistence_analytics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id                   INTEGER NOT NULL,
    window_days                 INTEGER NOT NULL CHECK (window_days > 0),
    as_of_date                  TEXT NOT NULL,
    trailing_observation_count  INTEGER NOT NULL CHECK (trailing_observation_count >= 0),
    consecutive_above_typical   INTEGER NOT NULL CHECK (consecutive_above_typical >= 0),
    consecutive_below_typical   INTEGER NOT NULL CHECK (consecutive_below_typical >= 0),
    persistence_class           TEXT NOT NULL,
    evidence_status             TEXT NOT NULL,
    attributes_json             TEXT,
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE', 'FAILED')),
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (series_id) REFERENCES analytics_series(id)
);

CREATE UNIQUE INDEX uq_persistence_current
ON persistence_analytics (series_id, window_days) WHERE status = 'CURRENT';

CREATE TABLE trend_analytics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id                   INTEGER NOT NULL,
    as_of_date                  TEXT NOT NULL,
    trend_point_count           INTEGER NOT NULL CHECK (trend_point_count >= 0),
    trend_start_date            TEXT,
    trend_end_date              TEXT,
    theil_sen_slope             REAL,
    spearman_rho                REAL,
    trend_class                 TEXT NOT NULL,
    evidence_status             TEXT NOT NULL,
    attributes_json             TEXT,
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE', 'FAILED')),
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (series_id) REFERENCES analytics_series(id)
);

CREATE UNIQUE INDEX uq_trend_current
ON trend_analytics (series_id) WHERE status = 'CURRENT';

CREATE TABLE change_point_analytics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id                   INTEGER NOT NULL,
    as_of_date                  TEXT NOT NULL,
    observation_count           INTEGER NOT NULL CHECK (observation_count >= 0),
    candidate_split_date        TEXT,
    shift_magnitude             REAL,
    change_class                TEXT NOT NULL,
    evidence_status             TEXT NOT NULL,
    attributes_json             TEXT,
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE', 'FAILED')),
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (series_id) REFERENCES analytics_series(id)
);

CREATE UNIQUE INDEX uq_change_point_current
ON change_point_analytics (series_id) WHERE status = 'CURRENT';

CREATE TABLE relationship_analytics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id_a                 INTEGER NOT NULL,
    series_id_b                 INTEGER NOT NULL,
    as_of_date                  TEXT NOT NULL,
    paired_count                INTEGER NOT NULL CHECK (paired_count >= 0),
    spearman_rho                REAL,
    relationship_class          TEXT NOT NULL,
    evidence_status             TEXT NOT NULL,
    attributes_json             TEXT,
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE', 'FAILED')),
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (series_id_a) REFERENCES analytics_series(id),
    FOREIGN KEY (series_id_b) REFERENCES analytics_series(id)
);

CREATE UNIQUE INDEX uq_relationship_current
ON relationship_analytics (series_id_a, series_id_b) WHERE status = 'CURRENT';

CREATE TABLE analytics_l3_inputs (
    analytic_type               TEXT NOT NULL
        CHECK (analytic_type IN ('DEVIATION', 'PERSISTENCE', 'TREND', 'CHANGE_POINT', 'RELATIONSHIP')),
    analytic_id                 INTEGER NOT NULL,
    l3_feature_id               INTEGER NOT NULL,
    l3_feature_name             TEXT NOT NULL,
    l3_local_date               TEXT NOT NULL,
    input_role                  TEXT NOT NULL DEFAULT 'VALUE_INPUT',
    created_at_utc              TEXT NOT NULL,

    PRIMARY KEY (analytic_type, analytic_id, l3_feature_id, input_role)
);

CREATE INDEX idx_analytics_l3_inputs_feature ON analytics_l3_inputs(l3_feature_id);

CREATE TABLE analytics_baseline_inputs (
    analytic_type               TEXT NOT NULL
        CHECK (analytic_type IN ('DEVIATION', 'PERSISTENCE', 'TREND', 'CHANGE_POINT', 'RELATIONSHIP')),
    analytic_id                 INTEGER NOT NULL,
    l4_baseline_id              INTEGER NOT NULL,
    l4_baseline_as_of_date      TEXT NOT NULL,
    l4_baseline_window          INTEGER NOT NULL,
    created_at_utc              TEXT NOT NULL,

    PRIMARY KEY (analytic_type, analytic_id, l4_baseline_id)
);

CREATE INDEX idx_analytics_baseline_inputs_baseline ON analytics_baseline_inputs(l4_baseline_id);

CREATE TABLE upstream_input_state (
    kind                        TEXT NOT NULL
        CHECK (kind IN ('L3_FEATURE', 'L4_SERIES', 'L4_BASELINE')),
    ref_id                      INTEGER NOT NULL,
    signature                   TEXT NOT NULL,
    meta_a                      TEXT,
    meta_b                      TEXT,
    updated_at_utc              TEXT NOT NULL,

    PRIMARY KEY (kind, ref_id)
);
