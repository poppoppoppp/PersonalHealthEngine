import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(".")
CAPTURES = ROOT / "captures"
LOGS = ROOT / "logs"
STATE = ROOT / "collector_state.json"

CN_TZ = timezone(timedelta(hours=8))

DATASETS = [
    "steps",
    "calories",
    "sleep",
    "heart_rate",
    "resting_heart_rate",
    "spo2",
    "stress",
    "abnormal_heart_beat",
    "sport_records",
]


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def raw_timestamp(record):
    for key in ("time", "start_time", "timestamp"):
        value = record.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def local_date(ts):
    return datetime.fromtimestamp(
        ts,
        tz=CN_TZ,
    ).date().isoformat()


def load_records(capture, dataset):
    path = capture / f"{dataset}.jsonl"

    if not path.exists():
        return {}

    result = {}

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            result[row["identity_hint"]] = row

    return result


captures = sorted(
    [
        p
        for p in CAPTURES.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    ],
    key=lambda p: p.name,
)

if len(captures) < 2:
    raise SystemExit("FAIL: not enough captures")

manifests = {
    p.name: load_json(p / "manifest.json")
    for p in captures
}

print("=" * 86)
print("PERSONAL HEALTH ENGINE")
print("LAYER 1 FINAL ACCEPTANCE AUDIT")
print("=" * 86)

print()
print("CAPTURE HISTORY")
print("-" * 86)

for p in captures:
    m = manifests[p.name]

    print(
        p.name,
        "|",
        m.get("collector_version"),
        "|",
        m.get("range_mode"),
        "|",
        m.get("start_date"),
        "->",
        m.get("end_date"),
    )

print()
print("SCHEDULED RUNS")
print("-" * 86)

run_results = []

for path in sorted(
    LOGS.glob("collector_*.summary.txt"),
    key=lambda p: p.name,
):
    data = {}

    for line in path.read_text(
        encoding="utf-8",
        errors="ignore",
    ).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()

    try:
        started = datetime.fromisoformat(
            data["started"]
        )
        exit_code = int(data["exit_code"])
    except Exception:
        continue

    run_results.append(
        (
            started,
            exit_code,
            path.name,
        )
    )

recent_runs = run_results[-7:]

for started, exit_code, name in recent_runs:
    print(
        started.isoformat(),
        "exit=",
        exit_code,
        name,
    )

successful_days = {
    started.astimezone(CN_TZ).date()
    for started, exit_code, _ in run_results
    if exit_code == 0
}

print()
print(
    "distinct successful scheduled days:",
    len(successful_days),
)

print()
print("UNION DATA COVERAGE")
print("-" * 86)

union = {
    dataset: {}
    for dataset in DATASETS
}

versions = {
    dataset: defaultdict(set)
    for dataset in DATASETS
}

for capture in captures:
    for dataset in DATASETS:
        records = load_records(
            capture,
            dataset,
        )

        for identity, row in records.items():
            union[dataset][identity] = row
            versions[dataset][identity].add(
                row["raw_record_sha256"]
            )

for dataset in DATASETS:
    records = union[dataset]

    date_counts = Counter()
    sid_counts = Counter()

    for row in records.values():
        raw = row["raw_record"]

        ts = raw_timestamp(raw)

        if ts is not None:
            date_counts[
                local_date(ts)
            ] += 1

        sid_counts[
            str(raw.get("sid"))
        ] += 1

    revised_identities = sum(
        1
        for hashes in versions[dataset].values()
        if len(hashes) > 1
    )

    print()
    print(f"[{dataset}]")
    print("  unique identities :", len(records))
    print(
        "  dates             :",
        dict(sorted(date_counts.items())),
    )
    print(
        "  sid counts        :",
        dict(sid_counts),
    )
    print(
        "  revised identities:",
        revised_identities,
    )

print()
print("SEQUENTIAL OVERLAP AUDIT")
print("-" * 86)

total_late = 0
total_revised = 0
total_missing = 0

