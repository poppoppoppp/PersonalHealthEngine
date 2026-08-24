"""Context Service (§23–§29).

Natural-language life facts -> AI structuring -> USER_REPORTED context through the sealed
L6 write semantics (append + SUPERSEDED revisions, never silent overwrite). Time semantics
(occurred/ongoing/ended/expiry/last-confirm) live in the L7 db keyed by L6 context ids, so
the sealed L6 schema stays untouched.

Fact priority is structural: user corrections create USER_REPORTED rows that supersede;
AI structuring only ever produces USER_REPORTED rows from the user's own words; AI
inference is never written here at all.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from l7.config import Config
from l7.engine.orchestrator import EngineOrchestrator
from l7.rendering.labels import body_part_label, context_label, status_label
from l7.store.db import open_readonly, utc_now
from l7.upstream.l6_bridge import L6Bridge

# One-shot events expire after their natural day; ongoing states (symptoms etc.) require
# re-confirmation after a validity window instead of staying active forever (§28).
ONGOING_TYPES = {
    "ILLNESS", "FEVER", "SORE_THROAT", "NASAL_CONGESTION", "HEADACHE",
    "FATIGUE", "STRESS", "MEDICATION",
}
ONGOING_VALID_DAYS = 7
ONESHOT_VALID_DAYS = 1


class ContextService:
    def __init__(self, config: Config, l7: sqlite3.Connection, bridge: L6Bridge,
                 orchestrator: EngineOrchestrator, reasoning_adapter=None):
        self.cfg = config
        self.l7 = l7
        self.bridge = bridge
        self.orch = orchestrator
        self._adapter = reasoning_adapter

    @property
    def adapter(self):
        if self._adapter is None:
            self._adapter = self.orch.reasoning_adapter
        return self._adapter

    # ------------------------------------------------------------------
    def ingest(self, user_id: str, text: str, today: str | None = None) -> dict:
        """Extract structured context from the user's own words and store it.

        Auto-save, non-blocking, no confirmation dialog (§25). `needs_confirmation` is only
        raised when extraction confidence is low AND the fact could materially change the
        current judgment — the client then asks once.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("empty context text")
        today = today or date.today().isoformat()

        events = self.adapter.extract_context(text, today)
        if not isinstance(events, list):
            events = []
        low_confidence = len(events) == 0
        if low_confidence:
            # Keep the raw user fact even when structuring found nothing — the raw text is
            # the user's own statement; but mark it unstructured (context_type OTHER is not
            # in the sealed vocabulary, so we store nothing in L6 and surface the gap).
            return {
                "status": "NO_STRUCTURED_FACT",
                "events": [],
                "context_ids": [],
                "needs_confirmation": False,
                "note": "没有识别出结构化事实；原始表达已记录在对话中，可换一种说法补充。",
            }

        l6w = sqlite3.connect(self.cfg.l6_db)
        l6w.row_factory = sqlite3.Row
        inserted: list[int] = []
        meta_rows: list[tuple] = []
        try:
            l6w.execute("BEGIN IMMEDIATE")
            now = utc_now()
            for event in events:
                cur = l6w.execute(
                    "INSERT INTO personal_context (context_date,context_type,body_part,severity,"
                    "raw_text,source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
                    (event["context_date"], event["context_type"], event.get("body_part"),
                     event.get("severity"), text, "USER_REPORTED", now, now),
                )
                inserted.append(cur.lastrowid)
                valid_days = ONGOING_VALID_DAYS if event["context_type"] in ONGOING_TYPES else ONESHOT_VALID_DAYS
                meta_rows.append((
                    cur.lastrowid, user_id, event["context_date"],
                    1 if event["context_type"] in ONGOING_TYPES else 0,
                    None,
                    (date.fromisoformat(event["context_date"]) + timedelta(days=valid_days)).isoformat(),
                    now, "high", now, now,
                ))
            l6w.commit()
        except Exception:
            l6w.rollback()
            raise
        finally:
            l6w.close()

        for row in meta_rows:
            self.l7.execute(
                "INSERT OR REPLACE INTO context_time_meta (l6_context_id,user_id,occurred_on,ongoing,"
                "ended_on,valid_until,last_confirmed_at,extraction_confidence,created_at_utc,updated_at_utc)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                row,
            )
        self.l7.commit()

        # Context changed the fact base -> re-evaluate Today (change detection decides cost).
        re_eval = self.orch.evaluate(user_id, trigger="context_added")
        return {
            "status": "SAVED",
            "events": events,
            "context_ids": inserted,
            "needs_confirmation": False,
            "re_evaluation": {
                "outcome": re_eval.outcome,
                "model_calls": re_eval.model_calls,
                "judgment_updated": re_eval.judgment_updated,
            },
        }

    # ------------------------------------------------------------------
    def list_current(self, user_id: str) -> dict:
        l6 = open_readonly(self.cfg.l6_db)
        try:
            rows = l6.execute(
                "SELECT id, context_date, context_type, body_part, severity, raw_text,"
                " status, created_at_utc FROM personal_context WHERE status='CURRENT'"
                " ORDER BY context_date DESC, id DESC"
            ).fetchall()
        finally:
            l6.close()
        metas = {
            r["l6_context_id"]: dict(r)
            for r in self.l7.execute(
                "SELECT * FROM context_time_meta WHERE user_id=?", (user_id,)
            ).fetchall()
        }
        items = []
        for r in rows:
            d = dict(r)
            d["context_type_label"] = context_label(d["context_type"])
            d["body_part_label"] = body_part_label(d["body_part"])
            d["status_label"] = status_label(d["status"])
            m = metas.get(d["id"])
            d["time_meta"] = {
                "ongoing": bool(m["ongoing"]) if m else None,
                "valid_until": m["valid_until"] if m else None,
                "ended_on": m["ended_on"] if m else None,
            }
            items.append(d)
        return {"contexts": items}

    # ------------------------------------------------------------------
    def correct(self, user_id: str, context_id: int, text: str, today: str | None = None) -> dict:
        """User correction: old row SUPERSEDED, new USER_REPORTED rows with provenance (§29)."""
        text = (text or "").strip()
        if not text:
            raise ValueError("empty correction text")
        today = today or date.today().isoformat()
        events = self.adapter.extract_context(text, today) or []

        core = self.bridge.core
        l6w = sqlite3.connect(self.cfg.l6_db)
        l6w.row_factory = sqlite3.Row
        try:
            l6w.execute("BEGIN IMMEDIATE")
            now = utc_now()
            old = l6w.execute(
                "SELECT * FROM personal_context WHERE id=? AND status='CURRENT'", (context_id,)
            ).fetchone()
            if old is None:
                raise LookupError("no CURRENT context to correct")
            l6w.execute(
                "UPDATE personal_context SET status='SUPERSEDED', updated_at_utc=? WHERE id=?",
                (now, context_id),
            )
            inserted = []
            for event in events:
                cur = l6w.execute(
                    "INSERT INTO personal_context (context_date,context_type,body_part,severity,"
                    "raw_text,source,status,supersedes_id,created_at_utc,updated_at_utc)"
                    " VALUES (?,?,?,?,?,?,'CURRENT',?,?,?)",
                    (event["context_date"], event["context_type"], event.get("body_part"),
                     event.get("severity"), text, "USER_REPORTED", context_id, now, now),
                )
                inserted.append(cur.lastrowid)
            l6w.execute(
                "INSERT INTO context_revisions (context_id,revision_kind,prior_json,new_json,created_at_utc)"
                " VALUES (?,?,?,?,?)",
                (context_id, "CORRECTION", core.canonical_json(dict(old)),
                 core.canonical_json({"events": events, "raw_text": text}), now),
            )
            l6w.commit()
        except Exception:
            l6w.rollback()
            raise
        finally:
            l6w.close()

        self.l7.execute(
            "UPDATE context_time_meta SET ended_on=?, updated_at_utc=? WHERE l6_context_id=?",
            (today, utc_now(), context_id),
        )
        self.l7.commit()
        re_eval = self.orch.evaluate(user_id, trigger="context_corrected")
        return {
            "status": "CORRECTED",
            "superseded": context_id,
            "new_context_ids": inserted,
            "re_evaluation": {
                "outcome": re_eval.outcome,
                "model_calls": re_eval.model_calls,
                "judgment_updated": re_eval.judgment_updated,
            },
        }

    # ------------------------------------------------------------------
    def delete(self, user_id: str, context_id: int) -> dict:
        core = self.bridge.core
        l6w = sqlite3.connect(self.cfg.l6_db)
        l6w.row_factory = sqlite3.Row
        try:
            l6w.execute("BEGIN IMMEDIATE")
            now = utc_now()
            old = l6w.execute(
                "SELECT * FROM personal_context WHERE id=? AND status='CURRENT'", (context_id,)
            ).fetchone()
            if old is None:
                raise LookupError("no CURRENT context to delete")
            l6w.execute(
                "UPDATE personal_context SET status='SUPERSEDED', updated_at_utc=? WHERE id=?",
                (now, context_id),
            )
            l6w.execute(
                "INSERT INTO context_revisions (context_id,revision_kind,prior_json,new_json,created_at_utc)"
                " VALUES (?,?,?,NULL,?)",
                (context_id, "DELETION", core.canonical_json(dict(old)), now),
            )
            l6w.commit()
        except Exception:
            l6w.rollback()
            raise
        finally:
            l6w.close()
        self.l7.execute(
            "UPDATE context_time_meta SET ended_on=?, updated_at_utc=? WHERE l6_context_id=?",
            (date.today().isoformat(), utc_now(), context_id),
        )
        self.l7.commit()
        re_eval = self.orch.evaluate(user_id, trigger="context_deleted")
        return {
            "status": "DELETED",
            "deleted": context_id,
            "re_evaluation": {
                "outcome": re_eval.outcome,
                "model_calls": re_eval.model_calls,
                "judgment_updated": re_eval.judgment_updated,
            },
        }

    # ------------------------------------------------------------------
    def expire_sweep(self, user_id: str, today: str) -> list[int]:
        """Time-semantics sweep (§28): mark expired ongoing contexts ended instead of
        letting them stay active forever. Returns affected L6 context ids."""
        rows = self.l7.execute(
            "SELECT l6_context_id FROM context_time_meta WHERE user_id=? AND ongoing=1"
            " AND ended_on IS NULL AND valid_until IS NOT NULL AND valid_until < ?",
            (user_id, today),
        ).fetchall()
        affected = [r["l6_context_id"] for r in rows]
        if affected:
            self.l7.execute(
                "UPDATE context_time_meta SET ended_on=?, updated_at_utc=?"
                " WHERE user_id=? AND l6_context_id IN ({})".format(",".join("?" for _ in affected)),
                (today, utc_now(), user_id, *affected),
            )
            self.l7.commit()
        return affected

    # ------------------------------------------------------------------
    def pending_question(self, user_id: str) -> dict:
        """Context Question Budget (§27): at most one pending question, only when the answer
        could materially change the judgment. MVP returns none — the learning loop that
        decides question value arrives with patterns/feedback history (Phase F+)."""
        return {"pending_question": None}
