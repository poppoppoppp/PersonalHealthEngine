"""History Service — Health Episode projection (§37–§40).

First organizing principle of history: continuous related changes aggregated into episodes
(开始 -> 发展 -> 变化 -> 恢复/结束). Ordinary stable days are hidden by default but never
deleted. The projection is deterministic (zero model calls) and append-only: each rebuild
supersedes the previous projection rows instead of rewriting them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from l7.config import Config
from l7.rendering.labels import (
    context_label,
    feedback_status_label,
    hypothesis_label,
    status_label,
)
from l7.store.db import open_readonly, utc_now

NOTABLE_STATES = ("NOTABLE_CHANGE",)  # sealed L6 vocabulary; MILD_CHANGE never opens an episode
EPISODE_GAP_DAYS = 1  # a one-day data gap does not split an episode

SLEEP_FEATURE = "sleep_source_episode.vendor_sleep_like_duration_seconds"
SLEEP_AWAKE_FEATURE = "sleep_source_episode.vendor_awake_duration_seconds"
SLEEP_SEGMENTS_FEATURE = "sleep_source_episode.vendor_stage_segment_count"

# UNKNOWN is an engine enum, not something a person can act on; the summary says what the
# user actually wants to know instead of exposing the internal label.
FRIENDLY_PRIMARY = {
    "UNKNOWN": "身体出现一些变化，具体原因还不明确",
}


def _label(htype: str | None) -> str:
    return FRIENDLY_PRIMARY.get(htype or "", hypothesis_label(htype))


def _md(iso_date: str) -> str:
    """2026-08-16 -> 8月16日 (history copy is for people, not for sorting)."""
    try:
        parts = iso_date.split("-")
        return f"{int(parts[1])}月{int(parts[2])}日"
    except (IndexError, ValueError):
        return iso_date


class HistoryService:
    def __init__(self, config: Config, l7: sqlite3.Connection):
        self.cfg = config
        self.l7 = l7

    # ------------------------------------------------------------------
    def rebuild(self, user_id: str) -> dict:
        """Deterministically project episodes from sealed L6 history."""
        l6 = open_readonly(self.cfg.l6_db)
        try:
            reasoning = [dict(r) for r in l6.execute(
                "SELECT id, analysis_date, overall_state, primary_hypothesis_type,"
                " confidence, status, created_at_utc FROM daily_reasoning"
                " ORDER BY analysis_date, id")]
            contexts = [dict(r) for r in l6.execute(
                "SELECT id, context_date, context_type, raw_text, status"
                " FROM personal_context ORDER BY context_date, id")]
            feedback = [dict(r) for r in l6.execute(
                "SELECT id, subject_type, subject_id, feedback_status, correction_text,"
                " created_at_utc FROM user_feedback ORDER BY id")]
        finally:
            l6.close()

        if not reasoning:
            return {"episodes": [], "stable_days_hidden": 0,
                    "note": "还没有任何每日推理记录。"}

        # One canonical judgment per analysis_date: the CURRENT row when present,
        # otherwise the latest version.
        by_date: dict[str, dict] = {}
        for row in reasoning:
            d = row["analysis_date"]
            if d not in by_date or row["status"] == "CURRENT":
                by_date[d] = row

        dates = sorted(by_date)
        episodes: list[dict] = []
        stable_days: list[str] = []

        for d in dates:
            row = by_date[d]
            if row["overall_state"] not in NOTABLE_STATES:
                stable_days.append(d)
                continue
            primary = row["primary_hypothesis_type"] or "UNKNOWN"
            # Merge into an open episode of the same primary theme when adjacent.
            if episodes:
                last = episodes[-1]
                gap = _day_diff(last["end_date"], d)
                if last["primary"] == primary and 0 < gap <= EPISODE_GAP_DAYS + 1:
                    last["end_date"] = d
                    last["dates"].append(d)
                    continue
            episodes.append({
                "primary": primary,
                "start_date": d,
                "end_date": d,
                "dates": [d],
            })

        latest_date = dates[-1]
        projected = []
        for ep in episodes:
            still_open = ep["end_date"] == latest_date
            phase = "DEVELOPING" if still_open else "CLOSED"
            span = (
                f"，持续到 {_md(ep['end_date'])}（共 {len(ep['dates'])} 天）"
                if ep["end_date"] != ep["start_date"] else ""
            )
            summary = f"{_label(ep['primary'])}：从 {_md(ep['start_date'])} 开始{span}。"
            projected.append({
                "episode_key": f"{ep['primary']}:{ep['start_date']}",
                "start_date": ep["start_date"],
                "end_date": ep["end_date"],
                "phase": phase,
                "summary": summary,
                "primary": ep["primary"],
                "dates": ep["dates"],
            })

        # Projection update. health_episodes is a read model keyed by (user_id, episode_key):
        # existing episodes are updated in place, vanished ones are marked SUPERSEDED, and the
        # underlying L6 history (the source of truth) is append-only regardless.
        now = utc_now()
        self.l7.execute("BEGIN IMMEDIATE")
        try:
            projected_keys = {ep["episode_key"] for ep in projected}
            for row in self.l7.execute(
                "SELECT id, episode_key FROM health_episodes"
                " WHERE user_id=? AND status='CURRENT'", (user_id,)):
                if row["episode_key"] not in projected_keys:
                    self.l7.execute(
                        "UPDATE health_episodes SET status='SUPERSEDED', updated_at_utc=?"
                        " WHERE id=?", (now, row["id"]))
            ids = {}
            for ep in projected:
                existing = self.l7.execute(
                    "SELECT id FROM health_episodes WHERE user_id=? AND episode_key=?",
                    (user_id, ep["episode_key"]),
                ).fetchone()
                if existing:
                    self.l7.execute(
                        "UPDATE health_episodes SET start_date=?,end_date=?,phase=?,summary=?,"
                        "status='CURRENT',updated_at_utc=? WHERE id=?",
                        (ep["start_date"], ep["end_date"], ep["phase"], ep["summary"],
                         now, existing["id"]),
                    )
                    ids[ep["episode_key"]] = existing["id"]
                else:
                    cur = self.l7.execute(
                        "INSERT INTO health_episodes (user_id,episode_key,start_date,end_date,"
                        "phase,summary,status,created_at_utc,updated_at_utc)"
                        " VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
                        (user_id, ep["episode_key"], ep["start_date"], ep["end_date"],
                         ep["phase"], ep["summary"], now, now),
                    )
                    ids[ep["episode_key"]] = cur.lastrowid
            self.l7.execute("DELETE FROM episode_events WHERE episode_id IN ({})".format(
                ",".join(str(i) for i in ids.values())))
            # One timeline judgment per day: the canonical (CURRENT or latest) row.
            # Superseded same-day versions stay in L6 for audit but never reach the UI.
            for row in by_date.values():
                key = self._episode_for(row["analysis_date"], projected)
                if key is None:
                    continue
                self.l7.execute(
                    "INSERT INTO episode_events (episode_id,event_date,kind,ref_layer,ref_id,"
                    "detail_json,created_at_utc) VALUES (?,?,?,?,?,?,?)",
                    (ids[key], row["analysis_date"], "JUDGMENT", "L6", row["id"],
                     json.dumps({"overall_state": row["overall_state"],
                                 "overall_state_label": (
                                     "变化较明显" if row["overall_state"] == "NOTABLE_CHANGE"
                                     else "状态变化"
                                 ),
                                 "primary": row["primary_hypothesis_type"],
                                 "primary_label": hypothesis_label(row["primary_hypothesis_type"]),
                                 "version_status": row["status"],
                                 "version_status_label": status_label(row["status"])},
                                ensure_ascii=False), now))
            for c in contexts:
                key = self._episode_for(c["context_date"], projected)
                if key is None:
                    continue
                self.l7.execute(
                    "INSERT INTO episode_events (episode_id,event_date,kind,ref_layer,ref_id,"
                    "detail_json,created_at_utc) VALUES (?,?,?,?,?,?,?)",
                    (ids[key], c["context_date"], "CONTEXT", "L6", c["id"],
                     json.dumps({"context_type": c["context_type"],
                                 "context_type_label": context_label(c["context_type"]),
                                 "raw_text": c["raw_text"],
                                 "status": c["status"],
                                 "status_label": status_label(c["status"])}, ensure_ascii=False), now))
            for f in feedback:
                if f["subject_type"] != "DAILY_REASONING":
                    continue
                date = next((r["analysis_date"] for r in reasoning if r["id"] == f["subject_id"]), None)
                if date is None:
                    continue
                key = self._episode_for(date, projected)
                if key is None:
                    continue
                self.l7.execute(
                    "INSERT INTO episode_events (episode_id,event_date,kind,ref_layer,ref_id,"
                    "detail_json,created_at_utc) VALUES (?,?,?,?,?,?,?)",
                    (ids[key], date, "FEEDBACK", "L6", f["id"],
                     json.dumps({"feedback_status": f["feedback_status"],
                                 "feedback_status_label": feedback_status_label(f["feedback_status"]),
                                 "correction": bool(f["correction_text"])}, ensure_ascii=False), now))
            metadata = {
                "stable_days_hidden": len(stable_days),
                "note": ("稳定日默认隐藏，但完整保存。" if stable_days else None),
            }
            self.l7.execute(
                "INSERT INTO read_projection_versions "
                "(user_id,projection,version,metadata_json,updated_at_utc) "
                "VALUES (?,'history_episodes',1,?,?) "
                "ON CONFLICT(user_id,projection) DO UPDATE SET "
                "version=read_projection_versions.version+1,"
                "metadata_json=excluded.metadata_json,updated_at_utc=excluded.updated_at_utc",
                (user_id, json.dumps(metadata, ensure_ascii=False), now),
            )
            self.l7.commit()
        except Exception:
            self.l7.rollback()
            raise

        return {
            "episodes": [
                {"id": ids[ep["episode_key"]],
                 **{k: ep[k] for k in ("episode_key", "start_date", "end_date", "phase", "summary")}}
                for ep in projected
            ],
            "stable_days_hidden": len(stable_days),
            "note": ("稳定日默认隐藏，但完整保存。" if stable_days else None),
        }

    @staticmethod
    def _episode_for(date: str, episodes: list[dict]) -> str | None:
        for ep in episodes:
            if date in ep["dates"]:
                return f"{ep['primary']}:{ep['start_date']}"
        return None

    # ------------------------------------------------------------------
    def list_episodes(self, user_id: str, *, limit: int = 30,
                      cursor: int | None = None) -> dict:
        params: list = [user_id]
        cursor_sql = ""
        if cursor is not None:
            cursor_sql = " AND id<?"
            params.append(cursor)
        params.append(limit + 1)
        rows = self.l7.execute(
            "SELECT id,episode_key,start_date,end_date,phase,summary"
            " FROM health_episodes WHERE user_id=? AND status='CURRENT'"
            + cursor_sql + " ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        projection = self.l7.execute(
            "SELECT version,metadata_json FROM read_projection_versions "
            "WHERE user_id=? AND projection='history_episodes'", (user_id,),
        ).fetchone()
        metadata = json.loads(projection["metadata_json"]) if projection else {}
        return {
            "episodes": [dict(row) for row in page],
            "next_cursor": page[-1]["id"] if has_more else None,
            "stable_days_hidden": metadata.get("stable_days_hidden", 0),
            "note": metadata.get("note"),
            "projection_version": projection["version"] if projection else 0,
        }

    def episode_detail(self, user_id: str, episode_id: int, *, limit: int = 30,
                       cursor: int | None = None) -> dict:
        row = self.l7.execute(
            "SELECT * FROM health_episodes WHERE id=? AND user_id=? AND status='CURRENT'",
            (episode_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError("episode not found")
        params: list = [episode_id]
        cursor_sql = ""
        if cursor is not None:
            cursor_sql = " AND id<?"
            params.append(cursor)
        params.append(limit + 1)
        event_rows = self.l7.execute(
            "SELECT id,event_date,kind,ref_layer,ref_id,detail_json FROM episode_events"
            " WHERE episode_id=?" + cursor_sql + " ORDER BY id DESC LIMIT ?", params,
        ).fetchall()
        has_more = len(event_rows) > limit
        event_rows = event_rows[:limit]
        next_cursor = event_rows[-1]["id"] if has_more else None
        event_rows = list(reversed(event_rows))
        events = [dict(e) for e in event_rows]
        for e in events:
            e["detail"] = json.loads(e.pop("detail_json"))
            e["kind_label"] = {
                "JUDGMENT": "健康判断",
                "CONTEXT": "个人情况",
                "FEEDBACK": "用户反馈",
            }.get(e["kind"], "事件记录")
        return {
            "episode": dict(row),
            "timeline": events,
            "next_cursor": next_cursor,
        }

    def search(self, user_id: str, q: str) -> dict:
        """Deterministic keyword search across episode summaries and event details."""
        q = (q or "").strip()
        if not q:
            return {"results": []}
        like = f"%{q}%"
        rows = self.l7.execute(
            "SELECT DISTINCT e.id, e.episode_key, e.start_date, e.end_date, e.phase, e.summary"
            " FROM health_episodes e LEFT JOIN episode_events ev ON ev.episode_id=e.id"
            " WHERE e.user_id=? AND e.status='CURRENT'"
            " AND (e.summary LIKE ? OR ev.detail_json LIKE ?)",
            (user_id, like, like),
        ).fetchall()
        return {"results": [dict(r) for r in rows]}

    # ------------------------------------------------------------------
    def sleep_structure(self, user_id: str, *, days: int = 14) -> dict:
        """Per-night sleep structure from the L3 feature store (read-only, zero model).

        Xiaomi Cloud exposes an awake/sleep two-stage structure for this source — deep/
        light/REM granularity is not available from the collector, so the structure is
        honest about that: total sleep, awake time with its share, and segment count."""
        del user_id  # single-owner MVP; kept for API symmetry
        l3 = open_readonly(self.cfg.l3_db, immutable_if_checkpointed=True)
        try:
            rows = l3.execute(
                "SELECT local_date, feature_name, value_num FROM derived_features"
                " WHERE feature_name IN (?, ?, ?) AND status='CURRENT' AND value_num IS NOT NULL"
                " ORDER BY local_date DESC LIMIT ?",
                (SLEEP_FEATURE, SLEEP_AWAKE_FEATURE, SLEEP_SEGMENTS_FEATURE, days * 3 * 4),
            ).fetchall()
        finally:
            l3.close()
        by_date: dict[str, dict] = {}
        for row in rows:
            night = by_date.setdefault(row["local_date"], {"local_date": row["local_date"]})
            value = row["value_num"]
            if row["feature_name"] == SLEEP_FEATURE:
                night["sleep_minutes"] = round(value / 60)
            elif row["feature_name"] == SLEEP_AWAKE_FEATURE:
                night["awake_minutes"] = round(value / 60)
            elif row["feature_name"] == SLEEP_SEGMENTS_FEATURE:
                night["segment_count"] = int(value)
        nights = []
        for night in sorted(by_date.values(), key=lambda n: n["local_date"], reverse=True)[:days]:
            sleep_minutes = night.get("sleep_minutes") or 0
            awake_minutes = night.get("awake_minutes") or 0
            total = sleep_minutes + awake_minutes
            nights.append({
                **night,
                "total_minutes": total,
                "awake_ratio": round(awake_minutes / total, 3) if total else None,
            })
        return {
            "nights": nights,
            "note": "数据来自小米手环：目前提供清醒/睡眠两段结构，暂无深睡、浅睡、REM 细分。",
        }


def _day_diff(a: str, b: str) -> int:
    from datetime import date
    da = date.fromisoformat(a)
    db = date.fromisoformat(b)
    return (db - da).days
