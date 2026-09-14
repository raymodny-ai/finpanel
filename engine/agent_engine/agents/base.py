"""BaseAgent — abstract runtime for all AI employees.

Every concrete agent subclass implements `build_user_prompt()` to translate the
current task context into a request, and `parse_payload()` to convert the LLM's
JSON response into a typed role-specific payload model.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..llm import LLMError, get_llm
from ..models.agent import AgentConfig
from ..models.output import (
    AgentOutputEnvelope,
    DataRef,
    OutputType,
    Stance,
)
from ..prompt_loader import get_prompt_loader

log = logging.getLogger(__name__)


# Canonical JSON schema for the `evidence_refs` array. Every agent's
# output_schema_hint() should use this exact shape so Qwen3.8-Max (and other
# CLI-backend models that receive no server-side response_format) sees the
# full DataRef item structure with concrete field descriptions.
#
# Two use cases for `source`:
#   1. Raw data provider: "gold-api", "treasury-gov", "market_snapshot"
#   2. Upstream agent citation: "metals-da", "metals-qm", "macro-strategist"
# Both are valid because DataRef.source is a plain str with no enum.
EVIDENCE_REFS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": (
        "Citations supporting this agent's conclusions. Each item is a DataRef. "
        "`source` may be a raw data provider (e.g. 'gold-api', 'treasury-gov', "
        "'market_snapshot') OR an upstream agent_id (e.g. 'metals-da', "
        "'metals-qm') when citing another agent's output. Do NOT leave items "
        "empty; if you have no direct data refs, cite the upstream agents "
        "whose stance/confidence/key_points you relied on."
    ),
    "items": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": (
                    "Data provider name OR upstream agent_id. Examples: "
                    "'gold-api', 'treasury-gov', 'market_snapshot', "
                    "'metals-da', 'metals-qm', 'macro-strategist'."
                ),
            },
            "indicator": {
                "type": "string",
                "description": (
                    "The specific fact being cited. For data refs: 'XAU_spot', "
                    "'UST_10Y', 'gold_silver_ratio'. For agent refs: 'stance', "
                    "'confidence', 'key_point_1', 'risk_status'."
                ),
            },
            "value": {
                "description": (
                    "The cited value. Number for prices/yields, string for "
                    "categorical values like 'neutral' or 'yellow'."
                ),
            },
            "date": {
                "type": "string",
                "description": (
                    "ISO-8601 timestamp of the observation, e.g. "
                    "'2026-09-07' or '2026-09-07T19:17:18Z'. Use the upstream "
                    "envelope's timestamp for agent refs."
                ),
            },
            "unit": {
                "type": "string",
                "description": (
                    "Unit of measurement, e.g. 'USD/oz', '%', 'ratio'. Leave "
                    "empty string for unitless values like stances."
                ),
            },
        },
        "required": ["source", "indicator", "value", "date", "unit"],
    },
}


@dataclass
class AgentContext:
    """Everything an agent needs to produce one output.

    Passed in by the Orchestrator before each `run()` call.
    """

    task_id: str
    task_category: str = "daily_review"
    task_priority: str = "normal"
    user_query: Optional[str] = None
    assets: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)

    # Data pipeline results (from /public/data/latest.json or fresh run)
    market_data: dict = field(default_factory=dict)

    # Prior agent outputs in this task's workflow (list of envelopes, JSON-serializable)
    prior_outputs: list[dict] = field(default_factory=list)

    # Retrieved historical memos (RAG-lite: recent memos on same asset)
    historical_memos: list[dict] = field(default_factory=list)

    # Optional extra hints injected by the Orchestrator
    notes: str = ""


@dataclass
class AgentRunResult:
    envelope: AgentOutputEnvelope
    raw_response: str = ""
    llm_echo_mode: bool = False
    elapsed_ms: int = 0
    error: Optional[str] = None


class BaseAgent(ABC):
    """Base class for all Agent implementations."""

    #: Expected OutputType produced by this agent
    output_type: OutputType = OutputType.opinion

    def __init__(self, config: AgentConfig):
        self.config = config
        self.prompt_loader = get_prompt_loader()
        # Per-agent model override (e.g. metals-da -> Qwen3.8-Flash for speed).
        # Falls back to settings.LLM_MODEL when config.llm_model is None.
        self.llm = get_llm(model_override=config.llm_model)

    # --- Overridable hooks ----------------------------------------------

    def system_prompt(self) -> str:
        """Load the 8-section Prompt Contract for this agent."""
        return self.prompt_loader.load(self.config.agent_id)

    @abstractmethod
    def build_user_prompt(self, ctx: AgentContext) -> str:
        """Construct the user-facing prompt for one run."""

    @abstractmethod
    def output_schema_hint(self) -> dict[str, Any]:
        """JSON schema hint passed to the LLM for structured output."""

    @abstractmethod
    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        """Extract and validate the role-specific payload from the LLM response."""

    def post_process(self, ctx: AgentContext, response: dict[str, Any]) -> dict[str, Any]:
        """Optional hook for enrichment (e.g. attach evidence refs from data)."""
        return response

    # --- Runtime --------------------------------------------------------

    async def run(self, ctx: AgentContext) -> AgentRunResult:
        """Execute one agent turn: build prompt, call LLM, parse, wrap in envelope."""
        start = datetime.now(timezone.utc)

        system_prompt = self.system_prompt()
        user_prompt = self.build_user_prompt(ctx)

        try:
            response = await self.llm.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_hint=self.output_schema_hint(),
                temperature=self._temperature(),
                max_tokens=self.config.governance.max_output_length,
            )
        except LLMError as e:
            log.error("Agent %s LLM error: %s", self.config.agent_id, e)
            return AgentRunResult(
                envelope=self._empty_envelope(ctx, error=str(e)),
                error=str(e),
                elapsed_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
            )

        response = self.post_process(ctx, response)
        envelope = self._build_envelope(ctx, response)

        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        return AgentRunResult(
            envelope=envelope,
            raw_response=json.dumps(response, ensure_ascii=False, indent=2)[:8000],
            llm_echo_mode=bool(response.get("_echo")),
            elapsed_ms=elapsed,
        )

    # --- Internal helpers ----------------------------------------------

    def _temperature(self) -> float:
        """Role-specific temperature. DA/Risk = low, QM = medium, CIO = medium."""
        role = self.config.role.value
        return {
            "DA": 0.15,
            "QM": 0.30,
            "RA": 0.15,
            "CIO": 0.35,
            "PMO": 0.10,
            "MACRO": 0.30,
            "SEC": 0.10,
        }.get(role, 0.25)

    def _empty_envelope(self, ctx: AgentContext, error: str = "") -> AgentOutputEnvelope:
        # Lazy import to avoid a circular dependency (orchestrator.permissions
        # -> models.agent; agents.base -> orchestrator.permissions).
        try:
            from ..orchestrator.permissions import ALLOWED_STANCES
            allowed = ALLOWED_STANCES.get(self.config.role)
        except Exception:
            allowed = None

        fallback = "no_view"
        if allowed:
            if "neutral" in allowed:
                fallback = "neutral"
            else:
                fallback = sorted(allowed)[0]
        try:
            stance = Stance(fallback)
        except ValueError:
            stance = Stance.no_view

        return AgentOutputEnvelope(
            task_id=ctx.task_id,
            agent_id=self.config.agent_id,
            department_id=self.config.department,
            output_type=self.output_type,
            stance=stance,
            confidence=0.0,
            narrative=f"[{self.config.display_name}] failed to produce output. Error: {error}",
            key_points=[],
            risk_flags=[f"agent_failure:{self.config.agent_id}"],
            low_confidence_flag=True,
        )

    def _build_envelope(self, ctx: AgentContext, response: dict[str, Any]) -> AgentOutputEnvelope:
        """Merge LLM response with envelope scaffolding."""
        # Extract envelope-level fields with defaults
        stance_raw = str(response.get("stance", "neutral")).lower()
        try:
            stance = Stance(stance_raw)
        except ValueError:
            stance = Stance.neutral

        confidence = float(response.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        narrative = str(response.get("narrative", "")).strip()
        key_points = _as_str_list(response.get("key_points"))
        risk_flags = _as_str_list(response.get("risk_flags"))
        next_actions = _as_str_list(response.get("next_actions"))

        # Safety net: derive key_points from narrative when the model omits them.
        # Prefer lines that look like list items or short declarative sentences,
        # skip markdown headings and blank lines, cap length to keep UI tidy.
        if not key_points and narrative:
            derived: list[str] = []
            for raw_line in narrative.split("\n"):
                line = raw_line.strip().lstrip("-*• \t")
                if not line or line.startswith("#"):
                    continue
                if len(line) < 5 or len(line) > 200:
                    continue
                derived.append(line)
                if len(derived) >= 5:
                    break
            key_points = derived

        evidence_refs = _parse_data_refs(response.get("evidence_refs"))

        # Safety net: if the model returned no evidence_refs but we DO have
        # upstream envelopes, auto-generate cross-agent citations so the UI
        # always has an evidence trail. Mirrors metals_da.post_process's
        # "only backfill when empty" pattern.
        if not evidence_refs and ctx.prior_outputs:
            evidence_refs = _parse_data_refs(self._auto_cross_refs(ctx))

        payload = self.parse_payload(response)

        envelope = AgentOutputEnvelope(
            task_id=ctx.task_id,
            agent_id=self.config.agent_id,
            department_id=self.config.department,
            output_type=self.output_type,
            stance=stance,
            confidence=confidence,
            evidence_refs=evidence_refs,
            key_points=key_points,
            risk_flags=risk_flags,
            next_actions=next_actions,
            escalation_required=bool(response.get("escalation_required", False)),
            low_confidence_flag=confidence < self.config.governance.confidence_threshold,
            narrative=narrative,
            payload=payload,
        )
        return envelope

    def _auto_cross_refs(self, ctx: AgentContext) -> list[dict[str, Any]]:
        """Generate cross-agent DataRefs from ctx.prior_outputs.

        Called from _build_envelope only when the LLM returned an empty
        evidence_refs list. Emits up to 4 upstream citations (stance +
        confidence per upstream agent) so downstream UI always has an
        evidence trail.
        """
        refs: list[dict[str, Any]] = []
        for env in ctx.prior_outputs[:4]:
            if not isinstance(env, dict):
                continue
            upstream_id = str(env.get("agent_id") or "")
            if not upstream_id or upstream_id == self.config.agent_id:
                continue
            ts = str(env.get("timestamp") or "")
            stance = env.get("stance")
            conf = env.get("confidence")
            if stance is not None:
                refs.append({
                    "source": upstream_id,
                    "indicator": "stance",
                    "value": stance if isinstance(stance, str) else str(stance),
                    "date": ts,
                    "unit": "",
                })
            if conf is not None:
                refs.append({
                    "source": upstream_id,
                    "indicator": "confidence",
                    "value": conf,
                    "date": ts,
                    "unit": "",
                })
        return refs


def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(v)]


def _parse_data_refs(v: Any) -> list[DataRef]:
    if not isinstance(v, list):
        return []
    out: list[DataRef] = []
    for item in v:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                DataRef(
                    source=str(item.get("source", "")),
                    indicator=str(item.get("indicator", "")),
                    value=item.get("value"),
                    date=str(item.get("date", "")),
                    unit=str(item.get("unit", "")),
                )
            )
        except Exception as e:
            log.warning("Skipped malformed DataRef: %s", e)
    return out
