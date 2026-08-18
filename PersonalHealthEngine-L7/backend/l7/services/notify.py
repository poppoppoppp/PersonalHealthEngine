"""Notification Service — Notification Threshold gate (§48–§52, Phase G).

Hard separation of thresholds: Recompute < UI Change < Notification. This service is only
ever consulted when a NEW judgment version exists (judgment_updated) — a mere UI
re-rendering never reaches it. Every decision is logged (audit trail), including
suppressions, so "为什么没通知/为什么通知了" is always answerable.

Modes:
  QUIET — safety (E) only.
  SMART — safety (E) + actionable changes (C) that updated the judgment. Default.
  DAILY — safety (E) + every judgment update (daily state refresh).

Safety overrides quiet hours; everything else respects them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time
from zoneinfo import ZoneInfo

from l7.config import Config
from l7.store.db import utc_now


def parse_quiet_hours(value) -> tuple[time, time] | None:
    """'22:00-07:00' -> (start, end); None/invalid -> None."""
    if not value or not isinstance(value, str) or "-" not in value:
        return None
    try:
        a, b = value.split("-", 1)
        return time.fromisoformat(a.strip()), time.fromisoformat(b.strip())
    except ValueError:
        return None


def in_quiet_hours(now_local: datetime, window: tuple[time, time] | None) -> bool:
    if window is None:
        return False
    start, end = window
    t = now_local.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # crosses midnight


class NotificationService:
    def __init__(self, config: Config, l7: sqlite3.Connection):
        self.cfg = config
        self.l7 = l7

    # ------------------------------------------------------------------
    def _settings(self, user_id: str) -> dict:
        rows = self.l7.execute(
            "SELECT key, value_json FROM settings WHERE user_id=?", (user_id,))
        out = {"notification_mode": "SMART", "quiet_hours": None}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value_json"])
            except (ValueError, TypeError):
                pass
        return out

    def consider(self, user_id: str, product_state: str, judgment_updated: bool,
                 change_note: str | None, version_id: int | None,
                 now_local: datetime | None = None) -> dict:
        """Notification Threshold gate. Only call this for a NEW judgment version."""
        now_local = now_local or datetime.now(ZoneInfo(self.cfg.timezone_name))
        settings = self._settings(user_id)
        mode = settings.get("notification_mode") or "SMART"
        if mode not in ("QUIET", "SMART", "DAILY"):
            mode = "SMART"
        quiet = parse_quiet_hours(settings.get("quiet_hours"))

        decision, reason = self._decide(mode, product_state, judgment_updated)

        if decision == "SEND" and product_state != "E" and in_quiet_hours(now_local, quiet):
            decision, reason = "SUPPRESS", "quiet_hours"

        self.l7.execute(
            "INSERT INTO notification_decisions (user_id,created_at_utc,decision,reason,"
            "mode,related_version_id) VALUES (?,?,?,?,?,?)",
            (user_id, utc_now(), decision, reason, mode, version_id),
        )
        self.l7.commit()
        return {"decision": decision, "reason": reason, "mode": mode}

    @staticmethod
    def _decide(mode: str, product_state: str, judgment_updated: bool) -> tuple[str, str]:
        if product_state == "E":
            return "SEND", "safety_attention"
        if not judgment_updated:
            return "SUPPRESS", "no_new_judgment"
        if mode == "QUIET":
            return "SUPPRESS", "quiet_mode"
        if mode == "SMART":
            if product_state == "C":
                return "SEND", "actionable_change"
            return "SUPPRESS", "smart_mode_no_action_needed"
        # DAILY
        return "SEND", "daily_update"

    # ------------------------------------------------------------------
    def feed(self, user_id: str, limit: int = 20) -> dict:
        """In-app notification feed: the SEND decisions with their Today version payload."""
        rows = self.l7.execute(
            "SELECT n.id, n.created_at_utc, n.reason, n.related_version_id,"
            " v.product_state, v.rendered_json"
            " FROM notification_decisions n"
            " LEFT JOIN today_versions v ON v.id=n.related_version_id"
            " WHERE n.user_id=? AND n.decision='SEND'"
            " ORDER BY n.id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        items = []
        for r in rows:
            payload = {}
            if r["rendered_json"]:
                try:
                    payload = json.loads(r["rendered_json"])
                except ValueError:
                    pass
            items.append({
                "id": r["id"],
                "created_at_utc": r["created_at_utc"],
                "reason": r["reason"],
                "product_state": r["product_state"],
                "headline": payload.get("headline"),
                "change_note": payload.get("change_note"),
            })
        return {"notifications": items}

    def decisions(self, user_id: str, limit: int = 50) -> dict:
        rows = self.l7.execute(
            "SELECT id, created_at_utc, decision, reason, mode, related_version_id"
            " FROM notification_decisions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return {"decisions": [dict(r) for r in rows]}
