"""Verify and restore a Personal Health Engine production-state bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_FORMAT = "phe.production.state.v1"
PHE_UID = 10001
PHE_GID = 10001

REQUIRED_DATABASES = (
    "srv/phe/l2/db/personal_health_raw.sqlite3",
    "srv/phe/l3/db/personal_health_features.sqlite3",
    "srv/phe/l4/db/personal_health_baselines.sqlite3",
    "srv/phe/l5/db/personal_health_analytics.sqlite3",
    "srv/phe/l6/db/personal_health_reasoning.sqlite3",
    "srv/phe/l7/db/l7_product.sqlite3",
)

REQUIRED_STATE = (
    "srv/phe/l1/state/collector_state.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def sqlite_integrity(path: Path) -> None:
    conn = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
    )

    try:
        result = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if result != "ok":
            raise RuntimeError(
                f"{path}: integrity_check = {result}"
            )
    finally:
        conn.close()


def validate_member_name(name: str) -> None:
    path = Path(name)

    if path.is_absolute():
        raise RuntimeError(
            f"absolute ZIP path is forbidden: {name}"
        )

    if ".." in path.parts:
        raise RuntimeError(
            f"path traversal detected: {name}"
        )


def verify_bundle(
    zip_path: Path,
    staging: Path,
) -> dict:
    if not zip_path.is_file():
        raise RuntimeError(
            f"migration bundle not found: {zip_path}"
        )

    with zipfile.ZipFile(zip_path, "r") as archive:
        infos = archive.infolist()

        for info in infos:
            validate_member_name(info.filename)

            unix_mode = (
                info.external_attr >> 16
            ) & 0o170000

            if unix_mode == stat.S_IFLNK:
                raise RuntimeError(
                    f"symlink in ZIP is forbidden: "
                    f"{info.filename}"
                )

        names = {
            info.filename
            for info in infos
            if not info.is_dir()
        }

        if "migration_manifest.json" not in names:
            raise RuntimeError(
                "migration_manifest.json is missing"
            )

        archive.extractall(staging)

    manifest_path = (
        staging / "migration_manifest.json"
    )

    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    if manifest.get("format") != EXPECTED_FORMAT:
        raise RuntimeError(
            "unsupported migration format: "
            f"{manifest.get('format')}"
        )

    if manifest.get("contains_secrets") is not False:
        raise RuntimeError(
            "bundle declares secrets; restore refused"
        )

    files = manifest.get("files")

    if not isinstance(files, list):
        raise RuntimeError(
            "manifest files field is invalid"
        )

    if manifest.get("file_count") != len(files):
        raise RuntimeError(
            "manifest file_count mismatch"
        )

    manifest_paths = []

    for item in files:
        relative = item["path"]

        validate_member_name(relative)

        if not relative.startswith("srv/phe/"):
            raise RuntimeError(
                f"unexpected payload path: {relative}"
            )

        manifest_paths.append(relative)

    if len(set(manifest_paths)) != len(
        manifest_paths
    ):
        raise RuntimeError(
            "duplicate manifest paths detected"
        )

    zip_payload = names - {
        "migration_manifest.json"
    }

    if zip_payload != set(manifest_paths):
        raise RuntimeError(
            "ZIP payload does not exactly match manifest"
        )

    for item in files:
        relative = item["path"]
        path = staging / relative

        if not path.is_file():
            raise RuntimeError(
                f"payload file missing: {relative}"
            )

        actual_size = path.stat().st_size

        if actual_size != item["size_bytes"]:
            raise RuntimeError(
                f"size mismatch: {relative}"
            )

        actual_hash = sha256_file(path)

        if actual_hash != item["sha256"]:
            raise RuntimeError(
                f"SHA256 mismatch: {relative}"
            )

    available = set(manifest_paths)

    for required in (
        *REQUIRED_DATABASES,
        *REQUIRED_STATE,
    ):
        if required not in available:
            raise RuntimeError(
                f"required payload missing: {required}"
            )

    # Collector state must be valid JSON.
    collector_state = (
        staging
        / "srv"
        / "phe"
        / "l1"
        / "state"
        / "collector_state.json"
    )

    json.loads(
        collector_state.read_text(
            encoding="utf-8"
        )
    )

    # Every production SQLite snapshot must still be sound.
    for relative in REQUIRED_DATABASES:
        sqlite_integrity(
            staging / relative
        )

    archive_files = [
        path
        for path in manifest_paths
        if path.startswith(
            "srv/phe/l2/archive/"
        )
    ]

    if not archive_files:
        raise RuntimeError(
            "L2 durable archive is empty"
        )

    return manifest


def backup_existing(
    destination: Path,
    target_root: Path,
    backup_root: Path,
) -> None:
    relative = destination.relative_to(
        target_root
    )

    backup = backup_root / relative
    backup.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(
        destination,
        backup,
    )


def install_bundle(
    staging: Path,
    manifest: dict,
    target_root: Path,
    force: bool,
) -> None:
    if hasattr(os, "geteuid"):
        if os.geteuid() != 0:
            raise RuntimeError(
                "actual restore must run as root"
            )

    existing = []

    for item in manifest["files"]:
        source_relative = Path(
            item["path"]
        )

        payload_relative = Path(
            *source_relative.parts[2:]
        )

        destination = (
            target_root
            / payload_relative
        )

        if destination.exists():
            existing.append(destination)

    if existing and not force:
        preview = "\n".join(
            str(path)
            for path in existing[:10]
        )

        raise RuntimeError(
            "restore target already contains payload "
            "files; use --force only after review:\n"
            f"{preview}"
        )

    backup_root = (
        target_root
        / "backups"
        / (
            "pre-restore-"
            + datetime.now(
                timezone.utc
            ).strftime(
                "%Y%m%dT%H%M%SZ"
            )
        )
    )

    if existing and force:
        for destination in existing:
            backup_existing(
                destination,
                target_root,
                backup_root,
            )

        print(
            f"existing_state_backup = "
            f"{backup_root}"
        )

    for item in manifest["files"]:
        relative = Path(
            item["path"]
        )

        source = staging / relative

        payload_relative = Path(
            *relative.parts[2:]
        )

        destination = (
            target_root
            / payload_relative
        )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = destination.with_name(
            destination.name + ".restore.tmp"
        )

        shutil.copy2(
            source,
            temporary,
        )

        os.replace(
            temporary,
            destination,
        )

        if hasattr(os, "chown"):
            os.chown(
                destination,
                PHE_UID,
                PHE_GID,
            )

        os.chmod(
            destination,
            0o640,
        )

    # Ensure all relevant directories belong to phe.
    for relative in (
        "l1/state",
        "l2/db",
        "l2/archive",
        "l3/db",
        "l4/db",
        "l5/db",
        "l6/db",
        "l7/db",
    ):
        root = target_root / relative

        if not root.exists():
            continue

        for current, dirs, _ in os.walk(root):
            current_path = Path(current)

            if hasattr(os, "chown"):
                os.chown(
                    current_path,
                    PHE_UID,
                    PHE_GID,
                )

            os.chmod(
                current_path,
                0o750,
            )

            for directory in dirs:
                path = current_path / directory

                if hasattr(os, "chown"):
                    os.chown(
                        path,
                        PHE_UID,
                        PHE_GID,
                    )

                os.chmod(
                    path,
                    0o750,
                )

    # Final integrity check from installed locations.
    for relative in REQUIRED_DATABASES:
        payload_relative = Path(
            *Path(relative).parts[2:]
        )

        sqlite_integrity(
            target_root / payload_relative
        )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "bundle",
        type=Path,
    )

    parser.add_argument(
        "--target-root",
        type=Path,
        default=Path("/srv/phe"),
    )

    parser.add_argument(
        "--verify-only",
        action="store_true",
    )

    parser.add_argument(
        "--force",
        action="store_true",
    )

    args = parser.parse_args()

    print(
        "========== PHE VPS STATE RESTORE =========="
    )
    print(
        f"bundle = {args.bundle}"
    )

    with tempfile.TemporaryDirectory(
        prefix="phe-restore-"
    ) as temporary:
        staging = Path(temporary)

        manifest = verify_bundle(
            args.bundle,
            staging,
        )

        print(
            f"manifest_files = "
            f"{manifest['file_count']}"
        )

        print(
            "bundle_verification = PASS"
        )

        if args.verify_only:
            print()
            print(
                "PHE VPS STATE VERIFY = PASS"
            )
            return 0

        install_bundle(
            staging,
            manifest,
            args.target_root,
            args.force,
        )

    print()
    print(
        "PHE VPS STATE RESTORE = PASS"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())