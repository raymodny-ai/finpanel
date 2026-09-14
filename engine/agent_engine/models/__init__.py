"""Pydantic models for FinPanel Agent Engine."""

from .agent import (
    AgentConfig,
    CollaborationGraph,
    GovernanceConfig,
    MemoryAccess,
    RoleTemplate,
    Seniority,
)
from .task import Task, TaskCategory, TaskStatus, TaskPriority, TriggerSource, WorkflowMode
from .decision import (
    ActionItem,
    Debate,
    DebateRound,
    DecisionMemo,
    MemoStatus,
    Opinion,
    OpinionStatus,
    Playbook,
    TriggerCondition,
    Timeframe,
)
from .output import (
    AgentOutputEnvelope,
    DataRef,
    FactBrief,
    MacroBrief,
    OutputType,
    RiskNote,
    RiskStatus,
    SignalNote,
    Stance,
    TaskBrief,
    CIOMemoPayload,
    Anomaly,
    NewsItem,
)
from .meeting import Meeting, MeetingRound, MeetingType
from .memory import MemoryEntry, MemoryType

__all__ = [
    # agent
    "AgentConfig",
    "CollaborationGraph",
    "GovernanceConfig",
    "MemoryAccess",
    "RoleTemplate",
    "Seniority",
    # task
    "Task",
    "TaskCategory",
    "TaskStatus",
    "TaskPriority",
    "TriggerSource",
    "WorkflowMode",
    # decision
    "ActionItem",
    "Debate",
    "DebateRound",
    "DecisionMemo",
    "MemoStatus",
    "Opinion",
    "OpinionStatus",
    "Playbook",
    "TriggerCondition",
    "Timeframe",
    # output
    "AgentOutputEnvelope",
    "DataRef",
    "FactBrief",
    "MacroBrief",
    "OutputType",
    "RiskNote",
    "RiskStatus",
    "SignalNote",
    "Stance",
    "TaskBrief",
    "CIOMemoPayload",
    "Anomaly",
    "NewsItem",
    # meeting
    "Meeting",
    "MeetingRound",
    "MeetingType",
    # memory
    "MemoryEntry",
    "MemoryType",
]
