"""Meeting model — record of multi-agent collaboration."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MeetingType(str, Enum):
    daily_review = "daily_review"
    desk_review = "desk_review"
    event_war_room = "event_war_room"
    risk_committee = "risk_committee"
    cio_allocation = "cio_allocation"
    post_mortem = "post_mortem"


class MeetingRound(BaseModel):
    """A single speaking turn in a meeting."""

    round_number: int
    speaker_agent_id: str
    speaker_display_name: str = ""
    output_type: str = ""
    narrative: str = ""                      # natural-language layer
    structured_output: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utc_now)


class Meeting(BaseModel):
    meeting_id: str
    task_id: str
    meeting_type: MeetingType
    title: str = ""
    participants: list[str] = Field(default_factory=list)
    rounds: list[MeetingRound] = Field(default_factory=list)
    debates: list[dict] = Field(default_factory=list)
    summary: str = ""
    decisions: list[str] = Field(default_factory=list)   # memo IDs produced
    created_at: str = Field(default_factory=_utc_now)
    concluded_at: Optional[str] = None
