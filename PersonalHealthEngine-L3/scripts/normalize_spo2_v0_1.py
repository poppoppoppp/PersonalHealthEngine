import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(r"D:\PersonalHealthEngine-L3")

L2_DB = Path(
    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3"
)

L3_DB = (
    ROOT
    / "db"
    / "personal_health_features.sqlite3"
)

DEFINITION_FILE = (
    ROOT
    / "definitions"
    / "normalizers"
    / "spo2_v0_1.json"
)

DEFINITION_ID = "normalize.spo2"
DEFINITION_VERSION = "0.1"
METRIC = "spo2"


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def epoch_to_utc_iso(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).isoformat(timespec="seconds")


definition_bytes = DEFINITION_FILE.read_bytes()

definition_sha256 = hashlib.sha256(
    definition_bytes
).hexdigest()


# ============================================================
# Open L2 read-only
# ============================================================

l2 = sqlite3.connect(
    f"file:{L2_DB}?mode=ro",
    uri=True
)

l2.row_factory = sqlite3.Row


# ============================================================
# Open L3
# ============================================================

l3 = sqlite3.connect(L3_DB)
l3.row_factory = sqlite3.Row
l3.execute("PRAGMA foreign_keys = ON")


try:

    schema_version = l3.execute(
        "PRAGMA user_version"
    ).fetchone()[0]

    if schema_version != 3:
        raise RuntimeError(
            f"L3 schema version must be 3, "
            f"found {schema_version}"
        )


    # ========================================================
    # Read latest SpO2 version per logical record
    # ========================================================

    rows = l2.execute(
        """
        WITH latest_versions AS (
            SELECT rv.*
            FROM raw_record_versions rv

            JOIN (
                SELECT
                    logical_record_id,
                    MAX(id) AS max_version_id
                FROM raw_record_versions
                GROUP BY logical_record_id
            ) x
              ON x.logical_record_id =
                 rv.logical_record_id
             AND x.max_version_id = rv.id
        )

        SELECT
            lr.id AS logical_record_id,
            lr.provider,
            lr.region,
            lr.dataset,
            lr.raw_sid,
            lr.raw_time,

            rv.id AS raw_version_id,
            rv.raw_json,
            rv.zone_name,
            rv.zone_offset

        FROM logical_records lr

        JOIN latest_versions rv
          ON rv.logical_record_id = lr.id

        WHERE lr.dataset = 'spo2'

        ORDER BY
            lr.raw_time,
            lr.id
        """
    ).fetchall()


    # ========================================================
    # Validate before business writes
    # ========================================================

    normalized = []
    issues = []

    for row in rows:

        try:

            outer = json.loads(
                row["raw_json"]
            )

            inner = json.loads(
                outer["value"]
            )

            event_time = inner.get(
                "time"
            )

            value = inner.get(
                "spo2"
            )

            if event_time is None:
                raise ValueError(
                    "missing value.time"
                )

            if value is None:
                raise ValueError(
                    "missing value.spo2"
                )

            if int(event_time) != int(
                row["raw_time"]
            ):
                raise ValueError(
                    "value.time != logical raw_time"
                )

            sid = str(
                row["raw_sid"]
            )

            source_class = (
                "XIAOMI_GENERATED"
                if sid.startswith("hlth.gen_")
                else "NUMERIC_SOURCE"
            )

            normalized.append(
                {
                    "logical_record_id":
                        row["logical_record_id"],

                    "raw_version_id":
                        row["raw_version_id"],

                    "event_time_utc":
                        epoch_to_utc_iso(
                            event_time
                        ),

                    "value_num":
                        float(value),

                    "provider":
                        row["provider"],

                    "source_sid":
                        sid,

                    "source_class":
                        source_class,

                    "timezone_name":
                        row["zone_name"],

                    "timezone_offset":
                        row["zone_offset"],
                }
            )

        except Exception as exc:

            issues.append(
                {
                    "logical_record_id":
                        row["logical_record_id"],

                    "raw_version_id":
                        row["raw_version_id"],

                    "message":
                        str(exc),
                }
            )


    # ========================================================
    # Pipeline run
    # ========================================================

    run_id = (
        "spo2-full-"
        + datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    l3.execute(
        """
        INSERT INTO pipeline_runs (
            run_id,
            mode,
            status,
            source_l2_path,
            source_schema_version,
            started_at_utc,
            details_json
        )
        VALUES (
            ?,
            'FULL_REBUILD',
            'RUNNING',
            ?,
            1,
            ?,
            ?
        )
        """,
        (
            run_id,
            str(L2_DB),
            utc_now(),
            json.dumps(
                {
                    "dataset":
                        "spo2",

                    "definition_id":
                        DEFINITION_ID,

                    "definition_version":
                        DEFINITION_VERSION,

                    "source_rows":
                        len(rows)
                },
                separators=(",", ":")
            )
        )
    )

    l3.commit()


    if issues:

        for issue in issues:

            l3.execute(
                """
                INSERT INTO normalization_issues (
                    run_id,
                    stage,
                    dataset,
                    l2_logical_record_id,
                    l2_raw_version_id,
                    issue_code,
                    severity,
                    message,
                    created_at_utc
                )
                VALUES (
                    ?,
                    'NORMALIZE',
                    'spo2',
                    ?,
                    ?,
                    'INVALID_SPO2_RAW',
                    'ERROR',
                    ?,
                    ?
                )
                """,
                (
                    run_id,
                    issue[
                        "logical_record_id"
                    ],
                    issue[
                        "raw_version_id"
                    ],
                    issue["message"],
                    utc_now()
                )
            )

        l3.execute(
            """
            UPDATE pipeline_runs
            SET
                status = 'FAIL',
                finished_at_utc = ?
            WHERE run_id = ?
            """,
            (
                utc_now(),
                run_id
            )
        )

        l3.commit()

        raise RuntimeError(
            f"SpO2 validation failed: "
            f"{len(issues)} issues"
        )


    # ========================================================
    # Business transaction
    # ========================================================

    inserted = 0
    skipped = 0
    superseded = 0

    l3.execute(
        "BEGIN IMMEDIATE"
    )

    try:

        existing_definition = l3.execute(
            """
            SELECT definition_sha256
            FROM definition_registry
            WHERE
                definition_id = ?
                AND definition_version = ?
            """,
            (
                DEFINITION_ID,
                DEFINITION_VERSION
            )
        ).fetchone()


        if existing_definition is None:

            l3.execute(
                """
                INSERT INTO definition_registry (
                    definition_id,
                    definition_version,
                    definition_type,
                    status,
                    definition_sha256,
                    registered_at_utc,
                    notes
                )
                VALUES (
                    ?,
                    ?,
                    'NORMALIZER',
                    'ACTIVE',
                    ?,
                    ?,
                    'SpO2 normalizer'
                )
                """,
                (
                    DEFINITION_ID,
                    DEFINITION_VERSION,
                    definition_sha256,
                    utc_now()
                )
            )

        elif (
            existing_definition[
                "definition_sha256"
            ]
            != definition_sha256
        ):
            raise RuntimeError(
                "SpO2 definition checksum mismatch"
            )


        for item in normalized:

            existing = l3.execute(
                """
                SELECT
                    fr.id AS fact_id,
                    fr.status,
                    fp.l2_raw_version_id

                FROM fact_registry fr

                JOIN fact_provenance fp
                  ON fp.fact_id = fr.id

                WHERE
                    fr.metric = ?
                    AND fr.definition_id = ?
                    AND fr.definition_version = ?
                    AND fp.l2_logical_record_id = ?

                ORDER BY fr.id DESC
                """,
                (
                    METRIC,
                    DEFINITION_ID,
                    DEFINITION_VERSION,
                    item[
                        "logical_record_id"
                    ]
                )
            ).fetchall()


            exact_current = any(
                old["status"] == "CURRENT"
                and old[
                    "l2_raw_version_id"
                ] == item["raw_version_id"]
                for old in existing
            )

            if exact_current:
                skipped += 1
                continue


            stale_ids = [
                old["fact_id"]
                for old in existing
                if old["status"] == "CURRENT"
            ]

            if stale_ids:

                placeholders = ",".join(
                    "?"
                    for _ in stale_ids
                )

                l3.execute(
                    f"""
                    UPDATE fact_registry
                    SET
                        status = 'STALE',
                        updated_at_utc = ?
                    WHERE id IN (
                        {placeholders}
                    )
                    """,
                    (
                        utc_now(),
                        *stale_ids
                    )
                )

                superseded += len(
                    stale_ids
                )


            now = utc_now()

            cursor = l3.execute(
                """
                INSERT INTO fact_registry (
                    fact_kind,
                    metric,
                    evidence_type,
                    definition_id,
                    definition_version,
                    status,
                    created_at_utc,
                    updated_at_utc
                )
                VALUES (
                    'POINT',
                    'spo2',
                    'SENSOR_DERIVED',
                    ?,
                    ?,
                    'CURRENT',
                    ?,
                    ?
                )
                """,
                (
                    DEFINITION_ID,
                    DEFINITION_VERSION,
                    now,
                    now
                )
            )

            fact_id = cursor.lastrowid


            l3.execute(
                """
                INSERT INTO normalized_point_facts (
                    fact_id,
                    event_time_utc,
                    value_num,
                    value_code,
                    unit,
                    provider,
                    source_sid,
                    source_class,
                    timezone_name,
                    timezone_offset_seconds,
                    attributes_json
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    NULL,
                    'percent',
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    NULL
                )
                """,
                (
                    fact_id,
                    item[
                        "event_time_utc"
                    ],
                    item[
                        "value_num"
                    ],
                    item[
                        "provider"
                    ],
                    item[
                        "source_sid"
                    ],
                    item[
                        "source_class"
                    ],
                    item[
                        "timezone_name"
                    ],
                    item[
                        "timezone_offset"
                    ]
                )
            )


            l3.execute(
                """
                INSERT INTO fact_provenance (
                    fact_id,
                    l2_logical_record_id,
                    l2_raw_version_id,
                    provenance_role,
                    created_at_utc
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    'SOURCE',
                    ?
                )
                """,
                (
                    fact_id,
                    item[
                        "logical_record_id"
                    ],
                    item[
                        "raw_version_id"
                    ],
                    now
                )
            )

            inserted += 1


        l3.commit()

    except Exception:

        l3.rollback()

        l3.execute(
            """
            UPDATE pipeline_runs
            SET
                status = 'FAIL',
                finished_at_utc = ?
            WHERE run_id = ?
            """,
            (
                utc_now(),
                run_id
            )
        )

        l3.commit()
        raise


    # ========================================================
    # Acceptance
    # ========================================================

    l2_logical_count = l2.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        WHERE dataset = 'spo2'
        """
    ).fetchone()[0]


    current_facts = l3.execute(
        """
        SELECT COUNT(*)
        FROM fact_registry
        WHERE
            metric = 'spo2'
            AND status = 'CURRENT'
            AND definition_id =
                'normalize.spo2'
            AND definition_version = '0.1'
        """
    ).fetchone()[0]


    point_count = l3.execute(
        """
        SELECT COUNT(*)

        FROM normalized_point_facts pf

        JOIN fact_registry fr
          ON fr.id = pf.fact_id

        WHERE
            fr.metric = 'spo2'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.spo2'
            AND fr.definition_version = '0.1'
        """
    ).fetchone()[0]


    provenance_count = l3.execute(
        """
        SELECT COUNT(*)

        FROM fact_provenance fp

        JOIN fact_registry fr
          ON fr.id = fp.fact_id

        WHERE
            fr.metric = 'spo2'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.spo2'
            AND fr.definition_version = '0.1'
        """
    ).fetchone()[0]


    distinct_logical = l3.execute(
        """
        SELECT COUNT(
            DISTINCT fp.l2_logical_record_id
        )

        FROM fact_provenance fp

        JOIN fact_registry fr
          ON fr.id = fp.fact_id

        WHERE
            fr.metric = 'spo2'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.spo2'
            AND fr.definition_version = '0.1'
        """
    ).fetchone()[0]


    wrong_unit = l3.execute(
        """
        SELECT COUNT(*)

        FROM normalized_point_facts pf

        JOIN fact_registry fr
          ON fr.id = pf.fact_id

        WHERE
            fr.metric = 'spo2'
            AND fr.status = 'CURRENT'
            AND pf.unit != 'percent'
        """
    ).fetchone()[0]


    null_values = l3.execute(
        """
        SELECT COUNT(*)

        FROM normalized_point_facts pf

        JOIN fact_registry fr
          ON fr.id = pf.fact_id

        WHERE
            fr.metric = 'spo2'
            AND fr.status = 'CURRENT'
            AND pf.value_num IS NULL
        """
    ).fetchone()[0]


    wrong_evidence = l3.execute(
        """
        SELECT COUNT(*)
        FROM fact_registry
        WHERE
            metric = 'spo2'
            AND status = 'CURRENT'
            AND evidence_type
                != 'SENSOR_DERIVED'
        """
    ).fetchone()[0]


    definition_count = l3.execute(
        """
        SELECT COUNT(*)
        FROM definition_registry
        WHERE
            definition_id =
                'normalize.spo2'
            AND definition_version = '0.1'
            AND definition_sha256 = ?
        """,
        (
            definition_sha256,
        )
    ).fetchone()[0]


    duplicate_current = l3.execute(
        """
        SELECT COUNT(*)

        FROM (
            SELECT
                fp.l2_logical_record_id

            FROM fact_registry fr

            JOIN fact_provenance fp
              ON fp.fact_id = fr.id

            WHERE
                fr.metric = 'spo2'
                AND fr.status = 'CURRENT'
                AND fr.definition_id =
                    'normalize.spo2'
                AND fr.definition_version = '0.1'

            GROUP BY
                fp.l2_logical_record_id

            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]


    integrity = l3.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_errors = l3.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()


    checks = [
        (
            "L2 logical record coverage",
            current_facts
            == l2_logical_count
        ),
        (
            "Point fact count",
            point_count
            == l2_logical_count
        ),
        (
            "Provenance count",
            provenance_count
            == l2_logical_count
        ),
        (
            "Distinct logical provenance",
            distinct_logical
            == l2_logical_count
        ),
        (
            "No duplicate CURRENT facts",
            duplicate_current == 0
        ),
        (
            "No null SpO2 values",
            null_values == 0
        ),
        (
            "Unit = percent",
            wrong_unit == 0
        ),
        (
            "Evidence semantics",
            wrong_evidence == 0
        ),
        (
            "Definition registered",
            definition_count == 1
        ),
        (
            "SQLite integrity",
            integrity == "ok"
        ),
        (
            "Foreign key integrity",
            len(fk_errors) == 0
        ),
    ]


    passed = sum(
        1
        for _, ok in checks
        if ok
    )


    final_status = (
        "PASS"
        if passed == len(checks)
        else "FAIL"
    )


    l3.execute(
        """
        UPDATE pipeline_runs
        SET
            status = ?,
            finished_at_utc = ?,
            details_json = ?
        WHERE run_id = ?
        """,
        (
            final_status,
            utc_now(),
            json.dumps(
                {
                    "dataset":
                        "spo2",

                    "source_latest_rows":
                        len(rows),

                    "inserted":
                        inserted,

                    "skipped":
                        skipped,

                    "superseded":
                        superseded,

                    "current_facts":
                        current_facts,

                    "l2_logical_records":
                        l2_logical_count
                },
                separators=(",", ":")
            ),
            run_id
        )
    )

    l3.commit()


    # ========================================================
    # Output
    # ========================================================

    print("=" * 80)
    print("L3 SpO2 NORMALIZER v0.1")
    print("=" * 80)

    print(
        "definition_sha256 =",
        definition_sha256
    )

    print(
        "L2 latest rows     =",
        len(rows)
    )

    print(
        "L2 logical records =",
        l2_logical_count
    )

    print(
        "inserted           =",
        inserted
    )

    print(
        "skipped            =",
        skipped
    )

    print(
        "superseded         =",
        superseded
    )

    print()

    for name, ok in checks:
        print(
            f"{'PASS' if ok else 'FAIL':<5} "
            f"{name}"
        )

    print()

    print(
        "checks_passed =",
        f"{passed}/{len(checks)}"
    )

    print(
        "current_spo2_facts =",
        current_facts
    )

    print(
        "run_id =",
        run_id
    )

    print()

    if final_status == "PASS":

        print("RESULT = PASS")
        print(
            "SPO2 NORMALIZATION = PASS"
        )

    else:

        print("RESULT = FAIL")
        print(
            "SPO2 NORMALIZATION = FAIL"
        )

        raise SystemExit(1)


finally:
    l2.close()
    l3.close()
