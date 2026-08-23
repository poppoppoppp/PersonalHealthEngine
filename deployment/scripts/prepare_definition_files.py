"""Reconcile deployed definition paths and bytes against sealed registries.

This fixes only two transport defects: Windows ZIP backslashes embedded in Linux
filenames and CRLF conversion. A file is rewritten only when its LF bytes match the
already-sealed registry checksum exactly; registry rows are never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path, PurePosixPath


LAYERS = (
    ("L3", "PersonalHealthEngine-L3", "l3/db/personal_health_features.sqlite3"),
    ("L4", "PersonalHealthEngine-L4", "l4/db/personal_health_baselines.sqlite3"),
    ("L5", "PersonalHealthEngine-L5", "l5/db/personal_health_analytics.sqlite3"),
    ("L6", "PersonalHealthEngine-L6", "l6/db/personal_health_reasoning.sqlite3"),
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def registry_hashes(database: Path) -> dict[tuple[str, str], str]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return {
            (row[0], row[1]): row[2]
            for row in connection.execute(
                "SELECT definition_id, definition_version, definition_sha256 "
                "FROM definition_registry WHERE status='ACTIVE'"
            )
        }
    finally:
        connection.close()


def reconcile_layer(definitions_root: Path, database: Path) -> dict[str, int]:
    expected = registry_hashes(database)
    plans = []
    keys = set()

    for path in sorted(definitions_root.rglob("*.json")):
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
        key = (payload["definition_id"], payload["definition_version"])
        registry_sha = expected.get(key)
        if registry_sha is None:
            raise RuntimeError(f"definition is absent from ACTIVE registry: {path}")

        repaired = raw
        if sha256(raw) != registry_sha:
            repaired = raw.replace(b"\r\n", b"\n")
            if sha256(repaired) != registry_sha:
                raise RuntimeError(f"definition checksum mismatch is not EOL-only: {path}")

        relative = path.relative_to(definitions_root).as_posix()
        normalized_relative = PurePosixPath(relative.replace("\\", "/"))
        if normalized_relative.is_absolute() or ".." in normalized_relative.parts:
            raise RuntimeError(f"unsafe definition path: {relative}")
        target = definitions_root.joinpath(*normalized_relative.parts)

        plans.append((path, target, raw, repaired, stat.S_IMODE(path.stat().st_mode)))
        keys.add(key)

    if len(plans) != len(expected) or keys != set(expected):
        raise RuntimeError(
            f"definition count mismatch: files={len(plans)}, active_registry={len(expected)}"
        )

    targets = [plan[1] for plan in plans]
    if len(set(targets)) != len(targets):
        raise RuntimeError("multiple definition files map to the same Linux path")

    for source, target, _, repaired, _ in plans:
        if target != source and target.exists() and target.read_bytes() != repaired:
            raise RuntimeError(f"conflicting definition paths: {source}")

    moved = 0
    normalized = 0
    for source, target, raw, repaired, mode in plans:
        if target != source:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                source.unlink()
            elif repaired == raw:
                os.replace(source, target)
            else:
                temporary = target.with_name(target.name + ".definition.tmp")
                temporary.write_bytes(repaired)
                os.chmod(temporary, mode)
                os.replace(temporary, target)
                source.unlink()
            moved += 1
        elif repaired != raw:
            temporary = source.with_name(source.name + ".definition.tmp")
            temporary.write_bytes(repaired)
            os.chmod(temporary, mode)
            os.replace(temporary, source)

        if repaired != raw:
            normalized += 1

    return {"checked": len(plans), "paths_repaired": moved, "eol_repaired": normalized}


def reconcile_definitions(code_root: Path, data_root: Path) -> dict[str, dict[str, int]]:
    results = {}
    for layer, project, database_relative in LAYERS:
        result = reconcile_layer(
            code_root / project / "definitions",
            data_root / database_relative,
        )
        results[layer] = result
        print(
            f"{layer} definitions = PASS "
            f"({result['checked']} checked, {result['paths_repaired']} paths repaired, "
            f"{result['eol_repaired']} EOL repaired)"
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code-root", type=Path, default=Path("/opt/phe"))
    parser.add_argument("--data-root", type=Path, default=Path("/srv/phe"))
    args = parser.parse_args()
    reconcile_definitions(args.code_root, args.data_root)
    print("DEFINITION DEPLOYMENT RECONCILIATION = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
