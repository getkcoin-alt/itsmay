"""Vault endpoints — export/import over HTTP.

Follows `test_console_api.py`: the real `create_app()` wiring and middleware, with
DB/embedder dependencies swapped for in-memory fakes via `dependency_overrides`,
so no Postgres and no ONNX model are needed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from apps.api.deps import get_embedder, get_episodic, get_semantic  # noqa: E402
from apps.api.main import create_app  # noqa: E402
from core.memory.semantic import MemoryRow  # noqa: E402

_USER = UUID("00000000-0000-0000-0000-000000000001")


class FakeEpisodic:
    async def get_or_create_user(self, handle: str) -> UUID:
        return _USER


class FakeEmbedder:
    async def embed(self, text: str) -> np.ndarray:
        return np.zeros(384, dtype=np.float32)


class FakeSemantic:
    def __init__(self, rows: list[MemoryRow] | None = None) -> None:
        self.rows = list(rows or [])

    async def list_recent(self, user_id, *, limit=50, offset=0, kind=None):
        return self.rows[offset : offset + limit]

    async def count(self, user_id):
        return len(self.rows)

    async def write(self, user_id, kind, content, embedding, *, source=None, importance=0.5):
        mid = uuid4()
        self.rows.append(
            MemoryRow(
                id=mid,
                kind=kind,
                content=content,
                source=source,
                importance=importance,
                created_at=datetime.now(UTC),
                last_used_at=None,
                use_count=0,
            )
        )
        return mid


def _row(content: str, *, source: str = "coder.build") -> MemoryRow:
    return MemoryRow(
        id=uuid4(),
        kind="factual",
        content=content,
        source=source,
        importance=0.7,
        created_at=datetime.now(UTC),
        last_used_at=None,
        use_count=0,
    )


def _client(semantic: FakeSemantic) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_episodic] = lambda: FakeEpisodic()
    app.dependency_overrides[get_semantic] = lambda: semantic
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    return TestClient(app)


@pytest.fixture
def semantic() -> FakeSemantic:
    return FakeSemantic([_row("built a ctimer app"), _row("Boss prefers short answers")])


@pytest.fixture
def client(semantic) -> TestClient:
    return _client(semantic)


# ── export ────────────────────────────────────────────────────────────


def test_export_writes_a_readable_bundle(client, tmp_path):
    out = tmp_path / "vault"
    r = client.post("/v1/vault/export", json={"out": str(out), "include_episodes": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["identity"] == "Scrappy Singh"
    assert body["memories"] == 2
    assert body["protocol_version"].startswith("1.")

    # Ordinary files another language could parse.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["protocol_version"] == body["protocol_version"]
    identity = json.loads((out / "identity.json").read_text())
    assert identity["operator"]["address_as"] == "Boss"
    # Secret NAMES may travel; values never do.
    assert "elevenlabs_api_key" in identity["secret_refs"]


def test_exported_bundle_carries_no_vectors(client, tmp_path):
    out = tmp_path / "vault"
    assert client.post("/v1/vault/export", json={"out": str(out)}).status_code == 200
    text = (out / "memories.jsonl").read_text()
    assert "embedding" not in text  # the portability rule, end to end
    assert "built a ctimer app" in text


def test_export_is_blocked_by_a_secret(tmp_path):
    # 409, not a masked-and-shipped bundle — a vault that quietly rewrote itself
    # would be worse than one that refused.
    poisoned = FakeSemantic([_row("the key is sk-live-abcdefghijklmnopqrstuvwx")])
    r = _client(poisoned).post("/v1/vault/export", json={"out": str(tmp_path / "v")})
    assert r.status_code == 409
    assert "refusing to export" in r.json()["detail"]
    assert not (tmp_path / "v").exists()  # nothing written


# ── import ────────────────────────────────────────────────────────────


def test_import_round_trips_through_the_api(client, tmp_path):
    out = tmp_path / "vault"
    assert client.post("/v1/vault/export", json={"out": str(out)}).status_code == 200

    # Into a DIFFERENT, empty host — the real migration case.
    fresh = FakeSemantic()
    r = _client(fresh).post("/v1/vault/import", json={"path": str(out)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["memories_added"] == 2
    assert body["source"]["identity"] == "Scrappy Singh"
    assert {row.content for row in fresh.rows} == {
        "built a ctimer app",
        "Boss prefers short answers",
    }
    # Provenance survives the hop.
    assert all(str(row.source).startswith("vault:") for row in fresh.rows)


def test_import_is_idempotent(client, tmp_path):
    out = tmp_path / "vault"
    client.post("/v1/vault/export", json={"out": str(out)})
    fresh = FakeSemantic()
    target = _client(fresh)
    target.post("/v1/vault/import", json={"path": str(out)})
    second = target.post("/v1/vault/import", json={"path": str(out)}).json()
    assert second["memories_added"] == 0 and second["memories_skipped"] == 2


def test_import_dry_run_changes_nothing(client, tmp_path):
    out = tmp_path / "vault"
    client.post("/v1/vault/export", json={"out": str(out)})
    fresh = FakeSemantic()
    body = _client(fresh).post(
        "/v1/vault/import", json={"path": str(out), "dry_run": True}
    ).json()
    assert body["dry_run"] is True and body["memories_added"] == 2
    assert fresh.rows == []


def test_import_rejects_a_missing_bundle(client, tmp_path):
    r = client.post("/v1/vault/import", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400
    assert "not a directory" in r.json()["detail"]


def test_import_refuses_a_future_protocol(client, tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"protocol_version": "9.0.0", "vault_id": "x"}))
    r = client.post("/v1/vault/import", json={"path": str(d)})
    assert r.status_code == 409
    assert "refusing" in r.json()["detail"]
