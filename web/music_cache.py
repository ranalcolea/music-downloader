#!/usr/bin/env python3

import json
import sqlite3
import threading
import time
from pathlib import Path

CACHE_DIR = Path("/opt/music-downloader/cache")
CACHE_DB = CACHE_DIR / "music-cache.sqlite3"

CACHE_TTL = {
    "youtube": 3 * 24 * 3600,
    "musicbrainz_search": 7 * 86400,
    "musicbrainz_album": 30 * 86400,
    "spotify_metadata": 24 * 3600,
}

CACHE_MAX_ENTRIES = 5000
CACHE_LOCK = threading.RLock()


def _connect():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(
        str(CACHE_DB),
        timeout=10
    )

    db.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            category TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (category, cache_key)
        )
    """)

    return db


def cache_get(category, key):
    category = str(category)
    key = str(key)

    with CACHE_LOCK:
        with _connect() as db:
            row = db.execute(
                """
                SELECT value_json, expires_at
                FROM cache
                WHERE category=? AND cache_key=?
                """,
                (category, key)
            ).fetchone()

            if row is None:
                return None

            value_json, expires_at = row

            if expires_at <= time.time():
                db.execute(
                    """
                    DELETE FROM cache
                    WHERE category=? AND cache_key=?
                    """,
                    (category, key)
                )
                return None

            try:
                return json.loads(value_json)
            except (ValueError, TypeError):
                return None


def cache_put(category, key, value, ttl=None):
    category = str(category)
    key = str(key)

    if category not in CACHE_TTL:
        raise ValueError("Categoría de caché no permitida")

    if ttl is None:
        ttl = CACHE_TTL[category]

    now = time.time()

    with CACHE_LOCK:
        with _connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO cache
                (category, cache_key, value_json, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    category,
                    key,
                    json.dumps(value, ensure_ascii=False),
                    now + ttl,
                    now
                )
            )

            db.execute(
                "DELETE FROM cache WHERE expires_at <= ?",
                (now,)
            )

            count = db.execute(
                "SELECT COUNT(*) FROM cache"
            ).fetchone()[0]

            if count > CACHE_MAX_ENTRIES:
                db.execute(
                    """
                    DELETE FROM cache
                    WHERE rowid IN (
                        SELECT rowid
                        FROM cache
                        ORDER BY created_at ASC
                        LIMIT ?
                    )
                    """,
                    (count - CACHE_MAX_ENTRIES,)
                )


def cache_stats():
    with CACHE_LOCK:
        with _connect() as db:
            rows = db.execute(
                """
                SELECT category, COUNT(*)
                FROM cache
                WHERE expires_at > ?
                GROUP BY category
                """,
                (time.time(),)
            ).fetchall()

    return dict(rows)


def cache_cleanup():
    with CACHE_LOCK:
        with _connect() as db:
            db.execute(
                "DELETE FROM cache WHERE expires_at <= ?",
                (time.time(),)
            )
