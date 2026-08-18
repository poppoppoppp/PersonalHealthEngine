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
    / "resting_heart_rate_v0_1.json"
)

DEFINITION_ID = "normalize.resting_heart_rate"
DEFINITION_VERSION = "0.1"
METRIC = "resting_heart_rate"


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def utc_calendar_date(epoch):
    return datetime.fromtimestamp(
        int(epoch),
        tz=timezone.utc
    ).date().isoformat()


def source_class_from_sid(sid):
    sid = str(sid)

    if sid.startswith("hlth.gen_"):
        return "XIAOMI_GENERATED"

    return "NUMERIC_SOURCE"


# ============================================================
# Definition
# ============================================================

definition_bytes = DEFINITION_FILE.read_bytes()

definition_sha256 = hashlib.sha256(
    definition_bytes
).hexdigest()

definition = json.loads(
    definition_bytes.decode("utf-8-sig")
)


# ============================================================
# Connections
# ============================================================

l2 = sqlite3.connect(
    f"file:{L2_DB}?mode=ro",
    uri=True
)

l2.row_factory = sqlite3.Row

l3 = sqlite3.connect(L3_DB)
l3.row_factory = sqlite3.Row
l3.execute("PRAGMA foreign_keys = ON")


try:

    schema_version = l3.execute(
        "PRAGMA user_version"
    ).fetchone()[0]

    if schema_version < 4:
        raise RuntimeError(
            "Resting HR DAILY normalizer requires "
            f"L3 schema >= 4, found {schema_version}"
        )

    # ========================================================
    # Current L2 state = latest raw version per logical record
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

        WHERE lr.dataset = 'resting_heart_rate'

        ORDER BY
            lr.raw_time,
            lr.id
        """
    ).fetchall()


    # ========================================================
    # Validate + normalize before business writes
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

            bpm = inner.get("bpm")
            date_time = inner.get(
                "date_time"
            )

            if bpm is None:
                raise ValueError(
                    "missing value.bpm"
                )

            if date_time is None:
                raise ValueError(
                    "missing value.date_time"
                )

            if int(date_time) != int(
                row["raw_time"]
            ):
                raise ValueError(
                    "value.date_time != logical raw_time"
                )

            local_date = utc_calendar_date(
                date_time
            )

            sid = str(
                row["raw_sid"]
            )

            attributes_json = json.dumps(
                {
                    "xiaomi_date_anchor_epoch":
                        int(date_time)
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":")
            )

            normalized.append(
                {
                    "logical_record_id":
                        row["logical_record_id"],

                    "raw_version_id":
                        row["raw_version_id"],

                    "local_date":
                        local_date,

                    "value_num":
                        float(bpm),

                    "provider":
                        row["provider"],

                    "source_sid":
                        sid,

                    "source_class":
                        source_class_from_sid(
                            sid
                        ),

                    "timezone_name":
                        row["zone_name"],

                    "timezone_offset_seconds":
                        row["zone_offset"],

                    "attributes_json":
                        attributes_json,
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
                        str(exc)
                }
            )


    # ========================================================
    # Pipeline run
    # ========================================================

    run_id = (
        "resting-heart-rate-full-"
        + datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    l2_schema_version = l2.execute(
        "PRAGMA user_version"
    ).fetchone()[0]

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
            ?,
            ?,
            ?
        )
        """,
        (
            run_id,
            str(L2_DB),
            l2_schema_version,
            utc_now(),
            json.dumps(
                {
                    "dataset":
                        "resting_heart_rate",

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


    # ========================================================
    # Validation failure = no business facts touched
    # ========================================================

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
                    'resting_heart_rate',
                    ?,
                    ?,
                    'INVALID_RESTING_HR_RAW',
                    'ERROR',
                    ?,
                    ?
                )
                """,
                (
                    run_id,
                    issue["logical_record_id"],
                    issue["raw_version_id"],
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
            "Resting HR validation failed: "
            f"{len(issues)} issue(s)"
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

        # ----------------------------------------------------
        # Definition Registry
        # ----------------------------------------------------

        registered = l3.execute(
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

        if registered is None:

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
                    ?
                )
                """,
                (
                    DEFINITION_ID,
                    DEFINITION_VERSION,
                    definition_sha256,
                    utc_now(),
                    "Resting Heart Rate DAILY normalizer"
                )
            )

        elif (
            registered[
                "definition_sha256"
            ]
            != definition_sha256
        ):
            raise RuntimeError(
                "Resting HR definition checksum mismatch"
            )


        # ----------------------------------------------------
        # Materialize current DAILY facts
        # ----------------------------------------------------

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
                    item["logical_record_id"]
                )
            ).fetchall()

            exact_current = any(
                old["status"] == "CURRENT"
                and old["l2_raw_version_id"]
                    == item["raw_version_id"]

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
                    'DAILY',
                    'resting_heart_rate',
                    'VENDOR_DERIVED',
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
                INSERT INTO normalized_daily_facts (
                    fact_id,
                    local_date,
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
                    'bpm',
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    fact_id,
                    item["local_date"],
                    item["value_num"],
                    item["provider"],
                    item["source_sid"],
                    item["source_class"],
                    item["timezone_name"],
                    item[
                        "timezone_offset_seconds"
                    ],
                    item["attributes_json"]
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
                    item["logical_record_id"],
                    item["raw_version_id"],
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
    # Acceptance audit
    # ========================================================

    l2_logical_count = l2.execute(
        """
        SELECT COUNT(*)

        FROM logical_records

        WHERE dataset =
            'resting_heart_rate'
        """
    ).fetchone()[0]


    actual_rows = l3.execute(
        """
        SELECT
            fp.l2_logical_record_id,
            fp.l2_raw_version_id,

            fr.fact_kind,
            fr.evidence_type,

            df.local_date,
            df.value_num,
            df.unit,
            df.provider,
            df.source_sid,
            df.source_class,
            df.timezone_name,
            df.timezone_offset_seconds,
            df.attributes_json

        FROM fact_registry fr

        JOIN normalized_daily_facts df
          ON df.fact_id = fr.id

        JOIN fact_provenance fp
          ON fp.fact_id = fr.id

        WHERE
            fr.metric =
                'resting_heart_rate'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.resting_heart_rate'
            AND fr.definition_version =
                '0.1'

        ORDER BY
            fp.l2_logical_record_id
        """
    ).fetchall()


    actual = {
        row["l2_logical_record_id"]:
            row

        for row in actual_rows
    }


    expected = {
        item["logical_record_id"]:
            item

        for item in normalized
    }


    missing = (
        set(expected)
        - set(actual)
    )

    extra = (
        set(actual)
        - set(expected)
    )

    semantic_mismatches = []

    for logical_id in (
        set(expected)
        & set(actual)
    ):

        exp = expected[
            logical_id
        ]

        act = actual[
            logical_id
        ]

        expected_attributes = (
            exp["attributes_json"]
        )

        conditions = [
            act["l2_raw_version_id"]
                == exp["raw_version_id"],

            act["fact_kind"]
                == "DAILY",

            act["evidence_type"]
                == "VENDOR_DERIVED",

            act["local_date"]
                == exp["local_date"],

            float(act["value_num"])
                == float(exp["value_num"]),

            act["unit"]
                == "bpm",

            act["provider"]
                == exp["provider"],

            act["source_sid"]
                == exp["source_sid"],

            act["source_class"]
                == exp["source_class"],

            act["timezone_name"]
                == exp["timezone_name"],

            act["timezone_offset_seconds"]
                == exp[
                    "timezone_offset_seconds"
                ],

            act["attributes_json"]
                == expected_attributes,
        ]

        if not all(conditions):

            semantic_mismatches.append(
                logical_id
            )


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
                fr.metric =
                    'resting_heart_rate'
                AND fr.status = 'CURRENT'
                AND fr.definition_id =
                    'normalize.resting_heart_rate'
                AND fr.definition_version =
                    '0.1'

            GROUP BY
                fp.l2_logical_record_id

            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]


    definition_count = l3.execute(
        """
        SELECT COUNT(*)

        FROM definition_registry

        WHERE
            definition_id =
                'normalize.resting_heart_rate'
            AND definition_version =
                '0.1'
            AND definition_sha256 = ?
        """,
        (
            definition_sha256,
        )
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
            len(actual_rows)
                == l2_logical_count
        ),
        (
            "Logical set parity",
            not missing
            and not extra
        ),
        (
            "Exact DAILY semantic parity",
            not semantic_mismatches
        ),
        (
            "Fact kind = DAILY",
            all(
                row["fact_kind"]
                    == "DAILY"
                for row in actual_rows
            )
        ),
        (
            "Evidence = VENDOR_DERIVED",
            all(
                row["evidence_type"]
                    == "VENDOR_DERIVED"
                for row in actual_rows
            )
        ),
        (
            "Unit = bpm",
            all(
                row["unit"] == "bpm"
                for row in actual_rows
            )
        ),
        (
            "No duplicate CURRENT facts",
            duplicate_current == 0
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
                        "resting_heart_rate",

                    "inserted":
                        inserted,

                    "skipped":
                        skipped,

                    "superseded":
                        superseded,

                    "current_facts":
                        len(actual_rows),

                    "semantic_mismatches":
                        len(semantic_mismatches)
                },
                separators=(",", ":")
            ),
            run_id
        )
    )

    l3.commit()


    print("=" * 82)
    print(
        "L3 RESTING HEART RATE DAILY NORMALIZER v0.1"
    )
    print("=" * 82)

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

    for row in actual_rows:

        print(
            "date =",
            row["local_date"],
            "| bpm =",
            row["value_num"],
            "| source_sid =",
            row["source_sid"],
            "| raw_version =",
            row["l2_raw_version_id"]
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
        "current_resting_hr_facts =",
        len(actual_rows)
    )

    print(
        "semantic_mismatches =",
        len(semantic_mismatches)
    )

    print(
        "run_id =",
        run_id
    )

    print()

    if final_status == "PASS":

        print("RESULT = PASS")
        print(
            "RESTING HEART RATE NORMALIZATION = PASS"
        )

    else:

        print("RESULT = FAIL")
        print(
            "RESTING HEART RATE NORMALIZATION = FAIL"
        )

        raise SystemExit(1)


finally:

    l2.close()
    l3.close()
