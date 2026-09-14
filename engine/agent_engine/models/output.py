"""Universal Agent Output Envelope + role-specific payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stance(str, Enum):
    bullish = "bullish"
    bearish = "bearish"
    neutral = "neutral"
    mixed = "mixed"
    risk_alert = "risk_alert"
    no_view = "no_view"


class OutputType(str, Enum):
    fact_brief = "fact_brief"
    signal_note = "signal_note"
    opinion = "opinion"
    risk_note = "risk_note"
    execution_plan = "execution_plan"
    macro_brief = "macro_brief"
    task_brief = "task_brief"
    cio_memo = "cio_memo"
    meeting_minutes = "meeting_minutes"
    qa_alert = "qa_alert"
    secretary_brief = "secretary_brief"


class RiskStatus(str, Enum):
    green = "green"
    yellow = "yellow"
    red = "red"


class DataRef(BaseModel):
    """Reference to a specific data point used as evidence."""

    source: str                              # e.g. "gold-api", "treasury-gov"
    indicator: str                           # e.g. "XAU_spot", "10Y_yield"
    value: Union[float, str, bool, None] = None
    date: str = ""
    unit: str = ""


class Anomaly(BaseModel):
    """Detected data anomaly."""

    field: str
    expected_range: str
    actual: Union[float, str]
    severity: str = "medium"                 # low / medium / high
    note: str = ""


class NewsItem(BaseModel):
    headline: str
    source: str
    published_at: str
    relevance: str = ""
    sentiment: str = "neutral"               # positive / negative / neutral


# === Role-specific payloads ==============================================


class FactBrief(BaseModel):
    """DA output payload."""

    data_time_range: str = ""
    data_sources: list[str] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    news_relevance: list[NewsItem] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    supported_questions: list[str] = Field(default_factory=list)


class SignalNote(BaseModel):
    """QM output payload."""

    indicators_used: list[str] = Field(default_factory=list)
    sample_period: str = ""
    current_regime: str = ""
    signal_direction: str = "neutral"        # long / short / neutral
    signal_strength: float = 0.0
    invalidation_condition: str = ""
    model_summary: str = ""
    position_suggestion: str = ""
    backtest_snapshot: Optional[dict] = None


class RiskNote(BaseModel):
    """RA output payload."""

    status: RiskStatus = RiskStatus.green
    risk_budget_opinion: str = ""
    tail_risk_warnings: list[str] = Field(default_factory=list)
    scenario_stress: str = ""
    veto_recommendation: bool = False
    veto_reason: Optional[str] = None
    downgrade_suggestion: Optional[str] = None


class MacroBrief(BaseModel):
    """Macro Strategist output payload."""

    regime_label: str = ""                   # e.g. "risk-on disinflation"
    growth_view: str = ""
    inflation_view: str = ""
    liquidity_view: str = ""
    policy_view: str = ""
    cross_asset_commentary: str = ""


class TaskBrief(BaseModel):
    """PMO output payload."""

    task_category: str = ""
    priority: str = "normal"
    required_departments: list[str] = Field(default_factory=list)
    required_agents: list[str] = Field(default_factory=list)
    recommended_mode: str = "fast"           # fast / meeting
    deliverables: list[str] = Field(default_factory=list)
    reasoning: str = ""


class CIOMemoPayload(BaseModel):
    """CIO output payload — becomes DecisionMemo after post-processing."""

    title: str = ""
    executive_summary: str = ""
    final_conclusion: str = ""
    main_line: str = ""                      # 今日主線
    supporting_evidence: list[dict] = Field(default_factory=list)
    dissenting_views: list[dict] = Field(default_factory=list)
    action_items: list[dict] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    next_watchpoints: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class ProgressItem(BaseModel):
    """One line in the secretary's progress-tracking section."""

    task_id: str = ""
    memo_id: str = ""
    kind: str = "task"                       # task / memo / meeting
    status: str = ""                         # e.g. collecting_facts, published
    title: str = ""
    last_activity: str = ""                  # ISO timestamp
    age_hours: float = 0.0                   # how stale
    attention: str = "normal"                # normal / follow_up / stale / blocked


class NewsDigestItem(BaseModel):
    """One news item after secretary processing (extends raw NewsItem)."""

    headline: str
    source: str                              # e.g. Reuters, Bloomberg, FT
    url: str = ""
    published_at: str = ""                   # ISO
    summary: str = ""                        # 1-2 sentence distillation
    sentiment: str = "neutral"               # positive / negative / neutral / mixed
    materiality: str = "low"                 # low / medium / high / critical
    affected_assets: list[str] = Field(default_factory=list)   # XAU, XAG, SPX, DXY...
    affected_departments: list[str] = Field(default_factory=list)
    linked_memo_ids: list[str] = Field(default_factory=list)   # cross-ref to firm memory
    linked_task_ids: list[str] = Field(default_factory=list)


class SecretaryBrief(BaseModel):
    """Corporate Secretary output payload.

    Covers three jobs in one artifact:
      1. News digest — past 24h capital-market-moving headlines
      2. Progress tracking — status of open tasks / recent memos
      3. Info correlation — links between news and firm artifacts
    """

    brief_type: str = "daily_morning"        # daily_morning / on_demand / event_driven
    period_covered: str = ""                 # e.g. "2026-09-07T07:00Z ~ 2026-09-08T07:00Z"
    generated_at: str = ""
    news_source: str = "cache"               # cache / finnhub / rss / browser / none
    news_fresh: bool = False                 # True if newest cache item < 24h old

    # Job 1: news digest
    top_stories: list[NewsDigestItem] = Field(default_factory=list)
    news_by_asset: dict[str, list[str]] = Field(default_factory=dict)   # asset -> headlines
    market_movers: list[str] = Field(default_factory=list)              # one-line summaries

    # Job 2: progress tracking
    open_tasks: list[ProgressItem] = Field(default_factory=list)
    recent_memos: list[ProgressItem] = Field(default_factory=list)
    stale_items: list[ProgressItem] = Field(default_factory=list)       # needs follow-up

    # Job 3: info correlation
    cross_references: list[dict] = Field(default_factory=list)
    # e.g. [{"news_headline": "...", "linked_memo": "memo-2026...", "reason": "..."}]

    # Meta
    follow_ups_needed: list[str] = Field(default_factory=list)
    executive_headline: str = ""             # one-sentence TL;DR for CIO
    confidence: float = 0.5


# === Universal Envelope ==================================================


PAYLOAD_UNION = Union[
    FactBrief,
    SignalNote,
    RiskNote,
    MacroBrief,
    TaskBrief,
    CIOMemoPayload,
    SecretaryBrief,
    dict,
]


class AgentOutputEnvelope(BaseModel):
    """Universal envelope — every agent output must have these fields."""

    task_id: str
    agent_id: str
    department_id: str
    timestamp: str = Field(default_factory=_utc_now)
    output_type: OutputType
    stance: Stance = Stance.neutral
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence_refs: list[DataRef] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    escalation_required: bool = False
    low_confidence_flag: bool = False
    narrative: str = ""                      # Natural-language layer (Markdown)
    payload: dict = Field(default_factory=dict)   # Role-specific typed payload (serialized)

    def with_payload(self, payload: BaseModel) -> "AgentOutputEnvelope":
        self.payload = payload.model_dump(mode="json")
        return self
