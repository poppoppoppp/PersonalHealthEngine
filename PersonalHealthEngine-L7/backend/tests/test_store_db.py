from pathlib import Path

from l7.store import db


class _ConnectionStub:
    row_factory = None


def test_open_readonly_uses_immutable_when_no_pending_wal(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream.sqlite3"
    upstream.touch()
    opened = []

    def fake_connect(database, *, uri: bool):
        opened.append((database, uri))
        return _ConnectionStub()

    monkeypatch.setattr(db.sqlite3, "connect", fake_connect)

    db.open_readonly(str(upstream), immutable_if_checkpointed=True)

    assert opened == [(upstream.resolve().as_uri() + "?mode=ro&immutable=1", True)]


def test_open_readonly_does_not_ignore_nonempty_wal(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream.sqlite3"
    upstream.touch()
    Path(f"{upstream}-wal").write_bytes(b"pending")
    opened = []

    def fake_connect(database, *, uri: bool):
        opened.append((database, uri))
        return _ConnectionStub()

    monkeypatch.setattr(db.sqlite3, "connect", fake_connect)

    db.open_readonly(str(upstream), immutable_if_checkpointed=True)

    assert opened == [(upstream.resolve().as_uri() + "?mode=ro", True)]


def test_open_readonly_defaults_to_live_readonly(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream.sqlite3"
    upstream.touch()
    opened = []

    def fake_connect(database, *, uri: bool):
        opened.append((database, uri))
        return _ConnectionStub()

    monkeypatch.setattr(db.sqlite3, "connect", fake_connect)

    db.open_readonly(str(upstream))

    assert opened == [(upstream.resolve().as_uri() + "?mode=ro", True)]
