"""Layer 4 Personal Baseline — pure deterministic computation core.

This module contains no database or file I/O side effects beyond definition
loading helpers. It is shared by the full/incremental materializer and by the
unit tests, so that every consumer computes baselines identically.

Baseline semantics (see L4_CONTRACT.md):

  - A *series* is one personal historical time series identified by
    (feature_name, scope_type, provider, source_sid, source_class,
     timezone_name, timezone_offset_seconds, unit).
  - A baseline *as of* date D with window W uses exactly the observations whose
    local_date lies in [D - W, D - 1] (D itself is excluded: no look-ahead).
  - Statistics use robust methods (median, MAD, quantiles); mean is provided for
    transparency. Health data is not assumed normal.
"""

import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

WINDOW_DEFINITION_ID = "l4c.baseline.windows"
MATURITY_DEFINITION_ID = "l4d.baseline.maturity"
SERIES_DEFINITION_ID = "l4b.baseline.series"
ELIGIBILITY_DEFINITION_ID = "l4a.baseline.eligibility"

SERIES_IDENTITY_FIELDS = (
    "feature_name",
    "scope_type",
    "provider",
    "source_sid",
    "source_class",
    "timezone_name",
    "timezone_offset_seconds",
    "unit",
)

EPISODE_SCOPE_KEYS_EXCLUDED = (
    "episode_start_utc",
    "episode_end_utc",
    "l2_logical_record_id",
)

STAT_ROUND = 12


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_date(value):
    return date.fromisoformat(value)


def add_days(value, days):
    return value + timedelta(days=days)


def date_range(start, end):
    """Inclusive list of ISO dates from start to end."""
    result = []
    current = start
    while current <= end:
        result.append(current.isoformat())
        current = add_days(current, 1)
    return result


def round_stat(value):
    if value is None:
        return None
    return round(float(value), STAT_ROUND)


