import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MIGRATION_PATTERN = re.compile(r"^(\d{3})_(.+)\.sql$")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def statements(sql_text):
    buffer = ""
    for line in sql_text.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        raise ValueError("incomplete SQL statement at end of migration")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--migrations-root", required=True)
    args = parser.parse_args()

    migration_files = []
    for path in Path(args.migrations_root).glob("*.sql"):
        match = MIGRATION_PATTERN.match(path.name)
        if match:
            migration_files.append((int(match.group(1)), match.group(2), path))
    migration_files.sort()
    versions = [item[0] for item in migration_files]
    if versions != list(range(1, max(versions, default=0) + 1)):
        raise RuntimeError(f"migration chain is not contiguous: {versions}")

    db = sqlite3.connect(args.l3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    applied = []
    try:
        has_registry = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        existing = (
            {
                row["version"]: row
                for row in db.execute("SELECT * FROM schema_migrations")
            }
            if has_registry
            else {}
        )
        for version, name, path in migration_files:
            raw = path.read_bytes()
            checksum = hashlib.sha256(raw).hexdigest()
            if version in existing:
                row = existing[version]
                if row["name"] != name or row["checksum_sha256"] != checksum:
                    raise RuntimeError(f"migration {version} registry mismatch")
                continue

            db.execute("BEGIN IMMEDIATE")
            try:
                sql_text = raw.decode("utf-8-sig")
                for statement in statements(sql_text):
                    db.execute(statement)
                db.execute(
                    """
                    INSERT INTO schema_migrations (
                        version,name,applied_at_utc,checksum_sha256
                    ) VALUES (?,?,?,?)
                    """,
                    (version, name, utc_now(), checksum),
                )
                db.execute(f"PRAGMA user_version = {version}")
                db.commit()
                applied.append(version)
            except Exception:
                db.rollback()
                raise

        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"post-migration integrity failure: {integrity}, fk={len(foreign_key_errors)}"
            )
        result = {
            "status": "PASS",
            "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            "applied": applied,
            "migration_count": len(migration_files),
        }
        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
