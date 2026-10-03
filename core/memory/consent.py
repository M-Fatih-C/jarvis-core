"""Local user consent management for sensitive memory categories.

Ensures explicit, granular, category-specific user consent before any
private or sensitive memory records can be retrieved or decrypted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import sqlite3
from typing import Any
from core.logging.setup import get_logger

logger = get_logger("jarvis.memory.consent")


class ConsentStatus(str, Enum):
    """Lifecycle status of category consent."""
    PENDING = "pending"      # Not yet reviewed or authorized by user; retrieval DISABLED
    GRANTED = "granted"      # Explicitly authorized by user; retrieval permitted under auth context
    REVOKED = "revoked"      # Explicitly revoked by user; retrieval immediately DISABLED


@dataclass
class ConsentRecord:
    category: str
    status: ConsentStatus
    display_name: str
    description: str
    granted_at: str | None = None
    revoked_at: str | None = None
    granted_by: str | None = None
    notes: str | None = None


# Canonical definitions for sensitive categories requiring explicit user consent
REGISTERED_SENSITIVE_CATEGORIES: dict[str, dict[str, str]] = {
    "identity_and_school_ids": {
        "display_name": "Kimlik ve Öğrenci Numaraları",
        "description": "T.C. Kimlik No, üniversite/okul öğrenci numaraları, anne/baba adı, doğum kayıtları.",
        "aliases": "personal_identity,identity,identity_private",
    },
    "financial_historical": {
        "display_name": "Tarihsel Finans ve Maaş Bilgileri",
        "description": "Banka borçları, kredi kartları, geçmiş maaş/staj gelirleri, KYK kredi geri ödemeleri.",
        "aliases": "financial,finance",
    },
    "health_historical": {
        "display_name": "Sağlık ve Fiziksel Geçmiş",
        "description": "Sağlık geçmişi, tetkik sonuçları, fiziksel ölçümler ve kişisel sağlık notları.",
        "aliases": "health",
    },
    "personal_historical": {
        "display_name": "Özel Hayat ve Kişisel Notlar",
        "description": "Kişisel düşünceler, ruh hali, özel hayat, ilişki notları ve geçmiş yansımalar.",
        "aliases": "personal_notes,personal",
    },
    "family": {
        "display_name": "Aile ve Yakın İlişkiler",
        "description": "Çekirdek aile üyeleri, kardeş ve ebeveyn özel bilgileri.",
        "aliases": "family_members",
    },
}


from contextlib import contextmanager

class ConsentStore:
    """Persistent SQLite-backed store for category consent decisions."""

    def __init__(self, db_path: str = ":memory:", conn: sqlite3.Connection | None = None) -> None:
        self._db_path = db_path
        self._conn = conn or (sqlite3.connect(":memory:") if db_path == ":memory:" else None)
        if self._conn is not None:
            self._conn.row_factory = sqlite3.Row
        self._init_db()

    @contextmanager
    def _connection(self):
        if self._conn is not None:
            yield self._conn
        else:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def _init_db(self) -> None:
        """Create consent table and populate default PENDING entries for sensitive categories."""
        with self._connection() as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memory_category_consent (
                        category TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        granted_at TEXT,
                        revoked_at TEXT,
                        granted_by TEXT,
                        notes TEXT
                    )
                """)

                # Seed standard categories with PENDING if not present
                for cat, meta in REGISTERED_SENSITIVE_CATEGORIES.items():
                    cur = conn.execute("SELECT status FROM memory_category_consent WHERE category = ?", (cat,))
                    row = cur.fetchone()
                    if row is None:
                        conn.execute(
                            """
                            INSERT INTO memory_category_consent (
                                category, status, display_name, description, granted_at, revoked_at, granted_by, notes
                            ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL)
                            """,
                            (cat, ConsentStatus.PENDING.value, meta["display_name"], meta["description"]),
                        )


    def resolve_category(self, category: str) -> str:
        """Map aliases or exact strings to canonical category key."""
        cat_lower = category.lower().strip()
        if cat_lower in REGISTERED_SENSITIVE_CATEGORIES:
            return cat_lower

        for canon, meta in REGISTERED_SENSITIVE_CATEGORIES.items():
            aliases = [a.strip() for a in meta.get("aliases", "").split(",")]
            if cat_lower in aliases:
                return canon

        return cat_lower

    def is_category_consented(self, category: str, user_id: str | None = None) -> bool:
        """Check if explicit user consent is currently granted for category.

        Returns False for PENDING or REVOKED, and for unregistered categories.
        """
        canon = self.resolve_category(category)
        with self._connection() as conn:
            cur = conn.execute("SELECT status, granted_by FROM memory_category_consent WHERE category = ?", (canon,))
            row = cur.fetchone()
            if row is None:
                # Any unrecognized sensitive category fails closed
                return False
            return row["status"] == ConsentStatus.GRANTED.value and (
                user_id is None or row["granted_by"] == user_id
            )

    def grant_consent(
        self,
        category: str,
        granted_by: str = "interactive_user",
        notes: str | None = None,
    ) -> bool:
        """Grant explicit consent for a category."""
        canon = self.resolve_category(category)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            with conn:
                cur = conn.execute("SELECT category FROM memory_category_consent WHERE category = ?", (canon,))
                if cur.fetchone() is None:
                    meta = REGISTERED_SENSITIVE_CATEGORIES.get(canon, {
                        "display_name": canon,
                        "description": f"Custom sensitive category {canon}",
                    })
                    conn.execute(
                        """
                        INSERT INTO memory_category_consent (
                            category, status, display_name, description, granted_at, revoked_at, granted_by, notes
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                        """,
                        (canon, ConsentStatus.GRANTED.value, meta["display_name"], meta["description"], now_iso, granted_by, notes),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE memory_category_consent
                        SET status = ?, granted_at = ?, revoked_at = NULL, granted_by = ?, notes = ?
                        WHERE category = ?
                        """,
                        (ConsentStatus.GRANTED.value, now_iso, granted_by, notes, canon),
                    )
        logger.info("consent_granted", category=canon, granted_by=granted_by)
        return True

    def revoke_consent(self, category: str, notes: str | None = None) -> bool:
        """Revoke user consent for a category immediately."""
        canon = self.resolve_category(category)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE memory_category_consent
                    SET status = ?, revoked_at = ?, notes = ?
                    WHERE category = ?
                    """,
                    (ConsentStatus.REVOKED.value, now_iso, notes, canon),
                )
        logger.info("consent_revoked", category=canon)
        return True

    def list_consents(self) -> list[ConsentRecord]:
        """List all category consents and their current status."""
        records: list[ConsentRecord] = []
        with self._connection() as conn:
            cur = conn.execute("SELECT * FROM memory_category_consent ORDER BY category ASC")
            for row in cur.fetchall():
                records.append(
                    ConsentRecord(
                        category=row["category"],
                        status=ConsentStatus(row["status"]),
                        display_name=row["display_name"],
                        description=row["description"],
                        granted_at=row["granted_at"],
                        revoked_at=row["revoked_at"],
                        granted_by=row["granted_by"],
                        notes=row["notes"],
                    )
                )
        return records
