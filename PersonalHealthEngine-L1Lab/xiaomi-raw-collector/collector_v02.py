import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mi_fitness_mcp.auth import load_mi_fitness_token
from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter


VERSION = "0.2"
SCHEMA_VERSION = "xiaomi_near_raw_record.v0.1"
FITNESS_ENDPOINT = "/app/v1/data/get_fitness_data_by_time"

DEFAULT_KEYS = [
    "steps",
    "sleep",
    "heart_rate",
    "spo2",
    "stress",
]

CN_TZ = timezone(timedelta(hours=8))


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


def build_identity_hint(
    endpoint: str,
    request_key: str,
    record: dict[str, Any],
) -> str:
    basis = {
        "endpoint": endpoint,
        "request_key": request_key,
        "sid": record.get("sid"),
        "key": record.get("key"),
        "time": record.get("time"),
    }

    return sha256_text(stable_json(basis))


def record_timestamp(record: dict[str, Any]) -> int | None:
    try:
        return int(record.get("time"))
    except (TypeError, ValueError):
        return None


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")

    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    temp.replace(path)


def find_latest_capture(captures_dir: Path) -> Path | None:
    candidates = sorted(
        [
            p
            for p in captures_dir.iterdir()
            if p.is_dir() and (p / "manifest.json").exists()
        ],
        key=lambda p: p.name,
    )

    return candidates[-1] if candidates else None


def load_capture_records(
    capture_dir: Path | None,
    key: str,
    range_start_ts: int,
    range_end_ts: int,
) -> dict[str, dict[str, Any]]:
    if capture_dir is None:
        return {}

    path = capture_dir / f"{key}.jsonl"

    if not path.exists():
        return {}

    result = {}

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)

            raw = row.get("raw_record", {})

            try:
                ts = int(raw.get("time"))
            except (TypeError, ValueError):
                continue

            if range_start_ts <= ts <= range_end_ts:
                result[row["identity_hint"]] = row

    return result


def date_range_epoch(
    start_date: str,
    end_date: str,
) -> tuple[int, int]:
    start = datetime.fromisoformat(start_date).replace(
        tzinfo=CN_TZ
    )

    end = datetime.fromisoformat(
        end_date + "T23:59:59"
    ).replace(tzinfo=CN_TZ)

    return int(start.timestamp()), int(end.timestamp())


def resolve_range(
    args: argparse.Namespace,
    state: dict[str, Any] | None,
) -> tuple[str, str, str]:
    if args.end_date:
        end_dt = datetime.strptime(
            args.end_date,
            "%Y-%m-%d",
        ).date()
    else:
        end_dt = datetime.now(CN_TZ).date()

    if args.start_date:
        start_dt = datetime.strptime(
            args.start_date,
            "%Y-%m-%d",
        ).date()

        return (
            start_dt.isoformat(),
            end_dt.isoformat(),
            "explicit",
        )

    if state and state.get("last_success_end_date"):
        previous_end = datetime.strptime(
            state["last_success_end_date"],
            "%Y-%m-%d",
        ).date()

        start_dt = previous_end - timedelta(
            days=max(0, args.overlap_days - 1)
        )

        return (
            start_dt.isoformat(),
            end_dt.isoformat(),
            "incremental_overlap",
        )

    start_dt = end_dt - timedelta(
        days=args.initial_lookback_days - 1
    )

    return (
        start_dt.isoformat(),
        end_dt.isoformat(),
        "initial_lookback",
    )


