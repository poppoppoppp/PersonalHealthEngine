CREATE TABLE normalized_daily_facts (
    fact_id                     INTEGER PRIMARY KEY,

    local_date                  TEXT NOT NULL,

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
        value_num IS NOT NULL
        OR value_code IS NOT NULL
    )
);


CREATE INDEX idx_daily_facts_local_date
ON normalized_daily_facts(local_date);


CREATE INDEX idx_daily_facts_source
ON normalized_daily_facts(
    provider,
    source_sid
);
