"""
history_manager.py - Çeviri oturumu geçmişini SQLite'ta saklar.

Sorumluluğu:
- Her çeviri oturumunu (başlangıç zamanı, süre, sayfa/kredi/hata sayısı) kaydetmek.
- Son N oturumu sorgulamak.
- Geçmişi temizlemek.
"""

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_VERSION = 1


def _get_db_path() -> Path:
    import os, platform
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    db_dir = base / "ToriiBatch"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "history.db"


@dataclass
class SessionRecord:
    id: int
    started_at: float       # unix timestamp
    ended_at: float         # unix timestamp
    duration_sec: float     # süre (saniye)
    source_folder: str
    output_folder: str
    total_pages: int
    successful_pages: int
    failed_pages: int
    total_chapters: int
    credits_spent: float
    translator: str


class HistoryManager:
    """SQLite tabanlı oturum geçmişi yöneticisi."""

    def __init__(self) -> None:
        self._db_path = _get_db_path()
        self._conn: sqlite3.Connection | None = None
        self._open()

    # ------------------------------------------------------------------
    # Bağlantı
    # ------------------------------------------------------------------

    def _open(self) -> None:
        try:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._migrate()
            logger.debug("HistoryManager: DB açıldı: %s", self._db_path)
        except Exception as exc:
            logger.error("HistoryManager: DB açılamadı: %s", exc)
            self._conn = None

    def _migrate(self) -> None:
        if self._conn is None:
            return
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at       REAL    NOT NULL,
                ended_at         REAL    NOT NULL,
                duration_sec     REAL    NOT NULL,
                source_folder    TEXT    NOT NULL DEFAULT '',
                output_folder    TEXT    NOT NULL DEFAULT '',
                total_pages      INTEGER NOT NULL DEFAULT 0,
                successful_pages INTEGER NOT NULL DEFAULT 0,
                failed_pages     INTEGER NOT NULL DEFAULT 0,
                total_chapters   INTEGER NOT NULL DEFAULT 0,
                credits_spent    REAL    NOT NULL DEFAULT 0.0,
                translator       TEXT    NOT NULL DEFAULT ''
            )
        """)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Yazma
    # ------------------------------------------------------------------

    def add_session(
        self,
        started_at: float,
        ended_at: float,
        source_folder: str,
        output_folder: str,
        total_pages: int,
        successful_pages: int,
        failed_pages: int,
        total_chapters: int,
        credits_spent: float,
        translator: str,
    ) -> None:
        if self._conn is None:
            return
        duration = ended_at - started_at
        try:
            self._conn.execute(
                """
                INSERT INTO sessions
                    (started_at, ended_at, duration_sec, source_folder, output_folder,
                     total_pages, successful_pages, failed_pages, total_chapters,
                     credits_spent, translator)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at, ended_at, duration,
                    source_folder, output_folder,
                    total_pages, successful_pages, failed_pages, total_chapters,
                    credits_spent, translator,
                ),
            )
            self._conn.commit()
            logger.debug("HistoryManager: Oturum kaydedildi.")
        except Exception as exc:
            logger.error("HistoryManager: Kayıt hatası: %s", exc)

    # ------------------------------------------------------------------
    # Okuma
    # ------------------------------------------------------------------

    def get_sessions(self, limit: int = 100) -> list[SessionRecord]:
        if self._conn is None:
            return []
        try:
            cur = self._conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
            return [
                SessionRecord(
                    id=r["id"],
                    started_at=r["started_at"],
                    ended_at=r["ended_at"],
                    duration_sec=r["duration_sec"],
                    source_folder=r["source_folder"],
                    output_folder=r["output_folder"],
                    total_pages=r["total_pages"],
                    successful_pages=r["successful_pages"],
                    failed_pages=r["failed_pages"],
                    total_chapters=r["total_chapters"],
                    credits_spent=r["credits_spent"],
                    translator=r["translator"],
                )
                for r in rows
            ]
        except Exception as exc:
            logger.error("HistoryManager: Okuma hatası: %s", exc)
            return []

    def get_totals(self) -> dict[str, Any]:
        """Toplam istatistikleri döndürür."""
        if self._conn is None:
            return {}
        try:
            cur = self._conn.execute(
                """
                SELECT
                    COUNT(*)          AS session_count,
                    SUM(total_pages)  AS total_pages,
                    SUM(successful_pages) AS successful_pages,
                    SUM(failed_pages) AS failed_pages,
                    SUM(credits_spent) AS credits_spent,
                    SUM(duration_sec)  AS total_duration_sec
                FROM sessions
                """
            )
            row = cur.fetchone()
            return dict(row) if row else {}
        except Exception as exc:
            logger.error("HistoryManager: Toplam hatası: %s", exc)
            return {}

    def clear(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM sessions")
            self._conn.commit()
        except Exception as exc:
            logger.error("HistoryManager: Temizleme hatası: %s", exc)