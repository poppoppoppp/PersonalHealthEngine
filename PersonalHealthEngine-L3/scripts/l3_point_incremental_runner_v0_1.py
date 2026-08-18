import argparse
import hashlib
import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone


VALID_CLASSIFICATIONS = {
    "NEW",
    "REVISION",
    "REOBSERVATION",
}


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def load_core(path):
    spec = importlib.util.spec_from_file_location(
        "l3_point_core_v0_1",
        path
    )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(module)

    return module


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--l2",
        required=True
    )

    parser.add_argument(
        "--l3",
        required=True
    )

    parser.add_argument(
        "--definition",
        required=True
    )

    parser.add_argument(
        "--core",
        required=True
    )

    args = parser.parse_args()

    l2_path = Path(args.l2)
    l3_path = Path(args.l3)

    definition_path = Path(
        args.definition
    )

    core_path = Path(
        args.core
    )

    # ========================================================
    # Definition
    # ========================================================

    definition_bytes = (
        definition_path.read_bytes()
    )

    definition_sha256 = hashlib.sha256(
        definition_bytes
    ).hexdigest()

    definition = json.loads(
        definition_bytes.decode(
            "utf-8-sig"
        )
    )

    if definition["temporal_type"] != "POINT":
        raise RuntimeError(
            "Generic POINT incremental runner "
            "received non-POINT definition"
        )

    dataset = definition["dataset"]
    metric = definition["metric"]

    definition_id = definition[
        "definition_id"
    ]

    definition_version = definition[
        "definition_version"
    ]

    evidence_type = definition[
        "evidence_type"
    ]

    pipeline_name = definition_id

    core = load_core(
        core_path
    )

    # ========================================================
    # Connections
    # ========================================================

    l2 = sqlite3.connect(
        f"file:{l2_path}?mode=ro",
        uri=True
    )

    l2.row_factory = sqlite3.Row

    l3 = sqlite3.connect(
        l3_path
    )

    l3.row_factory = sqlite3.Row

    l3.execute(
        "PRAGMA foreign_keys = ON"
    )

    try:

        schema_version = l3.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        if schema_version < 3:
            raise RuntimeError(
                "Generic POINT pipeline requires "
                f"L3 schema version >= 3, found "
                f"{schema_version}"
            )

        # ====================================================
        # Freeze L2 read snapshot
        # ====================================================

        l2.execute("BEGIN")

        checkpoint = l3.execute(
            """
            SELECT
                last_l2_observation_id,
                last_successful_run_id

            FROM processing_checkpoints

            WHERE pipeline_name = ?
            """,
            (
                pipeline_name,
            )
        ).fetchone()

        if checkpoint is None:
            raise RuntimeError(
                "Incremental checkpoint is not "
                f"initialized: {pipeline_name}"
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

        # ====================================================
        # Global observation stream
        # ====================================================

        pending = l2.execute(
            """
            SELECT
                o.id AS observation_id,
                o.classification,
                o.dataset
                    AS observation_dataset,
                o.provider
                    AS observation_provider,
                o.region
                    AS observation_region,
                o.late_arrival,

                rv.id
                    AS raw_version_id,
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
              ON rv.id =
                 o.raw_record_version_id

            JOIN logical_records lr
              ON lr.id =
                 rv.logical_record_id

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

        # ====================================================
        # Validate observation envelope first
        # ====================================================

        for row in pending:

            classification = row[
                "classification"
            ]

            if (
                classification
                not in VALID_CLASSIFICATIONS
            ):
                raise RuntimeError(
                    "Unknown classification "
                    f"{classification!r} "
                    "at observation "
                    f"{row['observation_id']}"
                )

            if (
                row[
                    "observation_dataset"
                ]
                != row["dataset"]
            ):
                raise RuntimeError(
                    "Observation/logical dataset "
                    "mismatch at observation "
                    f"{row['observation_id']}"
                )

            if (
                row[
                    "observation_provider"
                ]
                != row["provider"]
            ):
                raise RuntimeError(
                    "Observation/logical provider "
                    "mismatch at observation "
                    f"{row['observation_id']}"
                )

            if (
                row[
                    "observation_region"
                ]
                != row["region"]
            ):
                raise RuntimeError(
                    "Observation/logical region "
                    "mismatch at observation "
                    f"{row['observation_id']}"
                )

        # ====================================================
        # Registered definition must match
        # ====================================================

        registered = l3.execute(
            """
            SELECT definition_sha256

            FROM definition_registry

            WHERE
                definition_id = ?
                AND definition_version = ?
            """,
            (
                definition_id,
                definition_version
            )
        ).fetchone()

        if registered is None:
            raise RuntimeError(
                "Normalizer definition is "
                "not registered"
            )

        if (
            registered[
                "definition_sha256"
            ]
            != definition_sha256
        ):
            raise RuntimeError(
                "Registered definition checksum "
                "does not match file"
            )

        # ====================================================
        # Audit run
        # ====================================================

        run_id = (
            f"{metric}-generic-inc-"
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
                str(l2_path),
                l2_schema_version,
                utc_now(),
                json.dumps(
                    {
                        "dataset":
                            dataset,

                        "metric":
                            metric,

                        "runner":
                            "generic_point_incremental_v0.1",

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

        # ====================================================
        # Counters
        # ====================================================

        scanned = len(pending)

        target_new = 0
        target_revision = 0
        target_reobservation = 0

        non_target = 0

        inserted = 0
        skipped_existing = 0
        superseded = 0

        # ====================================================
        # Atomic business batch
        # ====================================================

        l3.execute(
            "BEGIN IMMEDIATE"
        )

        try:

            for row in pending:

                # --------------------------------------------
                # Each pipeline walks global L2 observation
                # order but acts only on its own dataset.
                # --------------------------------------------

                if row["dataset"] != dataset:

                    non_target += 1
                    continue

                classification = row[
                    "classification"
                ]

                # --------------------------------------------
                # REOBSERVATION is intentionally a no-op.
                # --------------------------------------------

                if (
                    classification
                    == "REOBSERVATION"
                ):

                    target_reobservation += 1
                    continue

                if classification == "NEW":

                    target_new += 1

                elif (
                    classification
                    == "REVISION"
                ):

                    target_revision += 1

                # --------------------------------------------
                # Generic normalization of exact raw version
                # referenced by this observation.
                # --------------------------------------------

                item = core.normalize_point_row(
                    row,
                    definition
                )

                # --------------------------------------------
                # Existing facts for logical record
                # --------------------------------------------

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
                        metric,
                        definition_id,
                        definition_version,
                        row[
                            "logical_record_id"
                        ]
                    )
                ).fetchall()

                exact_current = [
                    x
                    for x in existing

                    if (
                        x["status"]
                            == "CURRENT"

                        and x[
                            "l2_raw_version_id"
                        ]
                            == row[
                                "raw_version_id"
                            ]
                    )
                ]

                # Safe replay / already materialized.
                if exact_current:

                    skipped_existing += 1
                    continue

                current_existing = [
                    x
                    for x in existing

                    if x["status"]
                        == "CURRENT"
                ]

                # --------------------------------------------
                # Classification invariants
                # --------------------------------------------

                if (
                    classification == "NEW"
                    and current_existing
                ):
                    raise RuntimeError(
                        "NEW observation has an "
                        "existing different CURRENT "
                        "fact for logical_record_id="
                        f"{row['logical_record_id']}"
                    )

                if (
                    classification == "REVISION"
                    and not current_existing
                ):
                    raise RuntimeError(
                        "REVISION observation has no "
                        "prior CURRENT fact for "
                        "logical_record_id="
                        f"{row['logical_record_id']}"
                    )

                # --------------------------------------------
                # Revision supersedes CURRENT
                # --------------------------------------------

                if (
                    classification
                    == "REVISION"
                ):

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

                # --------------------------------------------
                # Insert canonical POINT fact
                # --------------------------------------------

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
                        ?,
                        ?,
                        ?,
                        'CURRENT',
                        ?,
                        ?
                    )
                    """,
                    (
                        metric,
                        evidence_type,
                        definition_id,
                        definition_version,
                        now,
                        now
                    )
                )

                fact_id = (
                    cursor.lastrowid
                )

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
                        ?,
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
                            "event_time_utc"
                        ],
                        item[
                            "value_num"
                        ],
                        item["unit"],
                        item["provider"],
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

            # ================================================
            # Advance checkpoint only after successful batch
            # ================================================

            finished_at = utc_now()

            details = {
                "dataset":
                    dataset,

                "metric":
                    metric,

                "runner":
                    "generic_point_incremental_v0.1",

                "checkpoint_before":
                    checkpoint_before,

                "checkpoint_after":
                    frontier,

                "observations_scanned":
                    scanned,

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
            }

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
                        details,
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
                    pipeline_name
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
                                dataset,

                            "metric":
                                metric,

                            "runner":
                                "generic_point_incremental_v0.1",

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

        # ====================================================
        # Acceptance
        # ====================================================

        checkpoint_after = l3.execute(
            """
            SELECT
                last_l2_observation_id,
                last_successful_run_id

            FROM processing_checkpoints

            WHERE pipeline_name = ?
            """,
            (
                pipeline_name,
            )
        ).fetchone()

        l2_logical_count = l2.execute(
            """
            SELECT COUNT(*)

            FROM logical_records

            WHERE dataset = ?
            """,
            (
                dataset,
            )
        ).fetchone()[0]

        current_count = l3.execute(
            """
            SELECT COUNT(*)

            FROM fact_registry

            WHERE
                metric = ?
                AND status = 'CURRENT'
                AND definition_id = ?
                AND definition_version = ?
            """,
            (
                metric,
                definition_id,
                definition_version
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
                    fr.metric = ?
                    AND fr.status = 'CURRENT'
                    AND fr.definition_id = ?
                    AND fr.definition_version = ?

                GROUP BY
                    fp.l2_logical_record_id

                HAVING COUNT(*) > 1
            )
            """,
            (
                metric,
                definition_id,
                definition_version
            )
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
            (
                run_id,
            )
        ).fetchone()[0]

        checks = [
            (
                "Observation frontier reached",
                checkpoint_after[
                    "last_l2_observation_id"
                ] == frontier
            ),
            (
                "Checkpoint linked to PASS run",
                checkpoint_after[
                    "last_successful_run_id"
                ] == run_id
                and run_status == "PASS"
            ),
            (
                "Current logical coverage",
                current_count
                    == l2_logical_count
            ),
            (
                "No duplicate CURRENT",
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

        passed = sum(
            1
            for _, ok in checks
            if ok
        )

        print("=" * 84)
        print(
            "GENERIC POINT INCREMENTAL RUNNER v0.1"
        )
        print("=" * 84)

        print(
            "dataset                 =",
            dataset
        )

        print(
            "metric                  =",
            metric
        )

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
            scanned
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
            "CURRENT facts           =",
            current_count
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

            print(
                "RESULT = PASS"
            )

            print(
                "GENERIC POINT INCREMENTAL = PASS"
            )

        else:

            print(
                "RESULT = FAIL"
            )

            raise SystemExit(1)

    finally:

        l2.close()
        l3.close()


if __name__ == "__main__":
    main()
