from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


L1_ROOT = Path(r"D:\PersonalHealthEngine-L1Lab\xiaomi-raw-collector")
CAPTURES_ROOT = L1_ROOT / "captures"

L2_ROOT = Path(r"D:\PersonalHealthEngine-L2")
DB_PATH = L2_ROOT / "db" / "personal_health_raw.sqlite3"
ARCHIVE_ROOT = L2_ROOT / "archive"
BACKUP_ROOT = L2_ROOT / "backups"

EXPECTED_COLLECTOR = "xiaomi-raw-collector"
EXPECTED_VERSION = "0.3.1"

SUPPORTED_DATASETS = {
    "steps",
    "calories",
    "sleep",
    "heart_rate",
    "resting_heart_rate",
    "spo2",
    "stress",
    "abnormal_heart_beat",
    "sport_records",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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


def integer_or_none(value):
    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA journal_mode = WAL")

    return conn


def make_backup():
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    backup_path = (
        BACKUP_ROOT /
        f"pre_import_{stamp}.sqlite3"
    )

    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(backup_path)

    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    return backup_path


def load_manifest(capture_dir: Path):
    manifest_path = capture_dir / "manifest.json"

    raw_bytes = manifest_path.read_bytes()
    raw_text = raw_bytes.decode("utf-8-sig")

    return (
        json.loads(raw_text),
        sha256_bytes(raw_bytes),
        raw_text,
    )


def capture_dirs():
    result = []

    for p in CAPTURES_ROOT.iterdir():

        if not p.is_dir():
            continue

        if not (p / "manifest.json").exists():
            continue

        manifest, _, _ = load_manifest(p)

        result.append(
            (
                manifest.get(
                    "capture_started_at_utc",
                    ""
                ),
                p,
            )
        )

    result.sort(
        key=lambda x: (x[0], x[1].name)
    )

    return [p for _, p in result]


def archive_file(source: Path, target: Path):
    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source_hash = sha256_file(source)

    if target.exists():

        target_hash = sha256_file(target)

        if target_hash != source_hash:
            raise RuntimeError(
                f"Archive conflict: {target}"
            )

        return source_hash

    shutil.copy2(source, target)

    target_hash = sha256_file(target)

    if target_hash != source_hash:
        raise RuntimeError(
            f"Archive verification failed: {target}"
        )

    return source_hash


def verify_envelope(
    envelope,
    expected_dataset,
    expected_capture_id,
):
    required = {
        "schema_version",
        "provider",
        "region",
        "endpoint",
        "dataset",
        "capture_id",
        "identity_hint",
        "ingested_at_utc",
        "raw_record_sha256",
        "raw_record",
    }

    missing = required - set(envelope)

    if missing:
        raise RuntimeError(
            f"Envelope missing fields: "
            f"{sorted(missing)}"
        )

    if envelope["dataset"] != expected_dataset:
        raise RuntimeError(
            "Envelope dataset mismatch."
        )

    if envelope["capture_id"] != expected_capture_id:
        raise RuntimeError(
            "Envelope capture_id mismatch."
        )

    raw = envelope["raw_record"]

    for field in ("key", "sid", "time"):
        if field not in raw:
            raise RuntimeError(
                f"raw_record missing {field}"
            )


def main():

    if not DB_PATH.exists():
        raise RuntimeError(
            f"L2 database missing: {DB_PATH}"
        )

    backup_path = make_backup()

    run_id = str(uuid.uuid4())
    run_started = utc_now()

    conn = connect_db()

    counters = defaultdict(int)

    prior_coverage = defaultdict(list)

    capture_list = capture_dirs()

    manifests = []

    for capture_dir in capture_list:

        manifest, manifest_hash, manifest_raw = (
            load_manifest(capture_dir)
        )

        if (
            manifest.get("collector")
            != EXPECTED_COLLECTOR
        ):
            raise RuntimeError(
                f"Unexpected collector in "
                f"{capture_dir.name}"
            )

        if (
            manifest.get("collector_version")
            != EXPECTED_VERSION
        ):
            raise RuntimeError(
                f"Unexpected collector version in "
                f"{capture_dir.name}"
            )

        manifests.append(
            (
                capture_dir,
                manifest,
                manifest_hash,
                manifest_raw,
            )
        )

    start_dates = [
        m[1].get("start_date")
        for m in manifests
        if m[1].get("start_date")
    ]

    end_dates = [
        m[1].get("end_date")
        for m in manifests
        if m[1].get("end_date")
    ]

    window_start = (
        min(start_dates)
        if start_dates
        else None
    )

    window_end = (
        max(end_dates)
        if end_dates
        else None
    )

    conn.execute(
        """
        INSERT INTO ingestion_runs (
            run_id,
            started_at_utc,
            window_start,
            window_end,
            status,
            collector_version,
            source_root
        )
        VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)
        """,
        (
            run_id,
            run_started,
            window_start,
            window_end,
            EXPECTED_VERSION,
            str(CAPTURES_ROOT),
        ),
    )

    conn.commit()

    try:
        conn.execute("BEGIN IMMEDIATE")

        for (
            capture_dir,
            manifest,
            manifest_hash,
            manifest_raw,
        ) in manifests:

            capture_id = manifest["capture_id"]

            counters["captures"] += 1

            archive_dir = (
                ARCHIVE_ROOT /
                capture_dir.name
            )

            archive_file(
                capture_dir / "manifest.json",
                archive_dir / "manifest.json",
            )

            existing_capture = conn.execute(
                """
                SELECT manifest_sha256
                FROM captures
                WHERE capture_id = ?
                """,
                (capture_id,),
            ).fetchone()

            now = utc_now()

            if existing_capture is None:

                conn.execute(
                    """
                    INSERT INTO captures (
                        capture_id,
                        provider,
                        region,
                        collector,
                        collector_version,
                        schema_version,
                        start_date,
                        end_date,
                        range_mode,
                        overlap_days,
                        capture_started_at_utc,
                        capture_finished_at_utc,
                        credentials_source,
                        credentials_embedded,
                        runtime_dependency,
                        source_dir,
                        manifest_sha256,
                        manifest_raw_json,
                        first_imported_at_utc,
                        last_seen_at_utc
                    )
                    VALUES (
                        ?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        capture_id,
                        manifest["provider"],
                        manifest["region"],
                        manifest.get("collector"),
                        manifest.get("collector_version"),
                        manifest.get("schema_version"),
                        manifest.get("start_date"),
                        manifest.get("end_date"),
                        manifest.get("range_mode"),
                        manifest.get("overlap_days"),
                        manifest.get(
                            "capture_started_at_utc"
                        ),
                        manifest.get(
                            "capture_finished_at_utc"
                        ),
                        manifest.get(
                            "credentials_source"
                        ),
                        int(
                            bool(
                                manifest.get(
                                    "credentials_embedded"
                                )
                            )
                        ),
                        manifest.get(
                            "runtime_dependency"
                        ),
                        str(capture_dir),
                        manifest_hash,
                        manifest_raw,
                        now,
                        now,
                    ),
                )

            else:

                if (
                    existing_capture["manifest_sha256"]
                    != manifest_hash
                ):
                    raise RuntimeError(
                        f"Manifest changed for "
                        f"capture {capture_id}"
                    )

                conn.execute(
                    """
                    UPDATE captures
                    SET last_seen_at_utc = ?
                    WHERE capture_id = ?
                    """,
                    (now, capture_id),
                )

            manifest_datasets = (
                manifest.get("datasets", {})
            )

            for dataset, meta in (
                manifest_datasets.items()
            ):

                if dataset not in SUPPORTED_DATASETS:
                    continue

                output_file = meta.get("output_file")

                if not output_file:
                    raise RuntimeError(
                        f"{capture_id}/{dataset}: "
                        "output_file missing"
                    )

                source_path = (
                    capture_dir /
                    output_file
                )

                if not source_path.exists():
                    raise RuntimeError(
                        f"Missing source artifact: "
                        f"{source_path}"
                    )

                counters["artifacts_seen"] += 1

                file_hash = sha256_file(
                    source_path
                )

                expected_hash = (
                    meta.get("output_sha256")
                )

                if (
                    expected_hash
                    and file_hash != expected_hash
                ):
                    raise RuntimeError(
                        f"{capture_id}/{dataset}: "
                        "artifact SHA256 mismatch"
                    )

                archived_path = (
                    archive_dir /
                    output_file
                )

                archive_file(
                    source_path,
                    archived_path,
                )

                artifact_row = conn.execute(
                    """
                    SELECT id, file_sha256
                    FROM source_artifacts
                    WHERE capture_id = ?
                      AND relative_path = ?
                    """,
                    (
                        capture_id,
                        output_file,
                    ),
                ).fetchone()

                artifact_new = False

                if artifact_row is None:

                    artifact_new = True

                    cur = conn.execute(
                        """
                        INSERT INTO source_artifacts (
                            capture_id,
                            dataset,
                            endpoint,
                            relative_path,
                            source_path,
                            archived_path,
                            file_sha256,
                            file_size,
                            manifest_output_sha256,
                            first_imported_at_utc,
                            last_verified_at_utc
                        )
                        VALUES (
                            ?,?,?,?,?,?,?,?,?,?,?
                        )
                        """,
                        (
                            capture_id,
                            dataset,
                            meta.get("endpoint"),
                            output_file,
                            str(source_path),
                            str(archived_path),
                            file_hash,
                            source_path.stat().st_size,
                            expected_hash,
                            now,
                            now,
                        ),
                    )

                    artifact_id = cur.lastrowid

                else:

                    artifact_id = artifact_row["id"]

                    if (
                        artifact_row["file_sha256"]
                        != file_hash
                    ):
                        raise RuntimeError(
                            f"Artifact changed: "
                            f"{source_path}"
                        )

                    conn.execute(
                        """
                        UPDATE source_artifacts
                        SET last_verified_at_utc = ?,
                            archived_path = ?
                        WHERE id = ?
                        """,
                        (
                            now,
                            str(archived_path),
                            artifact_id,
                        ),
                    )

                artifact_records = 0
                artifact_new_observations = 0

                with source_path.open(
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

                        counters["records_seen"] += 1
                        artifact_records += 1

                        envelope = json.loads(line)

                        verify_envelope(
                            envelope,
                            dataset,
                            capture_id,
                        )

                        raw = envelope["raw_record"]

                        supplied_hash = (
                            envelope[
                                "raw_record_sha256"
                            ]
                        )

                        calculated_hash = (
                            payload_hash(raw)
                        )

                        if (
                            supplied_hash
                            != calculated_hash
                        ):
                            raise RuntimeError(
                                f"{capture_id}/"
                                f"{dataset}/"
                                f"line {ordinal}: "
                                "payload hash mismatch"
                            )

                        existing_observation = (
                            conn.execute(
                                """
                                SELECT id
                                FROM raw_record_observations
                                WHERE source_artifact_id = ?
                                  AND source_record_ordinal = ?
                                """,
                                (
                                    artifact_id,
                                    ordinal,
                                ),
                            ).fetchone()
                        )

                        if (
                            existing_observation
                            is not None
                        ):
                            continue

                        provider = envelope["provider"]
                        region = envelope["region"]

                        raw_key = str(raw["key"])
                        raw_sid = str(raw["sid"])
                        raw_time = int(raw["time"])

                        lkey = logical_key(
                            provider,
                            region,
                            dataset,
                            raw_key,
                            raw_sid,
                            raw_time,
                        )

                        observation_time = (
                            envelope[
                                "ingested_at_utc"
                            ]
                        )

                        logical = conn.execute(
                            """
                            SELECT id
                            FROM logical_records
                            WHERE logical_key = ?
                            """,
                            (lkey,),
                        ).fetchone()

                        logical_was_new = (
                            logical is None
                        )

                        late_arrival = 0

                        if logical_was_new:

                            for (
                                scope_start,
                                scope_end,
                            ) in prior_coverage[
                                dataset
                            ]:

                                if (
                                    scope_start
                                    <= raw_time
                                    <= scope_end
                                ):
                                    late_arrival = 1
                                    break

                            cur = conn.execute(
                                """
                                INSERT INTO logical_records (
                                    provider,
                                    region,
                                    dataset,
                                    raw_key,
                                    raw_sid,
                                    raw_time,
                                    identity_version,
                                    logical_key,
                                    first_seen_at_utc,
                                    last_seen_at_utc,
                                    created_by_run_id
                                )
                                VALUES (
                                    ?,?,?,?,?,?,
                                    'xiaomi-v0.1',
                                    ?,?,?,?
                                )
                                """,
                                (
                                    provider,
                                    region,
                                    dataset,
                                    raw_key,
                                    raw_sid,
                                    raw_time,
                                    lkey,
                                    observation_time,
                                    observation_time,
                                    run_id,
                                ),
                            )

                            logical_id = cur.lastrowid

                            counters["logical_new"] += 1

                            if late_arrival:
                                counters[
                                    "late_arrivals"
                                ] += 1

                        else:

                            logical_id = logical["id"]

                            conn.execute(
                                """
                                UPDATE logical_records
                                SET last_seen_at_utc = ?
                                WHERE id = ?
                                """,
                                (
                                    observation_time,
                                    logical_id,
                                ),
                            )

                        version = conn.execute(
                            """
                            SELECT id
                            FROM raw_record_versions
                            WHERE logical_record_id = ?
                              AND payload_sha256 = ?
                            """,
                            (
                                logical_id,
                                supplied_hash,
                            ),
                        ).fetchone()

                        if version is None:

                            if logical_was_new:
                                classification = "NEW"
                            else:
                                classification = (
                                    "REVISION"
                                )
                                counters[
                                    "revisions"
                                ] += 1

                            cur = conn.execute(
                                """
                                INSERT INTO raw_record_versions (
                                    logical_record_id,
                                    payload_sha256,
                                    raw_json,
                                    raw_update_time,
                                    zone_name,
                                    zone_offset,
                                    first_seen_at_utc,
                                    last_seen_at_utc,
                                    first_seen_run_id
                                )
                                VALUES (
                                    ?,?,?,?,?,?,?,?,?
                                )
                                """,
                                (
                                    logical_id,
                                    supplied_hash,
                                    canonical_json(raw),
                                    integer_or_none(
                                        raw.get(
                                            "update_time"
                                        )
                                    ),
                                    raw.get(
                                        "zone_name"
                                    ),
                                    integer_or_none(
                                        raw.get(
                                            "zone_offset"
                                        )
                                    ),
                                    observation_time,
                                    observation_time,
                                    run_id,
                                ),
                            )

                            version_id = cur.lastrowid

                            counters[
                                "versions_new"
                            ] += 1

                        else:

                            version_id = version["id"]

                            classification = (
                                "REOBSERVATION"
                            )

                            counters[
                                "reobservations"
                            ] += 1

                            conn.execute(
                                """
                                UPDATE raw_record_versions
                                SET last_seen_at_utc = ?
                                WHERE id = ?
                                """,
                                (
                                    observation_time,
                                    version_id,
                                ),
                            )

                        conn.execute(
                            """
                            INSERT INTO raw_record_observations (
                                source_artifact_id,
                                source_record_ordinal,
                                raw_record_version_id,
                                ingestion_run_id,
                                capture_id,
                                ingested_at_utc,
                                classification,
                                late_arrival,
                                l1_identity_hint,
                                envelope_schema_version,
                                endpoint,
                                dataset,
                                provider,
                                region
                            )
                            VALUES (
                                ?,?,?,?,?,?,?,?,?,?,?,?,?,?
                            )
                            """,
                            (
                                artifact_id,
                                ordinal,
                                version_id,
                                run_id,
                                capture_id,
                                observation_time,
                                classification,
                                late_arrival,
                                envelope.get(
                                    "identity_hint"
                                ),
                                envelope.get(
                                    "schema_version"
                                ),
                                envelope.get(
                                    "endpoint"
                                ),
                                dataset,
                                provider,
                                region,
                            ),
                        )

                        counters[
                            "observations_new"
                        ] += 1

                        artifact_new_observations += 1

                expected_count = int(
                    meta.get(
                        "record_count",
                        0,
                    )
                )

                if artifact_records != expected_count:
                    raise RuntimeError(
                        f"{capture_id}/{dataset}: "
                        f"record count mismatch "
                        f"manifest={expected_count} "
                        f"actual={artifact_records}"
                    )

                artifact_status = (
                    "SUCCESS"
                    if (
                        artifact_new
                        or artifact_new_observations > 0
                    )
                    else "SKIPPED"
                )

                conn.execute(
                    """
                    INSERT INTO ingestion_run_artifacts (
                        run_id,
                        source_artifact_id,
                        status,
                        records_seen,
                        issues
                    )
                    VALUES (?, ?, ?, ?, 0)
                    """,
                    (
                        run_id,
                        artifact_id,
                        artifact_status,
                        artifact_records,
                    ),
                )

                comparison = (
                    meta.get("comparison") or {}
                )

                scope_start = integer_or_none(
                    comparison.get(
                        "scope_start_timestamp"
                    )
                )

                scope_end = integer_or_none(
                    comparison.get(
                        "scope_end_timestamp"
                    )
                )

                if (
                    scope_start is not None
                    and scope_end is not None
                ):
                    prior_coverage[
                        dataset
                    ].append(
                        (
                            scope_start,
                            scope_end,
                        )
                    )

        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if integrity != "ok":
            raise RuntimeError(
                f"SQLite integrity failure: "
                f"{integrity}"
            )

        if fk_errors:
            raise RuntimeError(
                f"Foreign key errors: "
                f"{len(fk_errors)}"
            )

        finished = utc_now()

        conn.execute(
            """
            UPDATE ingestion_runs
            SET finished_at_utc = ?,
                status = 'SUCCESS',
                artifacts_seen = ?,
                records_seen = ?,
                logical_new = ?,
                versions_new = ?,
                observations_new = ?,
                revisions = ?,
                late_arrivals = ?,
                reobservations = ?,
                issues = 0
            WHERE run_id = ?
            """,
            (
                finished,
                counters["artifacts_seen"],
                counters["records_seen"],
                counters["logical_new"],
                counters["versions_new"],
                counters["observations_new"],
                counters["revisions"],
                counters["late_arrivals"],
                counters["reobservations"],
                run_id,
            ),
        )

        conn.commit()

        table_counts = {}

        for table in (
            "captures",
            "source_artifacts",
            "logical_records",
            "raw_record_versions",
            "raw_record_observations",
            "ingestion_runs",
            "ingestion_issues",
        ):

            table_counts[table] = (
                conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )

        print()
        print("========== L2 HISTORICAL IMPORT ==========")
        print(f"run_id             = {run_id}")
        print(f"backup             = {backup_path}")
        print(f"captures            = {counters['captures']}")
        print(f"artifacts_seen      = {counters['artifacts_seen']}")
        print(f"records_seen        = {counters['records_seen']}")
        print(f"logical_new         = {counters['logical_new']}")
        print(f"versions_new        = {counters['versions_new']}")
        print(f"observations_new    = {counters['observations_new']}")
        print(f"revisions           = {counters['revisions']}")
        print(f"late_arrivals       = {counters['late_arrivals']}")
        print(f"reobservations      = {counters['reobservations']}")
        print(f"integrity_check     = {integrity}")
        print(f"foreign_key_errors  = {len(fk_errors)}")

        print()
        print("========== DATABASE COUNTS ==========")

        for table, count in table_counts.items():
            print(f"{table:26s} = {count}")

        print()

        if (
            counters["captures"] == 9
            and counters["artifacts_seen"] == 81
            and counters["records_seen"] == 15751
            and integrity == "ok"
            and len(fk_errors) == 0
        ):
            print("RESULT = PASS")
            print("L2 HISTORICAL IMPORT = COMPLETE")
        else:
            print("RESULT = REVIEW")

    except Exception as e:

        conn.rollback()

        conn.execute(
            """
            UPDATE ingestion_runs
            SET finished_at_utc = ?,
                status = 'FAILED',
                error_summary = ?
            WHERE run_id = ?
            """,
            (
                utc_now(),
                str(e),
                run_id,
            ),
        )

        conn.commit()

        print()
        print("RESULT = FAIL")
        print("DATABASE WRITE TRANSACTION = ROLLED BACK")
        print("ERROR =", e)

        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
