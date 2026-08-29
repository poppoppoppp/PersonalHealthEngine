"""Phase F tests: Health Episode projection + pattern display semantics."""

import json
import sqlite3

from l7.services.history import HistoryService, _day_diff
from l7.services.today import TodayService

TODAY = "2026-08-17"


def make_history(env):
    return HistoryService(env["cfg"], env["l7"])


def seed_reasoning(env, analysis_date, overall_state, primary, status="CURRENT",
                   confidence="LOW"):
    con = sqlite3.connect(env["l6_copy"])
    con.execute(
        "INSERT INTO daily_reasoning (analysis_date,evidence_bundle_id,overall_state,"
        "primary_hypothesis_type,confidence,recommended_actions_json,medical_review_state,"
        "reasoning_model,reasoning_summary,definition_id,definition_version,status,"
        "created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'BYPASSED','mock-reasoning-v0.1',"
        "'seed','l6.reasoning/v1','1',?,?,?)",
        (analysis_date, 1, overall_state, primary, confidence,
         json.dumps([], ensure_ascii=False), status, "2026-08-17T00:00:00+00:00",
         "2026-08-17T00:00:00+00:00"),
    )
    con.commit()
    con.close()


def test_episodes_from_real_data(env):
    svc = make_history(env)
    r = svc.rebuild("owner")
    eps = r["episodes"]
    assert len(eps) == 1
    ep = eps[0]
    assert ep["episode_key"] == "SLEEP_DEFICIT:2026-08-16"
    assert ep["start_date"] == "2026-08-16"
    assert ep["phase"] == "DEVELOPING"
    assert "睡眠不足" in ep["summary"]
    assert r["stable_days_hidden"] == 0

    detail = svc.episode_detail("owner", ep["id"])
    kinds = [e["kind"] for e in detail["timeline"]]
    assert "JUDGMENT" in kinds
    # Seeded USER_REPORTED contexts on 2026-08-16 belong to the episode timeline.
    assert "CONTEXT" in kinds


def test_multi_day_grouping_and_stable_day_hiding(env):
    seed_reasoning(env, "2026-08-10", "NOTABLE_CHANGE", "SLEEP_DEFICIT")
    seed_reasoning(env, "2026-08-11", "NOTABLE_CHANGE", "SLEEP_DEFICIT")
    seed_reasoning(env, "2026-08-12", "STABLE", None)
    seed_reasoning(env, "2026-08-14", "NOTABLE_CHANGE", "ALCOHOL_EFFECT")

    svc = make_history(env)
    r = svc.rebuild("owner")
    keys = sorted(e["episode_key"] for e in r["episodes"])
    assert "SLEEP_DEFICIT:2026-08-10" in keys
    assert "ALCOHOL_EFFECT:2026-08-14" in keys
    sleep_ep = next(e for e in r["episodes"] if e["episode_key"].startswith("SLEEP_DEFICIT"))
    assert sleep_ep["end_date"] == "2026-08-11", "consecutive same-theme days merge"
    assert r["stable_days_hidden"] >= 1, "stable days are hidden but counted"

    # The older episode is no longer the latest -> not DEVELOPING.
    assert sleep_ep["phase"] == "CLOSED"


def test_rebuild_is_idempotent_and_supersedes_vanished(env):
    svc = make_history(env)
    first = svc.rebuild("owner")
    first_id = first["episodes"][0]["id"]

    # Same facts -> same read-model identity, no duplicated events.
    second = svc.rebuild("owner")
    assert second["episodes"][0]["id"] == first_id
    n1 = env["l7"].execute("SELECT COUNT(*) FROM episode_events").fetchone()[0]
    svc.rebuild("owner")
    n2 = env["l7"].execute("SELECT COUNT(*) FROM episode_events").fetchone()[0]
    assert n1 == n2

    # A new episode appears, then vanishes when its facts are corrected away:
    # the read model marks it SUPERSEDED instead of silently deleting it.
    seed_reasoning(env, "2026-08-15", "NOTABLE_CHANGE", "ALCOHOL_EFFECT")
    grown = svc.rebuild("owner")
    assert any(e["episode_key"].startswith("ALCOHOL_EFFECT") for e in grown["episodes"])

    con = sqlite3.connect(env["l6_copy"])
    con.execute("DELETE FROM daily_reasoning WHERE analysis_date='2026-08-15'")
    con.commit()
    con.close()
    shrunk = svc.rebuild("owner")
    assert not any(e["episode_key"].startswith("ALCOHOL_EFFECT") for e in shrunk["episodes"])
    gone = env["l7"].execute(
        "SELECT status FROM health_episodes WHERE episode_key LIKE 'ALCOHOL_EFFECT%'"
    ).fetchall()
    assert gone and all(r["status"] == "SUPERSEDED" for r in gone)


