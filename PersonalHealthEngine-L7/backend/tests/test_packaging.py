"""Phase H tests: packaging smoke (prod-like env), backup/restore round-trip,
per-user export/delete."""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from l7.admin.export_delete import delete_user, export_user
from l7.config import Config


def test_prod_env_requires_explicit_token(monkeypatch):
    monkeypatch.setenv("L7_ENV", "prod")
    monkeypatch.delenv("L7_API_TOKEN", raising=False)
    cfg = Config()
    with pytest.raises(RuntimeError):
        cfg.resolve_api_token()


def test_prod_env_with_token_starts_headless(env, monkeypatch):
    """Packaging smoke: with prod-like settings the app factory still builds and serves
    /health without any dev fallback."""
    from fastapi.testclient import TestClient
    from l7.api.app import create_app

    monkeypatch.setenv("L7_ENV", "prod")
    monkeypatch.setenv("L7_API_TOKEN", "prod-smoke-token")
    env["cfg"].environment = "prod"
    env["cfg"].api_token = "prod-smoke-token"

    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "ok"
        # Dev token must NOT work in prod configuration.
        assert c.get("/today", headers={"Authorization": "Bearer dev-local-token"}).status_code == 401
        assert c.get("/today", headers={"Authorization": "Bearer prod-smoke-token"}).status_code == 200


def test_backup_restore_roundtrip(env, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from backup import backup_once

    written = backup_once(env["cfg"], str(tmp_path), keep=2)
    assert any("l7_product" in w for w in written)
    assert all(Path(w).exists() and Path(w).stat().st_size > 0 for w in written)

    # Restore = use the snapshot directly; it must open and contain our tables.
    snap = next(w for w in written if "l7_product" in w)
    con = sqlite3.connect(snap)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "today_versions" in tables and "users" in tables

    # Retention: with keep=1 a second backup prunes the first snapshot dir.
    backup_once(env["cfg"], str(tmp_path), keep=1)
    snaps = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(snaps) == 1


def test_export_contains_user_data_and_no_secrets(env):
    env["service"].get_today("owner", trigger="export-test")
    export = export_user(env["cfg"], "owner")
    assert export["user_id"] == "owner"
    assert export["l7"]["today_versions"], "today versions must be exported"
    assert export["l6_snapshot"]["daily_reasoning"], "current judgment snapshot included"
    blob = json.dumps(export, ensure_ascii=False)
    assert "DEEPSEEK" not in blob and "api_key" not in blob.lower()


def test_delete_user_removes_l7_rows(env):
    env["service"].get_today("owner", trigger="delete-test")
    before = env["l7"].execute(
        "SELECT COUNT(*) FROM today_versions WHERE user_id='owner'").fetchone()[0]
    assert before > 0

    result = delete_user(env["cfg"], "owner")
    assert result["status"] == "DELETED_FROM_L7"
    assert result["deleted_rows"]["today_versions"] == before
    assert "sealed layers" in result["upstream_note"]

    after = env["l7"].execute(
        "SELECT COUNT(*) FROM today_versions WHERE user_id='owner'").fetchone()[0]
    assert after == 0
