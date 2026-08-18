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
def_path = Path(args.definition)

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

pipeline = definition_id


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

    l2.execute("BEGIN")

    frontier = l2.execute(
        """
        SELECT COALESCE(MAX(id), 0)
        FROM raw_record_observations
        """
    ).fetchone()[0]

    l2_rows = l2.execute(
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
            latest.raw_version_id

        FROM logical_records lr

        JOIN latest
          ON latest.logical_record_id =
             lr.id

        WHERE lr.dataset = ?
        """,
        (dataset,)
    ).fetchall()

    l2_state = {
        x["logical_record_id"]:
            x["raw_version_id"]
        for x in l2_rows
    }

    l3_rows = l3.execute(
        """
        SELECT
            fp.l2_logical_record_id,
            fp.l2_raw_version_id

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

    l3_state = {}

    duplicate = False

    for x in l3_rows:

        logical_id = x[
            "l2_logical_record_id"
        ]

        if logical_id in l3_state:
            duplicate = True

        l3_state[logical_id] = x[
            "l2_raw_version_id"
        ]

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

    full_run = l3.execute(
        """
        SELECT run_id

        FROM pipeline_runs

        WHERE
            mode = 'FULL_REBUILD'
            AND status = 'PASS'
            AND json_extract(
                details_json,
                '$.dataset'
            ) = ?

        ORDER BY started_at_utc DESC

        LIMIT 1
        """,
        (dataset,)
    ).fetchone()

    checks = [
        (
            "Definition checksum",
            registered is not None
            and registered[
                "definition_sha256"
            ] == definition_sha256
        ),
        (
            "Successful full build exists",
            full_run is not None
        ),
        (
            "No duplicate CURRENT",
            not duplicate
        ),
        (
            "Logical set parity",
            set(l2_state)
                == set(l3_state)
        ),
        (
            "Raw-version parity",
            l2_state == l3_state
        ),
    ]

    if not all(
        ok
        for _, ok in checks
    ):
        raise RuntimeError(
            "Checkpoint preconditions failed"
        )

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
            pipeline,
            frontier,
            full_run["run_id"],
            utc_now()
        )
    )

    l3.commit()

    checkpoint = l3.execute(
        """
        SELECT last_l2_observation_id

        FROM processing_checkpoints

        WHERE pipeline_name = ?
        """,
        (pipeline,)
    ).fetchone()[0]

    checks += [
        (
            "Checkpoint at frontier",
            checkpoint == frontier
        ),
        (
            "SQLite integrity",
            l3.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0] == "ok"
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

    print("=" * 84)
    print(
        "GENERIC BUCKET CHECKPOINT BOOTSTRAP v0.1"
    )
    print("=" * 84)

    print(
        "dataset            =",
        dataset
    )

    print(
        "L2 frontier       =",
        frontier
    )

    print(
        "L2 logical        =",
        len(l2_state)
    )

    print(
        "L3 CURRENT        =",
        len(l3_state)
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
        checkpoint
    )

    print()

    if passed == len(checks):
        print("RESULT = PASS")
        print(
            "GENERIC BUCKET CHECKPOINT = PASS"
        )
    else:
        print("RESULT = FAIL")
        raise SystemExit(1)

finally:

    l2.close()
    l3.close()

