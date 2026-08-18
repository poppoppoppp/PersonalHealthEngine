import argparse
import hashlib
import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def load_core(core_path):
    spec = importlib.util.spec_from_file_location(
        "l3_point_core_v0_1",
        core_path
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
    core_path = Path(args.core)

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

    if definition["temporal_type"] != "POINT":
        raise RuntimeError(
            "Generic POINT runner received "
            "non-POINT definition"
        )

    core = load_core(
        core_path
    )

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

        # ----------------------------------------------------
        # Latest L2 version per logical record.
        # ----------------------------------------------------

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

                    GROUP BY
                        logical_record_id
                ) x
                  ON x.logical_record_id =
                     rv.logical_record_id
                 AND x.max_version_id =
                     rv.id
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
              ON rv.logical_record_id =
                 lr.id

            WHERE lr.dataset = ?

            ORDER BY
                lr.raw_time,
                lr.id
            """,
            (
                dataset,
            )
        ).fetchall()

        # ----------------------------------------------------
        # Normalize everything before business write.
        # ----------------------------------------------------

        normalized = []

        for row in rows:

            item = core.normalize_point_row(
                row,
                definition
            )

            normalized.append(
                item
            )

        run_id = (
            f"{metric}-generic-full-"
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
                            "generic_point_full_v0.1",

                        "definition_id":
                            definition_id,

                        "definition_version":
                            definition_version,

                        "source_rows":
                            len(rows)
                    },
                    separators=(",", ":")
                )
            )
        )

        l3.commit()

        inserted = 0
        skipped = 0
        superseded = 0

        # ====================================================
        # Business transaction
        # ====================================================

        l3.execute(
            "BEGIN IMMEDIATE"
        )

        try:

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
                        definition_id,
                        definition_version,
                        definition_sha256,
                        utc_now(),
                        "Generic POINT normalizer"
                    )
                )

            elif (
                registered[
                    "definition_sha256"
                ]
                != definition_sha256
            ):
                raise RuntimeError(
                    "Definition checksum mismatch: "
                    f"{definition_id} "
                    f"{definition_version}"
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
                        metric,
                        definition_id,
                        definition_version,
                        item[
                            "logical_record_id"
                        ]
                    )
                ).fetchall()

                exact_current = any(
                    old["status"]
                        == "CURRENT"
                    and old[
                        "l2_raw_version_id"
                    ]
                        == item[
                            "raw_version_id"
                        ]

                    for old in existing
                )

                if exact_current:

                    skipped += 1
                    continue

                stale_ids = [
                    old["fact_id"]

                    for old in existing

                    if old["status"]
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

        # ----------------------------------------------------
        # Runner-level acceptance
        # ----------------------------------------------------

        logical_count = l2.execute(
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

        checks = [
            (
                "Current coverage",
                current_count
                    == logical_count
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
                            dataset,

                        "metric":
                            metric,

                        "runner":
                            "generic_point_full_v0.1",

                        "source_rows":
                            len(rows),

                        "inserted":
                            inserted,

                        "skipped":
                            skipped,

                        "superseded":
                            superseded,

                        "current_facts":
                            current_count
                    },
                    separators=(",", ":")
                ),
                run_id
            )
        )

        l3.commit()

        print(
            "=" * 78
        )

        print(
            "GENERIC POINT FULL RUNNER v0.1"
        )

        print(
            "=" * 78
        )

        print(
            "dataset      =",
            dataset
        )

        print(
            "metric       =",
            metric
        )

        print(
            "source rows  =",
            len(rows)
        )

        print(
            "inserted     =",
            inserted
        )

        print(
            "skipped      =",
            skipped
        )

        print(
            "superseded   =",
            superseded
        )

        print(
            "CURRENT      =",
            current_count
        )

        print()

        for name, ok in checks:

            print(
                f"{'PASS' if ok else 'FAIL':<5} "
                f"{name}"
            )

        print()

        if final_status == "PASS":

            print(
                "RESULT = PASS"
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