def test_search_finds_episode_by_keyword(env):
    svc = make_history(env)
    svc.rebuild("owner")
    hits = svc.search("owner", "睡眠")
    assert hits["results"], "keyword search over summaries must hit"
    assert svc.search("owner", "不存在的词xyz")["results"] == []
    assert svc.search("owner", "")["results"] == []


def test_no_episodes_without_reasoning(env):
    con = sqlite3.connect(env["l6_copy"])
    con.execute("DELETE FROM daily_reasoning")
    con.commit()
    con.close()
    svc = make_history(env)
    r = svc.rebuild("owner")
    assert r["episodes"] == []
    assert r["note"]


def test_day_diff():
    assert _day_diff("2026-08-10", "2026-08-11") == 1
    assert _day_diff("2026-08-10", "2026-08-10") == 0


def test_episode_summary_is_plain_language(env):
    seed_reasoning(env, "2026-08-13", "NOTABLE_CHANGE", "UNKNOWN")

    svc = make_history(env)
    r = svc.rebuild("owner")
    for ep in r["episodes"]:
        assert "置信度" not in ep["summary"], "internal confidence wording never ships"
    unknown = next(
        e for e in r["episodes"] if e["episode_key"].startswith("UNKNOWN")
    )
    assert "原因还不明确" in unknown["summary"]
    assert "暂无法确定原因" not in unknown["summary"]


def test_timeline_keeps_one_judgment_per_day(env):
    seed_reasoning(
        env, "2026-08-16", "NOTABLE_CHANGE", "RECOVERY_STRAIN",
        status="STALE",
    )
    svc = make_history(env)
    r = svc.rebuild("owner")
    ep = next(
        e for e in r["episodes"] if e["episode_key"].startswith("SLEEP_DEFICIT")
    )
    detail = svc.episode_detail("owner", ep["id"])
    judgments = [
        e for e in detail["timeline"]
        if e["kind"] == "JUDGMENT" and e["event_date"] == "2026-08-16"
    ]
    assert len(judgments) == 1, "superseded same-day versions never reach the UI"


# ---------------------------------------------------------------- patterns display

def test_pattern_display_status_semantics(env):
    con = sqlite3.connect(env["l6_copy"])
    now = "2026-08-17T00:00:00+00:00"
    # Weakened: enough evidence, support below half.
    con.execute(
        "INSERT INTO personal_patterns (pattern_key,trigger_context_type,outcome_signal,"
        "support_count,total_count,maturity,first_seen_date,last_seen_date,created_at_utc,"
        "updated_at_utc) VALUES ('WEAK::X','WEAK','X',1,5,'OBSERVING','2026-07-01','2026-08-01',?,?)",
        (now, now))
    # Invalidated: enough evidence, zero support.
    con.execute(
        "INSERT INTO personal_patterns (pattern_key,trigger_context_type,outcome_signal,"
        "support_count,total_count,maturity,first_seen_date,last_seen_date,created_at_utc,"
        "updated_at_utc) VALUES ('DEAD::X','DEAD','X',0,4,'OBSERVING','2026-07-01','2026-08-01',?,?)",
        (now, now))
    con.commit()
    con.close()

    svc = TodayService(env["cfg"], env["l7"], env["orch"])
    r = svc.patterns("owner")
    by_key = {p["pattern_key"]: p for p in r["patterns"]}
    assert by_key["WEAK::X"]["display_status"] == "WEAKENED"
    assert by_key["DEAD::X"]["display_status"] == "INVALIDATED"
    # Counterevidence stays visible.
    assert by_key["WEAK::X"]["counter_examples"] == 4


def test_single_event_never_surfaced(env):
    con = sqlite3.connect(env["l6_copy"])
    now = "2026-08-17T00:00:00+00:00"
    con.execute(
        "INSERT INTO personal_patterns (pattern_key,trigger_context_type,outcome_signal,"
        "support_count,total_count,maturity,first_seen_date,last_seen_date,created_at_utc,"
        "updated_at_utc) VALUES ('ONE::X','ONE','X',1,1,'OBSERVING','2026-08-16','2026-08-16',?,?)",
        (now, now))
    con.commit()
    con.close()
    svc = TodayService(env["cfg"], env["l7"], env["orch"])
    r = svc.patterns("owner")
    assert all(p["pattern_key"] != "ONE::X" for p in r["patterns"])
    assert r["observing_count"] >= 1
