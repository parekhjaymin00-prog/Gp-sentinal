"""Storage and query layer for vehicle sightings in SQLite."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anpr.config import DATABASE_PATH

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vehicle_sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    plate_number TEXT NOT NULL,
    confidence REAL NOT NULL,
    timestamp TEXT NOT NULL,
    pts_ms REAL,
    snapshot_path TEXT,
    track_key TEXT
);
"""


def _connect(db_path=None) -> sqlite3.Connection:
    path = str(db_path) if db_path is not None else str(DATABASE_PATH)
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def ensure_sightings_table(db_path=None) -> None:
    """Create vehicle_sightings table if it does not exist."""
    with _connect(db_path) as connection:
        connection.execute(_TABLE_SQL)


def record_sighting(
    camera_id: str,
    plate_number: str,
    confidence: float,
    timestamp: str | None = None,
    pts_ms: float | None = None,
    snapshot_path: str | None = None,
    track_key: str | None = None,
    db_path=None,
) -> int:
    """Insert one vehicle sighting record and return its row ID."""
    ensure_sightings_table(db_path)
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO vehicle_sightings (
                camera_id, plate_number, confidence, timestamp, pts_ms, snapshot_path, track_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(camera_id),
                str(plate_number).upper().strip(),
                float(confidence),
                str(timestamp),
                float(pts_ms) if pts_ms is not None else None,
                str(snapshot_path) if snapshot_path else None,
                str(track_key) if track_key else None,
            ),
        )
        return cursor.lastrowid


def get_recent_sightings(limit: int = 50, db_path=None) -> list[dict[str, Any]]:
    """Return most recent vehicle sightings descending by id."""
    ensure_sightings_table(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, camera_id, plate_number, confidence, timestamp, pts_ms, snapshot_path, track_key
            FROM vehicle_sightings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_sightings_by_plate(plate_number: str, db_path=None) -> list[dict[str, Any]]:
    """Return all sightings for a normalized plate number in chronological order."""
    ensure_sightings_table(db_path)
    norm = "".join(ch for ch in str(plate_number).upper() if ch.isalnum())
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, camera_id, plate_number, confidence, timestamp, pts_ms, snapshot_path, track_key
            FROM vehicle_sightings
            WHERE replace(replace(replace(upper(plate_number), ' ', ''), '-', ''), '_', '') = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (norm,),
        ).fetchall()
        return [dict(row) for row in rows]