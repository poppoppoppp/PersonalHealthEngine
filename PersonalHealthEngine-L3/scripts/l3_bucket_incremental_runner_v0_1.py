import argparse
import hashlib
import json
import math
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone


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


def decode_raw(raw_json):
    outer = json.loads(raw_json)

    if isinstance(
        outer.get("value"),
        str
    ):
        outer["value"] = json.loads(
            outer["value"]
        )

    return outer


def get_path(obj, dotted):
    current = obj

    for part in dotted.split("."):
        if not isinstance(
            current,
            dict
        ):
            raise KeyError(dotted)

        current = current[part]

    return current


def build_attributes(
    payload,
    definition,
    source_class
):

    attrs = {}

    mappings = definition.get(
        "preserved_vendor_attributes",
        {}
    )

    for output_name, source_path in mappings.items():

        attrs[output_name] = get_path(
            payload,
            source_path
        )


    # Steps v0.1 verified invariant.
    relation = definition.get(
        "generated_source_verified_relation"
    )

    if (
        source_class
            == "XIAOMI_GENERATED"
        and relation
            == "embedded_calories = steps * 0.04"
    ):

        steps = float(
            get_path(
                payload,
                "value.steps"
            )
        )

        calories = float(
            get_path(
                payload,
                "value.calories"
            )
        )

        match = math.isclose(
            calories,
            steps * 0.04,
            rel_tol=0.0,
            abs_tol=1e-5
        )

        if not match:
            raise RuntimeError(
                "Generated steps embedded-calorie "
                "invariant changed"
            )

        attrs[
            "generated_embedded_calories_formula"
        ] = "steps_x_0.04"

        attrs[
            "generated_embedded_calories_formula_match"
        ] = True


    return json.dumps(
        attrs,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    )


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
    def_path = Path(
        args.definition
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

    bucket_anchor_source = definition[
        "bucket_anchor_source"
    ]

    value_source = definition[
        "value_source"
    ]

    bucket_width_seconds = definition.get(
        "bucket_width_seconds"
    )

    bucket_semantics = definition.get(
        "bucket_semantics",
        "VENDOR_UNRESOLVED"
    )

    pipeline_name = definition_id


    if definition.get(
        "temporal_type"
    ) != "BUCKET":

        raise RuntimeError(
            "Definition temporal_type "
            "must be BUCKET"
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

        if schema_version < 5:
            raise RuntimeError(
                "Generic BUCKET pipeline "
                f"requires L3 schema >= 5, "
                f"found {schema_version}"
            )


        # ====================================================
        # Definition registration check
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


        if (
            registered is None
            or registered[
                "definition_sha256"
            ] != definition_sha256
        ):

            raise RuntimeError(
                "Definition checksum mismatch "
                "or definition not registered"
            )


        # ====================================================
        # Freeze L2 observation frontier
        # ====================================================

        l2.execute("BEGIN")


        checkpoint = l3.execute(
            """
            SELECT
                last_l2_observation_id

            FROM processing_checkpoints

            WHERE pipeline_name = ?
            """,
            (
                pipeline_name,
            )
        ).fetchone()


        if checkpoint is None:
            raise RuntimeError(
                "Checkpoint not initialized"
            )


        checkpoint_before = checkpoint[
            "last_l2_observation_id"
        ]


        frontier = l2.execute(
            """
            SELECT COALESCE(
                MAX(id),
                0
            )

            FROM raw_record_observations
            """
        ).fetchone()[0]


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


        valid_classifications = {
            "NEW",
            "REVISION",
            "REOBSERVATION"
        }


        for row in pending:

            if (
                row["classification"]
                not in valid_classifications
            ):
                raise RuntimeError(
                    "Unknown observation classification"
                )

            if (
                row[
                    "observation_dataset"
                ]
                != row["dataset"]
            ):
                raise RuntimeError(
                    "Observation/logical "
                    "dataset mismatch"
                )

            if (
                row[
                    "observation_provider"
                ]
                != row["provider"]
            ):
                raise RuntimeError(
                    "Observation/logical "
                    "provider mismatch"
                )

            if (
                row[
                    "observation_region"
                ]
                != row["region"]
            ):
                raise RuntimeError(
                    "Observation/logical "
                    "region mismatch"
                )


        # ====================================================
        # Pipeline audit run
        # ====================================================

        run_id = (
            metric
            + "-bucket-inc-"
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

                        "checkpoint_before":
                            checkpoint_before,

                        "frontier":
                            frontier
                    },
                    separators=(",", ":")
                )
            )
        )

        l3.commit()


        target_new = 0
        target_revision = 0
        target_reobservation = 0
        non_target = 0

        inserted = 0
        skipped_existing = 0
        superseded = 0


        # ====================================================
        # Atomic incremental batch
        # ====================================================

        l3.execute(
            "BEGIN IMMEDIATE"
        )


        try:

            for row in pending:

                if row["dataset"] != dataset:
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


                payload = decode_raw(
                    row["raw_json"]
                )


                anchor = get_path(
                    payload,
                    bucket_anchor_source
                )

                value = get_path(
                    payload,
                    value_source
                )


                if int(anchor) != int(
                    row["raw_time"]
                ):
                    raise RuntimeError(
                        "Bucket anchor != "
                        "logical raw_time"
                    )


                sid = str(
                    row["raw_sid"]
                )

                source_class = (
                    source_class_from_sid(
                        sid
                    )
                )


                attributes_json = (
                    build_attributes(
                        payload,
                        definition,
                        source_class
                    )
                )


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


                exact_current = any(
                    old["status"] == "CURRENT"
                    and old[
                        "l2_raw_version_id"
                    ] == row[
                        "raw_version_id"
                    ]

                    for old in existing
                )


                if exact_current:

                    skipped_existing += 1
                    continue


                current_existing = [
                    old

                    for old in existing

                    if old["status"]
                        == "CURRENT"
                ]


                if (
                    classification == "NEW"
                    and current_existing
                ):
                    raise RuntimeError(
                        "NEW has different "
                        "existing CURRENT fact"
                    )


                if (
                    classification
                        == "REVISION"
                    and not current_existing
                ):
                    raise RuntimeError(
                        "REVISION has no prior "
                        "CURRENT fact"
                    )


                if (
                    classification
                        == "REVISION"
                ):

                    stale_ids = [
                        old["fact_id"]

                        for old
                        in current_existing
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
                        epoch_to_utc_iso(
                            anchor
                        ),
                        bucket_width_seconds,
                        bucket_semantics,
                        float(value),
                        unit,
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
                        ?, ?, ?,
                        'SOURCE',
                        ?
                    )
                    """,
                    (
                        fact_id,
                        row[
                            "logical_record_id"
                        ],
                        row[
                            "raw_version_id"
                        ],
                        now
                    )
                )


                inserted += 1


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
                                dataset,

                            "metric":
                                metric,

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


        print("=" * 84)
        print(
            "GENERIC BUCKET INCREMENTAL RUNNER v0.1"
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
            "CURRENT facts           =",
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
                "GENERIC BUCKET INCREMENTAL = PASS"
            )

        else:

            print("RESULT = FAIL")
            raise SystemExit(1)


    finally:

        l2.close()
        l3.close()


if __name__ == "__main__":
    main()