async def run(args: argparse.Namespace) -> int:
    captures_dir = Path(args.output_dir)
    captures_dir.mkdir(parents=True, exist_ok=True)

    state_path = Path(args.state_file)
    state = load_state(state_path)

    start_date, end_date, range_mode = resolve_range(
        args,
        state,
    )

    if start_date > end_date:
        print("ERROR: start_date is after end_date.")
        return 2

    user_id, pass_token = load_mi_fitness_token()

    if not user_id or not pass_token:
        print("ERROR: Xiaomi credentials not found.")
        return 3

    previous_capture = find_latest_capture(
        captures_dir
    )

    print("=" * 78)
    print(f"XIAOMI RAW COLLECTOR v{VERSION}")
    print("=" * 78)

    print(f"Region           : {args.region}")
    print(f"Range mode       : {range_mode}")
    print(f"Date range       : {start_date} -> {end_date}")
    print(f"Overlap days     : {args.overlap_days}")
    print(
        "Previous capture : "
        + (
            previous_capture.name
            if previous_capture
            else "NONE"
        )
    )
    print(f"Keys             : {', '.join(args.keys)}")
    print()

    adapter = MiFitnessCloudAdapter(
        user_id=user_id,
        pass_token=pass_token,
        region=args.region,
    )

    connected = await adapter.connect()

    if not connected:
        print("CONNECT: FAIL")
        print("Reason:", adapter.last_error)
        return 4

    print("CONNECT: PASS")

    capture_started = datetime.now(timezone.utc)
    capture_id = capture_started.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    capture_dir = (
        captures_dir
        / f"{capture_id}_{start_date}_to_{end_date}"
    )

    capture_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    range_start_ts, range_end_ts = (
        date_range_epoch(start_date, end_date)
    )

    manifest: dict[str, Any] = {
        "collector": "xiaomi-raw-collector",
        "collector_version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "provider": "xiaomi",
        "region": args.region,
        "capture_id": capture_id,
        "capture_started_at_utc": (
            capture_started.isoformat()
        ),
        "range_mode": range_mode,
        "start_date": start_date,
        "end_date": end_date,
        "overlap_days": args.overlap_days,
        "previous_capture": (
            previous_capture.name
            if previous_capture
            else None
        ),
        "auth_storage": "os_keyring",
        "credentials_embedded": False,
        "datasets": {},
    }

    total_new = 0
    total_revised = 0
    total_missing = 0

    try:
        for key in args.keys:
            print()
            print(f"[{key}] fetching...")

            records = await adapter._fetch_key(
                key,
                start_date,
                end_date,
            )

            previous = load_capture_records(
                previous_capture,
                key,
                range_start_ts,
                range_end_ts,
            )

            current: dict[
                str,
                dict[str, Any]
            ] = {}

            output_path = (
                capture_dir / f"{key}.jsonl"
            )

            sid_counts: Counter[str] = Counter()
            raw_hash_counts: Counter[str] = Counter()
            timestamps: list[int] = []

            with output_path.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as f:

                for record in records:
                    sid_counts[
                        str(record.get("sid"))
                    ] += 1

                    raw_json = stable_json(record)
                    raw_hash = sha256_text(raw_json)

                    raw_hash_counts[raw_hash] += 1

                    identity = build_identity_hint(
                        FITNESS_ENDPOINT,
                        key,
                        record,
                    )

                    ts = record_timestamp(record)

                    if ts is not None:
                        timestamps.append(ts)

                    envelope = {
                        "schema_version":
                            SCHEMA_VERSION,
                        "provider": "xiaomi",
                        "region": args.region,
                        "endpoint":
                            FITNESS_ENDPOINT,
                        "request_key": key,
                        "capture_id": capture_id,
                        "ingested_at_utc":
                            datetime.now(
                                timezone.utc
                            ).isoformat(),
                        "identity_hint":
                            identity,
                        "raw_record_sha256":
                            raw_hash,
                        "raw_record": record,
                    }

                    current[identity] = envelope

                    f.write(
                        json.dumps(
                            envelope,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )

            previous_ids = set(previous)
            current_ids = set(current)

            shared = previous_ids & current_ids

            new_ids = (
                current_ids - previous_ids
            )

            missing_ids = (
                previous_ids - current_ids
            )

            revised_ids = {
                identity
                for identity in shared
                if (
                    previous[identity][
                        "raw_record_sha256"
                    ]
                    != current[identity][
                        "raw_record_sha256"
                    ]
                )
            }

            unchanged_ids = (
                shared - revised_ids
            )

            exact_duplicates = sum(
                count - 1
                for count in raw_hash_counts.values()
                if count > 1
            )

            total_new += len(new_ids)
            total_revised += len(revised_ids)
            total_missing += len(missing_ids)

            dataset_manifest = {
                "endpoint":
                    FITNESS_ENDPOINT,
                "request_key": key,
                "record_count": len(records),
                "sid_counts":
                    dict(sid_counts),
                "first_raw_timestamp": (
                    min(timestamps)
                    if timestamps
                    else None
                ),
                "last_raw_timestamp": (
                    max(timestamps)
                    if timestamps
                    else None
                ),
                "exact_duplicate_records":
                    exact_duplicates,
                "comparison": {
                    "previous_in_scope":
                        len(previous),
                    "shared":
                        len(shared),
                    "unchanged":
                        len(unchanged_ids),
                    "revised":
                        len(revised_ids),
                    "new":
                        len(new_ids),
                    "missing":
                        len(missing_ids),
                },
                "output_file":
                    output_path.name,
                "output_sha256":
                    sha256_file(output_path),
            }

            manifest["datasets"][key] = (
                dataset_manifest
            )

            print(
                f"  records    : {len(records)}"
            )
            print(
                "  sid counts : "
                + json.dumps(
                    dict(sid_counts),
                    ensure_ascii=False,
                )
            )
            print(
                f"  unchanged  : "
                f"{len(unchanged_ids)}"
            )
            print(
                f"  revised    : "
                f"{len(revised_ids)}"
            )
            print(
                f"  new        : "
                f"{len(new_ids)}"
            )
            print(
                f"  missing    : "
                f"{len(missing_ids)}"
            )
            print(
                f"  duplicates : "
                f"{exact_duplicates}"
            )

        finished = datetime.now(timezone.utc)

        manifest[
            "capture_finished_at_utc"
        ] = finished.isoformat()

        manifest["change_summary"] = {
            "new": total_new,
            "revised": total_revised,
            "missing": total_missing,
        }

        manifest_path = (
            capture_dir / "manifest.json"
        )

        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        new_state = {
            "collector":
                "xiaomi-raw-collector",
            "collector_version":
                VERSION,
            "last_success_capture_id":
                capture_id,
            "last_success_capture_dir":
                capture_dir.name,
            "last_success_start_date":
                start_date,
            "last_success_end_date":
                end_date,
            "last_success_at_utc":
                finished.isoformat(),
            "overlap_days":
                args.overlap_days,
        }

        save_state(
            state_path,
            new_state,
        )

        print()
        print("=" * 78)
        print("CAPTURE: PASS")
        print("=" * 78)
        print(
            f"NEW      : {total_new}"
        )
        print(
            f"REVISED  : {total_revised}"
        )
        print(
            f"MISSING  : {total_missing}"
        )
        print()
        print("Output:")
        print(capture_dir)
        print()
        print("State:")
        print(state_path)

        return 0

    finally:
        await adapter._close_client()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--start-date",
    )

    parser.add_argument(
        "--end-date",
    )

    parser.add_argument(
        "--region",
        default="cn",
    )

    parser.add_argument(
        "--keys",
        nargs="+",
        default=DEFAULT_KEYS,
    )

    parser.add_argument(
        "--overlap-days",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--initial-lookback-days",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--output-dir",
        default="captures",
    )

    parser.add_argument(
        "--state-file",
        default="collector_state.json",
    )

    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(
            run(parse_args())
        )
    )
