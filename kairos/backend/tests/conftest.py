import os

os.environ.setdefault("KAIROS_DATABASE_URL", "postgresql+psycopg://kairos:kairos@localhost:5432/kairos_test")
os.environ["KAIROS_BACKGROUND_MATCHER"] = "false"
os.environ["KAIROS_ENV"] = "test"
os.environ.pop("KAIROS_REDIS_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.market import service as mkt  # noqa: E402
from app.security import limiter  # noqa: E402

H = {"x-kairos-client": "test"}


@pytest.fixture(scope="session", autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    seed.seed_instruments(db)
    db.close()
    seed.run()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()
    mkt.invalidate_cache()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def _client() -> TestClient:
    c = TestClient(app)
    c.headers.update(H)
    return c


@pytest.fixture
def client():
    return _client()


_counter = {"n": 0}


@pytest.fixture
def user_client():
    """A freshly registered user with ₹10,00,000 demo cash."""
    _counter["n"] += 1
    c = _client()
    r = c.post("/api/auth/register", json={
        "full_name": f"Test User {_counter['n']}", "email": f"user{_counter['n']}@example.test",
        "password": "Str0ng!Pass", "accept_terms": True,
    })
    assert r.status_code == 201, r.text
    return c


@pytest.fixture
def admin_client():
    c = _client()
    r = c.post("/api/auth/login", json={"email": "admin@platform.test", "password": "Admin@123456"})
    assert r.status_code == 200, r.text
    return c
