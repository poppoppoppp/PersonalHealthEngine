"""Layer 5 Health Analytics — pure deterministic computation core.

Contains the statistical math for deviation, persistence, trend, change-point, and
cross-metric relationship analytics. No database I/O lives here beyond definition loading
helpers, so every consumer computes identical results.

Personal deviation is NOT clinical abnormality. All analytics are deterministic, robust,
transparent, and versioned.
"""

import hashlib
import json
import math
from datetime import date, datetime, timezone

DEVIATION_DEFINITION_ID = "l5a.deviation.robust"
PERSISTENCE_DEFINITION_ID = "l5b.persistence"
TREND_DEFINITION_ID = "l5b.trend.robust"
CHANGE_POINT_DEFINITION_ID = "l5c.change_point"
RELATIONSHIP_DEFINITION_ID = "l5d.relationship"
EVIDENCE_DEFINITION_ID = "l5e.evidence"

SERIES_COMPONENT_FIELDS = (
    "feature_name",
    "scope_type",
    "provider",
    "source_sid",
    "source_class",
    "timezone_name",
    "timezone_offset_seconds",
    "unit",
)

STAT_ROUND = 12


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def round_stat(value):
    if value is None:
        return None
    return round(float(value), STAT_ROUND)


def parse_date(value):
    return date.fromisoformat(value)


def quantile(sorted_values, q):
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


def median(values):
    if not values:
        return None
    return quantile(sorted(values), 0.5)


def median_abs_deviation(values, center):
    if not values:
        return None
    return median([abs(v - center) for v in values])


