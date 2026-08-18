"""Phase G tests: Notification Threshold gate, modes, quiet hours, audit trail."""

from datetime import datetime

from zoneinfo import ZoneInfo

from l7.services.notify import NotificationService, in_quiet_hours, parse_quiet_hours

TZ = ZoneInfo("Asia/Shanghai")
NOON = datetime(2026, 8, 17, 12, 0, tzinfo=TZ)
NIGHT = datetime(2026, 8, 17, 23, 0, tzinfo=TZ)


def make_notify(env):
    return NotificationService(env["cfg"], env["l7"])


def set_settings(env, **kv):
    import json
    from l7.store.db import utc_now
    for k, v in kv.items():
        env["l7"].execute(
            "INSERT INTO settings (user_id,key,value_json,updated_at_utc) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value_json=excluded.value_json,"
            "updated_at_utc=excluded.updated_at_utc",
            ("owner", k, json.dumps(v), utc_now()),
        )
    env["l7"].commit()


# ---------------------------------------------------------------- pure helpers

def test_parse_quiet_hours():
    assert parse_quiet_hours("22:00-07:00") is not None
    assert parse_quiet_hours("00:00-23:59") is not None
    assert parse_quiet_hours(None) is None
    assert parse_quiet_hours("garbage") is None
    assert parse_quiet_hours("25:99-99:99") is None


def test_in_quiet_hours_crossing_midnight():
    w = parse_quiet_hours("22:00-07:00")
    assert in_quiet_hours(datetime(2026, 8, 17, 23, 30, tzinfo=TZ), w)
    assert in_quiet_hours(datetime(2026, 8, 18, 6, 0, tzinfo=TZ), w)
    assert not in_quiet_hours(datetime(2026, 8, 17, 12, 0, tzinfo=TZ), w)


# ---------------------------------------------------------------- mode matrix

def test_smart_mode_sends_only_actionable_changes(env):
    svc = make_notify(env)
    set_settings(env, notification_mode="SMART")
    assert svc.consider("owner", "C", True, None, None, now_local=NOON)["decision"] == "SEND"
    assert svc.consider("owner", "B", True, None, None, now_local=NOON)["decision"] == "SUPPRESS"
    assert svc.consider("owner", "A", True, None, None, now_local=NOON)["decision"] == "SUPPRESS"
    assert svc.consider("owner", "C", False, None, None, now_local=NOON)["decision"] == "SUPPRESS"


def test_quiet_mode_only_safety(env):
    svc = make_notify(env)
    set_settings(env, notification_mode="QUIET")
    assert svc.consider("owner", "C", True, None, None, now_local=NOON)["decision"] == "SUPPRESS"
    r = svc.consider("owner", "E", True, None, None, now_local=NOON)
    assert r["decision"] == "SEND" and r["reason"] == "safety_attention"


def test_daily_mode_sends_every_judgment_update(env):
    svc = make_notify(env)
    set_settings(env, notification_mode="DAILY")
    assert svc.consider("owner", "A", True, None, None, now_local=NOON)["decision"] == "SEND"
    assert svc.consider("owner", "A", False, None, None, now_local=NOON)["decision"] == "SUPPRESS"


def test_safety_overrides_quiet_hours(env):
    svc = make_notify(env)
    set_settings(env, notification_mode="SMART", quiet_hours="00:00-23:59")
    r = svc.consider("owner", "C", True, None, None, now_local=NOON)
    assert r["decision"] == "SUPPRESS" and r["reason"] == "quiet_hours"
    r2 = svc.consider("owner", "E", True, None, None, now_local=NOON)
    assert r2["decision"] == "SEND", "safety must override quiet hours"


def test_every_decision_is_audited(env):
    svc = make_notify(env)
    set_settings(env, notification_mode="SMART")
    svc.consider("owner", "C", True, None, None, now_local=NOON)
    svc.consider("owner", "B", True, None, None, now_local=NOON)
    rows = env["l7"].execute(
        "SELECT decision, reason FROM notification_decisions ORDER BY id").fetchall()
    assert [(r["decision"], r["reason"]) for r in rows] == [
        ("SEND", "actionable_change"), ("SUPPRESS", "smart_mode_no_action_needed")]
    feed = svc.feed("owner")
    assert len(feed["notifications"]) == 1


# ---------------------------------------------------------------- threshold separation

def test_notification_gate_only_fires_on_new_judgment(env):
    """Recompute/UI re-render must not reach the Notification Threshold; a genuinely new
    judgment must."""
    fired = []
    env["orch"].judgment_listeners.append(lambda uid, res: fired.append((uid, res.judgment_updated)))

    # 1) First render: initial version, not a "change" -> no notification.
    env["service"].get_today("owner", trigger="t1")
    assert fired == []

    # 2) Identical evidence again: UI re-render only -> still no notification.
    env["service"].get_today("owner", trigger="t2")
    assert fired == []

    # 3) Real fact change (fever) -> judgment signature changes -> exactly one notification hook.
    import sqlite3
    con = sqlite3.connect(env["l6_copy"])
    con.execute(
        "INSERT INTO personal_context (context_date,context_type,raw_text,source,status,"
        "created_at_utc,updated_at_utc) VALUES ('2026-08-16','FEVER','鎴戝彂鐑т簡','USER_REPORTED',"
        "'CURRENT','2026-08-17T00:00:00+00:00','2026-08-17T00:00:00+00:00')")
    con.commit()
    con.close()
    env["service"].get_today("owner", trigger="t3")
    assert fired == [("owner", True)]

    today = env["service"].get_today("owner", trigger="t4")
    assert today["product_state"] == "E"

