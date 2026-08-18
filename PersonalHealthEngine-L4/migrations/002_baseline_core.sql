CREATE TABLE baseline_series (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_key                  TEXT NOT NULL,
    feature_name                TEXT NOT NULL,
    scope_type                  TEXT NOT NULL,
    provider                    TEXT NOT NULL,
    source_sid                  TEXT NOT NULL,
    source_class                TEXT NOT NULL,
    timezone_name               TEXT,
    timezone_offset_seconds     INTEGER,
    unit                        TEXT NOT NULL,
    observation_semantics       TEXT NOT NULL
        CHECK (
            observation_semantics IN (
                'DAILY_VALUE',
                'SOURCE_EPISODE_VALUE'
            )
        ),
    definition_id               TEXT NOT NULL,
    definition_version          TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE')),
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    UNIQUE (series_key, definition_id, definition_version)
);

CREATE INDEX idx_baseline_series_feature
ON baseline_series(feature_name);

CREATE TABLE rolling_baselines (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id                   INTEGER NOT NULL,
    window_days                 INTEGER NOT NULL
        CHECK (window_days > 0),
    as_of_date                  TEXT NOT NULL,
    observation_count           INTEGER NOT NULL
        CHECK (observation_count >= 0),
    distinct_observation_dates  INTEGER NOT NULL
        CHECK (distinct_observation_dates >= 0),
    history_span_days           INTEGER,
    calendar_coverage           REAL NOT NULL
        CHECK (calendar_coverage >= 0.0 AND calendar_coverage <= 1.0),
    mean                        REAL,
    median                      REAL,
    mad                         REAL,
    q10                         REAL,
    q25                         REAL,
    q50                         REAL,
    q75                         REAL,
    q90                         REAL,
    unit                        TEXT NOT NULL,
    maturity                    TEXT NOT NULL
        CHECK (
            maturity IN (
                'INSUFFICIENT_HISTORY',
                'PROVISIONAL',
                'ESTABLISHED'
            )
        ),
    maturity_definition_id      TEXT NOT NULL,
    maturity_definition_version TEXT NOT NULL,
    window_definition_id        TEXT NOT NULL,
    window_definition_version   TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (status IN ('CURRENT', 'STALE', 'FAILED')),
    attributes_json             TEXT,
    created_at_utc              TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL,

    FOREIGN KEY (series_id)
    REFERENCES baseline_series(id)
);

CREATE UNIQUE INDEX uq_rolling_baseline_current
ON rolling_baselines (
    series_id,
    window_days,
    as_of_date,
    maturity_definition_id,
    maturity_definition_version
)
WHERE status = 'CURRENT';

CREATE INDEX idx_rolling_baselines_date
ON rolling_baselines(as_of_date, window_days, status);

CREATE TABLE baseline_feature_inputs (
    baseline_id                 INTEGER NOT NULL,
    l3_feature_id               INTEGER NOT NULL,
    l3_feature_name             TEXT NOT NULL,
    l3_local_date               TEXT NOT NULL,
    created_at_utc              TEXT NOT NULL,

    PRIMARY KEY (baseline_id, l3_feature_id),

    FOREIGN KEY (baseline_id)
    REFERENCES rolling_baselines(id)
    ON DELETE CASCADE
);

CREATE INDEX idx_baseline_inputs_feature
ON baseline_feature_inputs(l3_feature_id);

CREATE TABLE baseline_input_state (
    l3_feature_id               INTEGER PRIMARY KEY,
    l3_feature_name             TEXT NOT NULL,
    l3_local_date               TEXT NOT NULL,
    signature                   TEXT NOT NULL,
    updated_at_utc              TEXT NOT NULL
);
