"""SQLite-backed durable idempotency claims for reminder delivery.

A successful claim reserves the occurrence before external side effects. This is
at-most-once attempt semantics: a crash after claiming can lose a delivery; no
storage layer can atomically commit both SQLite and a Cast network operation.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3


class DeliveryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS delivery_claims_v2 (
                occurrence_date TEXT NOT NULL,
                collection_id TEXT NOT NULL,
                reminder_id TEXT NOT NULL,
                wall_slot TEXT NOT NULL,
                device_uuid TEXT NOT NULL,
                PRIMARY KEY (occurrence_date, collection_id, reminder_id, wall_slot, device_uuid)
            )""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def claim(self, occurrence_date: str, reminder_id: str, wall_slot: str,
              device_uuid: str, *, collection_id: str = "") -> bool:
        """Return True only for the first durable claim of this exact key."""
        if any(not isinstance(part, str) or not part.strip()
               for part in (occurrence_date, reminder_id, wall_slot, device_uuid)) or not isinstance(collection_id, str):
            raise ValueError("delivery claim requires four nonempty string key components")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "INSERT INTO delivery_claims_v2 VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (occurrence_date, collection_id, reminder_id, wall_slot, device_uuid),
            )
            return cursor.rowcount == 1
