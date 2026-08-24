"""Performance contracts for bounded, cacheable read paths."""

from fastapi.testclient import TestClient

from l7.api.app import create_app
from l7.services.history import HistoryService
from l7.services.today import TodayService


AUTH = {"Authorization": "Bearer dev-local-token"}


def test_today_serves_latest_projection_without_evaluation(env, monkeypatch):
    env["orch"].evaluate("owner", "test_seed")

    def forbidden(*args, **kwargs):
        raise AssertionError("GET /today must not evaluate")

    monkeypatch.setattr(env["orch"], "evaluate", forbidden)
    payload = TodayService(env["cfg"], env["l7"], env["orch"]).get_today("owner")
    assert payload["schema"] == "l7.today/v1"


def test_history_list_serves_projection_without_rebuild(env, monkeypatch):
    service = HistoryService(env["cfg"], env["l7"])
    service.rebuild("owner")

    def forbidden(*args, **kwargs):
        raise AssertionError("GET history must not rebuild")

    monkeypatch.setattr(service, "rebuild", forbidden)
    result = service.list_episodes("owner")
    assert result["episodes"]


def test_history_and_context_use_stable_bounded_cursors(env):
    HistoryService(env["cfg"], env["l7"]).rebuild("owner")
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as client:
        history_first = client.get("/history/episodes?limit=1", headers=AUTH).json()
        assert len(history_first["episodes"]) <= 1
        if history_first["next_cursor"]:
            history_second = client.get(
                f"/history/episodes?limit=1&cursor={history_first['next_cursor']}",
                headers=AUTH,
            ).json()
            first_ids = {item["id"] for item in history_first["episodes"]}
            assert not first_ids & {item["id"] for item in history_second["episodes"]}

        context_first = client.get("/context?limit=1", headers=AUTH).json()
        assert len(context_first["contexts"]) <= 1
        if context_first["next_cursor"]:
            context_second = client.get(
                f"/context?limit=1&cursor={context_first['next_cursor']}", headers=AUTH,
            ).json()
            first_ids = {item["id"] for item in context_first["contexts"]}
            assert not first_ids & {item["id"] for item in context_second["contexts"]}


def test_conditional_get_returns_304_for_versioned_reads(env):
    env["orch"].evaluate("owner", "test_seed")
    HistoryService(env["cfg"], env["l7"]).rebuild("owner")
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as client:
        for path in ("/today", "/patterns", "/history/episodes"):
            first = client.get(path, headers=AUTH)
            assert first.status_code == 200
            assert first.headers.get("etag")
            second = client.get(
                path, headers={**AUTH, "If-None-Match": first.headers["etag"]},
            )
            assert second.status_code == 304
            assert not second.content


def test_conversation_turns_are_bounded_and_cursor_ordered(env):
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as client:
        conversation_id = client.post("/qa/conversations", headers=AUTH).json()["conversation_id"]
        for index in range(4):
            env["l7"].execute(
                "INSERT INTO qa_turns (conversation_id,user_id,role,text,created_at_utc) "
                "VALUES (?,?,?,?,?)",
                (conversation_id, "owner", "USER", f"turn-{index}", f"2026-08-24T00:00:0{index}+00:00"),
            )
        env["l7"].commit()
        first = client.get(
            f"/qa/conversations/{conversation_id}?limit=2", headers=AUTH,
        ).json()
        assert [turn["text"] for turn in first["turns"]] == ["turn-2", "turn-3"]
        second = client.get(
            f"/qa/conversations/{conversation_id}?limit=2&cursor={first['next_cursor']}",
            headers=AUTH,
        ).json()
        assert [turn["text"] for turn in second["turns"]] == ["turn-0", "turn-1"]
        assert second["next_cursor"] is None


def test_growing_l7_reads_use_composite_indexes(env):
    plans = {
        "history": env["l7"].execute(
            "EXPLAIN QUERY PLAN SELECT id FROM health_episodes "
            "WHERE user_id=? AND status='CURRENT' AND id<? ORDER BY id DESC LIMIT ?",
            ("owner", 999999, 30),
        ).fetchall(),
        "timeline": env["l7"].execute(
            "EXPLAIN QUERY PLAN SELECT id FROM episode_events "
            "WHERE episode_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (1, 999999, 30),
        ).fetchall(),
        "conversation": env["l7"].execute(
            "EXPLAIN QUERY PLAN SELECT id FROM qa_turns "
            "WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (1, 999999, 30),
        ).fetchall(),
    }
    expected = {
        "history": "idx_health_episodes_user_status_id",
        "timeline": "idx_episode_events_episode_id_id",
        "conversation": "idx_qa_turns_conversation_id_id",
    }
    for name, rows in plans.items():
        detail = " ".join(row[3] for row in rows)
        assert expected[name] in detail, detail
        assert "USE TEMP B-TREE" not in detail
