"""Validated exact cache with in-process coalescing for medical reviews."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable

from l7.engine.qna_orchestration import validate_medical_review
from l7.store.db import utc_now


def unavailable_review() -> dict:
    return validate_medical_review({
        "review_status": "UNAVAILABLE",
        "medical_concerns": [],
        "causality_concerns": [],
        "missing_safety_considerations": [],
        "unsafe_actions": [],
        "required_changes": [],
        "escalation_reason": None,
        "review_summary": "medical review unavailable",
    })


class MedicalReviewCache:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self._lock = threading.RLock()
        self._inflight: dict[str, threading.Event] = {}

    def _read_validated(self, key: str) -> tuple[dict | None, bool]:
        row = self.con.execute(
            "SELECT response_json FROM medical_review_cache WHERE cache_key=?", (key,),
        ).fetchone()
        if row is None:
            return None, False
        try:
            return validate_medical_review(json.loads(row["response_json"])), False
        except Exception:
            return None, True

    def get_or_review(self, key: str, review: Callable[[], dict], *,
                      model_artifact_hash: str = "unknown") -> tuple[dict, str]:
        with self._lock:
            cached, corrupt = self._read_validated(key)
            if corrupt:
                return unavailable_review(), "CORRUPT"
            if cached is not None:
                self.con.execute(
                    "UPDATE medical_review_cache SET hit_count=hit_count+1,last_used_at_utc=? "
                    "WHERE cache_key=?", (utc_now(), key),
                )
                self.con.commit()
                return cached, "HIT"
            event = self._inflight.get(key)
            owner = event is None
            if owner:
                event = threading.Event()
                self._inflight[key] = event

        if not owner:
            event.wait()
            with self._lock:
                cached, corrupt = self._read_validated(key)
                if corrupt or cached is None:
                    return unavailable_review(), "UNAVAILABLE"
                self.con.execute(
                    "UPDATE medical_review_cache SET hit_count=hit_count+1,last_used_at_utc=? "
                    "WHERE cache_key=?", (utc_now(), key),
                )
                self.con.commit()
                return cached, "COALESCED"

        try:
            validated = validate_medical_review(review())
            now = utc_now()
            with self._lock:
                self.con.execute(
                    "INSERT INTO medical_review_cache "
                    "(cache_key,response_json,model_artifact_hash,created_at_utc,last_used_at_utc) "
                    "VALUES (?,?,?,?,?)",
                    (key, json.dumps(validated, ensure_ascii=False, sort_keys=True),
                     model_artifact_hash, now, now),
                )
                self.con.commit()
            return validated, "MISS"
        except Exception:
            return unavailable_review(), "UNAVAILABLE"
        finally:
            with self._lock:
                finished = self._inflight.pop(key, None)
                if finished is not None:
                    finished.set()
