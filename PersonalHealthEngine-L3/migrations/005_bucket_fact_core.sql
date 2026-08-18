CREATE TABLE normalized_bucket_facts (
    fact_id                     INTEGER PRIMARY KEY,

    bucket_anchor_time_utc      TEXT NOT NULL,

    bucket_width_seconds        INTEGER,

    bucket_semantics            TEXT NOT NULL
        DEFAULT 'VENDOR_UNRESOLVED',

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
    ),

    CHECK (
        bucket_width_seconds IS NULL
        OR bucket_width_seconds > 0
    )
);


CREATE INDEX idx_bucket_facts_anchor_time
ON normalized_bucket_facts(
    bucket_anchor_time_utc
);


CREATE INDEX idx_bucket_facts_source
ON normalized_bucket_facts(
    provider,
    source_sid
);
