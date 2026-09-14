"""Permission validation — hard rules enforced by Orchestrator (not prompts).

These rules are the deterministic guardrails of the firm. They are checked
programmatically after every Agent output so that no LLM hallucination can
violate the corporate governance model.
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..models.agent import RoleTemplate
from ..models.output import AgentOutputEnvelope, OutputType

log = logging.getLogger(__name__)


class PermissionViolation(Exception):
    """Raised when an agent output violates the firm's governance rules."""

    def __init__(self, agent_id: str, rule: str, details: str = ""):
        self.agent_id = agent_id
        self.rule = rule
        self.details = details
        super().__init__(f"[{agent_id}] permission violation: {rule}. {details}")


# === Rule tables ==========================================================

#: What output_type each role is allowed to produce.
ALLOWED_OUTPUT_TYPES: dict[RoleTemplate, set[OutputType]] = {
    RoleTemplate.DA: {OutputType.fact_brief},
    RoleTemplate.QM: {OutputType.signal_note, OutputType.opinion},
    RoleTemplate.TA: {OutputType.opinion},
    RoleTemplate.RA: {OutputType.risk_note},
    RoleTemplate.ET: {OutputType.execution_plan},
    RoleTemplate.MACRO: {OutputType.macro_brief, OutputType.opinion},
    RoleTemplate.QUANT: {OutputType.signal_note, OutputType.opinion},
    RoleTemplate.PMO: {OutputType.task_brief},
    RoleTemplate.CIO: {OutputType.cio_memo, OutputType.meeting_minutes},
    RoleTemplate.SEC: {OutputType.secretary_brief},
}

#: What stance each role is allowed to declare.
ALLOWED_STANCES: dict[RoleTemplate, set[str]] = {
    RoleTemplate.DA: {"neutral", "no_view"},
    RoleTemplate.RA: {"risk_alert", "neutral"},
    RoleTemplate.PMO: {"no_view"},
    RoleTemplate.SEC: {"no_view"},   # secretary never takes a market position
    # Others may declare any directional stance
}


def validate_output_permissions(
    envelope: AgentOutputEnvelope,
    role: RoleTemplate,
) -> None:
    """Raise PermissionViolation if the envelope breaks governance rules.

    Enforced rules (from FinPanel v3.1 §8.1):
    - DA cannot produce decision_memo / signal_note / risk_note
    - QM cannot produce decision_memo (must go through CIO)
    - RA cannot produce signal_note / decision_memo
    - PMO cannot produce anything except task_brief
    - DA stance must be neutral or no_view
    - RA stance must be risk_alert or neutral
    - PMO stance must be no_view
    """
    allowed_types = ALLOWED_OUTPUT_TYPES.get(role, set())
    if envelope.output_type not in allowed_types:
        raise PermissionViolation(
            envelope.agent_id,
            rule="output_type_not_allowed",
            details=(
                f"role={role.value} produced {envelope.output_type.value}, "
                f"allowed={[t.value for t in allowed_types]}"
            ),
        )

    allowed_stances = ALLOWED_STANCES.get(role)
    if allowed_stances is not None:
        if envelope.stance.value not in allowed_stances:
            raise PermissionViolation(
                envelope.agent_id,
                rule="stance_not_allowed",
                details=(
                    f"role={role.value} declared stance={envelope.stance.value}, "
                    f"allowed={sorted(allowed_stances)}"
                ),
            )


def validate_memo_hard_rules(memo_payload: dict) -> list[str]:
    """Check CIO memo payload against the '硬性規則' from cio.md §8.

    Returns a list of violations (empty list = passes). These are warnings
    the Orchestrator logs; they do not throw because the CIO output has
    already been normalized in parse_payload().
    """
    issues: list[str] = []

    dissenting = memo_payload.get("dissenting_views") or []
    if not dissenting:
        issues.append("dissenting_views_empty")

    invalidation = memo_payload.get("invalidation_conditions") or []
    if not invalidation:
        issues.append("invalidation_conditions_empty")

    conclusion = str(memo_payload.get("final_conclusion", "")).strip()
    if len(conclusion) < 10:
        issues.append("final_conclusion_too_short")

    supporting = memo_payload.get("supporting_evidence") or []
    if len(supporting) < 1:
        issues.append("no_supporting_evidence")

    return issues


def validate_risk_escalation(envelopes: Iterable[AgentOutputEnvelope]) -> bool:
    """Return True if any Risk CRO envelope demands escalation to CIO."""
    for e in envelopes:
        if e.agent_id != "risk-cro":
            continue
        payload = e.payload or {}
        if payload.get("status") == "red" or payload.get("veto_recommendation"):
            return True
        if e.escalation_required:
            return True
    return False