def quantile(sorted_values, q):
    """Type-7 linear interpolation quantile over already-sorted values."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def median_abs_deviation(values, center):
    """Median absolute deviation around `center`."""
    if not values:
        return None
    deviations = sorted(abs(v - center) for v in values)
    return quantile(deviations, 0.5)


def load_definition(path, expected_id):
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if payload.get("definition_id") != expected_id:
        raise ValueError(f"unexpected definition_id in {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def series_identity(feature):
    """Return (series_key, components) for a L3 derived_feature dict/row."""
    components = {
        "feature_name": feature["feature_name"],
        "scope_type": feature["scope_type"],
        "provider": feature["provider"],
        "source_sid": feature["source_sid"],
        "source_class": feature["source_class"],
        "timezone_name": feature["timezone_name"],
        "timezone_offset_seconds": feature["timezone_offset_seconds"],
        "unit": feature["unit"],
    }
    return canonical_json(components), components


def observation_semantics(scope_type):
    if scope_type == "SOURCE_EPISODE":
        return "SOURCE_EPISODE_VALUE"
    return "DAILY_VALUE"


def feature_input_signature(feature):
    """Canonical signature of one L3 derived_feature row, used for incremental diff."""
    payload = {
        "feature_name": feature["feature_name"],
        "scope_type": feature["scope_type"],
        "scope_key": feature["scope_key"],
        "local_date": feature["local_date"],
        "value_num": feature["value_num"],
        "value_code": feature["value_code"],
        "unit": feature["unit"],
        "provider": feature["provider"],
        "source_sid": feature["source_sid"],
        "source_class": feature["source_class"],
        "timezone_name": feature["timezone_name"],
        "timezone_offset_seconds": feature["timezone_offset_seconds"],
        "coverage_status": feature["coverage_status"],
        "attributes_json": feature["attributes_json"],
        "definition_id": feature["definition_id"],
        "definition_version": feature["definition_version"],
    }
    return canonical_json(payload)


def build_series(features):
    """Group eligible L3 features into per-source series.

    Returns a dict keyed by series_key:
        {series_key: {"components": ..., "observations": [...], "coverage_status": ...}}
    """
    series = {}
    for feature in features:
        if feature["value_num"] is None:
            continue
        key, components = series_identity(feature)
        entry = series.setdefault(
            key,
            {
                "components": components,
                "observations": [],
                "coverage_statuses": set(),
            },
        )
        entry["observations"].append(
            {
                "local_date": feature["local_date"],
                "value": float(feature["value_num"]),
                "feature_id": feature["id"],
                "feature_name": feature["feature_name"],
            }
        )
        entry["coverage_statuses"].add(feature["coverage_status"])
    for entry in series.values():
        entry["observations"].sort(key=lambda o: (o["local_date"], o["feature_id"]))
        statuses = sorted(entry["coverage_statuses"])
        entry["coverage_status"] = statuses[0] if len(statuses) == 1 else "MIXED"
    return series


def maturity_for(window_days, observation_count, span, distinct_dates, coverage, thresholds):
    threshold = thresholds.get(str(window_days))
    if threshold is None:
        raise ValueError(f"no maturity threshold configured for window {window_days}")
    if observation_count < threshold["min_observations"]:
        return "INSUFFICIENT_HISTORY"
    if (
        observation_count >= threshold["established_min_observations"]
        and span is not None
        and span >= threshold["established_min_span_days"]
        and coverage >= threshold["established_min_coverage"]
    ):
        return "ESTABLISHED"
    return "PROVISIONAL"


def compute_series_baselines(series_key, series, as_of_dates, windows, maturity_thresholds):
    """Compute every (window, as_of_date) baseline for one series."""
    observations = series["observations"]
    components = series["components"]
    coverage_status = series["coverage_status"]
    semantics = observation_semantics(components["scope_type"])
    unit = components["unit"]

    # Pre-index observations by local_date for fast window filtering.
    by_date = defaultdict(list)
    for obs in observations:
        by_date[obs["local_date"]].append(obs)
    sorted_dates = sorted(by_date)

    baselines = []
    for window_days in windows:
        start_offset = window_days
        for as_of in as_of_dates:
            as_of_d = parse_date(as_of)
            start_d = add_days(as_of_d, -start_offset)
            eligible = [
                obs
                for obs in observations
                if start_d.isoformat() <= obs["local_date"] < as_of
            ]
            values = sorted(obs["value"] for obs in eligible)
            n = len(values)
            distinct_dates = len({obs["local_date"] for obs in eligible})
            span = None
            if eligible:
                observed_dates = sorted({obs["local_date"] for obs in eligible})
                span = (parse_date(observed_dates[-1]) - parse_date(observed_dates[0])).days + 1
            coverage = round_stat(distinct_dates / window_days) if window_days else 0.0

            maturity = maturity_for(
                window_days, n, span, distinct_dates, coverage, maturity_thresholds
            )

            publish = maturity in ("PROVISIONAL", "ESTABLISHED")
            mean = round_stat(sum(values) / n) if publish and n else None
            median = round_stat(quantile(values, 0.5)) if publish else None
            mad = round_stat(median_abs_deviation(values, median)) if publish and n else None
            q10 = round_stat(quantile(values, 0.10)) if publish else None
            q25 = round_stat(quantile(values, 0.25)) if publish else None
            q50 = round_stat(quantile(values, 0.50)) if publish else None
            q75 = round_stat(quantile(values, 0.75)) if publish else None
            q90 = round_stat(quantile(values, 0.90)) if publish else None

            attributes = {
                "robust_statistics": True,
                "no_lookahead": True,
                "as_of_rule": "observations with local_date in [as_of_date - window_days, as_of_date - 1]",
                "source_scoped": True,
                "coverage_status": coverage_status,
                "observation_semantics": semantics,
            }
            if semantics == "SOURCE_EPISODE_VALUE":
                attributes["canonical_night"] = False
                attributes["vendor_inferred"] = coverage_status == "VENDOR_INFERENCE"

            baselines.append(
                {
                    "series_key": series_key,
                    "components": components,
                    "window_days": window_days,
                    "as_of_date": as_of,
                    "observation_count": n,
                    "distinct_observation_dates": distinct_dates,
                    "history_span_days": span,
                    "calendar_coverage": coverage,
                    "mean": mean,
                    "median": median,
                    "mad": mad,
                    "q10": q10,
                    "q25": q25,
                    "q50": q50,
                    "q75": q75,
                    "q90": q90,
                    "unit": unit,
                    "maturity": maturity,
                    "maturity_definition_id": MATURITY_DEFINITION_ID,
                    "maturity_definition_version": "0.1",
                    "window_definition_id": WINDOW_DEFINITION_ID,
                    "window_definition_version": "0.1",
                    "attributes": attributes,
                    "inputs": tuple(sorted(obs["feature_id"] for obs in eligible)),
                }
            )
    return baselines


def compute_all_baselines(series, as_of_dates, windows, maturity_thresholds):
    """Compute every baseline for every series."""
    desired = []
    for series_key in sorted(series):
        desired.extend(
            compute_series_baselines(
                series_key, series[series_key], as_of_dates, windows, maturity_thresholds
            )
        )
    return desired


def baseline_signature(baseline):
    """Canonical signature used to detect materialization deltas."""
    return (
        baseline["series_key"],
        baseline["window_days"],
        baseline["as_of_date"],
        baseline["observation_count"],
        baseline["distinct_observation_dates"],
        baseline["history_span_days"],
        baseline["calendar_coverage"],
        baseline["mean"],
        baseline["median"],
        baseline["mad"],
        baseline["q10"],
        baseline["q25"],
        baseline["q50"],
        baseline["q75"],
        baseline["q90"],
        baseline["unit"],
        baseline["maturity"],
        baseline["maturity_definition_id"],
        baseline["maturity_definition_version"],
        baseline["window_definition_id"],
        baseline["window_definition_version"],
        canonical_json(baseline["attributes"]),
        tuple(sorted(baseline["inputs"])),
    )


def baseline_identity(baseline):
    """(series_key, window_days, as_of_date) — the natural business key."""
    return (baseline["series_key"], baseline["window_days"], baseline["as_of_date"])
