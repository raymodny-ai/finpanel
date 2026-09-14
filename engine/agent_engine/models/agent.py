"""Agent configuration model."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RoleTemplate(str, Enum):
    """Standard role templates across the firm."""

    DA = "DA"          # Data Analyst
    QM = "QM"          # Quant Manager
    TA = "TA"          # Technical Analyst
    RA = "RA"          # Risk Analyst
    ET = "ET"          # Execution Trader
    CIO = "CIO"        # Chief Investment Officer
    PMO = "PMO"        # Project Management Officer
    MACRO = "MACRO"    # Macro Strategist
    QUANT = "QUANT"    # Quant Researcher
    SEC = "SEC"        # Corporate Secretary — progress tracking + news digest


class Seniority(str, Enum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    executive = "executive"


class CollaborationGraph(BaseModel):
    """Directed graph of agent-to-agent relationships."""

    reports_to: list[str] = Field(default_factory=list)
    works_with: list[str] = Field(default_factory=list)
    can_challenge: list[str] = Field(default_factory=list)
    must_escalate_to: list[str] = Field(default_factory=list)
    cannot_override: list[str] = Field(default_factory=list)


class MemoryAccess(BaseModel):
    """Memory access permissions per agent."""

    short_term: bool = True
    department_memory: bool = True
    firm_memory: bool = False
    cross_dept_meeting_records: bool = False


class GovernanceConfig(BaseModel):
    """Governance rules per agent."""

    approval_required: bool = False
    audit_level: str = "low"                    # low / medium / high
    confidence_threshold: float = 0.3           # Below this must be flagged low-confidence
    max_output_length: int = 2000               # tokens


class AgentConfig(BaseModel):
    """Complete agent configuration loaded from YAML/JSON."""

    # Identity
    agent_id: str
    display_name: str
    title: str
    department: str
    role: RoleTemplate
    seniority: Seniority = Seniority.senior
    persona: str = ""
    specialty: list[str] = Field(default_factory=list)
    supported_assets: list[str] = Field(default_factory=list)
    working_style: str = ""
    avatar: str = ""

    # Collaboration
    collaboration: CollaborationGraph = Field(default_factory=CollaborationGraph)

    # Capability
    data_sources: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    reasoning_mode: str = "structured"          # structured / creative / critical
    playbook_ids: list[str] = Field(default_factory=list)

    # Governance
    memory_access: MemoryAccess = Field(default_factory=MemoryAccess)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)

    # Prompt
    prompt_file: str = ""                        # relative path under prompts/system/

    # Per-agent LLM model override (None -> use settings.LLM_MODEL).
    # Useful for lightweight roles (e.g. DA data extraction) that can run on
    # a faster/cheaper model than the heavy-reasoning agents (CIO / QM / RA).
    # Honored by both the OpenAI and CLI backends via get_llm(model_override=...).
    llm_model: Optional[str] = None

    # Trigger
    trigger_modes: list[str] = Field(
        default_factory=lambda: ["scheduled", "event_driven"]
    )
