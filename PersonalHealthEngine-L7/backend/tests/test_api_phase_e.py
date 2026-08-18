"""Phase E API wiring tests (thin HTTP layer over the services)."""

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


def test_context_endpoints(client):
    r = client.post("/context", headers=AUTH, json={"text": "昨晚熬夜了", "date": "2026-08-17"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "SAVED"
    ctx_id = body["context_ids"][0]

    lst = client.get("/context", headers=AUTH).json()
    assert any(c["id"] == ctx_id for c in lst["contexts"])

    c = client.put(f"/context/{ctx_id}", headers=AUTH, json={"text": "其实是喝酒了"})
    assert c.status_code == 200
    assert c.json()["status"] == "CORRECTED"

    d = client.delete(f"/context/{ctx_id}", headers=AUTH)
    # ctx_id is now SUPERSEDED -> 404 is the correct contract answer.
    assert d.status_code == 404


def test_context_requires_text(client):
    assert client.post("/context", headers=AUTH, json={}).status_code == 400


def test_qa_endpoints(client):
    conv = client.post("/qa/conversations", headers=AUTH).json()
    assert "conversation_id" in conv

    r = client.post("/qa/ask", headers=AUTH,
                    json={"question": "今天能不能练腿？",
                          "conversation_id": conv["conversation_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer_first"] is True
    assert body["direct_answer"]
    assert body["conversation_id"] == conv["conversation_id"]

    state = client.get(f"/qa/conversations/{conv['conversation_id']}", headers=AUTH).json()
    assert len(state["turns"]) == 2

    # Out-of-scope stays guarded over HTTP too.
    off = client.post("/qa/ask", headers=AUTH, json={"question": "推荐一部电影"}).json()
    assert off["scope"] == "OUT_OF_SCOPE"


def test_qa_requires_question(client):
    assert client.post("/qa/ask", headers=AUTH, json={}).status_code == 400


def test_feedback_endpoint(client):
    client.get("/today", headers=AUTH)
    r = client.post("/feedback", headers=AUTH, json={"verdict": "准确"})
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "judgment_confirmed"
    bad = client.post("/feedback", headers=AUTH, json={"verdict": "五星好评"})
    assert bad.status_code == 400


def test_pending_question_budget_endpoint(client):
    r = client.get("/context/pending-question", headers=AUTH).json()
    assert r == {"pending_question": None}
