import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from auth import load_xiaomi_credentials
from xiaomi_client import (
    FITNESS_ENDPOINT,
    SPORT_ENDPOINT,
    XiaomiHealthClient,
)


COLLECTOR_VERSION = "0.3.1"
SCHEMA_VERSION = "xiaomi_near_raw_record.v0.2"

CN_TZ = timezone(timedelta(hours=8))

FITNESS_KEYS = [
    "steps",
    "calories",
    "sleep",
    "heart_rate",
    "resting_heart_rate",
    "spo2",
    "stress",
    "abnormal_heart_beat",
]

SPORT_DATASET = "sport_records"


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def record_timestamp(
    record: dict[str, Any],
) -> int | None:

    candidates = [
        record.get("time"),
        record.get("start_time"),
        record.get("timestamp"),
    ]

    for value in candidates:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue

    return None


def build_identity_hint(
    endpoint: str,
    dataset: str,
    record: dict[str, Any],
) -> str:

    # Fitness identity intentionally stays compatible with v0.1/v0.2.
    # Once an identity contract exists, changing it casually destroys
    # revision comparison across collector versions.
    if endpoint == FITNESS_ENDPOINT:
        basis = {
            "endpoint": endpoint,
            "request_key": dataset,
            "sid": record.get("sid"),
            "key": record.get("key"),
            "time": record.get("time"),
        }
    else:
        # Sport records did not exist in the historical baseline.
        basis = {
            "endpoint": endpoint,
            "dataset": dataset,
            "sid": record.get("sid"),
            "key": record.get("key"),
            "time": record.get("time"),
            "start_time": record.get("start_time"),
            "id": record.get("id"),
        }

    return sha256_text(
        stable_json(basis)
    )


def load_state(
    path: Path,
) -> dict[str, Any] | None:

    if not path.exists():
        return None

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def save_state(
    path: Path,
    state: dict[str, Any],
) -> None:

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(path)


def find_latest_capture(
    captures_dir: Path,
) -> Path | None:

    candidates = sorted(
        [
            p
            for p in captures_dir.iterdir()
            if (
                p.is_dir()
                and (
                    p / "manifest.json"
                ).exists()
            )
        ],
        key=lambda p: p.name,
    )

    return (
        candidates[-1]
        if candidates
        else None
    )


def date_range_epoch(
    start_date: str,
    end_date: str,
) -> tuple[int, int]:

    start = datetime.fromisoformat(
        start_date
    ).replace(
        tzinfo=CN_TZ
    )

    end = datetime.fromisoformat(
        end_date + "T23:59:59"
    ).replace(
        tzinfo=CN_TZ
    )

    return (
        int(start.timestamp()),
        int(end.timestamp()),
    )


def load_capture_records(
    capture_dir: Path | None,
    dataset: str,
    range_start_ts: int,
    range_end_ts: int,
) -> tuple[
    bool,
    dict[str, dict[str, Any]],
]:

    if capture_dir is None:
        return False, {}

    path = (
        capture_dir
        / f"{dataset}.jsonl"
    )

    if not path.exists():
        return False, {}

    result = {}

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        for line in f:
            row = json.loads(line)

            raw = row.get(
                "raw_record",
                {},
            )

            ts = record_timestamp(raw)

            if ts is None:
                continue

            if (
                range_start_ts
                <= ts
                <= range_end_ts
            ):
                result[
                    row["identity_hint"]
                ] = row

    return True, result


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
        end_dt = datetime.now(
            CN_TZ
        ).date()

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

    if (
        state
        and state.get(
            "last_success_end_date"
        )
    ):
        previous_end = (
            datetime.strptime(
                state[
                    "last_success_end_date"
                ],
                "%Y-%m-%d",
            ).date()
        )

        start_dt = (
            previous_end
            - timedelta(
                days=max(
                    0,
                    args.overlap_days - 1,
                )
            )
        )

        return (
            start_dt.isoformat(),
            end_dt.isoformat(),
            "incremental_overlap",
        )

    start_dt = (
        end_dt
        - timedelta(
            days=(
                args.initial_lookback_days
                - 1
            )
        )
    )

    return (
        start_dt.isoformat(),
        end_dt.isoformat(),
        "initial_lookback",
    )


def make_envelope(
    *,
    capture_id: str,
    endpoint: str,
    dataset: str,
    record: dict[str, Any],
) -> dict[str, Any]:

    raw_json = stable_json(record)

    return {
        "schema_version":
            SCHEMA_VERSION,

        "provider":
            "xiaomi",

        "region":
            "cn",

        "endpoint":
            endpoint,

        "dataset":
            dataset,

        "capture_id":
            capture_id,

        "ingested_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "identity_hint":
            build_identity_hint(
                endpoint,
                dataset,
                record,
            ),

        "raw_record_sha256":
            sha256_text(raw_json),

        "raw_record":
            record,
    }