def load_definition(path, expected_id):
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if payload.get("definition_id") != expected_id:
        raise ValueError(f"unexpected definition_id in {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def series_component_tuple(row):
    """Component tuple matching L3 features to L4 series identity."""
    return tuple(row.get(field) for field in SERIES_COMPONENT_FIELDS)


def series_key_from_row(row):
    payload = {field: row.get(field) for field in SERIES_COMPONENT_FIELDS}
    return canonical_json(payload)


def rank_values(values):
    """Average rank for ties (1-based)."""
    n = len(values)
    if n == 0:
        return []
    indexed = sorted((values[i], i) for i in range(n))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and indexed[j][0] == indexed[i][0]:
            j += 1
        avg = (i + j - 1) / 2.0 + 1.0
        for k in range(i, j):
            ranks[indexed[k][1]] = avg
        i = j
    return ranks


def pearson_corr(x, y):
    n = len(x)
    if n == 0:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    vx = sum((x[i] - mx) ** 2 for i in range(n))
    vy = sum((y[i] - my) ** 2 for i in range(n))
    if vx == 0.0 or vy == 0.0:
        return 0.0
    return cov / (math.sqrt(vx) * math.sqrt(vy))


def spearman_rho(points):
    """Spearman rank correlation between x and y of (x, y) points."""
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return round_stat(pearson_corr(rank_values(xs), rank_values(ys)))


def theil_sen_slope(points):
    """Median of pairwise (dy/dx) slopes; points = [(x, y), ...] sorted by x."""
    slopes = []
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            dx = points[j][0] - points[i][0]
            if dx != 0:
                slopes.append((points[j][1] - points[i][1]) / dx)
    if not slopes:
        return 0.0
    slopes.sort()
    return quantile(slopes, 0.5)


def quantile_position(current, q10, q25, q50, q75, q90):
    if current < q10:
        return "BELOW_Q10"
    if current < q25:
        return "Q10_Q25"
    if current < q50:
        return "Q25_Q50"
    if current < q75:
        return "Q50_Q75"
    if current < q90:
        return "Q75_Q90"
    return "ABOVE_Q90"


def deviation_side(current, center, eps=1e-9):
    if abs(current - center) <= eps:
        return "WITHIN"
    return "ABOVE" if current > center else "BELOW"


def deviation_metrics(current_value, unit, baseline, relative_units):
    """Compute the deviation result dict for one current value vs one baseline."""
    maturity = baseline["maturity"]
    median_value = baseline["median"]
    mad_value = baseline["mad"]
    result = {
        "baseline_maturity": maturity,
        "baseline_median": median_value,
        "baseline_mad": mad_value,
        "current_value": current_value,
        "absolute_deviation": None,
        "relative_deviation": None,
        "relative_deviation_applicable": 0,
        "robust_standardized_deviation": None,
        "robust_z_unavailable_reason": None,
        "quantile_position": None,
        "deviation_side": None,
        "deviation_class": "INSUFFICIENT_BASELINE",
        "evidence_status": "INSUFFICIENT_BASELINE",
    }
    if maturity == "INSUFFICIENT_HISTORY" or median_value is None:
        return result

    abs_dev = round_stat(current_value - median_value)
    result["absolute_deviation"] = abs_dev

    rel_applicable = 1 if (unit in relative_units and median_value != 0) else 0
    result["relative_deviation_applicable"] = rel_applicable
    result["relative_deviation"] = round_stat(abs_dev / median_value) if rel_applicable else None

    if mad_value is None:
        result["robust_z_unavailable_reason"] = "MAD_MISSING"
    elif mad_value == 0:
        result["robust_z_unavailable_reason"] = "MAD_ZERO"
    else:
        result["robust_standardized_deviation"] = round_stat(abs_dev / mad_value)

    result["quantile_position"] = quantile_position(
        current_value, baseline["q10"], baseline["q25"], baseline["q50"], baseline["q75"], baseline["q90"]
    )
    result["deviation_side"] = deviation_side(current_value, median_value)

    if current_value > baseline["q90"]:
        result["deviation_class"] = "ABOVE_TYPICAL_RANGE"
    elif current_value < baseline["q10"]:
        result["deviation_class"] = "BELOW_TYPICAL_RANGE"
    else:
        result["deviation_class"] = "WITHIN_TYPICAL_RANGE"

    result["evidence_status"] = "SUFFICIENT" if maturity == "ESTABLISHED" else "PROVISIONAL"
    return result


def trailing_run(classes, target):
    run = 0
    for cls in reversed(classes):
        if cls == target:
            run += 1
        else:
            break
    return run


def classify_persistence(classes, maturities, min_consecutive):
    """classes = chronological deviation_class list; maturities = matching baseline maturity list."""
    if not classes:
        return {
            "trailing_observation_count": 0,
            "consecutive_above_typical": 0,
            "consecutive_below_typical": 0,
            "persistence_class": "INSUFFICIENT_OBSERVATIONS",
            "evidence_status": "INSUFFICIENT_OBSERVATIONS",
        }
    above = trailing_run(classes, "ABOVE_TYPICAL_RANGE")
    below = trailing_run(classes, "BELOW_TYPICAL_RANGE")
    if len(classes) < min_consecutive:
        pclass = "INSUFFICIENT_OBSERVATIONS"
        evidence = "INSUFFICIENT_OBSERVATIONS"
    elif above >= min_consecutive:
        pclass = "PERSISTENT_ABOVE_TYPICAL"
        evidence = maturity_evidence(maturities)
    elif below >= min_consecutive:
        pclass = "PERSISTENT_BELOW_TYPICAL"
        evidence = maturity_evidence(maturities)
    else:
        pclass = "NO_PERSISTENT_DEVIATION"
        evidence = maturity_evidence(maturities)
    return {
        "trailing_observation_count": len(classes),
        "consecutive_above_typical": above,
        "consecutive_below_typical": below,
        "persistence_class": pclass,
        "evidence_status": evidence,
    }


def maturity_evidence(maturities):
    if not maturities:
        return "INSUFFICIENT_BASELINE"
    if "INSUFFICIENT_HISTORY" in maturities:
        return "INSUFFICIENT_BASELINE"
    if "PROVISIONAL" in maturities:
        return "PROVISIONAL"
    return "SUFFICIENT"


def classify_trend(points, min_points, rho_threshold):
    """points = [(day_number, value), ...] chronological. Returns dict."""
    n = len(points)
    if n < min_points:
        return {
            "trend_point_count": n,
            "theil_sen_slope": None,
            "spearman_rho": None,
            "trend_class": "INSUFFICIENT_OBSERVATIONS",
            "evidence_status": "INSUFFICIENT_OBSERVATIONS",
        }
    slope = round_stat(theil_sen_slope(points))
    rho = round_stat(spearman_rho(points)) if spearman_rho(points) is not None else None
    if slope > 0 and rho is not None and rho >= rho_threshold:
        tclass = "RISING"
    elif slope < 0 and rho is not None and rho <= -rho_threshold:
        tclass = "FALLING"
    else:
        tclass = "STABLE"
    return {
        "trend_point_count": n,
        "theil_sen_slope": slope,
        "spearman_rho": rho,
        "trend_class": tclass,
        "evidence_status": "SUFFICIENT",
    }


def detect_change(points, min_change_points, min_segment_points, shift_threshold):
    """points = [(date_iso, value), ...] chronological.

    Robust median level-shift over candidate splits. The shift magnitude is the absolute
    median-level difference normalized by the within-segment MAD (robust to the shift itself).
    A clean two-level shift (zero within-segment noise) is treated as unbounded magnitude.
    """
    n = len(points)
    if n < min_change_points:
        return {
            "observation_count": n,
            "candidate_split_date": None,
            "shift_magnitude": None,
            "change_class": "INSUFFICIENT_EVIDENCE",
            "evidence_status": "INSUFFICIENT_EVIDENCE",
        }
    values = [p[1] for p in points]
    best = None  # (shift, k)
    for k in range(min_segment_points, n - min_segment_points + 1):
        before = values[:k]
        after = values[k:]
        mb = median(before)
        ma = median(after)
        diff = ma - mb
        deviations = [abs(v - mb) for v in before] + [abs(v - ma) for v in after]
        within_mad = median(deviations)
        if within_mad is None or within_mad == 0:
            shift = 0.0 if diff == 0 else float("inf")
        else:
            shift = abs(diff) / within_mad
        if best is None or shift > best[0]:
            best = (shift, k)
    shift, k = best
    if shift >= shift_threshold:
        magnitude = None if shift == float("inf") else round_stat(shift)
        return {
            "observation_count": n,
            "candidate_split_date": points[k][0],
            "shift_magnitude": magnitude,
            "change_class": "CHANGE_DETECTED",
            "evidence_status": "SUFFICIENT",
        }
    return {
        "observation_count": n,
        "candidate_split_date": None,
        "shift_magnitude": round_stat(shift) if shift not in (0.0, float("inf")) else None,
        "change_class": "NO_CHANGE",
        "evidence_status": "SUFFICIENT",
    }


def classify_relationship(x, y, min_paired, rho_threshold):
    n = len(x)
    if n < min_paired:
        return {
            "paired_count": n,
            "spearman_rho": None,
            "relationship_class": "INSUFFICIENT_PAIRED_DATA",
            "evidence_status": "INSUFFICIENT_PAIRED_DATA",
        }
    rho = round_stat(pearson_corr(rank_values(x), rank_values(y)))
    if rho >= rho_threshold:
        rclass = "POSITIVE_ASSOCIATION"
    elif rho <= -rho_threshold:
        rclass = "NEGATIVE_ASSOCIATION"
    else:
        rclass = "NO_ASSOCIATION"
    return {
        "paired_count": n,
        "spearman_rho": rho,
        "relationship_class": rclass,
        "evidence_status": "SUFFICIENT",
    }
