"""Macro-Strategist — Head of Macro Strategy."""

from __future__ import annotations

from typing import Any

from ..models.output import OutputType
from .base import EVIDENCE_REFS_SCHEMA, AgentContext, BaseAgent
from ._prompt_util import (
    current_utc_iso,
    format_historical_memos,
    format_market_data_block,
    format_prior_outputs,
)


class MacroStrategistAgent(BaseAgent):
    output_type = OutputType.macro_brief

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return f"""## 任務上下文
- Task ID: {ctx.task_id}
- Category: {ctx.task_category}
- Generated at: {current_utc_iso()}

## 用戶提問（若為用戶觸發）
{ctx.user_query or "(無 — 這是排程盤後任務)"}

## 市場數據快照
```json
{format_market_data_block(ctx.market_data)}
```

## 上游部門輸出
{format_prior_outputs(ctx.prior_outputs)}

## 歷史 Macro Memo
{format_historical_memos(ctx.historical_memos)}

## 你的任務
按照 System Prompt Contract §5，產出：
1. Regime Label（如 late-cycle disinflation）
2. 四維分解：增長 / 通脹 / 流動性 / 政策
3. 跨資產含義（黃金、美債、美元、股票、加密）
4. Risk-On/Off 傾向
5. 與歷史 Regime 對比（若有變化說明原因）
6. 2-3 個關鍵觀察點

**切記**：
- stance 通常為 neutral 或 mixed
- 每個維度必須引用具體數據（DataRef）
- 不做細顆粒度資產建模
- 不使用「必然」「一定再現」等決定論措辭
"""

    def output_schema_hint(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "stance": {"type": "string", "enum": ["neutral", "mixed", "bullish", "bearish"]},
                "confidence": {"type": "number"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": EVIDENCE_REFS_SCHEMA,
                "escalation_required": {"type": "boolean"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "regime_label": {"type": "string"},
                        "growth_view": {"type": "string"},
                        "inflation_view": {"type": "string"},
                        "liquidity_view": {"type": "string"},
                        "policy_view": {"type": "string"},
                        "cross_asset_commentary": {"type": "string"},
                    },
                    "required": ["regime_label"],
                },
            },
            "required": ["narrative", "stance", "confidence", "payload"],
        }

    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        p = response.get("payload") or {}
        if not isinstance(p, dict):
            p = {}
        return {
            "regime_label": str(p.get("regime_label", "")),
            "growth_view": str(p.get("growth_view", "")),
            "inflation_view": str(p.get("inflation_view", "")),
            "liquidity_view": str(p.get("liquidity_view", "")),
            "policy_view": str(p.get("policy_view", "")),
            "cross_asset_commentary": str(p.get("cross_asset_commentary", "")),
        }
