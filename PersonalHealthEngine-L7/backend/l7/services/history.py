"""History Service — Health Episode projection (§37–§40).

First organizing principle of history: continuous related changes aggregated into episodes
(开始 -> 发展 -> 变化 -> 恢复/结束). Ordinary stable days are hidden by default but never
deleted. The projection is deterministic (zero model calls) and append-only: each rebuild
supersedes the previous projection rows instead of rewriting them.
"""

from __future__ import annotations

import json
import sqlite3

from l7.config import Config
from l7.store.db import open_readonly, utc_now

HYPOTHESIS_LABELS = {
    "SLEEP_DEFICIT": "睡眠不足",
    "SLEEP_EXCESS": "睡眠过多",
    "HIGH_TRAINING_LOAD": "训练负荷偏高",
    "INSUFFICIENT_RECOVERY": "恢复不足",
    "ALCOHOL_EFFECT": "酒精影响",
    "ACUTE_ILLNESS_SUSPECTED": "疑似急性疾病",
    "STRESS_LOAD": "压力负荷",
    "CIRCADIAN_DISRUPTION": "作息紊乱",
    "INACTIVITY": "活动量不足",
    "UNKNOWN": "未确定原因",
}

NOTABLE_STATES = ("NOTABLE_CHANGE",)  # sealed L6 vocabulary; MILD_CHANGE never opens an episode
EPISODE_GAP_DAYS = 1  # a one-day data gap does not split an episode


def _label(htype: str | None) -> str:
    return HYPOTHESIS_LABELS.get(htype or "", htype or "未确定原因")


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
            last_row = by_date[ep["end_date"]]
            still_open = ep["end_date"] == latest_date
            phase = "DEVELOPING" if still_open else "CLOSED"
            summary = (
                f"{_label(ep['primary'])}：{ep['start_date']} 起"
                + (f"，持续到 {ep['end_date']}。" if ep['end_date'] != ep['start_date'] else "。")
                + (f"最新判断置信度 {last_row['confidence']}。" if last_row.get('confidence') else "")
            )
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
            for row in reasoning:
                key = self._episode_for(row["analysis_date"], projected)
                if key is None:
                    continue
                self.l7.execute(
                    "INSERT INTO episode_events (episode_id,event_date,kind,ref_layer,ref_id,"
                    "detail_json,created_at_utc) VALUES (?,?,?,?,?,?,?)",
                    (ids[key], row["analysis_date"], "JUDGMENT", "L6", row["id"],
                     json.dumps({"overall_state": row["overall_state"],
                                 "primary": row["primary_hypothesis_type"],
                                 "version_status": row["status"]}, ensure_ascii=False), now))
            for c in contexts:
                key = self._episode_for(c["context_date"], projected)
                if key is None:
                    continue
                self.l7.execute(
                    "INSERT INTO episode_events (episode_id,event_date,kind,ref_layer,ref_id,"
                    "detail_json,created_at_utc) VALUES (?,?,?,?,?,?,?)",
                    (ids[key], c["context_date"], "CONTEXT", "L6", c["id"],
                     json.dumps({"context_type": c["context_type"],
                                 "raw_text": c["raw_text"],
                                 "status": c["status"]}, ensure_ascii=False), now))
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
                                 "correction": bool(f["correction_text"])}, ensure_ascii=False), now))
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
    def list_episodes(self, user_id: str) -> dict:
        return self.rebuild(user_id)

    def episode_detail(self, user_id: str, episode_id: int) -> dict:
        row = self.l7.execute(
            "SELECT * FROM health_episodes WHERE id=? AND user_id=? AND status='CURRENT'",
            (episode_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError("episode not found")
        events = [dict(e) for e in self.l7.execute(
            "SELECT event_date, kind, ref_layer, ref_id, detail_json FROM episode_events"
            " WHERE episode_id=? ORDER BY event_date, id", (episode_id,))]
        for e in events:
            e["detail"] = json.loads(e.pop("detail_json"))
        return {
            "episode": dict(row),
            "timeline": events,
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


def _day_diff(a: str, b: str) -> int:
    from datetime import date
    da = date.fromisoformat(a)
    db = date.fromisoformat(b)
    return (db - da).days
