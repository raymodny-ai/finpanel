"""Decision objects: Opinion, Debate, Decision Memo, Playbook."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .output import DataRef, Stance


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Timeframe(str, Enum):
    short = "short"        # 1-5 days
    medium = "medium"      # 1-4 weeks
    long = "long"          # 1-6 months


class OpinionStatus(str, Enum):
    draft = "draft"
    submitted = "submitted"
    reviewed = "reviewed"
    challenged = "challenged"
    accepted = "accepted"
    rejected = "rejected"
    archived = "archived"


class Opinion(BaseModel):
    """Single-role viewpoint on an asset or topic."""

    opinion_id: str
    task_id: str
    agent_id: str
    department_id: str
    timestamp: str = Field(default_factory=_utc_now)
    asset: str
    direction: Stance = Stance.neutral
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    timeframe: Timeframe = Timeframe.short
    core_arguments: list[str] = Field(default_factory=list)
    evidence_refs: list[DataRef] = Field(default_factory=list)
    risk_note: str = ""
    narrative: str = ""                      # natural-language layer
    status: OpinionStatus = OpinionStatus.draft
    historical_refs: list[str] = Field(default_factory=list)  # prior memo IDs


class DebateRound(BaseModel):
    round_number: int
    speaker_agent_id: str
    argument: str
    evidence_refs: list[DataRef] = Field(default_factory=list)
    stance: str = "support"                  # support / oppose / modify
    challenged_point: str = ""


class Debate(BaseModel):
    """Multi-role disagreement record."""

    debate_id: str
    task_id: str
    trigger_opinion_ids: list[str] = Field(default_factory=list)
    department_id: str
    topic: str
    participants: list[dict] = Field(default_factory=list)     # [{agentId, stance}]
    rounds: list[DebateRound] = Field(default_factory=list)
    max_rounds: int = 3
    unresolved_questions: list[str] = Field(default_factory=list)
    resolution: str = ""                     # consensus / escalated_to_cio / timeout_neutral
    resolved_opinion_id: Optional[str] = None
    status: str = "active"                   # active / resolved / escalated


class ActionItem(BaseModel):
    asset: str
    action: str                              # hold / add / reduce / hedge / watch / exit
    urgency: str = "monitoring"              # immediate / this_week / monitoring
    size: Optional[str] = None
    condition: Optional[str] = None
    note: str = ""


class MemoStatus(str, Enum):
    draft = "draft"
    published = "published"
    under_review = "under_review"
    validated = "validated"
    invalidated = "invalidated"


class DecisionMemo(BaseModel):
    """Firm-level official conclusion."""

    memo_id: str
    task_id: str
    date: str
    title: str
    owner_agent_id: str = "cio"
    participating_depts: list[str] = Field(default_factory=list)
    participating_agents: list[str] = Field(default_factory=list)
    executive_summary: str = ""
    supporting_evidence: list[dict] = Field(default_factory=list)
    dissenting_views: list[dict] = Field(default_factory=list)
    final_conclusion: str = ""
    action_items: list[ActionItem] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    next_watchpoints: list[str] = Field(default_factory=list)
    review_date: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    version: int = 1
    status: MemoStatus = MemoStatus.draft
    created_at: str = Field(default_factory=_utc_now)


class TriggerCondition(BaseModel):
    indicator: str
    operator: str                            # > / < / == / cross_above / cross_below
    threshold: float
    lookback: str = "5d"


class Playbook(BaseModel):
    """Reusable quantitative decision template."""

    playbook_id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    trigger_conditions: list[TriggerCondition] = Field(default_factory=list)
    required_agents: list[str] = Field(default_factory=list)
    required_data: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    meeting_format: str = "fast"             # fast / desk_review / full_meeting
    max_debate_rounds: int = 3
    output_templates: list[str] = Field(default_factory=list)
    historical_performance: dict = Field(default_factory=dict)
