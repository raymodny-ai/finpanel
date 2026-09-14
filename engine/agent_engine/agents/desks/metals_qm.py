"""Metals-QM — Precious Metals Quantitative Manager."""

from __future__ import annotations

from typing import Any

from ...models.output import OutputType
from ..base import EVIDENCE_REFS_SCHEMA, AgentContext, BaseAgent
from .._prompt_util import (
    current_utc_iso,
    format_historical_memos,
    format_market_data_block,
    format_prior_outputs,
)


class MetalsQMAgent(BaseAgent):
    output_type = OutputType.signal_note

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return f"""## 任務上下文
- Task ID: {ctx.task_id}
- Category: {ctx.task_category}
- Assets: {", ".join(ctx.assets) if ctx.assets else "XAU, XAG"}
- Generated at: {current_utc_iso()}

## 用戶提問（若為用戶觸發）
{ctx.user_query or "(無 — 這是排程盤後任務)"}

## 市場數據快照
```json
{format_market_data_block(ctx.market_data)}
```

## 上游 DA 的 Fact Brief（同任務）
{format_prior_outputs([p for p in ctx.prior_outputs if p.get("output_type") == "fact_brief"])}

## 歷史相關 Memo
{format_historical_memos(ctx.historical_memos)}

## 你的任務
基於 DA 的 Fact Brief 與市場數據，按照 System Prompt Contract §5 產出：
1. 當前 Regime 標籤
2. 使用的指標清單 + 樣本區間
3. 信號方向 + 強度 + 置信度
4. 核心論據（3-5 條，每條必須帶具體數字）
5. 倉位建議（含規模區間）
6. **失效條件（必填，不可省略）**
7. 若有 backtest 數據則附上

**切記**：
- 樣本量 < 30 時不能宣稱統計顯著性
- 每個信號必須有 invalidation_condition
- 若與 DA 標記的重大缺口衝突，confidence 必須 <= 0.4
- 使用「在 X 條件下，Y 機率的 Z」句式，不用絕對措辭
"""

    def output_schema_hint(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "stance": {"type": "string", "enum": ["bullish", "bearish", "neutral", "mixed"]},
                "confidence": {"type": "number"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": EVIDENCE_REFS_SCHEMA,
                "escalation_required": {"type": "boolean"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "indicators_used": {"type": "array", "items": {"type": "string"}},
                        "sample_period": {"type": "string"},
                        "current_regime": {"type": "string"},
                        "signal_direction": {"type": "string"},
                        "signal_strength": {"type": "number"},
                        "invalidation_condition": {"type": "string"},
                        "model_summary": {"type": "string"},
                        "position_suggestion": {"type": "string"},
                        "backtest_snapshot": {"type": ["object", "null"]},
                    },
                    "required": ["signal_direction", "invalidation_condition"],
                },
            },
            "required": ["narrative", "stance", "confidence", "key_points", "payload"],
        }

    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        p = response.get("payload") or {}
        if not isinstance(p, dict):
            p = {}
        invalidation = str(p.get("invalidation_condition", "")).strip()
        if not invalidation:
            invalidation = "(未提供 — 此信號應被視為低置信)"
        return {
            "indicators_used": list(p.get("indicators_used", [])),
            "sample_period": str(p.get("sample_period", "")),
            "current_regime": str(p.get("current_regime", "")),
            "signal_direction": str(p.get("signal_direction", "neutral")),
            "signal_strength": float(p.get("signal_strength", 0.0) or 0.0),
            "invalidation_condition": invalidation,
            "model_summary": str(p.get("model_summary", "")),
            "position_suggestion": str(p.get("position_suggestion", "")),
            "backtest_snapshot": p.get("backtest_snapshot"),
        }

    def post_process(self, ctx: AgentContext, response: dict[str, Any]) -> dict[str, Any]:
        """Add risk_flags if invalidation missing or confidence is very low."""
        flags = list(response.get("risk_flags") or [])
        payload = response.get("payload") or {}
        if not payload.get("invalidation_condition"):
            flags.append("missing_invalidation_condition")
        conf = float(response.get("confidence", 0.5))
        if conf < 0.4:
            flags.append("low_confidence")
        response["risk_flags"] = flags
        return response
