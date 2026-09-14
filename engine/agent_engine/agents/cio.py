"""CIO — Chief Investment Officer."""

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


class CIOAgent(BaseAgent):
    output_type = OutputType.cio_memo

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return f"""## 任務上下文
- Task ID: {ctx.task_id}
- Category: {ctx.task_category}
- Priority: {ctx.task_priority}
- Generated at: {current_utc_iso()}

## 用戶原始提問（若為用戶觸發）
{ctx.user_query or "(無 — 這是排程盤後任務，請產出當日公司級 Decision Memo)"}

## 市場數據快照
```json
{format_market_data_block(ctx.market_data)}
```

## 所有下屬輸出（DA / QM / Macro / Risk）
{format_prior_outputs(ctx.prior_outputs, limit=15)}

## 歷史相關 Memo
{format_historical_memos(ctx.historical_memos)}

## 你的任務
按照 System Prompt Contract §5，產出公司級 Decision Memo：

1. **title**: 一行簡潔標題（含部門與立場）
2. **executive_summary**: 3-5 句摘要
3. **main_line**: 今日主線（一段話）
4. **supporting_evidence**: 至少 3 條，每條含 agent_id + point + ref
5. **dissenting_views**: 若有反對則完整保留；若無分歧，寫一個物件 `{{"agent_id": "none", "objection": "本次無實質分歧"}}`
6. **final_conclusion**: 一段話公司立場
7. **action_items**: 具體行動清單（含 asset / action / urgency / size / condition / note）
8. **invalidation_conditions**: 至少 2 條失效條件（必填！）
9. **next_watchpoints**: 2-4 個觀察點
10. **confidence**: 加權置信度（0.4-0.8 為常見區間）

**硬性規則**：
- dissenting_views 不可為空陣列，若無分歧必須明示
- 每個結論必須有 invalidation_conditions
- 若 Risk CRO 給紅燈 → escalation_required = true，並在 final_conclusion 中說明「本次採納 Risk 否決」或「將召開 Risk Committee」
- 若所有 Agent confidence < 0.4 → 不發布強結論，只發布「觀察中」狀態
- 若為用戶提問 → Memo 必須直接回答用戶問題，不能只用內部術語
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
                        "title": {"type": "string"},
                        "executive_summary": {"type": "string"},
                        "final_conclusion": {"type": "string"},
                        "main_line": {"type": "string"},
                        "supporting_evidence": {"type": "array"},
                        "dissenting_views": {"type": "array"},
                        "action_items": {"type": "array"},
                        "invalidation_conditions": {"type": "array", "items": {"type": "string"}},
                        "next_watchpoints": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                    },
                    "required": ["title", "final_conclusion", "invalidation_conditions", "dissenting_views"],
                },
            },
            "required": ["narrative", "stance", "confidence", "payload"],
        }

    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        p = response.get("payload") or {}
        if not isinstance(p, dict):
            p = {}

        dissenting = list(p.get("dissenting_views") or [])
        if not dissenting:
            dissenting = [{"agent_id": "none", "objection": "本次無實質分歧"}]

        invalidation = list(p.get("invalidation_conditions") or [])
        if not invalidation:
            invalidation = ["(未提供 — 本 Memo 應被標記為低置信，需 CIO 補充)"]

        return {
            "title": str(p.get("title", "")).strip() or "CIO Decision Memo",
            "executive_summary": str(p.get("executive_summary", "")),
            "final_conclusion": str(p.get("final_conclusion", "")),
            "main_line": str(p.get("main_line", "")),
            "supporting_evidence": list(p.get("supporting_evidence") or []),
            "dissenting_views": dissenting,
            "action_items": list(p.get("action_items") or []),
            "invalidation_conditions": invalidation,
            "next_watchpoints": list(p.get("next_watchpoints") or []),
            "confidence": float(p.get("confidence", response.get("confidence", 0.5))),
        }

    def post_process(self, ctx: AgentContext, response: dict[str, Any]) -> dict[str, Any]:
        """Hard rule: Risk CRO red light forces escalation."""
        for prior in ctx.prior_outputs:
            if prior.get("agent_id") == "risk-cro":
                payload = prior.get("payload") or {}
                if payload.get("status") == "red" or payload.get("veto_recommendation"):
                    response["escalation_required"] = True
                    flags = list(response.get("risk_flags") or [])
                    if "risk_cro_red_light" not in flags:
                        flags.append("risk_cro_red_light")
                    response["risk_flags"] = flags
        return response
