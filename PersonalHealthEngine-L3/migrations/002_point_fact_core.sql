CREATE TABLE fact_registry (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    fact_kind           TEXT NOT NULL
        CHECK (
            fact_kind IN (
                'POINT',
                'DAILY',
                'BUCKET',
                'INTERVAL'
            )
        ),

    metric              TEXT NOT NULL,

    evidence_type       TEXT NOT NULL
        CHECK (
            evidence_type IN (
                'SENSOR_DERIVED',
                'VENDOR_DERIVED',
                'VENDOR_INFERRED'
            )
        ),

    definition_id       TEXT NOT NULL,
    definition_version  TEXT NOT NULL,

    status              TEXT NOT NULL DEFAULT 'CURRENT'
        CHECK (
            status IN (
                'CURRENT',
                'STALE',
                'FAILED'
            )
        ),

    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);


CREATE TABLE normalized_point_facts (
    fact_id                 INTEGER PRIMARY KEY,

    event_time_utc          TEXT NOT NULL,

    value_num               REAL,
    value_code              TEXT,

    unit                    TEXT,

    provider                TEXT NOT NULL,
    source_sid              TEXT NOT NULL,
    source_class            TEXT NOT NULL,

    timezone_name           TEXT,
    timezone_offset_seconds INTEGER,

    FOREIGN KEY (fact_id)
    REFERENCES fact_registry(id)
    ON DELETE CASCADE,

    CHECK (
        value_num IS NOT NULL
        OR value_code IS NOT NULL
    )
);


CREATE TABLE fact_provenance (
    fact_id                 INTEGER NOT NULL,

    l2_logical_record_id    INTEGER NOT NULL,
    l2_raw_version_id       INTEGER NOT NULL,

    provenance_role         TEXT NOT NULL DEFAULT 'SOURCE',

    created_at_utc          TEXT NOT NULL,

    PRIMARY KEY (
        fact_id,
        l2_raw_version_id
    ),

    FOREIGN KEY (fact_id)
    REFERENCES fact_registry(id)
    ON DELETE CASCADE
);


CREATE INDEX idx_fact_registry_metric
ON fact_registry(metric);


CREATE INDEX idx_fact_registry_status
ON fact_registry(status);


CREATE INDEX idx_fact_registry_definition
ON fact_registry(
    definition_id,
    definition_version
);


CREATE INDEX idx_point_facts_event_time
ON normalized_point_facts(event_time_utc);


CREATE INDEX idx_point_facts_source
ON normalized_point_facts(
    provider,
    source_sid
);


CREATE INDEX idx_fact_provenance_logical
ON fact_provenance(l2_logical_record_id);


CREATE INDEX idx_fact_provenance_version
ON fact_provenance(l2_raw_version_id);
