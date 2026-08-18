import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(r"D:\PersonalHealthEngine-L3\resting_hr_revision_test")

L2_DB = Path(
    r"D:\PersonalHealthEngine-L3\resting_hr_revision_test\l2\personal_health_raw_test.sqlite3"
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
    / "resting_heart_rate_v0_1.json"
)

PIPELINE = "normalize.resting_heart_rate"
DEFINITION_ID = "normalize.resting_heart_rate"
DEFINITION_VERSION = "0.1"
METRIC = "resting_heart_rate"
DATASET = "resting_heart_rate"

VALID_CLASSIFICATIONS = {
    "NEW",
    "REVISION",
    "REOBSERVATION",
}


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


definition_bytes = DEF.read_bytes()

definition_sha256 = hashlib.sha256(
    definition_bytes
).hexdigest()


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
            f"DAILY incremental requires "
            f"L3 schema >= 4, found {schema_version}"
        )

    # ========================================================
    # Freeze L2 snapshot
    # ========================================================

    l2.execute("BEGIN")

    checkpoint = l3.execute(
        """
        SELECT
            last_l2_observation_id,
            last_successful_run_id
        FROM processing_checkpoints
        WHERE pipeline_name = ?
        """,
        (PIPELINE,)
    ).fetchone()

    if checkpoint is None:
        raise RuntimeError(
            "Resting HR checkpoint not initialized"
        )

    checkpoint_before = checkpoint[
        "last_l2_observation_id"
    ]

    frontier = l2.execute(
        """
        SELECT COALESCE(MAX(id), 0)
        FROM raw_record_observations
        """
    ).fetchone()[0]


    # ========================================================
    # Read global observation stream after checkpoint
    # ========================================================

    pending = l2.execute(
        """
        SELECT
            o.id AS observation_id,
            o.classification,
            o.dataset AS observation_dataset,
            o.provider AS observation_provider,
            o.region AS observation_region,

            rv.id AS raw_version_id,
            rv.logical_record_id,
            rv.raw_json,
            rv.zone_name,
            rv.zone_offset,

            lr.provider,
            lr.region,
            lr.dataset,
            lr.raw_sid,
            lr.raw_time

        FROM raw_record_observations o

        JOIN raw_record_versions rv
          ON rv.id = o.raw_record_version_id

        JOIN logical_records lr
          ON lr.id = rv.logical_record_id

        WHERE
            o.id > ?
            AND o.id <= ?

        ORDER BY o.id
        """,
        (
            checkpoint_before,
            frontier
        )
    ).fetchall()


    # ========================================================
    # Validate observation feed envelope
    # ========================================================

    for row in pending:

        if row["classification"] not in VALID_CLASSIFICATIONS:
            raise RuntimeError(
                "Unknown observation classification: "
                f"{row['classification']}"
            )

        if (
            row["observation_dataset"]
            != row["dataset"]
        ):
            raise RuntimeError(
                "Observation/logical dataset mismatch"
            )

        if (
            row["observation_provider"]
            != row["provider"]
        ):
            raise RuntimeError(
                "Observation/logical provider mismatch"
            )

        if (
            row["observation_region"]
            != row["region"]
        ):
            raise RuntimeError(
                "Observation/logical region mismatch"
            )


    # ========================================================
    # Definition checksum
    # ========================================================

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

    if (
        registered is None
        or registered["definition_sha256"]
            != definition_sha256
    ):
        raise RuntimeError(
            "Resting HR definition checksum mismatch"
        )


    # ========================================================
    # Audit run
    # ========================================================

    run_id = (
        "resting-heart-rate-inc-"
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
            'INCREMENTAL',
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
                    "checkpoint_before":
                        checkpoint_before,
                    "frontier":
                        frontier,
                    "observations_scanned":
                        len(pending)
                },
                separators=(",", ":")
            )
        )
    )

    l3.commit()


    # ========================================================
    # Business transaction
    # ========================================================

    target_new = 0
    target_revision = 0
    target_reobservation = 0
    non_target = 0

    inserted = 0
    skipped_existing = 0
    superseded = 0

    l3.execute("BEGIN IMMEDIATE")

    try:

        for row in pending:

            if row["dataset"] != DATASET:
                non_target += 1
                continue

            classification = row[
                "classification"
            ]

            if classification == "REOBSERVATION":
                target_reobservation += 1
                continue

            if classification == "NEW":
                target_new += 1

            elif classification == "REVISION":
                target_revision += 1


            # -----------------------------------------------
            # Normalize exact raw version referenced
            # by observation.
            # -----------------------------------------------

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


            # -----------------------------------------------
            # Existing materialized state
            # -----------------------------------------------

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
                    row["logical_record_id"]
                )
            ).fetchall()


            exact_current = any(
                x["status"] == "CURRENT"
                and x["l2_raw_version_id"]
                    == row["raw_version_id"]

                for x in existing
            )

            if exact_current:
                skipped_existing += 1
                continue


            current_existing = [
                x
                for x in existing
                if x["status"] == "CURRENT"
            ]


            if (
                classification == "NEW"
                and current_existing
            ):
                raise RuntimeError(
                    "NEW has an existing "
                    "different CURRENT fact"
                )


            if (
                classification == "REVISION"
                and not current_existing
            ):
                raise RuntimeError(
                    "REVISION has no prior CURRENT fact"
                )


            if classification == "REVISION":

                stale_ids = [
                    x["fact_id"]
                    for x in current_existing
                ]

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


            # -----------------------------------------------
            # Insert DAILY fact
            # -----------------------------------------------

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
                    local_date,
                    float(bpm),
                    row["provider"],
                    sid,
                    source_class_from_sid(
                        sid
                    ),
                    row["zone_name"],
                    row["zone_offset"],
                    attributes_json
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
                    row["logical_record_id"],
                    row["raw_version_id"],
                    now
                )
            )

            inserted += 1


        # ====================================================
        # Advance checkpoint only on successful batch
        # ====================================================

        finished_at = utc_now()

        l3.execute(
            """
            UPDATE pipeline_runs
            SET
                status = 'PASS',
                finished_at_utc = ?,
                details_json = ?
            WHERE run_id = ?
            """,
            (
                finished_at,
                json.dumps(
                    {
                        "dataset":
                            DATASET,
                        "checkpoint_before":
                            checkpoint_before,
                        "checkpoint_after":
                            frontier,
                        "observations_scanned":
                            len(pending),
                        "target_new":
                            target_new,
                        "target_revision":
                            target_revision,
                        "target_reobservation":
                            target_reobservation,
                        "non_target":
                            non_target,
                        "inserted":
                            inserted,
                        "skipped_existing":
                            skipped_existing,
                        "superseded":
                            superseded
                    },
                    separators=(",", ":")
                ),
                run_id
            )
        )


        l3.execute(
            """
            UPDATE processing_checkpoints
            SET
                last_l2_observation_id = ?,
                last_successful_run_id = ?,
                updated_at_utc = ?
            WHERE pipeline_name = ?
            """,
            (
                frontier,
                run_id,
                finished_at,
                PIPELINE
            )
        )

        l3.commit()

    except Exception as exc:

        l3.rollback()

        l3.execute(
            """
            UPDATE pipeline_runs
            SET
                status = 'FAIL',
                finished_at_utc = ?,
                details_json = ?
            WHERE run_id = ?
            """,
            (
                utc_now(),
                json.dumps(
                    {
                        "dataset":
                            DATASET,
                        "checkpoint_before":
                            checkpoint_before,
                        "frontier":
                            frontier,
                        "error":
                            str(exc)
                    },
                    ensure_ascii=False,
                    separators=(",", ":")
                ),
                run_id
            )
        )

        l3.commit()
        raise


    # ========================================================
    # Acceptance
    # ========================================================

    checkpoint_after = l3.execute(
        """
        SELECT
            last_l2_observation_id,
            last_successful_run_id
        FROM processing_checkpoints
        WHERE pipeline_name = ?
        """,
        (PIPELINE,)
    ).fetchone()


    l2_logical_count = l2.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        WHERE dataset =
            'resting_heart_rate'
        """
    ).fetchone()[0]


    current_count = l3.execute(
        """
        SELECT COUNT(*)
        FROM fact_registry
        WHERE
            metric =
                'resting_heart_rate'
            AND status = 'CURRENT'
            AND definition_id =
                'normalize.resting_heart_rate'
            AND definition_version = '0.1'
        """
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


    integrity = l3.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_errors = l3.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()


    checks = [
        (
            "Observation frontier reached",
            checkpoint_after[
                "last_l2_observation_id"
            ] == frontier
        ),
        (
            "Checkpoint linked to this PASS run",
            checkpoint_after[
                "last_successful_run_id"
            ] == run_id
        ),
        (
            "CURRENT DAILY coverage",
            current_count
                == l2_logical_count
        ),
        (
            "No duplicate CURRENT logical facts",
            duplicate_current == 0
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


    print("=" * 86)
    print(
        "RESTING HR DAILY INCREMENTAL RUNNER v0.1"
    )
    print("=" * 86)

    print(
        "checkpoint_before       =",
        checkpoint_before
    )

    print(
        "frontier                =",
        frontier
    )

    print(
        "observations scanned    =",
        len(pending)
    )

    print()

    print(
        "target NEW              =",
        target_new
    )

    print(
        "target REVISION         =",
        target_revision
    )

    print(
        "target REOBSERVATION    =",
        target_reobservation
    )

    print(
        "non-target observations =",
        non_target
    )

    print()

    print(
        "inserted                =",
        inserted
    )

    print(
        "skipped existing        =",
        skipped_existing
    )

    print(
        "superseded              =",
        superseded
    )

    print(
        "CURRENT DAILY facts     =",
        current_count
    )

    print()

    passed = 0

    for name, ok in checks:

        print(
            f"{'PASS' if ok else 'FAIL':<5} "
            f"{name}"
        )

        if ok:
            passed += 1


    print()

    print(
        "checks_passed =",
        f"{passed}/{len(checks)}"
    )

    print(
        "checkpoint_after =",
        checkpoint_after[
            "last_l2_observation_id"
        ]
    )

    print(
        "run_id =",
        run_id
    )

    print()

    if passed == len(checks):

        print("RESULT = PASS")
        print(
            "RESTING HR DAILY INCREMENTAL = PASS"
        )

    else:

        print("RESULT = FAIL")
        raise SystemExit(1)


finally:

    l2.close()
    l3.close()

