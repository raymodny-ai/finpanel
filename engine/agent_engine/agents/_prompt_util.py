"""Shared helper for building user prompts across agents."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def format_market_data_block(market_data: dict[str, Any]) -> str:
    """Render market_data as a compact JSON block for LLM consumption."""
    if not market_data:
        return "(no market data provided)"
    return json.dumps(market_data, ensure_ascii=False, indent=2, default=str)[:8000]


def format_prior_outputs(prior: list[dict[str, Any]], limit: int = 8) -> str:
    """Render prior agent outputs as a bulleted summary."""
    if not prior:
        return "(no prior outputs)"
    lines: list[str] = []
    for p in prior[:limit]:
        agent = p.get("agent_id", "?")
        otype = p.get("output_type", "?")
        stance = p.get("stance", "?")
        conf = p.get("confidence", "?")
        narrative = (p.get("narrative") or "").strip().replace("\n", " ")
        if len(narrative) > 400:
            narrative = narrative[:400] + "..."
        key_points = p.get("key_points") or []
        kp_str = " | ".join(str(k)[:120] for k in key_points[:3])
        lines.append(
            f"- **{agent}** ({otype}, stance={stance}, confidence={conf})\n"
            f"  Narrative: {narrative}\n"
            f"  Key points: {kp_str}"
        )
    return "\n".join(lines)


def format_historical_memos(memos: list[dict[str, Any]], limit: int = 5) -> str:
    if not memos:
        return "(no historical memos on this topic)"
    lines: list[str] = []
    for m in memos[:limit]:
        mid = m.get("memo_id", "?")
        date = m.get("date", "?")
        title = m.get("title", "")
        concl = (m.get("final_conclusion") or "").strip().replace("\n", " ")
        if len(concl) > 250:
            concl = concl[:250] + "..."
        lines.append(f"- [{date}] #{mid} — {title}\n  Conclusion: {concl}")
    return "\n".join(lines)


def current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
