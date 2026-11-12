from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()


def normalise_url(url: str) -> str:
    """Accept postgres:// and postgresql:// URLs (as injected by most hosts)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = normalise_url(_settings.database_url)
_kwargs: dict = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _kwargs = {"connect_args": {"check_same_thread": False}}
elif _settings.db_serverless:
    # Transaction-mode poolers (Supavisor/PgBouncer) cannot keep prepared statements.
    _kwargs = {"poolclass": NullPool, "connect_args": {"prepare_threshold": None}}

engine = create_engine(DATABASE_URL, **_kwargs)
if _settings.db_schema:
    engine = engine.execution_options(schema_translate_map={None: _settings.db_schema})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
