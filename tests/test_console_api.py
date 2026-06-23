"""Tests for the web console: API endpoints, static serving, and auth.

Uses FastAPI's TestClient over the real `create_app()` so the actual route
wiring, static mount, and bearer middleware are exercised. The DB- and
embedder-backed dependencies are replaced with in-memory fakes via
`dependency_overrides`, so no Postgres or ONNX model is needed.

The whole module skips cleanly where the server deps aren't installed (the
default minimal test venv), and runs fully wherever `pip install -e .` has.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from apps.api.deps import get_embedder, get_episodic, get_semantic  # noqa: E402
from apps.api.main import create_app  # noqa: E402
from apps.api.middleware.auth import BearerAuthMiddleware  # noqa: E402
from core.memory.semantic import MemoryRow, RetrievedMemory  # noqa: E402

_USER = UUID("00000000-0000-0000-0000-000000000001")


# ── fakes ─────────────────────────────────────────────────────────────


class FakeEpisodic:
    async def get_or_create_user(self, handle: str) -> UUID:
        return _USER


class FakeEmbedder:
    async def embed(self, text: str) -> np.ndarray:
        return np.zeros(384, dtype=np.float32)


class FakeSemantic:
    """In-memory stand-in for SemanticStore with the methods the console uses."""

    def __init__(self) -> None:
        self.rows: list[MemoryRow] = []

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

    async def list_recent(self, user_id, *, limit=50, offset=0, kind=None):
        newest = list(reversed(self.rows))
        return newest[offset : offset + limit]

    async def count(self, user_id) -> int:
        return len(self.rows)

    async def delete(self, user_id, memory_id) -> bool:
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.id != memory_id]
        return len(self.rows) < before

    async def search(self, user_id, query_embedding, k=8, *, min_importance=0.0):
        out = []
        for r in list(reversed(self.rows))[:k]:
            out.append(
                RetrievedMemory(
                    id=r.id,
                    kind=r.kind,
                    content=r.content,
                    importance=r.importance,
                    similarity=0.91,
                    created_at=r.created_at,
                )
            )
        return out


@pytest.fixture
def client():
    app = create_app()
    semantic = FakeSemantic()
    app.dependency_overrides[get_episodic] = lambda: FakeEpisodic()
    app.dependency_overrides[get_semantic] = lambda: semantic
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    # No context manager → lifespan (DB connect/migrate) does not run.
    c = TestClient(app)
    c._semantic = semantic  # expose for assertions if needed
    return c


# ── static shell ──────────────────────────────────────────────────────


def test_root_serves_console_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Scrappy" in r.text


def test_static_assets_served(client):
    for path, needle in (("/static/app.js", "parseSSE"), ("/static/styles.css", "--accent")):
        r = client.get(path)
        assert r.status_code == 200, path
        assert needle in r.text


# ── identity ──────────────────────────────────────────────────────────


def test_identity_endpoint(client):
    d = client.get("/v1/console/identity").json()
    assert d["agent"] == "Scrappy Singh"
    assert d["operator"] == "Karnveer Singh"
    assert isinstance(d["days_remaining"], int)
    assert d["model"] and d["provider"]
    assert "mission" in d


# ── system status ─────────────────────────────────────────────────────


def test_status_lists_connectors_and_experts(client):
    d = client.get("/v1/console/status").json()
    conn_names = {c["name"] for c in d["connectors"]}
    # memory + mac connectors are always discovered; gmail is skipped (no creds).
    assert {"memory", "mac"} <= conn_names

    experts = {e["name"]: e for e in d["experts"]}
    # Strategist needs nothing; Memory Keeper needs the (present) memory connector.
    assert experts["strategist"]["available"] is True
    assert experts["memory_keeper"]["available"] is True
    # Email needs the gmail connector, which isn't installed here.
    assert experts["email"]["available"] is False
    assert experts["email"]["missing_connectors"] == ["gmail"]


def test_status_marks_executor_and_approval(client):
    d = client.get("/v1/console/status").json()
    memory = next(c for c in d["connectors"] if c["name"] == "memory")
    save = next(t for t in memory["tools"] if t["name"] == "save")
    assert save["executor"] == "server"


# ── memory CRUD ───────────────────────────────────────────────────────


def test_memory_add_list_search_delete_flow(client):
    # Initially empty.
    assert client.get("/v1/console/memory").json()["total"] == 0

    # Add.
    add = client.post(
        "/v1/console/memory",
        json={"content": "Karnveer uses Stripe for payouts", "importance": 0.8, "kind": "factual"},
    )
    assert add.status_code == 201
    mem_id = add.json()["id"]

    # List shows it.
    listed = client.get("/v1/console/memory").json()
    assert listed["total"] == 1
    assert listed["memories"][0]["content"] == "Karnveer uses Stripe for payouts"
    assert listed["memories"][0]["importance"] == 0.8

    # Search returns it with a similarity score.
    found = client.post("/v1/console/memory/search", json={"query": "payment setup", "k": 5}).json()
    assert found["results"]
    assert found["results"][0]["id"] == mem_id
    assert "similarity" in found["results"][0]

    # Delete it.
    assert client.delete(f"/v1/console/memory/{mem_id}").status_code == 200
    assert client.get("/v1/console/memory").json()["total"] == 0


def test_delete_missing_memory_404(client):
    r = client.delete(f"/v1/console/memory/{uuid4()}")
    assert r.status_code == 404


def test_add_memory_validates_input(client):
    # Empty content fails validation (min_length=1).
    assert client.post("/v1/console/memory", json={"content": ""}).status_code == 422
    # Importance out of range fails.
    bad = client.post("/v1/console/memory", json={"content": "x", "importance": 5})
    assert bad.status_code == 422


# ── auth middleware ───────────────────────────────────────────────────


def _auth_app(token: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        BearerAuthMiddleware,
        token=token,
        allow_paths=("/", "/status", "/v1/health"),
        allow_prefixes=("/static/",),
    )

    @app.get("/")
    def root():
        return {"ok": True}

    @app.get("/static/app.js")
    def asset():
        return {"asset": True}

    @app.get("/v1/secret")
    def secret():
        return {"secret": True}

    return app


def test_auth_open_mode_passes_everything():
    client = TestClient(_auth_app(""))  # empty token = open mode
    assert client.get("/v1/secret").status_code == 200


def test_auth_guards_protected_paths():
    client = TestClient(_auth_app("s3cret"))
    # Shell + static load without a token.
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    # Protected path needs the token.
    assert client.get("/v1/secret").status_code == 401
    assert client.get("/v1/secret", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/v1/secret", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert ok.json() == {"secret": True}
