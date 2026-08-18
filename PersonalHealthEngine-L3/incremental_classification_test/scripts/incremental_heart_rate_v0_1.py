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

PIPELINE_NAME = "normalize.heart_rate"
DEFINITION_ID = "normalize.heart_rate"
DEFINITION_VERSION = "0.1"
METRIC = "heart_rate"

VALID_CLASSIFICATIONS = {
    "NEW",
    "REVISION",
    "REOBSERVATION",
}


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def epoch_to_utc_iso(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).isoformat(timespec="seconds")


# ============================================================
# Definition
# ============================================================

definition_bytes = DEFINITION_FILE.read_bytes()

definition_sha256 = hashlib.sha256(
    definition_bytes
).hexdigest()


# ============================================================
# Open databases
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

    if l3.execute(
        "PRAGMA user_version"
    ).fetchone()[0] != 3:
        raise RuntimeError(
            "L3 schema version must be 3"
        )

    # --------------------------------------------------------
    # Freeze an L2 read snapshot.
    # Writers may continue, but this run sees one consistent
    # observation frontier.
    # --------------------------------------------------------

    l2.execute("BEGIN")

    checkpoint_row = l3.execute(
        """
        SELECT
            last_l2_observation_id,
            last_successful_run_id
        FROM processing_checkpoints
        WHERE pipeline_name = ?
        """,
        (PIPELINE_NAME,)
    ).fetchone()

    if checkpoint_row is None:
        raise RuntimeError(
            "Heart Rate checkpoint is not initialized"
        )

    checkpoint_before = checkpoint_row[
        "last_l2_observation_id"
    ]

    frontier = l2.execute(
        """
        SELECT COALESCE(MAX(id), 0)
        FROM raw_record_observations
        """
    ).fetchone()[0]

    pending = l2.execute(
        """
        SELECT
            o.id AS observation_id,
            o.classification,
            o.dataset AS observation_dataset,
            o.provider AS observation_provider,
            o.region AS observation_region,
            o.late_arrival,

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

    # --------------------------------------------------------
    # Validate observation feed before touching L3.
    # --------------------------------------------------------

    for row in pending:

        classification = row[
            "classification"
        ]

        if classification not in VALID_CLASSIFICATIONS:
            raise RuntimeError(
                "Unknown L2 observation classification: "
                f"{classification}"
            )

        if row["observation_dataset"] != row["dataset"]:
            raise RuntimeError(
                "Observation/logical dataset mismatch "
                f"at observation {row['observation_id']}"
            )

    # --------------------------------------------------------
    # Create audit run.
    # --------------------------------------------------------

    run_id = (
        "hr-inc-"
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
                    "dataset": "heart_rate",
                    "checkpoint_before":
                        checkpoint_before,
                    "frontier":
                        frontier,
                    "observations_scanned":
                        len(pending),
                },
                separators=(",", ":")
            )
        )
    )

    l3.commit()

    # --------------------------------------------------------
    # Verify registered normalizer definition.
    # --------------------------------------------------------

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
        raise RuntimeError(
            "Heart Rate normalizer definition "
            "is not registered"
        )

    if (
        registered["definition_sha256"]
        != definition_sha256
    ):
        raise RuntimeError(
            "Registered Heart Rate definition "
            "checksum mismatch"
        )

    # ========================================================
    # Incremental processing
    # ========================================================

    scanned = len(pending)

    hr_new_observations = 0
    hr_revision_observations = 0
    hr_reobservations = 0
    non_hr_observations = 0

    inserted = 0
    skipped_existing = 0
    superseded = 0

    l3.execute("BEGIN IMMEDIATE")

    try:

        for row in pending:

            # ------------------------------------------------
            # Every pipeline checkpoint walks the global L2
            # observation stream, but this normalizer only
            # acts on heart_rate.
            # ------------------------------------------------

            if row["dataset"] != "heart_rate":
                non_hr_observations += 1
                continue

            classification = row[
                "classification"
            ]

            if classification == "REOBSERVATION":
                hr_reobservations += 1
                continue

            if classification == "NEW":
                hr_new_observations += 1

            elif classification == "REVISION":
                hr_revision_observations += 1

            # ------------------------------------------------
            # Parse exact raw version referenced by the
            # actionable observation.
            # ------------------------------------------------

            outer = json.loads(
                row["raw_json"]
            )

            inner = json.loads(
                outer["value"]
            )

            if inner.get("time") is None:
                raise ValueError(
                    f"observation {row['observation_id']}: "
                    "missing value.time"
                )

            if inner.get("bpm") is None:
                raise ValueError(
                    f"observation {row['observation_id']}: "
                    "missing value.bpm"
                )

            if int(inner["time"]) != int(
                row["raw_time"]
            ):
                raise ValueError(
                    f"observation {row['observation_id']}: "
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

            attributes_json = json.dumps(
                {
                    "xiaomi_type_code":
                        inner.get("type")
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":")
            )

            # ------------------------------------------------
            # Existing facts for this logical record.
            # ------------------------------------------------

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

            exact_current = [
                x
                for x in existing
                if (
                    x["status"] == "CURRENT"
                    and x["l2_raw_version_id"]
                        == row["raw_version_id"]
                )
            ]

            # Already materialized, safe idempotent no-op.
            if exact_current:
                skipped_existing += 1
                continue

            current_existing = [
                x
                for x in existing
                if x["status"] == "CURRENT"
            ]

            # ------------------------------------------------
            # Classification/state invariants.
            # ------------------------------------------------

            if (
                classification == "NEW"
                and current_existing
            ):
                raise RuntimeError(
                    "NEW observation encountered for "
                    "logical record that already has "
                    "a different CURRENT fact: "
                    f"{row['logical_record_id']}"
                )

            if (
                classification == "REVISION"
                and not current_existing
            ):
                raise RuntimeError(
                    "REVISION observation has no prior "
                    "CURRENT L3 fact: "
                    f"{row['logical_record_id']}"
                )

            # ------------------------------------------------
            # Revision: supersede prior CURRENT fact.
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Insert new canonical point fact.
            # ------------------------------------------------

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
                    'heart_rate',
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
                    epoch_to_utc_iso(
                        inner["time"]
                    ),
                    float(inner["bpm"]),
                    row["provider"],
                    sid,
                    source_class,
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

        # ----------------------------------------------------
        # Successful batch:
        # advance checkpoint only now.
        # ----------------------------------------------------

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
                            "heart_rate",

                        "checkpoint_before":
                            checkpoint_before,

                        "checkpoint_after":
                            frontier,

                        "observations_scanned":
                            scanned,

                        "hr_new":
                            hr_new_observations,

                        "hr_revision":
                            hr_revision_observations,

                        "hr_reobservation":
                            hr_reobservations,

                        "non_hr":
                            non_hr_observations,

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
                PIPELINE_NAME
            )
        )

        l3.commit()

    except Exception as exc:

        l3.rollback()

        # Record failed run separately.
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
                            "heart_rate",

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
    # Acceptance audit
    # ========================================================

    checkpoint_after = l3.execute(
        """
        SELECT
            last_l2_observation_id,
            last_successful_run_id
        FROM processing_checkpoints
        WHERE pipeline_name = ?
        """,
        (PIPELINE_NAME,)
    ).fetchone()

    current_hr = l3.execute(
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

    l2_hr_logical_snapshot = l2.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        WHERE dataset = 'heart_rate'
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
                fr.metric = 'heart_rate'
                AND fr.status = 'CURRENT'
                AND fr.definition_id =
                    'normalize.heart_rate'
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

    run_status = l3.execute(
        """
        SELECT status
        FROM pipeline_runs
        WHERE run_id = ?
        """,
        (run_id,)
    ).fetchone()[0]

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
            and run_status == "PASS"
        ),
        (
            "CURRENT HR coverage",
            current_hr
            == l2_hr_logical_snapshot
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

    print("=" * 82)
    print("L3 HEART RATE INCREMENTAL SCANNER v0.1")
    print("=" * 82)

    print(
        "checkpoint_before      =",
        checkpoint_before
    )
    print(
        "L2 frontier            =",
        frontier
    )
    print(
        "observations scanned   =",
        scanned
    )
    print()

    print(
        "HR NEW                 =",
        hr_new_observations
    )
    print(
        "HR REVISION            =",
        hr_revision_observations
    )
    print(
        "HR REOBSERVATION       =",
        hr_reobservations
    )
    print(
        "non-HR observations    =",
        non_hr_observations
    )
    print()

    print(
        "inserted               =",
        inserted
    )
    print(
        "skipped existing       =",
        skipped_existing
    )
    print(
        "superseded             =",
        superseded
    )
    print(
        "CURRENT HR facts       =",
        current_hr
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
            "HEART RATE INCREMENTAL SCANNER = PASS"
        )
    else:
        print("RESULT = FAIL")
        print(
            "HEART RATE INCREMENTAL SCANNER = FAIL"
        )
        raise SystemExit(1)

finally:
    l2.close()
    l3.close()