for previous_capture, current_capture in zip(
    captures,
    captures[1:],
):
    prev_manifest = manifests[
        previous_capture.name
    ]
    curr_manifest = manifests[
        current_capture.name
    ]

    prev_start = datetime.fromisoformat(
        prev_manifest["start_date"]
    ).date()

    prev_end = datetime.fromisoformat(
        prev_manifest["end_date"]
    ).date()

    curr_start = datetime.fromisoformat(
        curr_manifest["start_date"]
    ).date()

    curr_end = datetime.fromisoformat(
        curr_manifest["end_date"]
    ).date()

    overlap_start = max(
        prev_start,
        curr_start,
    )

    overlap_end = min(
        prev_end,
        curr_end,
    )

    if overlap_start > overlap_end:
        continue

    print()
    print(
        previous_capture.name,
        "->",
        current_capture.name,
    )

    for dataset in DATASETS:
        prev = load_records(
            previous_capture,
            dataset,
        )

        curr = load_records(
            current_capture,
            dataset,
        )

        def in_overlap(row):
            ts = raw_timestamp(
                row["raw_record"]
            )

            if ts is None:
                return False

            d = datetime.fromtimestamp(
                ts,
                tz=CN_TZ,
            ).date()

            return (
                overlap_start
                <= d
                <= overlap_end
            )

        prev_scope = {
            k: v
            for k, v in prev.items()
            if in_overlap(v)
        }

        curr_scope = {
            k: v
            for k, v in curr.items()
            if in_overlap(v)
        }

        pids = set(prev_scope)
        cids = set(curr_scope)

        late = cids - pids
        missing = pids - cids

        shared = pids & cids

        revised = {
            i
            for i in shared
            if (
                prev_scope[i][
                    "raw_record_sha256"
                ]
                !=
                curr_scope[i][
                    "raw_record_sha256"
                ]
            )
        }

        total_late += len(late)
        total_missing += len(missing)
        total_revised += len(revised)

        if late or revised or missing:
            print(
                f"  {dataset:<22}"
                f" late={len(late):<5}"
                f" revised={len(revised):<5}"
                f" missing={len(missing):<5}"
            )

print()
print("HEART RATE CONTINUITY")
print("-" * 86)

hr_by_date = defaultdict(list)

for row in union["heart_rate"].values():
    ts = raw_timestamp(
        row["raw_record"]
    )

    if ts is not None:
        hr_by_date[
            local_date(ts)
        ].append(ts)

for date, timestamps in sorted(
    hr_by_date.items()
):
    timestamps = sorted(
        set(timestamps)
    )

    intervals = [
        b - a
        for a, b in zip(
            timestamps,
            timestamps[1:],
        )
        if b > a
    ]

    if intervals:
        median_min = (
            statistics.median(intervals)
            / 60
        )
        max_gap_min = (
            max(intervals)
            / 60
        )
    else:
        median_min = None
        max_gap_min = None

    print(
        date,
        "samples=",
        len(timestamps),
        "median_interval_min=",
        (
            round(median_min, 2)
            if median_min is not None
            else None
        ),
        "max_gap_min=",
        (
            round(max_gap_min, 2)
            if max_gap_min is not None
            else None
        ),
    )

print()
print("LATEST STATE")
print("-" * 86)

if STATE.exists():
    state = load_json(STATE)

    for key in (
        "collector_version",
        "last_success_capture_id",
        "last_success_start_date",
        "last_success_end_date",
        "last_success_at_utc",
        "overlap_days",
    ):
        print(
            f"{key}:",
            state.get(key),
        )
else:
    print("STATE FILE MISSING")

print()
print("=" * 86)
print("FINAL MACHINE CHECK")
print("=" * 86)

checks = {}

checks[
    "successful_scheduled_days>=3"
] = len(successful_days) >= 3

checks[
    "heart_rate_multi_day"
] = len(hr_by_date) >= 3

checks[
    "sleep_multi_day"
] = len({
    local_date(
        raw_timestamp(
            row["raw_record"]
        )
    )
    for row in union["sleep"].values()
    if raw_timestamp(
        row["raw_record"]
    ) is not None
}) >= 3

checks[
    "spo2_present"
] = bool(union["spo2"])

checks[
    "stress_present"
] = bool(union["stress"])

checks[
    "steps_present"
] = bool(union["steps"])

checks[
    "calories_present"
] = bool(union["calories"])

checks[
    "no_missing_overlap_records"
] = total_missing == 0

checks[
    "collector_state_present"
] = STATE.exists()

checks[
    "sport_positive_sample"
] = bool(union["sport_records"])

for key, value in checks.items():
    print(
        f"{key:<38}",
        "PASS" if value else "PENDING/FAIL",
    )

critical = [
    "successful_scheduled_days>=3",
    "heart_rate_multi_day",
    "sleep_multi_day",
    "spo2_present",
    "stress_present",
    "steps_present",
    "calories_present",
    "no_missing_overlap_records",
    "collector_state_present",
]

critical_pass = all(
    checks[key]
    for key in critical
)

print()
print("late-arriving overlap records :", total_late)
print("revised overlap records       :", total_revised)
print("missing overlap records       :", total_missing)

print()

if critical_pass:
    print("L1 CORE ACCEPTANCE = PASS")
else:
    print("L1 CORE ACCEPTANCE = REVIEW")

if checks["sport_positive_sample"]:
    print("SPORT POSITIVE SAMPLE = PASS")
else:
    print("SPORT POSITIVE SAMPLE = PENDING")

print("=" * 86)
