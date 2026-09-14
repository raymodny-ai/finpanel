"""Risk-CRO — Chief Risk Officer."""

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


class RiskCROAgent(BaseAgent):
    output_type = OutputType.risk_note

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return f"""## 任務上下文
- Task ID: {ctx.task_id}
- Category: {ctx.task_category}
- Generated at: {current_utc_iso()}

## 市場數據快照
```json
{format_market_data_block(ctx.market_data)}
```

## 所有上游輸出（DA / QM / Macro）
{format_prior_outputs(ctx.prior_outputs)}

## 歷史風險否決記錄
{format_historical_memos(ctx.historical_memos)}

## 你的任務
按照 System Prompt Contract §5（精簡版），審查所有上游 signal / opinion / macro_brief，產出：
1. 每個信號的紅/黃/綠燈 + 一行尾部情景 + 動作（approve / downgrade(→X%) / veto）
2. 整體狀態 green / yellow / red
3. 若否決或降級，明示原因（寫在 payload.veto_reason / downgrade_suggestion）
4. 給 CIO 的一段話總結（narrative 末段，不超過 3 句）

**輸出紀律（重要 — 影響 latency）**：
- 逐信號審查每條 ≤ 3 行，禁止複製上游 narrative
- 相關性 / 流動性只在異常時提及
- narrative 總長建議 ≤ 600 中文字
- risk_flags 每條一句話，≤ 8 條

**硬性規則**：
- 低樣本 (<30) / 高槓桿 (>2x) / 高擁擠度 / 高事件風險 → 提高警戒
- 關鍵信息缺失 → 預設降級，絕不預設通過
- 任何紅燈 → escalation_required = true
- QM confidence < 0.4 → 預設黃燈

**切記**：
- stance 只能是 risk_alert 或 neutral
- 你不提出替代投資方向
- risk_flags 是你的核心輸出，必須詳盡但精簡
"""

    def output_schema_hint(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "stance": {"type": "string", "enum": ["risk_alert", "neutral"]},
                "confidence": {"type": "number"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": EVIDENCE_REFS_SCHEMA,
                "escalation_required": {"type": "boolean"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["green", "yellow", "red"]},
                        "risk_budget_opinion": {"type": "string"},
                        "tail_risk_warnings": {"type": "array", "items": {"type": "string"}},
                        "scenario_stress": {"type": "string"},
                        "veto_recommendation": {"type": "boolean"},
                        "veto_reason": {"type": ["string", "null"]},
                        "downgrade_suggestion": {"type": ["string", "null"]},
                    },
                    "required": ["status"],
                },
            },
            "required": ["narrative", "stance", "confidence", "risk_flags", "payload"],
        }

    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        p = response.get("payload") or {}
        if not isinstance(p, dict):
            p = {}
        status = str(p.get("status", "yellow")).lower()
        if status not in ("green", "yellow", "red"):
            status = "yellow"
        return {
            "status": status,
            "risk_budget_opinion": str(p.get("risk_budget_opinion", "")),
            "tail_risk_warnings": list(p.get("tail_risk_warnings", [])),
            "scenario_stress": str(p.get("scenario_stress", "")),
            "veto_recommendation": bool(p.get("veto_recommendation", False)),
            "veto_reason": p.get("veto_reason"),
            "downgrade_suggestion": p.get("downgrade_suggestion"),
        }

    def post_process(self, ctx: AgentContext, response: dict[str, Any]) -> dict[str, Any]:
        """Hard rule: any red light must set escalation_required."""
        payload = response.get("payload") or {}
        if payload.get("status") == "red":
            response["escalation_required"] = True
        if payload.get("veto_recommendation"):
            response["escalation_required"] = True
        return response
