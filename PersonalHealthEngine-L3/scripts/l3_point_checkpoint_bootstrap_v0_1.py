import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


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

    args = parser.parse_args()

    l2_path = Path(args.l2)
    l3_path = Path(args.l3)

    definition_path = Path(
        args.definition
    )

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
            "Checkpoint bootstrap v0.1 "
            "supports POINT definitions only"
        )

    dataset = definition["dataset"]
    metric = definition["metric"]

    definition_id = definition[
        "definition_id"
    ]

    definition_version = definition[
        "definition_version"
    ]

    pipeline_name = definition_id

    # ========================================================
    # Open databases
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
        # Freeze one consistent L2 snapshot.
        # ====================================================

        l2.execute("BEGIN")

        frontier = l2.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM raw_record_observations
            """
        ).fetchone()[0]

        # ====================================================
        # Exact current L2 latest-version state
        # ====================================================

        l2_rows = l2.execute(
            """
            WITH latest_versions AS (
                SELECT
                    logical_record_id,
                    MAX(id) AS raw_version_id

                FROM raw_record_versions

                GROUP BY
                    logical_record_id
            )

            SELECT
                lr.id AS logical_record_id,
                lv.raw_version_id

            FROM logical_records lr

            JOIN latest_versions lv
              ON lv.logical_record_id =
                 lr.id

            WHERE lr.dataset = ?

            ORDER BY lr.id
            """,
            (
                dataset,
            )
        ).fetchall()

        l2_state = {
            row["logical_record_id"]:
                row["raw_version_id"]

            for row in l2_rows
        }

        # ====================================================
        # Exact current L3 provenance state
        # ====================================================

        l3_rows = l3.execute(
            """
            SELECT
                fp.l2_logical_record_id,
                fp.l2_raw_version_id

            FROM fact_registry fr

            JOIN fact_provenance fp
              ON fp.fact_id = fr.id

            WHERE
                fr.metric = ?
                AND fr.status = 'CURRENT'
                AND fr.definition_id = ?
                AND fr.definition_version = ?

            ORDER BY
                fp.l2_logical_record_id
            """,
            (
                metric,
                definition_id,
                definition_version
            )
        ).fetchall()

        l3_state = {}

        duplicate_logical_ids = []

        for row in l3_rows:

            logical_id = row[
                "l2_logical_record_id"
            ]

            if logical_id in l3_state:
                duplicate_logical_ids.append(
                    logical_id
                )

            l3_state[
                logical_id
            ] = row[
                "l2_raw_version_id"
            ]

        # ====================================================
        # Compare exact logical/version state
        # ====================================================

        missing_in_l3 = (
            set(l2_state)
            - set(l3_state)
        )

        extra_in_l3 = (
            set(l3_state)
            - set(l2_state)
        )

        version_mismatches = [
            logical_id

            for logical_id
            in (
                set(l2_state)
                & set(l3_state)
            )

            if (
                l2_state[logical_id]
                != l3_state[logical_id]
            )
        ]

        # ====================================================
        # Definition registry must match file
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

        definition_match = (
            registered is not None
            and registered[
                "definition_sha256"
            ] == definition_sha256
        )

        # ====================================================
        # Find a successful full build to anchor checkpoint
        # ====================================================

        latest_full_run = l3.execute(
            """
            SELECT
                run_id,
                started_at_utc,
                finished_at_utc

            FROM pipeline_runs

            WHERE
                mode = 'FULL_REBUILD'
                AND status = 'PASS'
                AND json_extract(
                    details_json,
                    '$.dataset'
                ) = ?

            ORDER BY
                started_at_utc DESC

            LIMIT 1
            """,
            (
                dataset,
            )
        ).fetchone()

        # ====================================================
        # Pre-write acceptance
        # ====================================================

        prechecks = [
            (
                "Definition checksum",
                definition_match
            ),
            (
                "Successful full build exists",
                latest_full_run
                    is not None
            ),
            (
                "No duplicate CURRENT logical facts",
                not duplicate_logical_ids
            ),
            (
                "L2/L3 logical set parity",
                not missing_in_l3
                and not extra_in_l3
            ),
            (
                "L2/L3 raw-version parity",
                not version_mismatches
            ),
        ]

        if not all(
            ok
            for _, ok in prechecks
        ):

            print("=" * 84)
            print(
                "GENERIC POINT CHECKPOINT BOOTSTRAP v0.1"
            )
            print("=" * 84)

            print(
                "dataset =",
                dataset
            )

            print(
                "metric =",
                metric
            )

            print(
                "L2 frontier =",
                frontier
            )

            print()

            for name, ok in prechecks:
                print(
                    f"{'PASS' if ok else 'FAIL':<5} "
                    f"{name}"
                )

            print()

            print(
                "L2 logical records =",
                len(l2_state)
            )

            print(
                "L3 CURRENT facts   =",
                len(l3_rows)
            )

            print(
                "missing in L3      =",
                len(missing_in_l3)
            )

            print(
                "extra in L3        =",
                len(extra_in_l3)
            )

            print(
                "version mismatches =",
                len(version_mismatches)
            )

            if version_mismatches:

                logical_id = (
                    version_mismatches[0]
                )

                print()
                print(
                    "FIRST VERSION MISMATCH"
                )

                print(
                    "logical_record_id =",
                    logical_id
                )

                print(
                    "L2 latest version =",
                    l2_state[
                        logical_id
                    ]
                )

                print(
                    "L3 provenance     =",
                    l3_state[
                        logical_id
                    ]
                )

            print()
            print(
                "RESULT = FAIL"
            )

            print(
                "CHECKPOINT NOT MODIFIED"
            )

            raise SystemExit(1)

        # ====================================================
        # Safe checkpoint bootstrap
        # ====================================================

        l3.execute(
            "BEGIN IMMEDIATE"
        )

        try:

            l3.execute(
                """
                INSERT INTO processing_checkpoints (
                    pipeline_name,
                    last_l2_observation_id,
                    last_successful_run_id,
                    updated_at_utc
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?
                )

                ON CONFLICT(pipeline_name)

                DO UPDATE SET
                    last_l2_observation_id =
                        excluded.last_l2_observation_id,

                    last_successful_run_id =
                        excluded.last_successful_run_id,

                    updated_at_utc =
                        excluded.updated_at_utc
                """,
                (
                    pipeline_name,
                    frontier,
                    latest_full_run[
                        "run_id"
                    ],
                    utc_now()
                )
            )

            l3.commit()

        except Exception:
            l3.rollback()
            raise

        # ====================================================
        # Post-write acceptance
        # ====================================================

        checkpoint = l3.execute(
            """
            SELECT
                pipeline_name,
                last_l2_observation_id,
                last_successful_run_id

            FROM processing_checkpoints

            WHERE pipeline_name = ?
            """,
            (
                pipeline_name,
            )
        ).fetchone()

        integrity = l3.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = l3.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        checks = [
            (
                "Definition checksum",
                definition_match
            ),
            (
                "Successful full build exists",
                latest_full_run
                    is not None
            ),
            (
                "No duplicate CURRENT logical facts",
                not duplicate_logical_ids
            ),
            (
                "L2/L3 logical set parity",
                not missing_in_l3
                and not extra_in_l3
            ),
            (
                "L2/L3 raw-version parity",
                not version_mismatches
            ),
            (
                "Checkpoint created",
                checkpoint is not None
            ),
            (
                "Checkpoint at frozen L2 frontier",
                checkpoint is not None
                and checkpoint[
                    "last_l2_observation_id"
                ] == frontier
            ),
            (
                "Checkpoint linked to PASS full run",
                checkpoint is not None
                and checkpoint[
                    "last_successful_run_id"
                ]
                    == latest_full_run[
                        "run_id"
                    ]
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

        print("=" * 84)
        print(
            "GENERIC POINT CHECKPOINT BOOTSTRAP v0.1"
        )
        print("=" * 84)

        print(
            "dataset               =",
            dataset
        )

        print(
            "metric                =",
            metric
        )

        print(
            "definition            =",
            f"{definition_id} "
            f"{definition_version}"
        )

        print(
            "L2 frontier           =",
            frontier
        )

        print(
            "L2 logical records    =",
            len(l2_state)
        )

        print(
            "L3 CURRENT facts      =",
            len(l3_rows)
        )

        print(
            "version mismatches    =",
            len(version_mismatches)
        )

        print(
            "linked full run       =",
            latest_full_run[
                "run_id"
            ]
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
            "checkpoint =",
            checkpoint[
                "last_l2_observation_id"
            ]
        )

        print()

        if passed == len(checks):

            print(
                "RESULT = PASS"
            )

            print(
                "GENERIC POINT CHECKPOINT = READY"
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
