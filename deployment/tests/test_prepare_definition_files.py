import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from deployment.scripts.prepare_definition_files import reconcile_layer


def test_daily_service_reconciles_definitions_before_pipeline():
    service = (
        Path(__file__).parents[1] / "systemd" / "phe-daily.service"
    ).read_text(encoding="utf-8")
    preflight = (
        "ExecStartPre=/opt/phe/.venv/bin/python "
        "/opt/phe/deployment/scripts/prepare_definition_files.py "
        "--code-root /opt/phe --data-root /srv/phe"
    )
    pipeline = (
        "ExecStart=/opt/phe/.venv/bin/python "
        "/opt/phe/deployment/scripts/run_daily_pipeline.py"
    )

    assert preflight in service
    assert service.index(preflight) < service.index(pipeline)


def _registry(database, definition_id, version, checksum):
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE definition_registry ("
        "definition_id TEXT, definition_version TEXT, definition_sha256 TEXT, status TEXT)"
    )
    connection.execute(
        "INSERT INTO definition_registry VALUES (?, ?, ?, 'ACTIVE')",
        (definition_id, version, checksum),
    )
    connection.commit()
    connection.close()


@pytest.mark.skipif(os.name == "nt", reason="backslash is a path separator on Windows")
def test_reconcile_repairs_backslash_path_and_eol_only(tmp_path):
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    lf = b'{\n  "definition_id": "l3.test",\n  "definition_version": "0.1"\n}\n'
    source = definitions / "normalizers\\test.json"
    source.write_bytes(lf.replace(b"\n", b"\r\n"))
    database = tmp_path / "layer.sqlite3"
    _registry(database, "l3.test", "0.1", hashlib.sha256(lf).hexdigest())

    result = reconcile_layer(definitions, database)

    assert result == {"checked": 1, "paths_repaired": 1, "eol_repaired": 1}
    assert (definitions / "normalizers" / "test.json").read_bytes() == lf
    assert not source.exists()


def test_reconcile_repairs_eol_only(tmp_path):
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    lf = b'{\n  "definition_id": "l3.test",\n  "definition_version": "0.1"\n}\n'
    source = definitions / "test.json"
    source.write_bytes(lf.replace(b"\n", b"\r\n"))
    database = tmp_path / "layer.sqlite3"
    _registry(database, "l3.test", "0.1", hashlib.sha256(lf).hexdigest())

    result = reconcile_layer(definitions, database)

    assert result == {"checked": 1, "paths_repaired": 0, "eol_repaired": 1}
    assert source.read_bytes() == lf


def test_reconcile_preserves_registry_matched_utf8_bom(tmp_path):
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    raw = (
        b"\xef\xbb\xbf"
        b'{"definition_id":"l3.test","definition_version":"0.1"}'
    )
    source = definitions / "test.json"
    source.write_bytes(raw)
    database = tmp_path / "layer.sqlite3"
    _registry(database, "l3.test", "0.1", hashlib.sha256(raw).hexdigest())

    result = reconcile_layer(definitions, database)

    assert result == {"checked": 1, "paths_repaired": 0, "eol_repaired": 0}
    assert source.read_bytes() == raw


def test_reconcile_refuses_non_eol_checksum_mismatch(tmp_path):
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    source = definitions / "test.json"
    source.write_text(
        '{"definition_id":"l3.test","definition_version":"0.1","value":2}',
        encoding="utf-8",
    )
    database = tmp_path / "layer.sqlite3"
    expected = b'{"definition_id":"l3.test","definition_version":"0.1","value":1}'
    _registry(database, "l3.test", "0.1", hashlib.sha256(expected).hexdigest())

    before = source.read_bytes()
    with pytest.raises(RuntimeError, match="not EOL-only"):
        reconcile_layer(definitions, database)

    assert source.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="backslash is a path separator on Windows")
def test_reconcile_validates_before_repairing_layout(tmp_path):
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    source = definitions / "normalizers\\test.json"
    source.write_text(
        '{"definition_id":"l3.test","definition_version":"0.1","value":2}',
        encoding="utf-8",
    )
    database = tmp_path / "layer.sqlite3"
    expected = b'{"definition_id":"l3.test","definition_version":"0.1","value":1}'
    _registry(database, "l3.test", "0.1", hashlib.sha256(expected).hexdigest())

    with pytest.raises(RuntimeError, match="not EOL-only"):
        reconcile_layer(definitions, database)

    assert source.exists()
    assert not (definitions / "normalizers" / "test.json").exists()
