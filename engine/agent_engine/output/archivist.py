"""Archivist — deterministic replacement for the Knowledge Archivist Agent.

The Archivist is *not* an LLM Agent in FinPanel v3.1. Its duties (writing
meeting minutes, indexing memos, appending to firm memory) are all
deterministic and can be done in plain Python. This module implements them.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import settings
from ..models.decision import DecisionMemo
from ..models.meeting import Meeting
from ..models.memory import MemoryEntry, MemoryType

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Archivist:
    """Writes memory entries to SQLite; Phase 3 will add ChromaDB embeddings."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (settings.STORAGE_DIR / "memory.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    department_id TEXT,
                    agent_ids TEXT,
                    task_id TEXT,
                    date TEXT,
                    title TEXT,
                    content TEXT,
                    tags TEXT,
                    related_memo_ids TEXT,
                    retrieval_count INTEGER DEFAULT 0,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(type)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_date ON memory_entries(date DESC)
                """
            )

    # --- Writes ---------------------------------------------------------

    def archive_memo(self, memo: DecisionMemo) -> MemoryEntry:
        entry = MemoryEntry(
            id=f"mem-{memo.memo_id}",
            type=MemoryType.decision_memo,
            department_id=",".join(memo.participating_depts),
            agent_ids=memo.participating_agents,
            task_id=memo.task_id,
            date=memo.date,
            title=memo.title,
            content=json.dumps(memo.model_dump(mode="json"), ensure_ascii=False, default=str),
            tags=_extract_memo_tags(memo),
            related_memo_ids=[],
        )
        self._insert(entry)
        return entry

    def archive_meeting(self, meeting: Meeting) -> MemoryEntry:
        entry = MemoryEntry(
            id=f"mem-{meeting.meeting_id}",
            type=MemoryType.meeting_minutes,
            department_id="",
            agent_ids=meeting.participants,
            task_id=meeting.task_id,
            date=meeting.created_at[:10],
            title=meeting.title or meeting.meeting_id,
            content=json.dumps(meeting.model_dump(mode="json"), ensure_ascii=False, default=str),
            tags=[meeting.meeting_type.value],
            related_memo_ids=list(meeting.decisions),
        )
        self._insert(entry)
        return entry

    def _insert(self, entry: MemoryEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_entries
                (id, type, department_id, agent_ids, task_id, date, title, content,
                 tags, related_memo_ids, retrieval_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.type.value,
                    entry.department_id,
                    json.dumps(entry.agent_ids),
                    entry.task_id,
                    entry.date,
                    entry.title,
                    entry.content,
                    json.dumps(entry.tags),
                    json.dumps(entry.related_memo_ids),
                    entry.retrieval_count,
                    entry.created_at,
                ),
            )
        log.info("Archived %s (%s)", entry.id, entry.type.value)

    # --- Reads ----------------------------------------------------------

    def recent_memos(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, date, title, content, tags, task_id
                FROM memory_entries
                WHERE type = ?
                ORDER BY date DESC, created_at DESC
                LIMIT ?
                """,
                (MemoryType.decision_memo.value, limit),
            ).fetchall()
        return [self._row_to_memo_dict(r) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Naive substring search — ChromaDB will replace this in Phase 3."""
        q = query.lower()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, date, title, content, tags, task_id, type
                FROM memory_entries
                WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (f"%{q}%", f"%{q}%", limit),
            ).fetchall()
        return [self._row_to_memo_dict(r) for r in rows]

    def _row_to_memo_dict(self, row: sqlite3.Row) -> dict:
        try:
            content = json.loads(row["content"])
        except Exception:
            content = {"raw": row["content"]}
        return {
            "memory_id": row["id"],
            "date": row["date"],
            "title": row["title"],
            "task_id": row["task_id"],
            "type": row["type"] if "type" in row.keys() else "decision_memo",
            "content": content,
            "tags": json.loads(row["tags"]) if row["tags"] else [],
        }


def _extract_memo_tags(memo: DecisionMemo) -> list[str]:
    tags: set[str] = set()
    for d in memo.participating_depts:
        tags.add(d)
    for ai in memo.action_items:
        if ai.asset:
            tags.add(ai.asset.upper())
    return sorted(tags)


# Module singleton
_archivist: Optional[Archivist] = None


def get_archivist() -> Archivist:
    global _archivist
    if _archivist is None:
        _archivist = Archivist()
    return _archivist
