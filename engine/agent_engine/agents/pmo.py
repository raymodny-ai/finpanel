"""PMO — Project Management Officer / Chief of Staff."""

from __future__ import annotations

import json
from typing import Any

from ..models.output import OutputType
from .base import AgentContext, BaseAgent
from ._prompt_util import current_utc_iso, format_historical_memos


# Full agent roster for PMO to pick from (kept in sync with registry)
AVAILABLE_AGENTS = [
    {"agent_id": "metals-da", "department": "metals", "role": "DA", "assets": ["XAU", "XAG", "GC=F", "SI=F", "GDX"]},
    {"agent_id": "metals-qm", "department": "metals", "role": "QM", "assets": ["XAU", "XAG", "GC=F", "SI=F", "GDX"]},
    {"agent_id": "macro-strategist", "department": "macro", "role": "MACRO", "assets": ["ALL"]},
    {"agent_id": "risk-cro", "department": "risk", "role": "RA", "assets": ["ALL"]},
    {"agent_id": "cio", "department": "cio-office", "role": "CIO", "assets": ["ALL"]},
]


class PMOAgent(BaseAgent):
    output_type = OutputType.task_brief

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return f"""## 用戶提問
{ctx.user_query or "(無 — 這是排程盤後任務，直接歸類為 daily_review)"}

## 當前時間
{current_utc_iso()}

## 可用員工名單
```json
{json.dumps(AVAILABLE_AGENTS, ensure_ascii=False, indent=2)}
```

## 歷史相關 Task / Memo
{format_historical_memos(ctx.historical_memos)}

## 你的任務
按照 System Prompt Contract §5，把上述用戶提問拆解為 Task Brief：
1. task_category: 從 (asset_opinion / macro_review / risk_check / cross_asset / event_response / daily_review / historical_compare) 中選一個
2. priority: low / normal / high / urgent
3. required_departments: 涉及的部門 ID 列表
4. required_agents: 需要參與的 agent_id 列表（必須包含 cio 作為最終匯總者）
5. recommended_mode: fast（單資產、不複雜）或 meeting（跨資產、需辯論）
6. deliverables: 預期產出類型列表
7. detected_assets: 從問題中抽取的資產代碼
8. detected_topics: 問題涉及的主題標籤
9. reasoning: 一句話說明判斷理由

**切記**：
- stance 必須是 no_view（你不做方向判斷）
- 不加入任何市場觀點
- 涉及 3+ 部門 → recommended_mode = meeting
- 涉及「加槓桿」「all-in」「重倉」等激進意圖 → risk_flags 加 aggressive_intent
- 若為排程盤後任務（user_query 為空）→ category=daily_review, mode=fast, priority=normal
"""

    def output_schema_hint(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "stance": {"type": "string", "enum": ["no_view"]},
                "confidence": {"type": "number"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "escalation_required": {"type": "boolean"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "task_category": {"type": "string"},
                        "priority": {"type": "string"},
                        "required_departments": {"type": "array", "items": {"type": "string"}},
                        "required_agents": {"type": "array", "items": {"type": "string"}},
                        "recommended_mode": {"type": "string"},
                        "deliverables": {"type": "array", "items": {"type": "string"}},
                        "reasoning": {"type": "string"},
                        "detected_assets": {"type": "array", "items": {"type": "string"}},
                        "detected_topics": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["task_category", "recommended_mode", "required_agents"],
                },
            },
            "required": ["narrative", "stance", "confidence", "payload"],
        }

    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        p = response.get("payload") or {}
        if not isinstance(p, dict):
            p = {}

        agents = list(p.get("required_agents") or [])
        if "cio" not in agents:
            agents.append("cio")

        mode = str(p.get("recommended_mode", "fast")).lower()
        if mode not in ("fast", "meeting"):
            mode = "fast"

        return {
            "task_category": str(p.get("task_category", "daily_review")),
            "priority": str(p.get("priority", "normal")),
            "required_departments": list(p.get("required_departments") or []),
            "required_agents": agents,
            "recommended_mode": mode,
            "deliverables": list(p.get("deliverables") or []),
            "reasoning": str(p.get("reasoning", "")),
            "detected_assets": list(p.get("detected_assets") or []),
            "detected_topics": list(p.get("detected_topics") or []),
        }
