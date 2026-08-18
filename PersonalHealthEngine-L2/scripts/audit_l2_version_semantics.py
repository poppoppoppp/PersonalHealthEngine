import sqlite3
from pathlib import Path
from collections import defaultdict

DB = Path(
    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3"
)


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    try:
        print("========== L2 VERSION SEMANTICS AUDIT ==========")

        logical_count = conn.execute(
            "SELECT COUNT(*) FROM logical_records"
        ).fetchone()[0]

        version_count = conn.execute(
            "SELECT COUNT(*) FROM raw_record_versions"
        ).fetchone()[0]

        observation_count = conn.execute(
            "SELECT COUNT(*) FROM raw_record_observations"
        ).fetchone()[0]

        classification = {
            row["classification"]: row["n"]
            for row in conn.execute(
                """
                SELECT classification, COUNT(*) AS n
                FROM raw_record_observations
                GROUP BY classification
                """
            )
        }

        new_count = classification.get("NEW", 0)
        revision_count = classification.get("REVISION", 0)
        reobs_count = classification.get("REOBSERVATION", 0)

        expected_revisions = version_count - logical_count
        expected_reobs = observation_count - version_count

        print()
        print("========== GLOBAL INVARIANTS ==========")
        print(f"logical_records        = {logical_count}")
        print(f"raw_record_versions    = {version_count}")
        print(f"observations           = {observation_count}")
        print()
        print(f"NEW                     = {new_count}")
        print(f"REVISION                = {revision_count}")
        print(f"REOBSERVATION           = {reobs_count}")
        print()
        print(f"expected_NEW            = {logical_count}")
        print(f"expected_REVISION       = {expected_revisions}")
        print(f"expected_REOBSERVATION  = {expected_reobs}")

        print()
        print("========== REVISION DATASETS ==========")

        revision_rows = conn.execute(
            """
            SELECT
                lr.dataset,
                COUNT(*) AS revised_logical_records,
                SUM(x.version_count - 1) AS revision_versions
            FROM (
                SELECT
                    logical_record_id,
                    COUNT(*) AS version_count
                FROM raw_record_versions
                GROUP BY logical_record_id
                HAVING COUNT(*) > 1
            ) x
            JOIN logical_records lr
              ON lr.id = x.logical_record_id
            GROUP BY lr.dataset
            ORDER BY lr.dataset
            """
        ).fetchall()

        for row in revision_rows:
            print(
                f"{row['dataset']:22s} "
                f"revised_records={row['revised_logical_records']:<5} "
                f"revision_versions={row['revision_versions']}"
            )

        print()
        print("========== LATE ARRIVALS ==========")

        late_rows = conn.execute(
            """
            SELECT
                dataset,
                COUNT(*) AS n
            FROM raw_record_observations
            WHERE late_arrival = 1
            GROUP BY dataset
            ORDER BY dataset
            """
        ).fetchall()

        late_total = 0

        for row in late_rows:
            late_total += row["n"]
            print(
                f"{row['dataset']:22s} "
                f"{row['n']}"
            )

        print(f"{'TOTAL':22s} {late_total}")

        bad_late = conn.execute(
            """
            SELECT COUNT(*)
            FROM raw_record_observations
            WHERE late_arrival = 1
              AND classification <> 'NEW'
            """
        ).fetchone()[0]

        duplicate_new = conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT rv.logical_record_id
                FROM raw_record_observations o
                JOIN raw_record_versions rv
                  ON rv.id = o.raw_record_version_id
                WHERE o.classification = 'NEW'
                GROUP BY rv.logical_record_id
                HAVING COUNT(*) <> 1
            )
            """
        ).fetchone()[0]

        multi_version_math = conn.execute(
            """
            SELECT COALESCE(SUM(version_count - 1), 0)
            FROM (
                SELECT
                    logical_record_id,
                    COUNT(*) AS version_count
                FROM raw_record_versions
                GROUP BY logical_record_id
            )
            """
        ).fetchone()[0]

        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        print()
        print("========== CONSISTENCY CHECK ==========")
        print(f"revision_math          = {multi_version_math}")
        print(f"bad_late_arrivals      = {bad_late}")
        print(f"invalid_NEW_groups     = {duplicate_new}")
        print(f"integrity_check        = {integrity}")
        print(f"foreign_key_errors     = {len(fk_errors)}")

        passed = (
            new_count == logical_count
            and revision_count == expected_revisions
            and revision_count == multi_version_math
            and reobs_count == expected_reobs
            and new_count + revision_count + reobs_count
                == observation_count
            and bad_late == 0
            and duplicate_new == 0
            and integrity == "ok"
            and len(fk_errors) == 0
        )

        print()

        if passed:
            print("RESULT = PASS")
            print("L2 REVISION SEMANTICS = PASS")
            print("L2 LATE-ARRIVAL SEMANTICS = PASS")
        else:
            print("RESULT = FAIL")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
