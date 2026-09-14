"""Metals-DA — Precious Metals Data & News Analyst."""

from __future__ import annotations

from typing import Any

from ...models.output import OutputType
from ..base import AgentContext, BaseAgent
from .._prompt_util import (
    current_utc_iso,
    format_historical_memos,
    format_market_data_block,
    format_prior_outputs,
)


class MetalsDAAgent(BaseAgent):
    output_type = OutputType.fact_brief

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return f"""## 任務上下文
- Task ID: {ctx.task_id}
- Category: {ctx.task_category}
- Priority: {ctx.task_priority}
- Assets in scope: {", ".join(ctx.assets) if ctx.assets else "XAU, XAG (default)"}
- Generated at: {current_utc_iso()}

## 用戶提問（若為用戶觸發）
{ctx.user_query or "(無 — 這是排程盤後任務)"}

## 市場數據快照
```json
{format_market_data_block(ctx.market_data)}
```

## 歷史相關 Memo
{format_historical_memos(ctx.historical_memos)}

## 你的任務
按照 System Prompt Contract 中 §5 定義的輸出格式，產出：
1. 數據摘要（現貨價、金銀比、TIPS 10Y、曲線形態）
2. 3-5 條關鍵事實
3. 異常與缺口
4. 相關新聞關聯（若無新聞數據，明說）
5. 2-3 個可支撐的後續問題

**切記**：
- stance 只能是 neutral 或 no_view（你不做方向判斷）
- 每個關鍵事實必須有 DataRef
- 數據缺口必須在 gaps 中明示
"""

    def output_schema_hint(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "stance": {"type": "string", "enum": ["neutral", "no_view"]},
                "confidence": {"type": "number"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "indicator": {"type": "string"},
                            "value": {},
                            "date": {"type": "string"},
                            "unit": {"type": "string"},
                        },
                    },
                },
                "escalation_required": {"type": "boolean"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "data_time_range": {"type": "string"},
                        "data_sources": {"type": "array", "items": {"type": "string"}},
                        "anomalies": {"type": "array"},
                        "gaps": {"type": "array", "items": {"type": "string"}},
                        "news_relevance": {"type": "array"},
                        "key_facts": {"type": "array", "items": {"type": "string"}},
                        "supported_questions": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "required": ["narrative", "stance", "confidence", "key_points", "payload"],
        }

    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        p = response.get("payload") or {}
        if not isinstance(p, dict):
            p = {}

        # Auto-populate data_sources if empty
        if not p.get("data_sources"):
            p["data_sources"] = ["gold-api", "treasury-gov"]

        return {
            "data_time_range": str(p.get("data_time_range", "")),
            "data_sources": list(p.get("data_sources", [])),
            "anomalies": list(p.get("anomalies", [])),
            "gaps": list(p.get("gaps", [])),
            "news_relevance": list(p.get("news_relevance", [])),
            "key_facts": list(p.get("key_facts", [])) or list(response.get("key_points", [])),
            "supported_questions": list(p.get("supported_questions", [])),
        }

    def post_process(self, ctx: AgentContext, response: dict[str, Any]) -> dict[str, Any]:
        """Auto-inject DataRefs from market_data if the LLM missed them."""
        md = ctx.market_data or {}
        spot = md.get("spot", {}) if isinstance(md, dict) else {}
        yields = md.get("yields", {}) if isinstance(md, dict) else {}
        existing = response.get("evidence_refs") or []

        if not existing:
            today = md.get("date", "")
            if "XAU" in spot:
                existing.append({
                    "source": "gold-api",
                    "indicator": "XAU_spot",
                    "value": spot["XAU"].get("price"),
                    "date": today,
                    "unit": "USD/oz",
                })
            if "XAG" in spot:
                existing.append({
                    "source": "gold-api",
                    "indicator": "XAG_spot",
                    "value": spot["XAG"].get("price"),
                    "date": today,
                    "unit": "USD/oz",
                })
            nominal = yields.get("nominal", {}) if isinstance(yields, dict) else {}
            real = yields.get("real", {}) if isinstance(yields, dict) else {}
            if "10Y" in nominal:
                existing.append({
                    "source": "treasury-gov",
                    "indicator": "UST_10Y_nominal",
                    "value": nominal["10Y"],
                    "date": today,
                    "unit": "percent",
                })
            if "10Y_REAL" in real:
                existing.append({
                    "source": "treasury-gov",
                    "indicator": "TIPS_10Y_real",
                    "value": real["10Y_REAL"],
                    "date": today,
                    "unit": "percent",
                })
            response["evidence_refs"] = existing

        return response
