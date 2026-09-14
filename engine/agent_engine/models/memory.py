"""Memory model — three-layer knowledge store."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryType(str, Enum):
    decision_memo = "decision_memo"
    meeting_minutes = "meeting_minutes"
    research_note = "research_note"
    failed_idea = "failed_idea"
    opinion_snapshot = "opinion_snapshot"
    kpi_record = "kpi_record"


class MemoryEntry(BaseModel):
    """Entry in firm/department memory."""

    id: str
    type: MemoryType
    department_id: str = ""
    agent_ids: list[str] = Field(default_factory=list)
    task_id: str = ""
    date: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    title: str
    content: str                             # Markdown
    tags: list[str] = Field(default_factory=list)
    related_memo_ids: list[str] = Field(default_factory=list)
    retrieval_count: int = 0
    created_at: str = Field(default_factory=_utc_now)
    embedding: Optional[list[float]] = None  # filled by ChromaDB layer