def compare_records(
    *,
    baseline_available: bool,
    previous: dict[
        str,
        dict[str, Any]
    ],
    current: dict[
        str,
        dict[str, Any]
    ],
) -> dict[str, Any]:

    if not baseline_available:
        return {
            "baseline_available": False,
            "previous_in_scope": None,
            "shared": None,
            "unchanged": None,
            "revised": None,
            "new": None,
            "missing": None,
        }

    previous_ids = set(previous)
    current_ids = set(current)

    shared = (
        previous_ids
        & current_ids
    )

    new_ids = (
        current_ids
        - previous_ids
    )

    missing_ids = (
        previous_ids
        - current_ids
    )

    revised_ids = {
        identity
        for identity in shared
        if (
            previous[identity][
                "raw_record_sha256"
            ]
            !=
            current[identity][
                "raw_record_sha256"
            ]
        )
    }

    unchanged_ids = (
        shared - revised_ids
    )

    return {
        "baseline_available": True,
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
    }


async def collect_dataset(
    *,
    capture_dir: Path,
    capture_id: str,
    previous_capture: Path | None,
    dataset: str,
    endpoint: str,
    records: list[dict[str, Any]],
    range_start_ts: int,
    range_end_ts: int,
) -> dict[str, Any]:

    comparison_start_ts = range_start_ts
    comparison_end_ts = range_end_ts
    previous_manifest = None

    if previous_capture is not None:
        manifest_path = previous_capture / "manifest.json"

        if manifest_path.exists():
            previous_manifest = json.loads(
                manifest_path.read_text(
                    encoding="utf-8"
                )
            )

            previous_start = previous_manifest.get("start_date")
            previous_end = previous_manifest.get("end_date")

            if previous_start and previous_end:
                previous_start_ts, previous_end_ts = date_range_epoch(
                    previous_start,
                    previous_end,
                )

                comparison_start_ts = max(
                    range_start_ts,
                    previous_start_ts,
                )

                comparison_end_ts = min(
                    range_end_ts,
                    previous_end_ts,
                )

    has_overlap = (
        comparison_start_ts
        <= comparison_end_ts
    )

    if has_overlap:
        baseline_available, previous = (
            load_capture_records(
                previous_capture,
                dataset,
                comparison_start_ts,
                comparison_end_ts,
            )
        )
    else:
        baseline_available = False
        previous = {}

    output_path = (
        capture_dir
        / f"{dataset}.jsonl"
    )

    current = {}

    sid_counts: Counter[str] = (
        Counter()
    )

    raw_hash_counts: Counter[str] = (
        Counter()
    )

    timestamps = []

    with output_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:

        for record in records:

            envelope = make_envelope(
                capture_id=capture_id,
                endpoint=endpoint,
                dataset=dataset,
                record=record,
            )

            identity = envelope[
                "identity_hint"
            ]

            current[identity] = (
                envelope
            )

            raw_hash_counts[
                envelope[
                    "raw_record_sha256"
                ]
            ] += 1

            sid_counts[
                str(
                    record.get("sid")
                )
            ] += 1

            ts = record_timestamp(
                record
            )

            if ts is not None:
                timestamps.append(ts)

            f.write(
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    current_comparison_scope = {}

    if baseline_available and has_overlap:
        for identity, envelope in current.items():
            ts = record_timestamp(
                envelope["raw_record"]
            )

            if (
                ts is not None
                and comparison_start_ts
                <= ts
                <= comparison_end_ts
            ):
                current_comparison_scope[
                    identity
                ] = envelope

    comparison = compare_records(
        baseline_available=(
            baseline_available
        ),
        previous=previous,
        current=current_comparison_scope,
    )

    if baseline_available and has_overlap:
        comparison["scope_start_timestamp"] = (
            comparison_start_ts
        )
        comparison["scope_end_timestamp"] = (
            comparison_end_ts
        )
        comparison[
            "current_outside_baseline_scope"
        ] = (
            len(current)
            - len(current_comparison_scope)
        )
    else:
        comparison[
            "current_outside_baseline_scope"
        ] = None

    exact_duplicates = sum(
        count - 1
        for count
        in raw_hash_counts.values()
        if count > 1
    )

    return {
        "endpoint":
            endpoint,

        "dataset":
            dataset,

        "record_count":
            len(records),

        "sid_counts":
            dict(sid_counts),

        "first_raw_timestamp":
            (
                min(timestamps)
                if timestamps
                else None
            ),

        "last_raw_timestamp":
            (
                max(timestamps)
                if timestamps
                else None
            ),

        "exact_duplicate_records":
            exact_duplicates,

        "comparison":
            comparison,

        "output_file":
            output_path.name,

        "output_sha256":
            sha256_file(
                output_path
            ),
    }


async def run(
    args: argparse.Namespace,
) -> int:

    captures_dir = Path(
        args.output_dir
    )

    captures_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    state_path = Path(
        args.state_file
    )

    state = load_state(
        state_path
    )

    (
        start_date,
        end_date,
        range_mode,
    ) = resolve_range(
        args,
        state,
    )

    if start_date > end_date:
        print(
            "ERROR: start_date "
            "is after end_date."
        )
        return 2

    credentials = (
        load_xiaomi_credentials()
    )

    previous_capture = (
        find_latest_capture(
            captures_dir
        )
    )

    print("=" * 78)
    print(
        "XIAOMI RAW COLLECTOR "
        f"v{COLLECTOR_VERSION}"
    )
    print("=" * 78)

    print(
        f"Region           : cn"
    )
    print(
        f"Range mode       : "
        f"{range_mode}"
    )
    print(
        f"Date range       : "
        f"{start_date} -> {end_date}"
    )
    print(
        f"Overlap days     : "
        f"{args.overlap_days}"
    )
    print(
        "Previous capture : "
        + (
            previous_capture.name
            if previous_capture
            else "NONE"
        )
    )
    print(
        "Runtime dependency: "
        "independent"
    )
    print()

    client = XiaomiHealthClient(
        user_id=credentials.user_id,
        pass_token=credentials.pass_token,
        region="cn",
    )

    await client.connect()

    print("CONNECT: PASS")

    started = datetime.now(
        timezone.utc
    )

    capture_id = (
        started.strftime(
            "%Y%m%dT%H%M%SZ"
        )
    )

    capture_dir = (
        captures_dir
        / (
            f"{capture_id}_"
            f"{start_date}_to_"
            f"{end_date}"
        )
    )

    capture_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    (
        range_start_ts,
        range_end_ts,
    ) = date_range_epoch(
        start_date,
        end_date,
    )

    manifest = {
        "collector":
            "xiaomi-raw-collector",

        "collector_version":
            COLLECTOR_VERSION,

        "schema_version":
            SCHEMA_VERSION,

        "runtime_dependency":
            "independent",

        "provider":
            "xiaomi",

        "region":
            "cn",

        "capture_id":
            capture_id,

        "capture_started_at_utc":
            started.isoformat(),

        "range_mode":
            range_mode,

        "start_date":
            start_date,

        "end_date":
            end_date,

        "overlap_days":
            args.overlap_days,

        "previous_capture":
            (
                previous_capture.name
                if previous_capture
                else None
            ),

        "credentials_source":
            "os_keyring",

        "credentials_embedded":
            False,

        "datasets":
            {},
    }

    try:
        for key in FITNESS_KEYS:

            print()
            print(
                f"[{key}] fetching..."
            )

            records = (
                await client
                .fetch_fitness_key(
                    key,
                    start_date,
                    end_date,
                )
            )

            result = (
                await collect_dataset(
                    capture_dir=
                        capture_dir,
                    capture_id=
                        capture_id,
                    previous_capture=
                        previous_capture,
                    dataset=key,
                    endpoint=
                        FITNESS_ENDPOINT,
                    records=records,
                    range_start_ts=
                        range_start_ts,
                    range_end_ts=
                        range_end_ts,
                )
            )

            manifest["datasets"][
                key
            ] = result

            comparison = result[
                "comparison"
            ]

            print(
                f"  records    : "
                f"{result['record_count']}"
            )

            print(
                "  sid counts : "
                + json.dumps(
                    result["sid_counts"],
                    ensure_ascii=False,
                )
            )

            print(
                f"  duplicates : "
                f"{result['exact_duplicate_records']}"
            )

            if comparison[
                "baseline_available"
            ]:
                print(
                    f"  unchanged  : "
                    f"{comparison['unchanged']}"
                )
                print(
                    f"  revised    : "
                    f"{comparison['revised']}"
                )
                print(
                    f"  new        : "
                    f"{comparison['new']}"
                )
                print(
                    f"  missing    : "
                    f"{comparison['missing']}"
                )
            else:
                print(
                    "  comparison : "
                    "NO PREVIOUS DATASET BASELINE"
                )

        print()
        print(
            "[sport_records] fetching..."
        )

        sport_records = (
            await client
            .fetch_sport_records(
                start_date,
                end_date,
            )
        )

        sport_result = (
            await collect_dataset(
                capture_dir=
                    capture_dir,
                capture_id=
                    capture_id,
                previous_capture=
                    previous_capture,
                dataset=
                    SPORT_DATASET,
                endpoint=
                    SPORT_ENDPOINT,
                records=
                    sport_records,
                range_start_ts=
                    range_start_ts,
                range_end_ts=
                    range_end_ts,
            )
        )

        manifest["datasets"][
            SPORT_DATASET
        ] = sport_result

        print(
            f"  records    : "
            f"{sport_result['record_count']}"
        )

        print(
            "  duplicates : "
            f"{sport_result['exact_duplicate_records']}"
        )

        finished = datetime.now(
            timezone.utc
        )

        manifest[
            "capture_finished_at_utc"
        ] = finished.isoformat()

        manifest_path = (
            capture_dir
            / "manifest.json"
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
                COLLECTOR_VERSION,

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
            "Output:",
            capture_dir,
        )

        print(
            "State :",
            state_path,
        )

        return 0

    finally:
        await client.close()


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Independent Xiaomi "
            "near-raw health collector."
        )
    )

    parser.add_argument(
        "--start-date",
    )

    parser.add_argument(
        "--end-date",
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
        default=
            "collector_state.json",
    )

    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(
            run(
                parse_args()
            )
        )
    )
