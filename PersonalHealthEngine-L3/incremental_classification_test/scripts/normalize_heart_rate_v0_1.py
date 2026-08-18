import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(r"D:\PersonalHealthEngine-L3\incremental_classification_test")

L2_DB = Path(
    r"D:\PersonalHealthEngine-L3\incremental_classification_test\l2\personal_health_raw_test.sqlite3"
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
    / "heart_rate_v0_1.json"
)

DEFINITION_ID = "normalize.heart_rate"
DEFINITION_VERSION = "0.1"
METRIC = "heart_rate"


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def epoch_to_utc_iso(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).isoformat(timespec="seconds")


# ------------------------------------------------------------
# Load and hash definition.
# ------------------------------------------------------------

definition_bytes = DEFINITION_FILE.read_bytes()

definition_sha256 = hashlib.sha256(
    definition_bytes
).hexdigest()

definition = json.loads(
    definition_bytes.decode("utf-8-sig")
)


# ------------------------------------------------------------
# Open L2 read-only.
# ------------------------------------------------------------

l2 = sqlite3.connect(
    f"file:{L2_DB}?mode=ro",
    uri=True
)

l2.row_factory = sqlite3.Row


# ------------------------------------------------------------
# Open L3.
# ------------------------------------------------------------

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

    # --------------------------------------------------------
    # Read current L2 Heart Rate state.
    #
    # L3 is materialized current state, so initial rebuild
    # uses the latest raw version of each logical record.
    # Historical versions remain preserved in L2.
    # --------------------------------------------------------

    rows = l2.execute(
        """
        WITH latest_versions AS (
            SELECT
                rv.*
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

        WHERE lr.dataset = 'heart_rate'

        ORDER BY
            lr.raw_time,
            lr.id
        """
    ).fetchall()

    # --------------------------------------------------------
    # Validate everything BEFORE writing L3 business facts.
    # --------------------------------------------------------

    normalized = []
    validation_issues = []

    for row in rows:

        try:
            outer = json.loads(
                row["raw_json"]
            )

            inner = json.loads(
                outer["value"]
            )

            inner_time = inner.get("time")
            bpm = inner.get("bpm")
            type_code = inner.get("type")

            if inner_time is None:
                raise ValueError(
                    "missing value.time"
                )

            if bpm is None:
                raise ValueError(
                    "missing value.bpm"
                )

            if int(inner_time) != int(
                row["raw_time"]
            ):
                raise ValueError(
                    "value.time != logical raw_time"
                )

            event_time_utc = epoch_to_utc_iso(
                inner_time
            )

            sid = str(
                row["raw_sid"]
            )

            source_class = (
                "XIAOMI_GENERATED"
                if sid.startswith("hlth.gen_")
                else "NUMERIC_SOURCE"
            )

            attributes = json.dumps(
                {
                    "xiaomi_type_code":
                        type_code
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

                    "event_time_utc":
                        event_time_utc,

                    "value_num":
                        float(bpm),

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

                    "attributes_json":
                        attributes,
                }
            )

        except Exception as exc:

            validation_issues.append(
                {
                    "logical_record_id":
                        row["logical_record_id"],

                    "raw_version_id":
                        row["raw_version_id"],

                    "message":
                        str(exc),
                }
            )

    # --------------------------------------------------------
    # Create pipeline run.
    # --------------------------------------------------------

    run_id = (
        "hr-full-"
        + datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    started_at = utc_now()

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
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            "FULL_REBUILD",
            "RUNNING",
            str(L2_DB),
            1,
            started_at,
            json.dumps(
                {
                    "dataset":
                        "heart_rate",

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

    # --------------------------------------------------------
    # If validation failed, record issues and stop BEFORE
    # touching normalized facts.
    # --------------------------------------------------------

    if validation_issues:

        for issue in validation_issues:

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
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run_id,
                    "NORMALIZE",
                    "heart_rate",
                    issue[
                        "logical_record_id"
                    ],
                    issue[
                        "raw_version_id"
                    ],
                    "INVALID_HEART_RATE_RAW",
                    "ERROR",
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
            f"Heart Rate validation failed: "
            f"{len(validation_issues)} issues"
        )

    # --------------------------------------------------------
    # Business write transaction.
    # --------------------------------------------------------

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

        existing_definition = l3.execute(
            """
            SELECT
                definition_sha256
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
                    ?, ?, 'NORMALIZER',
                    'ACTIVE', ?, ?, ?
                )
                """,
                (
                    DEFINITION_ID,
                    DEFINITION_VERSION,
                    definition_sha256,
                    utc_now(),
                    "Heart Rate normalizer"
                )
            )

        elif (
            existing_definition[
                "definition_sha256"
            ]
            != definition_sha256
        ):
            raise RuntimeError(
                "Heart Rate definition checksum "
                "mismatch for registered v0.1"
            )

        # ----------------------------------------------------
        # Normalize current facts.
        # ----------------------------------------------------

        for item in normalized:

            # All facts previously generated from this
            # logical record by this definition.
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

            exact_current = None

            for old in existing:

                if (
                    old["l2_raw_version_id"]
                    == item["raw_version_id"]
                    and old["status"]
                    == "CURRENT"
                ):
                    exact_current = old
                    break

            if exact_current is not None:
                skipped += 1
                continue

            # A newer L2 revision supersedes previous
            # materialized CURRENT facts for this logical
            # record.
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
                    ?,
                    'SENSOR_DERIVED',
                    ?,
                    ?,
                    'CURRENT',
                    ?,
                    ?
                )
                """,
                (
                    METRIC,
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
                    ?, ?, ?, NULL, 'bpm',
                    ?, ?, ?, ?, ?, ?
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
                    ],
                    item[
                        "attributes_json"
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
                    ?, ?, ?, 'SOURCE', ?
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

    # --------------------------------------------------------
    # Acceptance audit.
    # --------------------------------------------------------

    l2_logical_count = l2.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        WHERE dataset = 'heart_rate'
        """
    ).fetchone()[0]

    current_facts = l3.execute(
        """
        SELECT COUNT(*)
        FROM fact_registry
        WHERE
            metric = 'heart_rate'
            AND status = 'CURRENT'
            AND definition_id =
                'normalize.heart_rate'
            AND definition_version = '0.1'
        """
    ).fetchone()[0]

    point_facts = l3.execute(
        """
        SELECT COUNT(*)
        FROM normalized_point_facts pf
        JOIN fact_registry fr
          ON fr.id = pf.fact_id
        WHERE
            fr.metric = 'heart_rate'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.heart_rate'
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
            fr.metric = 'heart_rate'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.heart_rate'
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
            fr.metric = 'heart_rate'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.heart_rate'
            AND fr.definition_version = '0.1'
        """
    ).fetchone()[0]

    null_values = l3.execute(
        """
        SELECT COUNT(*)
        FROM normalized_point_facts pf
        JOIN fact_registry fr
          ON fr.id = pf.fact_id
        WHERE
            fr.metric = 'heart_rate'
            AND fr.status = 'CURRENT'
            AND pf.value_num IS NULL
        """
    ).fetchone()[0]

    wrong_units = l3.execute(
        """
        SELECT COUNT(*)
        FROM normalized_point_facts pf
        JOIN fact_registry fr
          ON fr.id = pf.fact_id
        WHERE
            fr.metric = 'heart_rate'
            AND fr.status = 'CURRENT'
            AND pf.unit != 'bpm'
        """
    ).fetchone()[0]

    wrong_evidence = l3.execute(
        """
        SELECT COUNT(*)
        FROM fact_registry
        WHERE
            metric = 'heart_rate'
            AND status = 'CURRENT'
            AND evidence_type
                != 'SENSOR_DERIVED'
        """
    ).fetchone()[0]

    wrong_type_code = l3.execute(
        """
        SELECT COUNT(*)
        FROM normalized_point_facts pf
        JOIN fact_registry fr
          ON fr.id = pf.fact_id
        WHERE
            fr.metric = 'heart_rate'
            AND fr.status = 'CURRENT'
            AND json_extract(
                pf.attributes_json,
                '$.xiaomi_type_code'
            ) != 0
        """
    ).fetchone()[0]

    definition_count = l3.execute(
        """
        SELECT COUNT(*)
        FROM definition_registry
        WHERE
            definition_id =
                'normalize.heart_rate'
            AND definition_version = '0.1'
            AND definition_sha256 = ?
        """,
        (
            definition_sha256,
        )
    ).fetchone()[0]

    integrity = l3.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_violations = l3.execute(
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
            point_facts
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
            "No null heart rates",
            null_values == 0
        ),
        (
            "Unit = bpm",
            wrong_units == 0
        ),
        (
            "Evidence semantics",
            wrong_evidence == 0
        ),
        (
            "Xiaomi type code preserved",
            wrong_type_code == 0
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
            len(fk_violations) == 0
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
                        "heart_rate",

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

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    print("=" * 80)
    print("L3 HEART RATE NORMALIZER v0.1")
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
        "current_hr_facts =",
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
            "HEART RATE NORMALIZATION = PASS"
        )
    else:
        print("RESULT = FAIL")
        print(
            "HEART RATE NORMALIZATION = FAIL"
        )
        raise SystemExit(1)

finally:
    l2.close()
    l3.close()
