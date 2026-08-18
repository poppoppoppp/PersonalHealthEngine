import argparse
import hashlib
import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone


def load_core(path):

    spec = importlib.util.spec_from_file_location(
        "bucket_core",
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
    def_path = Path(args.definition)

    core = load_core(
        Path(args.core)
    )

    definition_bytes = (
        def_path.read_bytes()
    )

    definition_sha256 = (
        hashlib.sha256(
            definition_bytes
        ).hexdigest()
    )

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

    unit = definition["unit"]

    anchor_source = definition[
        "bucket_anchor_source"
    ]

    value_source = definition[
        "value_source"
    ]

    bucket_width = definition.get(
        "bucket_width_seconds"
    )

    bucket_semantics = definition.get(
        "bucket_semantics",
        "VENDOR_UNRESOLVED"
    )

    if definition.get(
        "temporal_type"
    ) != "BUCKET":
        raise RuntimeError(
            "Definition must be BUCKET"
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

        if (
            l3.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            < 5
        ):
            raise RuntimeError(
                "BUCKET FULL requires "
                "schema >= 5"
            )

        rows = l2.execute(
            """
            WITH latest AS (
                SELECT rv.*

                FROM raw_record_versions rv

                JOIN (
                    SELECT
                        logical_record_id,
                        MAX(id) AS raw_version_id

                    FROM raw_record_versions

                    GROUP BY logical_record_id
                ) x
                  ON x.logical_record_id =
                     rv.logical_record_id
                 AND x.raw_version_id =
                     rv.id
            )

            SELECT
                lr.id AS logical_record_id,
                lr.provider,
                lr.region,
                lr.raw_sid,
                lr.raw_time,

                rv.id AS raw_version_id,
                rv.raw_json,
                rv.zone_name,
                rv.zone_offset

            FROM logical_records lr

            JOIN latest rv
              ON rv.logical_record_id =
                 lr.id

            WHERE lr.dataset = ?

            ORDER BY lr.id
            """,
            (dataset,)
        ).fetchall()

        normalized = []

        for row in rows:

            payload = core.decode_raw(
                row["raw_json"]
            )

            anchor = core.get_path(
                payload,
                anchor_source
            )

            value = core.get_path(
                payload,
                value_source
            )

            if (
                int(anchor)
                != int(row["raw_time"])
            ):
                raise RuntimeError(
                    "Bucket anchor != "
                    "logical raw_time"
                )

            sid = str(
                row["raw_sid"]
            )

            source_class = (
                core.source_class_from_sid(
                    sid
                )
            )

            attrs = core.build_attributes(
                payload,
                definition,
                source_class
            )

            normalized.append(
                {
                    "logical_record_id":
                        row["logical_record_id"],

                    "raw_version_id":
                        row["raw_version_id"],

                    "anchor":
                        core.epoch_to_utc_iso(
                            anchor
                        ),

                    "value":
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

                    "attributes_json":
                        attrs,
                }
            )

        run_id = (
            metric
            + "-bucket-full-"
            + datetime.now(
                timezone.utc
            ).strftime(
                "%Y%m%dT%H%M%SZ"
            )
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
                ?,
                ?,
                ?
            )
            """,
            (
                run_id,
                str(l2_path),

                l2.execute(
                    "PRAGMA user_version"
                ).fetchone()[0],

                core.utc_now(),

                json.dumps(
                    {
                        "dataset":
                            dataset,
                        "metric":
                            metric,
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
                        core.utc_now(),
                        f"{metric} BUCKET normalizer"
                    )
                )

            elif (
                registered[
                    "definition_sha256"
                ]
                != definition_sha256
            ):
                raise RuntimeError(
                    "Definition checksum mismatch"
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
                    row["status"]
                        == "CURRENT"
                    and row[
                        "l2_raw_version_id"
                    ] == item[
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

                    marks = ",".join(
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
                            {marks}
                        )
                        """,
                        (
                            core.utc_now(),
                            *stale_ids
                        )
                    )

                    superseded += len(
                        stale_ids
                    )

                now = core.utc_now()

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
                        ?, ?, ?, ?, ?,
                        NULL,
                        ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        fact_id,
                        item["anchor"],
                        bucket_width,
                        bucket_semantics,
                        item["value"],
                        unit,
                        item["provider"],
                        item["source_sid"],
                        item["source_class"],
                        item["timezone_name"],
                        item["timezone_offset"],
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
                        ?, ?, ?,
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
            raise

        current = l3.execute(
            """
            SELECT
                fp.l2_logical_record_id,
                fp.l2_raw_version_id,
                bf.bucket_width_seconds,
                bf.bucket_semantics,
                bf.unit,
                bf.source_class

            FROM fact_registry fr

            JOIN normalized_bucket_facts bf
              ON bf.fact_id = fr.id

            JOIN fact_provenance fp
              ON fp.fact_id = fr.id

            WHERE
                fr.metric = ?
                AND fr.status = 'CURRENT'
                AND fr.definition_id = ?
                AND fr.definition_version = ?
            """,
            (
                metric,
                definition_id,
                definition_version
            )
        ).fetchall()

        expected = {
            x["logical_record_id"]:
                x["raw_version_id"]

            for x in normalized
        }

        actual = {
            x[
                "l2_logical_record_id"
            ]:
                x[
                    "l2_raw_version_id"
                ]

            for x in current
        }

        duplicate_current = (
            len(actual)
            != len(current)
        )

        generated = sum(
            1
            for x in current
            if x["source_class"]
                == "XIAOMI_GENERATED"
        )

        numeric = sum(
            1
            for x in current
            if x["source_class"]
                == "NUMERIC_SOURCE"
        )

        checks = [
            (
                "Logical coverage",
                len(current)
                    == len(rows)
            ),
            (
                "Exact latest-version parity",
                actual == expected
            ),
            (
                "No duplicate CURRENT",
                not duplicate_current
            ),
            (
                "Bucket width unresolved",
                all(
                    x[
                        "bucket_width_seconds"
                    ] is None
                    for x in current
                )
            ),
            (
                "Bucket semantics unresolved",
                all(
                    x[
                        "bucket_semantics"
                    ]
                    == "VENDOR_UNRESOLVED"
                    for x in current
                )
            ),
            (
                "Unit preserved by definition",
                all(
                    x["unit"] == unit
                    for x in current
                )
            ),
            (
                "SQLite integrity",
                l3.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                == "ok"
            ),
            (
                "Foreign key integrity",
                len(
                    l3.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                ) == 0
            ),
        ]

        passed = sum(
            ok
            for _, ok in checks
        )

        status = (
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
                status,
                core.utc_now(),

                json.dumps(
                    {
                        "dataset":
                            dataset,
                        "metric":
                            metric,
                        "inserted":
                            inserted,
                        "skipped":
                            skipped,
                        "superseded":
                            superseded,
                        "current":
                            len(current),
                        "generated":
                            generated,
                        "numeric":
                            numeric
                    },
                    separators=(",", ":")
                ),

                run_id
            )
        )

        l3.commit()

        print("=" * 84)
        print(
            "GENERIC BUCKET FULL RUNNER v0.1"
        )
        print("=" * 84)

        print(
            "dataset             =",
            dataset
        )

        print(
            "metric              =",
            metric
        )

        print(
            "L2 latest rows      =",
            len(rows)
        )

        print(
            "inserted            =",
            inserted
        )

        print(
            "skipped             =",
            skipped
        )

        print(
            "superseded          =",
            superseded
        )

        print(
            "CURRENT facts       =",
            len(current)
        )

        print(
            "generated           =",
            generated
        )

        print(
            "numeric             =",
            numeric
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
            "run_id =",
            run_id
        )

        print()

        if status == "PASS":
            print("RESULT = PASS")
            print(
                "GENERIC BUCKET FULL = PASS"
            )
        else:
            print("RESULT = FAIL")
            raise SystemExit(1)

    finally:

        l2.close()
        l3.close()


if __name__ == "__main__":
    main()

