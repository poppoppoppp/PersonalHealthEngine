CREATE TABLE normalized_interval_facts (
    fact_id                     INTEGER PRIMARY KEY,

    start_time_utc              TEXT NOT NULL,
    end_time_utc                TEXT NOT NULL,

    duration_seconds            INTEGER,

    interval_semantics          TEXT NOT NULL,

    value_num                   REAL,
    value_code                  TEXT,

    unit                        TEXT,

    provider                    TEXT NOT NULL,
    source_sid                  TEXT NOT NULL,
    source_class                TEXT NOT NULL,

    timezone_name               TEXT,
    timezone_offset_seconds     INTEGER,

    attributes_json             TEXT,

    FOREIGN KEY (fact_id)
    REFERENCES fact_registry(id)
    ON DELETE CASCADE,

    CHECK (
        duration_seconds IS NULL
        OR duration_seconds >= 0
    )
);

CREATE INDEX idx_interval_facts_start
ON normalized_interval_facts(start_time_utc);

CREATE INDEX idx_interval_facts_end
ON normalized_interval_facts(end_time_utc);

CREATE INDEX idx_interval_facts_source
ON normalized_interval_facts(
    provider,
    source_sid
);
