import json
from datetime import datetime, timezone


def get_path(obj, path):
    """
    Resolve dotted paths such as:
        value.time
        value.bpm

    'value' in Xiaomi raw JSON is itself a JSON string,
    so callers should pass the decoded outer + inner objects.
    """

    if not path:
        raise ValueError("empty field path")

    parts = path.split(".")

    if parts[0] == "value":
        current = obj["__inner__"]
        parts = parts[1:]
    else:
        current = obj["__outer__"]

    for part in parts:
        if not isinstance(current, dict):
            raise ValueError(
                f"path {path!r} crosses non-object value"
            )

        if part not in current:
            raise KeyError(
                f"missing field path: {path}"
            )

        current = current[part]

    return current


def epoch_to_utc_iso(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).isoformat(timespec="seconds")


def classify_source_sid(sid):
    sid = str(sid)

    if sid.startswith("hlth.gen_"):
        return "XIAOMI_GENERATED"

    return "NUMERIC_SOURCE"


def normalize_point_row(
    row,
    definition,
):
    """
    Convert one L2 latest raw version into one canonical
    normalized POINT fact payload.

    This function performs no database writes.
    """

    if definition["temporal_type"] != "POINT":
        raise ValueError(
            "definition is not POINT temporal type"
        )

    outer = json.loads(
        row["raw_json"]
    )

    raw_value = outer.get("value")

    if not isinstance(raw_value, str):
        raise ValueError(
            "outer.value must be JSON string"
        )

    inner = json.loads(raw_value)

    context = {
        "__outer__": outer,
        "__inner__": inner,
    }

    event_time = get_path(
        context,
        definition[
            "event_time_source"
        ]
    )

    value = get_path(
        context,
        definition[
            "value_source"
        ]
    )

    if event_time is None:
        raise ValueError(
            "normalized event time is null"
        )

    if value is None:
        raise ValueError(
            "normalized numeric value is null"
        )

    if int(event_time) != int(
        row["raw_time"]
    ):
        raise ValueError(
            "normalized event time != L2 raw_time"
        )

    attributes = {}

    for output_name, source_path in (
        definition.get(
            "preserved_vendor_attributes",
            {}
        ).items()
    ):
        attributes[output_name] = get_path(
            context,
            source_path
        )

    attributes_json = (
        json.dumps(
            attributes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":")
        )
        if attributes
        else None
    )

    sid = str(
        row["raw_sid"]
    )

    return {
        "logical_record_id":
            row["logical_record_id"],

        "raw_version_id":
            row["raw_version_id"],

        "metric":
            definition["metric"],

        "fact_kind":
            "POINT",

        "evidence_type":
            definition["evidence_type"],

        "event_time_utc":
            epoch_to_utc_iso(
                event_time
            ),

        "value_num":
            float(value),

        "unit":
            definition["unit"],

        "provider":
            row["provider"],

        "source_sid":
            sid,

        "source_class":
            classify_source_sid(sid),

        "timezone_name":
            row["zone_name"],

        "timezone_offset_seconds":
            row["zone_offset"],

        "attributes_json":
            attributes_json,
    }
