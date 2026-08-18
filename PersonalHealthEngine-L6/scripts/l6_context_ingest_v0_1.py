"""Layer 6 Personal Context ingestion and revision.

Ingests a natural-language life event (extracted via the reasoning adapter, mock by default)
into USER_REPORTED context, and supports formal CORRECTION/DELETION revisions that supersede
rather than silently overwrite prior context.
"""

import argparse
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from l6_core_v0_1 import canonical_json, utc_now
from l6_adapters_v0_1 import MockReasoningModelAdapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l6", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--date", default=None, help="today's date (YYYY-MM-DD)")
    parser.add_argument("--correct", type=int, default=None, help="context id to revise (CORRECTION)")
    parser.add_argument("--delete", type=int, default=None, help="context id to delete (DELETION)")
    args = parser.parse_args()

    today = args.date or date.today().isoformat()
    adapter = MockReasoningModelAdapter()
    events = adapter.extract_context(args.text, today)

    db = sqlite3.connect(args.l6)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        db.execute("BEGIN IMMEDIATE")
        now = utc_now()

        if args.correct is not None:
            old = db.execute("SELECT * FROM personal_context WHERE id=? AND status='CURRENT'", (args.correct,)).fetchone()
            if old is None:
                raise RuntimeError("no CURRENT context to correct")
            db.execute("UPDATE personal_context SET status='SUPERSEDED', updated_at_utc=? WHERE id=?", (now, args.correct))
            inserted = []
            for event in events:
                cursor = db.execute(
                    "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,source,status,supersedes_id,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (event["context_date"], event["context_type"], event.get("body_part"), event.get("severity"), args.text, "USER_REPORTED", "CURRENT", args.correct, now, now),
                )
                inserted.append(cursor.lastrowid)
            db.execute(
                "INSERT INTO context_revisions (context_id,revision_kind,prior_json,new_json,created_at_utc) VALUES (?,?,?,?,?)",
                (args.correct, "CORRECTION", canonical_json(dict(old)), canonical_json({"events": events, "raw_text": args.text}), now),
            )
            db.commit()
            print(json.dumps({"status": "PASS", "revised": args.correct, "new_context_ids": inserted}, indent=2))
            return

        if args.delete is not None:
            old = db.execute("SELECT * FROM personal_context WHERE id=? AND status='CURRENT'", (args.delete,)).fetchone()
            if old is None:
                raise RuntimeError("no CURRENT context to delete")
            db.execute("UPDATE personal_context SET status='SUPERSEDED', updated_at_utc=? WHERE id=?", (now, args.delete))
            db.execute("INSERT INTO context_revisions (context_id,revision_kind,prior_json,new_json,created_at_utc) VALUES (?,?,?,?,?)", (args.delete, "DELETION", canonical_json(dict(old)), None, now))
            db.commit()
            print(json.dumps({"status": "PASS", "deleted": args.delete}, indent=2))
            return

        inserted = []
        for event in events:
            cursor = db.execute(
                "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                (event["context_date"], event["context_type"], event.get("body_part"), event.get("severity"), args.text, "USER_REPORTED", "CURRENT", now, now),
            )
            inserted.append(cursor.lastrowid)
        db.commit()
        print(json.dumps({"status": "PASS", "events": events, "context_ids": inserted}, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
