import hashlib
import json
import math
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

DEF = (
    ROOT
    / "definitions"
    / "normalizers"
    / "steps_v0_1.json"
)

DEFINITION_ID = "normalize.steps"
DEFINITION_VERSION = "0.1"
DATASET = "steps"
METRIC = "steps"


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def epoch_to_utc_iso(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).isoformat(timespec="seconds")


def source_class_from_sid(sid):

    sid = str(sid)

    if sid.startswith("hlth.gen_"):
        return "XIAOMI_GENERATED"

    return "NUMERIC_SOURCE"


# ============================================================
# Definition
# ============================================================

definition_bytes = DEF.read_bytes()

definition_sha256 = hashlib.sha256(
    definition_bytes
).hexdigest()


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

l3.execute(
    "PRAGMA foreign_keys = ON"
)


try:

    schema_version = l3.execute(
        "PRAGMA user_version"
    ).fetchone()[0]

    if schema_version < 5:
        raise RuntimeError(
            "Steps BUCKET normalizer requires "
            f"L3 schema >= 5, found {schema_version}"
        )


    # ========================================================
    # Latest L2 raw version per logical record
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

        WHERE lr.dataset = 'steps'

        ORDER BY
            lr.raw_time,
            lr.id
        """
    ).fetchall()


    # ========================================================
    # Validate + normalize before writes
    # ========================================================

    normalized = []
    issues = []

    generated_count = 0
    numeric_count = 0

    generated_formula_matches = 0
    generated_formula_mismatches = 0


    for row in rows:

        try:

            outer = json.loads(
                row["raw_json"]
            )

            inner = json.loads(
                outer["value"]
            )

            anchor_time = inner.get(
                "time"
            )

            steps = inner.get(
                "steps"
            )

            distance = inner.get(
                "distance"
            )

            embedded_calories = inner.get(
                "calories"
            )


            if anchor_time is None:
                raise ValueError(
                    "missing value.time"
                )

            if steps is None:
                raise ValueError(
                    "missing value.steps"
                )

            if distance is None:
                raise ValueError(
                    "missing value.distance"
                )

            if embedded_calories is None:
                raise ValueError(
                    "missing value.calories"
                )

            if int(anchor_time) != int(
                row["raw_time"]
            ):
                raise ValueError(
                    "value.time != logical raw_time"
                )


            sid = str(
                row["raw_sid"]
            )

            source_class = (
                source_class_from_sid(
                    sid
                )
            )


            formula_match = None

            if (
                source_class
                == "XIAOMI_GENERATED"
            ):

                generated_count += 1

                expected_calories = (
                    float(steps) * 0.04
                )

                formula_match = math.isclose(
                    float(
                        embedded_calories
                    ),
                    expected_calories,
                    rel_tol=0.0,
                    abs_tol=1e-5
                )

                if formula_match:
                    generated_formula_matches += 1
                else:
                    generated_formula_mismatches += 1

            else:

                numeric_count += 1


            attributes = {
                "vendor_distance_value":
                    distance,

                "vendor_embedded_calories_value":
                    embedded_calories
            }


            if (
                source_class
                == "XIAOMI_GENERATED"
            ):

                attributes[
                    "generated_embedded_calories_formula"
                ] = "steps_x_0.04"

                attributes[
                    "generated_embedded_calories_formula_match"
                ] = formula_match


            attributes_json = json.dumps(
                attributes,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":")
            )


            normalized.append(
                {
                    "logical_record_id":
                        row[
                            "logical_record_id"
                        ],

                    "raw_version_id":
                        row[
                            "raw_version_id"
                        ],

                    "bucket_anchor_time_utc":
                        epoch_to_utc_iso(
                            anchor_time
                        ),

                    "value_num":
                        float(steps),

                    "provider":
                        row["provider"],

                    "source_sid":
                        sid,

                    "source_class":
                        source_class,

                    "timezone_name":
                        row["zone_name"],

                    "timezone_offset_seconds":
                        row["zone_offset"],

                    "attributes_json":
                        attributes_json
                }
            )


        except Exception as exc:

            issues.append(
                {
                    "logical_record_id":
                        row[
                            "logical_record_id"
                        ],

                    "raw_version_id":
                        row[
                            "raw_version_id"
                        ],

                    "message":
                        str(exc)
                }
            )


    # ========================================================
    # v0.1 verified generated-source invariant
    # ========================================================

    if generated_formula_mismatches:

        issues.append(
            {
                "logical_record_id":
                    None,

                "raw_version_id":
                    None,

                "message":
                    (
                        "Generated steps embedded-calorie "
                        "formula changed or no longer "
                        "matches steps*0.04: "
                        f"{generated_formula_mismatches} "
                        "record(s)"
                    )
            }
        )


    # ========================================================
    # Pipeline run
    # ========================================================

    run_id = (
        "steps-full-"
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%SZ"
        )
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
                        DATASET,

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
    # Validation failure
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
                    'steps',
                    ?,
                    ?,
                    'INVALID_STEPS_RAW',
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
            "Steps validation failed: "
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
        # Definition registry
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
                    "Steps BUCKET normalizer"
                )
            )

        elif (
            registered[
                "definition_sha256"
            ]
            != definition_sha256
        ):

            raise RuntimeError(
                "Steps definition checksum mismatch"
            )


        # ----------------------------------------------------
        # Materialize
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
                    item[
                        "logical_record_id"
                    ]
                )
            ).fetchall()


            exact_current = any(
                row["status"] == "CURRENT"
                and row[
                    "l2_raw_version_id"
                ]
                    == item[
                        "raw_version_id"
                    ]

                for row in existing
            )


            if exact_current:

                skipped += 1
                continue


            stale_ids = [
                row["fact_id"]

                for row in existing

                if row["status"]
                    == "CURRENT"
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
                    'BUCKET',
                    'steps',
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


            fact_id = (
                cursor.lastrowid
            )


            l3.execute(
                """
                INSERT INTO normalized_bucket_facts (
                    fact_id,
                    bucket_anchor_time_utc,
                    bucket_width_seconds,
                    bucket_semantics,
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
                    NULL,
                    'VENDOR_UNRESOLVED',
                    ?,
                    NULL,
                    'steps',
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
                    item[
                        "bucket_anchor_time_utc"
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
                        "timezone_offset_seconds"
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
    # Acceptance audit
    # ========================================================

    l2_logical_count = l2.execute(
        """
        SELECT COUNT(*)

        FROM logical_records

        WHERE dataset = 'steps'
        """
    ).fetchone()[0]


    current_rows = l3.execute(
        """
        SELECT
            fr.id AS fact_id,
            fr.fact_kind,
            fr.evidence_type,

            bf.bucket_anchor_time_utc,
            bf.bucket_width_seconds,
            bf.bucket_semantics,
            bf.value_num,
            bf.unit,
            bf.source_sid,
            bf.source_class,
            bf.attributes_json,

            fp.l2_logical_record_id,
            fp.l2_raw_version_id

        FROM fact_registry fr

        JOIN normalized_bucket_facts bf
          ON bf.fact_id = fr.id

        JOIN fact_provenance fp
          ON fp.fact_id = fr.id

        WHERE
            fr.metric = 'steps'
            AND fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.steps'
            AND fr.definition_version =
                '0.1'

        ORDER BY
            fp.l2_logical_record_id
        """
    ).fetchall()


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
                fr.metric = 'steps'
                AND fr.status = 'CURRENT'
                AND fr.definition_id =
                    'normalize.steps'
                AND fr.definition_version =
                    '0.1'

            GROUP BY
                fp.l2_logical_record_id

            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]


    generated_l3 = sum(
        1
        for row in current_rows
        if row["source_class"]
            == "XIAOMI_GENERATED"
    )


    numeric_l3 = sum(
        1
        for row in current_rows
        if row["source_class"]
            == "NUMERIC_SOURCE"
    )


    bad_bucket_width = sum(
        1
        for row in current_rows
        if row[
            "bucket_width_seconds"
        ] is not None
    )


    bad_bucket_semantics = sum(
        1
        for row in current_rows
        if row[
            "bucket_semantics"
        ] != "VENDOR_UNRESOLVED"
    )


    bad_fact_kind = sum(
        1
        for row in current_rows
        if row["fact_kind"]
            != "BUCKET"
    )


    bad_evidence = sum(
        1
        for row in current_rows
        if row["evidence_type"]
            != "VENDOR_DERIVED"
    )


    bad_unit = sum(
        1
        for row in current_rows
        if row["unit"] != "steps"
    )


    generated_attribute_mismatch = 0

    for row in current_rows:

        if (
            row["source_class"]
            != "XIAOMI_GENERATED"
        ):
            continue

        attrs = json.loads(
            row["attributes_json"]
        )

        if (
            attrs.get(
                "generated_embedded_calories_formula"
            )
            != "steps_x_0.04"
            or attrs.get(
                "generated_embedded_calories_formula_match"
            )
            is not True
        ):
            generated_attribute_mismatch += 1


    definition_count = l3.execute(
        """
        SELECT COUNT(*)

        FROM definition_registry

        WHERE
            definition_id =
                'normalize.steps'
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
            len(current_rows)
                == l2_logical_count
        ),
        (
            "No duplicate CURRENT facts",
            duplicate_current == 0
        ),
        (
            "Fact kind = BUCKET",
            bad_fact_kind == 0
        ),
        (
            "Evidence = VENDOR_DERIVED",
            bad_evidence == 0
        ),
        (
            "Unit = steps",
            bad_unit == 0
        ),
        (
            "Bucket width unresolved",
            bad_bucket_width == 0
        ),
        (
            "Bucket semantics unresolved",
            bad_bucket_semantics == 0
        ),
        (
            "Generated source retained",
            generated_l3
                == generated_count
        ),
        (
            "Numeric source retained",
            numeric_l3
                == numeric_count
        ),
        (
            "Generated calories relation preserved",
            generated_attribute_mismatch
                == 0
            and generated_formula_mismatches
                == 0
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
                        "steps",

                    "inserted":
                        inserted,

                    "skipped":
                        skipped,

                    "superseded":
                        superseded,

                    "current_facts":
                        len(current_rows),

                    "generated":
                        generated_l3,

                    "numeric":
                        numeric_l3,

                    "generated_formula_matches":
                        generated_formula_matches
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

    print("=" * 84)
    print(
        "L3 STEPS BUCKET NORMALIZER v0.1"
    )
    print("=" * 84)

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

    print(
        "generated facts    =",
        generated_l3
    )

    print(
        "numeric facts      =",
        numeric_l3
    )

    print(
        "generated formula matches =",
        generated_formula_matches
    )

    print(
        "generated formula mismatch =",
        generated_formula_mismatches
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
        "current_steps_facts =",
        len(current_rows)
    )

    print(
        "run_id =",
        run_id
    )

    print()

    if final_status == "PASS":

        print("RESULT = PASS")
        print(
            "STEPS NORMALIZATION = PASS"
        )

    else:

        print("RESULT = FAIL")
        print(
            "STEPS NORMALIZATION = FAIL"
        )

        raise SystemExit(1)


finally:

    l2.close()
    l3.close()

