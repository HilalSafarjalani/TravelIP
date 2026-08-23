"""Shared SQLite storage: geo-lookup cache and trace history.

A single aiosqlite connection is opened once at app startup (WAL mode for
concurrent read/write) and reused for the lifetime of the process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import aiosqlite

DB_PATH = Path(__file__).parent / "cache.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS geo_cache (
    ip TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    country TEXT,
    country_code TEXT,
    city TEXT,
    lat REAL,
    lon REAL,
    isp TEXT,
    org TEXT,
    asn TEXT,
    as_name TEXT,
    reverse TEXT,
    source TEXT,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    hop_count INTEGER,
    origin_json TEXT,
    hops_json TEXT
);
"""

_conn: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(DB_PATH)
        await _conn.execute("PRAGMA journal_mode=WAL;")
        await _conn.executescript(SCHEMA)
        await _conn.commit()
    return _conn


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None
