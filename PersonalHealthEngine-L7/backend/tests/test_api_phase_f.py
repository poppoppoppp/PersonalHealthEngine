"""Phase F API wiring tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from l7.api.app import create_app

TOKEN = "dev-local-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(env):
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as c:
        yield c


def test_history_endpoints(client):
    r = client.get("/history/episodes", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "episodes" in body and "stable_days_hidden" in body
    assert len(body["episodes"]) >= 1

    ep_id = body["episodes"][0]["id"]
    d = client.get(f"/history/episodes/{ep_id}", headers=AUTH)
    assert d.status_code == 200
    assert d.json()["timeline"], "episode detail must carry a timeline"

    missing = client.get("/history/episodes/999999", headers=AUTH)
    assert missing.status_code == 404

    s = client.get("/history/search", params={"q": "睡眠"}, headers=AUTH)
    assert s.status_code == 200
    assert s.json()["results"]


def test_patterns_endpoint_has_display_status(client):
    client.get("/today", headers=AUTH)
    r = client.get("/patterns", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert "patterns" in body and "observing_count" in body
