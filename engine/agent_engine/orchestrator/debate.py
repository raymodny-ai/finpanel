"""Meeting Engine — multi-round debate logic (Phase 2 feature).

Phase 1 uses the simplified round-robin meeting in Orchestrator._convene_meeting.
This module holds the more sophisticated multi-round debate resolution that
lands in Phase 2.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..models.decision import Debate, DebateRound
from ..models.output import AgentOutputEnvelope

log = logging.getLogger(__name__)


def detect_conflicts(envelopes: list[AgentOutputEnvelope]) -> list[dict[str, Any]]:
    """Detect stance conflicts between agents on the same asset.

    Returns a list of conflict records:
      {asset, bullish_agents, bearish_agents, evidence_refs}
    """
    from collections import defaultdict

    by_asset: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"bullish": [], "bearish": [], "neutral": []})

    for e in envelopes:
        if e.output_type.value not in ("signal_note", "opinion"):
            continue
        # Try to detect asset from evidence_refs or payload
        asset = _guess_asset(e)
        if not asset:
            continue
        stance = e.stance.value
        if stance == "bullish":
            by_asset[asset]["bullish"].append(e.agent_id)
        elif stance == "bearish":
            by_asset[asset]["bearish"].append(e.agent_id)
        else:
            by_asset[asset]["neutral"].append(e.agent_id)

    conflicts = []
    for asset, buckets in by_asset.items():
        if buckets["bullish"] and buckets["bearish"]:
            conflicts.append(
                {
                    "asset": asset,
                    "bullish_agents": buckets["bullish"],
                    "bearish_agents": buckets["bearish"],
                    "topic": f"{asset} direction disagreement",
                }
            )
    return conflicts


def _guess_asset(envelope: AgentOutputEnvelope) -> Optional[str]:
    """Best-effort asset detection from evidence refs or narrative."""
    for ref in envelope.evidence_refs:
        ind = (ref.indicator or "").upper()
        for asset in ("XAU", "XAG", "GC=F", "SI=F", "GDX", "BTC", "ETH"):
            if asset in ind:
                return asset
    text = (envelope.narrative or "").upper()
    for asset in ("XAU", "XAG", "GOLD", "SILVER", "BTC", "ETH"):
        if asset in text:
            return {"GOLD": "XAU", "SILVER": "XAG"}.get(asset, asset)
    return None
