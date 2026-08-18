"""Create a consistent Personal Health Engine VPS migration bundle.

The bundle contains production state only:
- L1 collector state
- L2 database + durable archive
- L3-L7 databases

It deliberately excludes credentials, API keys, tokens, runtime.env,
model files, logs, and virtual environments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


DATABASES = {
    "L2": (
        Path(r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3"),
        Path("srv/phe/l2/db/personal_health_raw.sqlite3"),
    ),
    "L3": (
        Path(r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3"),
        Path("srv/phe/l3/db/personal_health_features.sqlite3"),
    ),
    "L4": (
        Path(r"D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3"),
        Path("srv/phe/l4/db/personal_health_baselines.sqlite3"),
    ),
    "L5": (
        Path(r"D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3"),
        Path("srv/phe/l5/db/personal_health_analytics.sqlite3"),
    ),
    "L6": (
        Path(r"D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3"),
        Path("srv/phe/l6/db/personal_health_reasoning.sqlite3"),
    ),
    "L7": (
        Path(r"D:\PersonalHealthEngine-L7\backend\data\l7_product.sqlite3"),
        Path("srv/phe/l7/db/l7_product.sqlite3"),
    ),
}

L1_STATE = Path(
    r"D:\PersonalHealthEngine-L1Lab\xiaomi-raw-collector\collector_state.json"
)

L2_ARCHIVE = Path(
    r"D:\PersonalHealthEngine-L2\archive"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def sqlite_integrity(path: Path) -> None:
    conn = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
    )

    try:
        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"{path}: integrity_check = {integrity}"
            )
    finally:
        conn.close()


def backup_sqlite(
    name: str,
    source: Path,
    destination: Path,
) -> None:
    if not source.is_file():
        raise RuntimeError(
            f"{name} source database missing: {source}"
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print(f"SNAPSHOT {name}")
    print(f"source = {source}")
    print(f"target = {destination}")

    sqlite_integrity(source)

    source_conn = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro",
        uri=True,
    )

    target_conn = sqlite3.connect(destination)

    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()

    sqlite_integrity(destination)

    print("integrity = ok")


def build_manifest(staging: Path) -> dict:
    payload_root = staging / "srv" / "phe"

    files = []

    for path in sorted(
        p
        for p in payload_root.rglob("*")
        if p.is_file()
    ):
        relative = path.relative_to(staging).as_posix()

        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    return {
        "format": "phe.production.state.v1",
        "created_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "contains_secrets": False,
        "file_count": len(files),
        "files": files,
    }


def create_zip(
    staging: Path,
    zip_path: Path,
) -> None:
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(
            p
            for p in staging.rglob("*")
            if p.is_file()
        ):
            archive.write(
                path,
                path.relative_to(staging).as_posix(),
            )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--output-root",
        default=r"D:\PHE-VPS-Migration",
    )

    args = parser.parse_args()

    output_root = Path(args.output_root)
    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    bundle_name = (
        f"phe-production-state-{timestamp}"
    )

    zip_path = output_root / f"{bundle_name}.zip"

    print(
        "========== PHE VPS MIGRATION BUNDLE =========="
    )

    if not L1_STATE.is_file():
        raise RuntimeError(
            f"L1 collector state missing: {L1_STATE}"
        )

    if not L2_ARCHIVE.is_dir():
        raise RuntimeError(
            f"L2 archive missing: {L2_ARCHIVE}"
        )

    for name, (source, _) in DATABASES.items():
        if not source.is_file():
            raise RuntimeError(
                f"{name} database missing: {source}"
            )

    with tempfile.TemporaryDirectory(
        prefix=f"{bundle_name}-",
        dir=output_root,
    ) as temporary:
        staging = Path(temporary)

        # L1 state.
        l1_target = (
            staging
            / "srv"
            / "phe"
            / "l1"
            / "state"
            / "collector_state.json"
        )

        l1_target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            L1_STATE,
            l1_target,
        )

        # SQLite databases.
        for name, (
            source,
            relative_target,
        ) in DATABASES.items():
            backup_sqlite(
                name,
                source,
                staging / relative_target,
            )

        # L2 archive.
        print()
        print("COPY L2 ARCHIVE")

        archive_target = (
            staging
            / "srv"
            / "phe"
            / "l2"
            / "archive"
        )

        shutil.copytree(
            L2_ARCHIVE,
            archive_target,
        )

        # Manifest.
        manifest = build_manifest(staging)

        manifest_path = (
            staging
            / "migration_manifest.json"
        )

        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # ZIP.
        print()
        print("COMPRESSING...")

        create_zip(
            staging,
            zip_path,
        )

    zip_hash = sha256_file(zip_path)

    zip_size_mb = (
        zip_path.stat().st_size
        / 1024
        / 1024
    )

    print()
    print("=" * 54)
    print("PHE VPS MIGRATION BUNDLE = PASS")
    print(
        f"FILE COUNT = {manifest['file_count']}"
    )
    print(
        f"ZIP SIZE MB = {zip_size_mb:.2f}"
    )
    print(f"ZIP = {zip_path}")
    print(f"SHA256 = {zip_hash}")
    print("SECRETS INCLUDED = False")
    print("=" * 54)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
