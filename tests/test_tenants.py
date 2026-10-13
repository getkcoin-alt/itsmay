"""Tenant registry — unique URL + PIN per person, sharing one deployment.

Real SQLite (via tmp_path), no server — mirrors tests/test_companion_engine.py's
style. `get_settings()` is process-wide `@lru_cache`d, so each test points
TENANTS_DB_PATH/TENANTS_DATA_DIR at its own tmp_path and clears the cache
before AND after, so no test leaks its settings into another.
"""

from __future__ import annotations

import pytest

from core.config import get_settings


@pytest.fixture
def tenant_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANTS_DB_PATH", str(tmp_path / "tenants.db"))
    monkeypatch.setenv("TENANTS_DATA_DIR", str(tmp_path / "tenants"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_create_tenant_generates_slug_and_pin(tenant_env):
    from core.companion.tenants import create_tenant

    tenant, pin = create_tenant("Priya")
    assert tenant.slug == "priya"
    assert len(pin) == 4 and pin.isdigit()
    assert tenant.owner_name == "Priya"
    # Its own isolated sqlite file was actually created.
    from pathlib import Path
    assert Path(tenant.db_path).exists()


def test_duplicate_owner_name_gets_a_unique_slug(tenant_env):
    from core.companion.tenants import create_tenant

    t1, _ = create_tenant("Priya")
    t2, _ = create_tenant("Priya")
    assert t1.slug != t2.slug
    assert t1.db_path != t2.db_path


def test_get_tenant_by_slug_roundtrip(tenant_env):
    from core.companion.tenants import create_tenant, get_tenant_by_slug

    tenant, _ = create_tenant("Karan")
    got = get_tenant_by_slug(tenant.slug)
    assert got is not None
    assert got.slug == tenant.slug
    assert got.owner_name == "Karan"


def test_get_tenant_by_slug_unknown_or_invalid_returns_none(tenant_env):
    from core.companion.tenants import get_tenant_by_slug

    assert get_tenant_by_slug("does-not-exist") is None
    assert get_tenant_by_slug("bad slug!") is None  # rejected charset
    assert get_tenant_by_slug("") is None


def test_verify_pin(tenant_env):
    from core.companion.tenants import create_tenant, verify_pin

    tenant, pin = create_tenant("Sara")
    assert verify_pin(tenant, pin) is True
    assert verify_pin(tenant, "0000") is False
    assert verify_pin(tenant, "") is False


def test_create_tenant_with_explicit_slug_pin_and_db_path(tenant_env, tmp_path):
    from core.companion.tenants import create_tenant, verify_pin

    existing_db = str(tmp_path / "existing.db")
    tenant, pin = create_tenant(
        "Khushi", pin="8080", slug="khushi", db_path=existing_db,
    )
    assert tenant.slug == "khushi"
    assert pin == "8080"
    assert tenant.db_path == existing_db
    assert verify_pin(tenant, "8080")


def test_get_tenant_by_db_path(tenant_env, tmp_path):
    from core.companion.tenants import create_tenant, get_tenant_by_db_path

    existing_db = str(tmp_path / "existing.db")
    tenant, _ = create_tenant("Khushi", pin="8080", slug="khushi", db_path=existing_db)

    found = get_tenant_by_db_path(existing_db)
    assert found is not None
    assert found.slug == "khushi"
    assert get_tenant_by_db_path(str(tmp_path / "nope.db")) is None


def test_list_tenants(tenant_env):
    from core.companion.tenants import create_tenant, list_tenants

    create_tenant("A")
    create_tenant("B")
    slugs = {t.slug for t in list_tenants()}
    assert slugs == {"a", "b"}


def test_delete_tenant_removes_registry_row_and_db_file(tenant_env):
    from pathlib import Path

    from core.companion.tenants import create_tenant, delete_tenant, get_tenant_by_slug

    tenant, _ = create_tenant("Priya")
    assert Path(tenant.db_path).exists()

    assert delete_tenant(tenant.slug) is True
    assert get_tenant_by_slug(tenant.slug) is None
    assert not Path(tenant.db_path).exists()


def test_delete_tenant_unknown_slug_returns_false(tenant_env):
    from core.companion.tenants import delete_tenant

    assert delete_tenant("does-not-exist") is False


def test_pin_is_never_stored_in_plaintext(tenant_env):
    """`pin_hash` must not equal the plaintext PIN under any encoding."""
    from core.companion.tenants import create_tenant

    tenant, pin = create_tenant("Priya", pin="1234")
    assert tenant.pin_hash != pin
    assert "1234" not in tenant.pin_hash
