import hashlib
import json
import sqlite3
import uuid
from collections import Counter
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
    / "sleep_v0_1.json"
)

DEFINITION_ID = "normalize.sleep"
DEFINITION_VERSION = "0.1"

EPISODE_METRIC = "sleep_source_episode"
SEGMENT_METRIC = "sleep_vendor_stage_segment"

PIPELINE = "normalize.sleep"


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def iso(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).isoformat(timespec="seconds")


def source_class(sid):

    sid = str(sid)

    if sid.startswith("hlth.gen_"):
        return "XIAOMI_GENERATED"

    return "NUMERIC_SOURCE"


def decode(raw_json):

    outer = json.loads(raw_json)

    value = outer.get("value")

    if isinstance(value, str):
        inner = json.loads(value)
    elif isinstance(value, dict):
        inner = value
    else:
        raise RuntimeError(
            "sleep value is not JSON"
        )

    return outer, inner


definition_bytes = DEF.read_bytes()

definition_sha256 = hashlib.sha256(
    definition_bytes
).hexdigest()

definition = json.loads(
    definition_bytes.decode("utf-8-sig")
)

stage_map = definition[
    "stage_mapping"
]


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

    if schema_version < 6:
        raise RuntimeError(
            "Sleep normalizer requires "
            f"L3 schema >= 6, found {schema_version}"
        )


    # ========================================================
    # Freeze L2 latest state
    # ========================================================

    l2.execute("BEGIN")

    frontier = l2.execute(
        """
        SELECT COALESCE(MAX(id), 0)
        FROM raw_record_observations
        """
    ).fetchone()[0]


    rows = l2.execute(
        """
        WITH latest AS (
            SELECT
                logical_record_id,
                MAX(id) AS raw_version_id

            FROM raw_record_versions

            GROUP BY logical_record_id
        )

        SELECT
            lr.id AS logical_record_id,
            lr.provider,
            lr.raw_sid,
            lr.raw_time,

            rv.id AS raw_version_id,
            rv.raw_json,
            rv.zone_name,
            rv.zone_offset

        FROM logical_records lr

        JOIN latest
          ON latest.logical_record_id =
             lr.id

        JOIN raw_record_versions rv
          ON rv.id =
             latest.raw_version_id

        WHERE lr.dataset = 'sleep'

        ORDER BY lr.id
        """
    ).fetchall()


    # ========================================================
    # Normalize in memory
    # ========================================================

    expected_by_logical = {}

    stage_counts = Counter()

    episode_source_counts = Counter()

    expected_episode_count = 0
    expected_segment_count = 0


    for row in rows:

        outer, inner = decode(
            row["raw_json"]
        )

        sid = str(
            row["raw_sid"]
        )

        cls = source_class(sid)

        episode_source_counts[
            cls
        ] += 1


        bedtime = inner.get(
            "bedtime"
        )

        wake = inner.get(
            "wake_up_time"
        )


        if bedtime is None:
            raise RuntimeError(
                f"logical {row['logical_record_id']}: "
                "missing bedtime"
            )

        if wake is None:
            raise RuntimeError(
                f"logical {row['logical_record_id']}: "
                "missing wake_up_time"
            )

        bedtime = int(bedtime)
        wake = int(wake)


        if wake <= bedtime:
            raise RuntimeError(
                f"logical {row['logical_record_id']}: "
                "invalid episode interval"
            )


        # Xiaomi sleep logical raw_time behaves as
        # the episode/wake anchor in the current contract.
        if int(row["raw_time"]) != wake:
            raise RuntimeError(
                f"logical {row['logical_record_id']}: "
                "raw_time != wake_up_time"
            )


        vendor_fields = {
            key: value

            for key, value
            in inner.items()

            if key != "items"
            and not isinstance(
                value,
                (list, dict)
            )
        }


        items = inner.get(
            "items",
            []
        )

        if items is None:
            items = []

        if not isinstance(items, list):
            raise RuntimeError(
                f"logical {row['logical_record_id']}: "
                "items is not list"
            )


        expected = []


        episode_attrs = json.dumps(
            {
                "item_count":
                    len(items),

                "vendor_fields":
                    vendor_fields
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":")
        )


        expected.append(
            {
                "metric":
                    EPISODE_METRIC,

                "start":
                    iso(bedtime),

                "end":
                    iso(wake),

                "duration":
                    wake - bedtime,

                "semantics":
                    "XIAOMI_SOURCE_EPISODE",

                "value_code":
                    None,

                "unit":
                    None,

                "source_sid":
                    sid,

                "source_class":
                    cls,

                "provider":
                    row["provider"],

                "timezone_name":
                    row["zone_name"],

                "timezone_offset":
                    row["zone_offset"],

                "attributes_json":
                    episode_attrs,

                "segment_index":
                    -1
            }
        )

        expected_episode_count += 1


        for index, item in enumerate(
            items
        ):

            if not isinstance(
                item,
                dict
            ):
                raise RuntimeError(
                    f"logical {row['logical_record_id']}: "
                    f"item {index} is not object"
                )


            start = item.get(
                "start_time"
            )

            end = item.get(
                "end_time"
            )

            state = item.get(
                "state"
            )


            if (
                start is None
                or end is None
                or state is None
            ):
                raise RuntimeError(
                    f"logical {row['logical_record_id']}: "
                    f"item {index} missing fields"
                )


            start = int(start)
            end = int(end)
            state_key = str(
                int(state)
            )


            if end < start:
                raise RuntimeError(
                    f"logical {row['logical_record_id']}: "
                    f"item {index} invalid interval"
                )


            if state_key not in stage_map[
                cls
            ]:
                raise RuntimeError(
                    f"logical {row['logical_record_id']}: "
                    f"unknown state {state_key} "
                    f"for {cls}"
                )


            stage = stage_map[
                cls
            ][
                state_key
            ]


            stage_counts[
                (
                    cls,
                    stage
                )
            ] += 1


            segment_attrs = json.dumps(
                {
                    "segment_index":
                        index,

                    "xiaomi_state_code":
                        int(state),

                    "stage_mapping_version":
                        "sleep-stage-map-v0.1"
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":")
            )


            expected.append(
                {
                    "metric":
                        SEGMENT_METRIC,

                    "start":
                        iso(start),

                    "end":
                        iso(end),

                    "duration":
                        end - start,

                    "semantics":
                        "XIAOMI_VENDOR_STAGE_SEGMENT",

                    "value_code":
                        stage,

                    "unit":
                        "vendor_stage",

                    "source_sid":
                        sid,

                    "source_class":
                        cls,

                    "provider":
                        row["provider"],

                    "timezone_name":
                        row["zone_name"],

                    "timezone_offset":
                        row["zone_offset"],

                    "attributes_json":
                        segment_attrs,

                    "segment_index":
                        index
                }
            )

            expected_segment_count += 1


        expected_by_logical[
            row["logical_record_id"]
        ] = {
            "raw_version_id":
                row["raw_version_id"],

            "facts":
                expected
        }


    # ========================================================
    # Audit run
    # ========================================================

    run_id = (
        "sleep-full-"
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
            str(L2_DB),

            l2.execute(
                "PRAGMA user_version"
            ).fetchone()[0],

            utc_now(),

            json.dumps(
                {
                    "dataset":
                        "sleep",

                    "definition_id":
                        DEFINITION_ID,

                    "definition_version":
                        DEFINITION_VERSION,

                    "logical_records":
                        len(rows),

                    "expected_episode_facts":
                        expected_episode_count,

                    "expected_segment_facts":
                        expected_segment_count
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

        # ====================================================
        # Definition registry
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
                    "Sleep source episode and vendor segment normalizer"
                )
            )

        elif (
            registered[
                "definition_sha256"
            ]
            != definition_sha256
        ):

            raise RuntimeError(
                "Sleep definition checksum mismatch"
            )


        # ====================================================
        # Per logical record materialization
        # ====================================================

        for logical_id, spec in (
            expected_by_logical.items()
        ):

            raw_version_id = spec[
                "raw_version_id"
            ]

            expected_facts = spec[
                "facts"
            ]


            existing = l3.execute(
                """
                SELECT
                    fr.id AS fact_id,
                    fr.metric,
                    fr.status,

                    inf.start_time_utc,
                    inf.end_time_utc,
                    inf.interval_semantics,
                    inf.value_code,
                    inf.attributes_json,

                    fp.l2_raw_version_id

                FROM fact_registry fr

                JOIN normalized_interval_facts inf
                  ON inf.fact_id = fr.id

                JOIN fact_provenance fp
                  ON fp.fact_id = fr.id

                WHERE
                    fr.definition_id =
                        'normalize.sleep'
                    AND fr.definition_version =
                        '0.1'
                    AND fp.l2_logical_record_id = ?

                ORDER BY fr.id
                """,
                (
                    logical_id,
                )
            ).fetchall()


            current_existing = [
                x
                for x in existing
                if x["status"]
                    == "CURRENT"
            ]


            def signature_existing(x):

                attrs = json.loads(
                    x["attributes_json"]
                    or "{}"
                )

                return (
                    x["metric"],
                    x["start_time_utc"],
                    x["end_time_utc"],
                    x["value_code"],
                    x["interval_semantics"],
                    attrs.get(
                        "segment_index",
                        -1
                    ),
                    x["l2_raw_version_id"]
                )


            def signature_expected(x):

                return (
                    x["metric"],
                    x["start"],
                    x["end"],
                    x["value_code"],
                    x["semantics"],
                    x["segment_index"],
                    raw_version_id
                )


            existing_signatures = {
                signature_existing(x)

                for x in current_existing
            }

            expected_signatures = {
                signature_expected(x)

                for x in expected_facts
            }


            exact = (
                len(current_existing)
                    == len(expected_facts)

                and existing_signatures
                    == expected_signatures
            )


            if exact:

                skipped += len(
                    expected_facts
                )

                continue


            stale_ids = [
                x["fact_id"]

                for x in current_existing
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
                        utc_now(),
                        *stale_ids
                    )
                )

                superseded += len(
                    stale_ids
                )


            for fact in expected_facts:

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
                        'INTERVAL',
                        ?,
                        'VENDOR_INFERRED',
                        'normalize.sleep',
                        '0.1',
                        'CURRENT',
                        ?,
                        ?
                    )
                    """,
                    (
                        fact["metric"],
                        now,
                        now
                    )
                )


                fact_id = (
                    cursor.lastrowid
                )


                l3.execute(
                    """
                    INSERT INTO normalized_interval_facts (
                        fact_id,
                        start_time_utc,
                        end_time_utc,
                        duration_seconds,
                        interval_semantics,
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
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        fact_id,
                        fact["start"],
                        fact["end"],
                        fact["duration"],
                        fact["semantics"],
                        fact["value_code"],
                        fact["unit"],
                        fact["provider"],
                        fact["source_sid"],
                        fact["source_class"],
                        fact["timezone_name"],
                        fact["timezone_offset"],
                        fact["attributes_json"]
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
                        logical_id,
                        raw_version_id,
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
    # Acceptance
    # ========================================================

    current_rows = l3.execute(
        """
        SELECT
            fr.id AS fact_id,
            fr.metric,
            fr.fact_kind,
            fr.evidence_type,

            inf.start_time_utc,
            inf.end_time_utc,
            inf.duration_seconds,
            inf.interval_semantics,
            inf.value_code,
            inf.source_class,
            inf.attributes_json,

            fp.l2_logical_record_id,
            fp.l2_raw_version_id

        FROM fact_registry fr

        JOIN normalized_interval_facts inf
          ON inf.fact_id = fr.id

        JOIN fact_provenance fp
          ON fp.fact_id = fr.id

        WHERE
            fr.status = 'CURRENT'
            AND fr.definition_id =
                'normalize.sleep'
            AND fr.definition_version =
                '0.1'
        """
    ).fetchall()


    episodes = [
        x
        for x in current_rows
        if x["metric"]
            == EPISODE_METRIC
    ]

    segments = [
        x
        for x in current_rows
        if x["metric"]
            == SEGMENT_METRIC
    ]


    latest_map = {
        logical_id:
            spec["raw_version_id"]

        for logical_id, spec
        in expected_by_logical.items()
    }


    raw_version_mismatch = [
        x["fact_id"]

        for x in current_rows

        if latest_map.get(
            x["l2_logical_record_id"]
        ) != x[
            "l2_raw_version_id"
        ]
    ]


    episode_by_logical = Counter(
        x["l2_logical_record_id"]
        for x in episodes
    )


    expected_segment_by_logical = {
        logical_id:
            len(spec["facts"]) - 1

        for logical_id, spec
        in expected_by_logical.items()
    }


    actual_segment_by_logical = Counter(
        x["l2_logical_record_id"]
        for x in segments
    )


    segment_count_mismatch = [
        logical_id

        for logical_id, expected_count
        in expected_segment_by_logical.items()

        if actual_segment_by_logical[
            logical_id
        ] != expected_count
    ]


    duration_mismatch = [
        x["fact_id"]

        for x in current_rows

        if x["duration_seconds"]
            != int(
                (
                    datetime.fromisoformat(
                        x["end_time_utc"]
                    )
                    -
                    datetime.fromisoformat(
                        x["start_time_utc"]
                    )
                ).total_seconds()
            )
    ]


    current_stage_counts = Counter(
        (
            x["source_class"],
            x["value_code"]
        )

        for x in segments
    )


    canonical_session_count = l3.execute(
        """
        SELECT COUNT(*)

        FROM fact_registry

        WHERE
            status = 'CURRENT'
            AND metric =
                'sleep_canonical_session'
        """
    ).fetchone()[0]


    duplicate_signatures = l3.execute(
        """
        SELECT COUNT(*)

        FROM (
            SELECT
                fr.metric,
                fp.l2_logical_record_id,
                fp.l2_raw_version_id,
                inf.start_time_utc,
                inf.end_time_utc,
                COALESCE(
                    inf.value_code,
                    ''
                ) AS value_code,
                inf.interval_semantics,
                COUNT(*) AS n

            FROM fact_registry fr

            JOIN normalized_interval_facts inf
              ON inf.fact_id = fr.id

            JOIN fact_provenance fp
              ON fp.fact_id = fr.id

            WHERE
                fr.status = 'CURRENT'
                AND fr.definition_id =
                    'normalize.sleep'
                AND fr.definition_version =
                    '0.1'

            GROUP BY
                fr.metric,
                fp.l2_logical_record_id,
                fp.l2_raw_version_id,
                inf.start_time_utc,
                inf.end_time_utc,
                COALESCE(
                    inf.value_code,
                    ''
                ),
                inf.interval_semantics

            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]


    checks = [
        (
            "One source episode per logical record",
            len(episodes)
                == len(rows)
            and all(
                episode_by_logical[
                    logical_id
                ] == 1

                for logical_id
                in expected_by_logical
            )
        ),
        (
            "All vendor segments materialized",
            len(segments)
                == expected_segment_count
            and not segment_count_mismatch
        ),
        (
            "Exact total fact count",
            len(current_rows)
                == (
                    expected_episode_count
                    + expected_segment_count
                )
        ),
        (
            "Latest raw-version provenance",
            not raw_version_mismatch
        ),
        (
            "Fact kind = INTERVAL",
            all(
                x["fact_kind"]
                    == "INTERVAL"

                for x in current_rows
            )
        ),
        (
            "Evidence = VENDOR_INFERRED",
            all(
                x["evidence_type"]
                    == "VENDOR_INFERRED"

                for x in current_rows
            )
        ),
        (
            "Interval durations exact",
            not duration_mismatch
        ),
        (
            "Verified stage mapping preserved",
            current_stage_counts
                == stage_counts
        ),
        (
            "Generated + numeric episodes coexist",
            episode_source_counts[
                "XIAOMI_GENERATED"
            ] == 7
            and episode_source_counts[
                "NUMERIC_SOURCE"
            ] == 7
        ),
        (
            "No canonical session invented",
            canonical_session_count == 0
        ),
        (
            "No duplicate CURRENT intervals",
            duplicate_signatures == 0
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


    # ========================================================
    # Checkpoint bootstrap only after exact acceptance
    # ========================================================

    if status == "PASS":

        l3.execute(
            """
            INSERT INTO processing_checkpoints (
                pipeline_name,
                last_l2_observation_id,
                last_successful_run_id,
                updated_at_utc
            )
            VALUES (?, ?, ?, ?)

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
                PIPELINE,
                frontier,
                run_id,
                utc_now()
            )
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
            utc_now(),

            json.dumps(
                {
                    "dataset":
                        "sleep",

                    "inserted":
                        inserted,

                    "skipped":
                        skipped,

                    "superseded":
                        superseded,

                    "episodes":
                        len(episodes),

                    "segments":
                        len(segments),

                    "current_total":
                        len(current_rows),

                    "checkpoint":
                        frontier
                        if status == "PASS"
                        else None
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

    print("=" * 90)
    print(
        "L3 SLEEP SOURCE EPISODE + SEGMENT NORMALIZER v0.1"
    )
    print("=" * 90)

    print(
        "L2 logical records =",
        len(rows)
    )

    print(
        "expected episodes  =",
        expected_episode_count
    )

    print(
        "expected segments  =",
        expected_segment_count
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

    print(
        "CURRENT episodes   =",
        len(episodes)
    )

    print(
        "CURRENT segments   =",
        len(segments)
    )

    print(
        "CURRENT total      =",
        len(current_rows)
    )

    print()

    print(
        "stage counts:"
    )

    for key in sorted(
        current_stage_counts
    ):

        print(
            " ",
            key,
            "=",
            current_stage_counts[key]
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
        "checkpoint =",
        frontier
        if status == "PASS"
        else "NOT UPDATED"
    )

    print(
        "run_id =",
        run_id
    )

    print()

    if status == "PASS":

        print("RESULT = PASS")
        print(
            "SLEEP L3A MATERIALIZATION = PASS"
        )

    else:

        print("RESULT = FAIL")
        raise SystemExit(1)


finally:

    l2.close()
    l3.close()

