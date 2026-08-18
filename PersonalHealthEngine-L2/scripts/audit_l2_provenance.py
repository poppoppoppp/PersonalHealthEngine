from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_hash(raw_record: dict) -> str:
    return sha256_bytes(
        canonical_json(raw_record).encode("utf-8")
    )


def logical_key(
    provider,
    region,
    dataset,
    raw_key,
    raw_sid,
    raw_time,
):
    identity = {
        "provider": str(provider),
        "region": str(region),
        "dataset": str(dataset),
        "key": str(raw_key),
        "sid": str(raw_sid),
        "time": int(raw_time),
    }

    return sha256_bytes(
        canonical_json(identity).encode("utf-8")
    )


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        artifacts = conn.execute(
            """
            SELECT
                id,
                capture_id,
                dataset,
                relative_path,
                archived_path,
                file_sha256
            FROM source_artifacts
            ORDER BY id
            """
        ).fetchall()

        counters = {
            "artifacts": 0,
            "artifact_hash_ok": 0,
            "observations": 0,
            "archive_lines": 0,
            "payload_hash_ok": 0,
            "logical_key_ok": 0,
            "raw_json_ok": 0,
            "envelope_ok": 0,
            "errors": 0,
        }

        print("========== L2 PROVENANCE AUDIT ==========")

        for artifact in artifacts:
            counters["artifacts"] += 1

            path = Path(artifact["archived_path"])

            if not path.exists():
                print(
                    f"ERROR missing archive: {path}"
                )
                counters["errors"] += 1
                continue

            actual_file_hash = sha256_file(path)

            if actual_file_hash != artifact["file_sha256"]:
                print(
                    f"ERROR artifact hash mismatch: {path}"
                )
                counters["errors"] += 1
                continue

            counters["artifact_hash_ok"] += 1

            source_lines = {}

            with path.open(
                "r",
                encoding="utf-8-sig",
            ) as f:

                for ordinal, line in enumerate(
                    f,
                    start=1,
                ):
                    line = line.strip()

                    if not line:
                        continue

                    source_lines[ordinal] = line

            counters["archive_lines"] += len(
                source_lines
            )

            observations = conn.execute(
                """
                SELECT
                    o.id AS observation_id,
                    o.source_record_ordinal,
                    o.capture_id,
                    o.dataset,
                    o.provider,
                    o.region,

                    rv.payload_sha256,
                    rv.raw_json,

                    lr.logical_key,
                    lr.raw_key,
                    lr.raw_sid,
                    lr.raw_time

                FROM raw_record_observations o

                JOIN raw_record_versions rv
                  ON rv.id = o.raw_record_version_id

                JOIN logical_records lr
                  ON lr.id = rv.logical_record_id

                WHERE o.source_artifact_id = ?

                ORDER BY o.source_record_ordinal
                """,
                (artifact["id"],),
            ).fetchall()

            if len(observations) != len(source_lines):
                print(
                    f"ERROR observation count mismatch: "
                    f"{path.name} "
                    f"archive={len(source_lines)} "
                    f"db={len(observations)}"
                )
                counters["errors"] += 1

            for row in observations:
                counters["observations"] += 1

                ordinal = row[
                    "source_record_ordinal"
                ]

                line = source_lines.get(ordinal)

                if line is None:
                    print(
                        f"ERROR missing ordinal "
                        f"{ordinal} in {path}"
                    )
                    counters["errors"] += 1
                    continue

                try:
                    envelope = json.loads(line)
                except Exception as e:
                    print(
                        f"ERROR invalid JSON "
                        f"{path}:{ordinal}: {e}"
                    )
                    counters["errors"] += 1
                    continue

                raw = envelope.get("raw_record")

                if not isinstance(raw, dict):
                    print(
                        f"ERROR raw_record missing "
                        f"{path}:{ordinal}"
                    )
                    counters["errors"] += 1
                    continue

                envelope_ok = (
                    envelope.get("capture_id")
                    == row["capture_id"]

                    and envelope.get("dataset")
                    == row["dataset"]

                    and envelope.get("provider")
                    == row["provider"]

                    and envelope.get("region")
                    == row["region"]
                )

                if envelope_ok:
                    counters["envelope_ok"] += 1
                else:
                    print(
                        f"ERROR envelope mismatch "
                        f"{path}:{ordinal}"
                    )
                    counters["errors"] += 1

                calculated_payload_hash = (
                    payload_hash(raw)
                )

                if (
                    calculated_payload_hash
                    == row["payload_sha256"]
                    == envelope.get(
                        "raw_record_sha256"
                    )
                ):
                    counters[
                        "payload_hash_ok"
                    ] += 1
                else:
                    print(
                        f"ERROR payload hash mismatch "
                        f"{path}:{ordinal}"
                    )
                    counters["errors"] += 1

                calculated_logical_key = (
                    logical_key(
                        envelope["provider"],
                        envelope["region"],
                        envelope["dataset"],
                        raw["key"],
                        raw["sid"],
                        raw["time"],
                    )
                )

                identity_fields_match = (
                    str(raw["key"])
                    == row["raw_key"]

                    and str(raw["sid"])
                    == row["raw_sid"]

                    and int(raw["time"])
                    == row["raw_time"]
                )

                if (
                    calculated_logical_key
                    == row["logical_key"]
                    and identity_fields_match
                ):
                    counters[
                        "logical_key_ok"
                    ] += 1
                else:
                    print(
                        f"ERROR logical identity mismatch "
                        f"{path}:{ordinal}"
                    )
                    counters["errors"] += 1

                if (
                    canonical_json(raw)
                    == row["raw_json"]
                ):
                    counters["raw_json_ok"] += 1
                else:
                    print(
                        f"ERROR raw_json mismatch "
                        f"{path}:{ordinal}"
                    )
                    counters["errors"] += 1

        print()
        print("========== AUDIT SUMMARY ==========")
        print(
            f"artifacts          = "
            f"{counters['artifacts']}"
        )
        print(
            f"artifact_hash_ok   = "
            f"{counters['artifact_hash_ok']}"
        )
        print(
            f"archive_lines      = "
            f"{counters['archive_lines']}"
        )
        print(
            f"observations       = "
            f"{counters['observations']}"
        )
        print(
            f"envelope_ok        = "
            f"{counters['envelope_ok']}"
        )
        print(
            f"payload_hash_ok    = "
            f"{counters['payload_hash_ok']}"
        )
        print(
            f"logical_key_ok     = "
            f"{counters['logical_key_ok']}"
        )
        print(
            f"raw_json_ok        = "
            f"{counters['raw_json_ok']}"
        )
        print(
            f"integrity_check    = "
            f"{integrity}"
        )
        print(
            f"foreign_key_errors = "
            f"{len(fk_errors)}"
        )
        print(
            f"errors             = "
            f"{counters['errors']}"
        )

        print()

        expected_observations = conn.execute(
            """
            SELECT COUNT(*)
            FROM raw_record_observations
            """
        ).fetchone()[0]

        if (
            counters["artifacts"] == 81
            and counters["artifact_hash_ok"] == 81
            and counters["archive_lines"]
                == expected_observations
            and counters["observations"]
                == expected_observations
            and counters["envelope_ok"]
                == expected_observations
            and counters["payload_hash_ok"]
                == expected_observations
            and counters["logical_key_ok"]
                == expected_observations
            and counters["raw_json_ok"]
                == expected_observations
            and counters["errors"] == 0
            and integrity == "ok"
            and len(fk_errors) == 0
        ):
            print("RESULT = PASS")
            print(
                "L2 PROVENANCE TRACEABILITY = PASS"
            )
        else:
            print("RESULT = FAIL")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
