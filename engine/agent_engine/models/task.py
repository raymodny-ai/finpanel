"""Task model — first-class citizen driving all workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskCategory(str, Enum):
    asset_opinion = "asset_opinion"
    macro_review = "macro_review"
    risk_check = "risk_check"
    cross_asset = "cross_asset"
    event_response = "event_response"
    daily_review = "daily_review"
    historical_compare = "historical_compare"


class TaskPriority(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class WorkflowMode(str, Enum):
    fast = "fast"                # DA -> QM -> Risk -> CIO serial
    meeting = "meeting"          # Multi-agent meeting with debate


class TriggerSource(str, Enum):
    user_query = "user_query"
    daily_cron = "daily_cron"
    event_alert = "event_alert"
    playbook = "playbook"


class TaskStatus(str, Enum):
    """Task state machine.

    created -> assigned -> collecting_facts -> generating_opinions
      -> risk_review -> meeting_optional -> cio_summary -> archived
    Any stage may transition to failed; failed tasks can retry to assigned.
    """

    created = "created"
    assigned = "assigned"
    collecting_facts = "collecting_facts"
    generating_opinions = "generating_opinions"
    risk_review = "risk_review"
    meeting_optional = "meeting_optional"
    cio_summary = "cio_summary"
    archived = "archived"
    failed = "failed"


# Legal transitions
TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.created: {TaskStatus.assigned, TaskStatus.failed},
    TaskStatus.assigned: {TaskStatus.collecting_facts, TaskStatus.failed},
    TaskStatus.collecting_facts: {TaskStatus.generating_opinions, TaskStatus.failed},
    TaskStatus.generating_opinions: {TaskStatus.risk_review, TaskStatus.failed},
    TaskStatus.risk_review: {
        TaskStatus.meeting_optional,
        TaskStatus.cio_summary,
        TaskStatus.failed,
    },
    TaskStatus.meeting_optional: {TaskStatus.cio_summary, TaskStatus.failed},
    TaskStatus.cio_summary: {TaskStatus.archived, TaskStatus.failed},
    TaskStatus.archived: set(),
    TaskStatus.failed: {TaskStatus.assigned},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Task(BaseModel):
    """A unit of work in the firm.

    Every user query, every daily cron run, and every triggered playbook
    creates exactly one Task. Tasks are the traceable spine of the firm's
    activity — the frontend has a /tasks/[taskId] page showing the full
    state-machine history and intermediate outputs.
    """

    task_id: str
    user_query: Optional[str] = None
    category: TaskCategory = TaskCategory.daily_review
    priority: TaskPriority = TaskPriority.normal
    workflow_mode: WorkflowMode = WorkflowMode.fast
    trigger_source: TriggerSource = TriggerSource.daily_cron
    participants: list[str] = Field(default_factory=list)      # agent IDs
    departments: list[str] = Field(default_factory=list)       # department IDs
    assets: list[str] = Field(default_factory=list)            # ["XAU", "GC=F"]
    status: TaskStatus = TaskStatus.created
    status_history: list[dict] = Field(default_factory=list)   # [{status, at, note}]
    outputs: list[str] = Field(default_factory=list)           # opinion/memo IDs
    error: Optional[str] = None
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)
    completed_at: Optional[str] = None

    def transition(self, new_status: TaskStatus, note: str = "") -> None:
        """Transition status with validation."""
        allowed = TASK_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Illegal task transition: {self.status.value} -> {new_status.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        self.status_history.append(
            {"from": self.status.value, "to": new_status.value, "at": _utc_now(), "note": note}
        )
        self.status = new_status
        self.updated_at = _utc_now()
        if new_status == TaskStatus.archived:
            self.completed_at = _utc_now()
