from __future__ import annotations

import pytest

import backend.db as db_module


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    """Point backend.db at a fresh temp SQLite file and reset the module's
    cached connection so each test starts with an empty geo_cache.
    """
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_cache.db")
    db_module._conn = None
    yield
    await db_module.close_db()
