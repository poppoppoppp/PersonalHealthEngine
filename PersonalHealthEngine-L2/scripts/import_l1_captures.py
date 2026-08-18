from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone

L1_ROOT = Path(r"D:\PersonalHealthEngine-L1Lab\xiaomi-raw-collector")
CAPTURES_ROOT = L1_ROOT / "captures"

L2_ROOT = Path(r"D:\PersonalHealthEngine-L2")
DB_PATH = L2_ROOT / "db" / "personal_health_raw.sqlite3"

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


def now_utc():
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


def calculate_payload_hash(raw_record: dict) -> str:
    return sha256_bytes(
        canonical_json(raw_record).encode("utf-8")
    )


def build_logical_key(
    provider: str,
    region: str,
    dataset: str,
    raw_key,
    raw_sid,
    raw_time,
) -> str:

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


def load_manifest(capture_dir: Path):
    path = capture_dir / "manifest.json"

    if not path.exists():
        raise RuntimeError(f"manifest missing: {path}")

    raw = path.read_bytes()

    return (
        json.loads(raw.decode("utf-8-sig")),
        sha256_bytes(raw),
        raw.decode("utf-8-sig"),
    )


def validate_manifest(manifest, capture_dir):
    if manifest.get("collector") != EXPECTED_COLLECTOR:
        raise RuntimeError(
            f"{capture_dir.name}: unexpected collector "
            f"{manifest.get('collector')}"
        )

    if manifest.get("collector_version") != EXPECTED_VERSION:
        raise RuntimeError(
            f"{capture_dir.name}: unexpected collector version "
            f"{manifest.get('collector_version')}"
        )


def iter_capture_dirs():
    dirs = []

    for p in CAPTURES_ROOT.iterdir():
        if not p.is_dir():
            continue

        manifest_path = p / "manifest.json"

        if not manifest_path.exists():
            continue

        manifest, _, _ = load_manifest(p)

        dirs.append(
            (
                manifest.get("capture_started_at_utc", ""),
                p,
            )
        )

    dirs.sort(key=lambda x: (x[0], x[1].name))

    return [p for _, p in dirs]


def extract_zone_offset(raw_record):
    value = raw_record.get("zone_offset")

    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def verify_record_envelope(envelope, expected_dataset):
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

    missing = required - set(envelope.keys())

    if missing:
        raise RuntimeError(
            f"missing envelope fields: {sorted(missing)}"
        )

    if envelope["dataset"] != expected_dataset:
        raise RuntimeError(
            f"dataset mismatch: file={expected_dataset}, "
            f"envelope={envelope['dataset']}"
        )

    raw = envelope["raw_record"]

    for key in ("key", "sid", "time"):
        if key not in raw:
            raise RuntimeError(
                f"raw_record missing identity field: {key}"
            )


def dry_run():
    totals = {
        "captures": 0,
        "artifacts": 0,
        "records": 0,
        "hash_ok": 0,
        "hash_mismatch": 0,
        "errors": 0,
    }

    datasets = {}

    print("========== L2 IMPORTER DRY RUN ==========")

    capture_dirs = iter_capture_dirs()

    for capture_dir in capture_dirs:

        manifest, manifest_hash, _ = load_manifest(capture_dir)
        validate_manifest(manifest, capture_dir)

        totals["captures"] += 1

        capture_id = manifest["capture_id"]

        print()
        print(
            f"CAPTURE {capture_id} "
            f"{manifest.get('start_date')} -> "
            f"{manifest.get('end_date')}"
        )

        manifest_datasets = manifest.get("datasets", {})

        for dataset, meta in manifest_datasets.items():

            if dataset not in SUPPORTED_DATASETS:
                print(f"  {dataset}: SKIP unsupported")
                continue

            output_file = meta.get("output_file")

            if not output_file:
                print(f"  {dataset}: ERROR no output_file")
                totals["errors"] += 1
                continue

            path = capture_dir / output_file

            if not path.exists():
                print(f"  {dataset}: ERROR file missing")
                totals["errors"] += 1
                continue

            totals["artifacts"] += 1

            actual_file_hash = sha256_file(path)
            expected_file_hash = meta.get("output_sha256")

            if (
                expected_file_hash
                and actual_file_hash != expected_file_hash
            ):
                print(f"  {dataset}: ERROR file SHA256 mismatch")
                totals["errors"] += 1
                continue

            count = 0

            with path.open(
                "r",
                encoding="utf-8-sig",
            ) as f:

                for ordinal, line in enumerate(f, start=1):

                    line = line.strip()

                    if not line:
                        continue

                    try:
                        envelope = json.loads(line)

                        verify_record_envelope(
                            envelope,
                            dataset,
                        )

                        raw = envelope["raw_record"]

                        calculated = calculate_payload_hash(raw)
                        supplied = envelope["raw_record_sha256"]

                        if calculated != supplied:
                            totals["hash_mismatch"] += 1
                            raise RuntimeError(
                                "raw_record SHA256 mismatch"
                            )

                        totals["hash_ok"] += 1

                        build_logical_key(
                            envelope["provider"],
                            envelope["region"],
                            envelope["dataset"],
                            raw["key"],
                            raw["sid"],
                            raw["time"],
                        )

                        count += 1
                        totals["records"] += 1

                    except Exception as e:
                        totals["errors"] += 1
                        print(
                            f"  {dataset} line {ordinal}: "
                            f"ERROR {e}"
                        )

            expected_count = int(meta.get("record_count", 0))

            if count != expected_count:
                print(
                    f"  {dataset}: ERROR count "
                    f"manifest={expected_count}, parsed={count}"
                )
                totals["errors"] += 1
            else:
                print(
                    f"  {dataset}: PASS "
                    f"records={count}"
                )

            datasets[dataset] = (
                datasets.get(dataset, 0) + count
            )

    print()
    print("========== DATASET TOTALS ==========")

    for dataset in sorted(datasets):
        print(
            f"{dataset:24s} "
            f"{datasets[dataset]}"
        )

    print()
    print("========== DRY RUN SUMMARY ==========")
    print(f"captures       = {totals['captures']}")
    print(f"artifacts      = {totals['artifacts']}")
    print(f"records        = {totals['records']}")
    print(f"hash_ok        = {totals['hash_ok']}")
    print(f"hash_mismatch  = {totals['hash_mismatch']}")
    print(f"errors         = {totals['errors']}")

    print()

    if (
        totals["captures"] > 0
        and totals["records"] > 0
        and totals["hash_mismatch"] == 0
        and totals["errors"] == 0
    ):
        print("RESULT = PASS")
        print("L2 IMPORTER DRY RUN = READY")
    else:
        print("RESULT = FAIL")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate L1 captures without writing SQLite.",
    )

    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    raise SystemExit(
        "Write mode is intentionally disabled in importer v0.1 "
        "until dry-run acceptance passes."
    )


if __name__ == "__main__":
    main()
