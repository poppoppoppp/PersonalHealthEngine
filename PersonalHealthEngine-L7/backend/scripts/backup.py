"""Backup: online-consistent VACUUM INTO copies of every database the product depends on,
with retention.

Usage:
    python scripts/backup.py [--dir <backup root>] [--keep 14]

The L7 process keeps running; VACUUM INTO takes a consistent snapshot without locking
writers out for the duration of a copy.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l7.config import Config  # noqa: E402


def backup_once(cfg: Config, backup_dir: str, keep: int = 14) -> list[str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(backup_dir) / stamp
    n = 1
    while root.exists():  # two runs inside the same second must not collide
        root = Path(backup_dir) / f"{stamp}_{n}"
        n += 1
    root.mkdir(parents=True, exist_ok=False)
    sources = {
        "l3_features": cfg.l3_db,
        "l4_baselines": cfg.l4_db,
        "l5_analytics": cfg.l5_db,
        "l6_reasoning": cfg.l6_db,
        "l7_product": cfg.l7_db,
    }
    written = []
    for name, path in sources.items():
        src = Path(path)
        if not src.exists():
            continue
        dest = root / f"{name}.sqlite3"
        con = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            con.execute("VACUUM INTO ?", (str(dest),))
        finally:
            con.close()
        written.append(str(dest))

    # Retention: keep the newest `keep` snapshots.
    snaps = sorted(
        (p for p in Path(backup_dir).iterdir() if p.is_dir() and p.name[:8].isdigit()),
        key=lambda p: p.name,
    )
    for old in snaps[:-keep] if keep > 0 else []:
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=r"D:\PersonalHealthEngine-L7\backups")
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()
    written = backup_once(Config(), args.dir, args.keep)
    print(f"backup complete: {len(written)} databases")
    for w in written:
        print("  " + w)


if __name__ == "__main__":
    main()
