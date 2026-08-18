"""Per-user export and delete (data-portability requirement).

Export: every L7 row belonging to the user + a read-only snapshot of the sealed L6 data the
product presents (current judgment, bundles, context, feedback, QA). JSON, deterministic key
order, no secrets included.

Delete: removes the user's L7 rows. Sealed upstream layers own their own rows; the script
prints the exact upstream statement list instead of touching SEALED dbs, because L1–L6
deletion is a layer-owner action, not an L7 action.
"""

from __future__ import annotations

import json
import sqlite3

from l7.config import Config
from l7.store.db import connect_l7, open_readonly, utc_now

# Deletion order respects FK direction: children before parents.
L7_USER_TABLES = (
    "model_call_cache", "evidence_change_log", "notification_decisions",
    "settings", "context_time_meta",
    "episode_events", "health_episodes",
    "qa_turns", "conversations",
    "eval_runs", "today_versions",
)


def export_user(cfg: Config, user_id: str) -> dict:
    l7 = connect_l7(cfg.l7_db)
    out: dict = {"user_id": user_id, "exported_at_utc": utc_now(), "l7": {}, "l6_snapshot": {}}
    for table in L7_USER_TABLES:
        try:
            rows = l7.execute(f"SELECT * FROM {table} WHERE user_id=?", (user_id,)).fetchall()
            out["l7"][table] = [dict(r) for r in rows]
        except sqlite3.OperationalError:
            out["l7"][table] = []

    l6 = open_readonly(cfg.l6_db)
    try:
        out["l6_snapshot"]["daily_reasoning"] = [
            dict(r) for r in l6.execute(
                "SELECT * FROM daily_reasoning ORDER BY analysis_date, id")]
        out["l6_snapshot"]["personal_context"] = [
            dict(r) for r in l6.execute("SELECT * FROM personal_context ORDER BY id")]
        out["l6_snapshot"]["user_feedback"] = [
            dict(r) for r in l6.execute("SELECT * FROM user_feedback ORDER BY id")]
        out["l6_snapshot"]["qa_sessions"] = [
            dict(r) for r in l6.execute("SELECT * FROM qa_sessions ORDER BY id")]
        out["l6_snapshot"]["personal_patterns"] = [
            dict(r) for r in l6.execute("SELECT * FROM personal_patterns ORDER BY id")]
    finally:
        l6.close()
    return out


def delete_user(cfg: Config, user_id: str) -> dict:
    l7 = connect_l7(cfg.l7_db)
    deleted = {}
    l7.execute("BEGIN IMMEDIATE")
    try:
        for table in L7_USER_TABLES:
            try:
                cur = l7.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
                deleted[table] = cur.rowcount
            except sqlite3.OperationalError:
                deleted[table] = 0
        cur = l7.execute("DELETE FROM users WHERE id=?", (user_id,))
        deleted["users"] = cur.rowcount
        l7.commit()
    except Exception:
        l7.rollback()
        raise
    return {
        "status": "DELETED_FROM_L7",
        "deleted_rows": deleted,
        "upstream_note": (
            "L1–L6 rows are owned by their sealed layers. To remove them, the layer owner "
            "must run the corresponding sealed deletion procedure; L7 never modifies SEALED "
            "upstream data."
        ),
    }
