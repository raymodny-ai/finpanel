"""Corporate Secretary — progress tracking + news digest + info correlation.

Three jobs, one artifact (SecretaryBrief):

1. **News digest** — read the past-24h news cache written by the QoderWork
   browser cron (see :mod:`agent_engine.data.news_cache`) and distill the
   market-moving items.
2. **Progress tracking** — scan ``frontend/public/tasks/*.json`` and
   ``frontend/public/memos/**/*.json`` to report open work, stale items, and
   recent decisions.
3. **Info correlation** — link news headlines to existing firm artifacts
   (memos / tasks / supported assets) so the CIO can see the connective
   tissue without asking.

Design notes:
-------------
* Stance is always ``no_view`` — the secretary never takes a market position.
* OutputType is ``secretary_brief`` — the only type SEC is allowed to emit.
* Runs on the fast/cheap model (Qwen3.8-Flash by default via ``llm_model``
  in YAML) because it is a distillation task, not a reasoning task.
* Not part of the daily-review DAG. Triggered on demand via
  ``POST /api/secretary-brief`` or the ``secretary_brief.py`` CLI, or by an
  external scheduler (e.g. QoderWork ``qoder_cron``).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..data import news_cache
from ..models.output import OutputType
from .base import EVIDENCE_REFS_SCHEMA, AgentContext, BaseAgent
from ._prompt_util import current_utc_iso

log = logging.getLogger(__name__)


# === Progress scanning ===================================================


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _age_hours(ts: str, now: datetime | None = None) -> float:
    dt = _parse_iso(ts)
    if not dt:
        return float("inf")
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def _attention_for(status: str, age_h: float, kind: str) -> str:
    """Classify a progress item as normal / follow_up / stale / blocked."""
    s = (status or "").lower()
    if s in ("failed", "blocked"):
        return "blocked"
    if kind == "task" and s not in ("archived",) and age_h > 48:
        return "stale"
    if kind == "memo" and s == "draft" and age_h > 24:
        return "follow_up"
    if kind == "task" and s in ("created", "assigned") and age_h > 12:
        return "follow_up"
    return "normal"


def scan_progress(now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """Scan ``frontend/public/tasks`` and ``memos`` for progress items.

    Returns dict with keys ``open_tasks``, ``recent_memos``, ``stale_items``.
    Each item is a plain dict matching the ``ProgressItem`` shape.
    """
    now = now or datetime.now(timezone.utc)
    root: Path = settings.FRONTEND_PUBLIC

    open_tasks: list[dict[str, Any]] = []
    tasks_dir = root / "tasks"
    if tasks_dir.exists():
        for path in sorted(tasks_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    t = json.load(f)
            except Exception as e:
                log.debug("skip unreadable task %s: %s", path, e)
                continue
            if not isinstance(t, dict) or not t.get("task_id"):
                continue
            status = str(t.get("status", ""))
            # task JSON may store status as enum-serialized string
            last_act = str(
                t.get("updated_at")
                or t.get("completed_at")
                or (t.get("status_history") or [{}])[-1].get("at", "")
                or t.get("created_at", "")
            )
            age = _age_hours(last_act, now)
            if status == "archived":
                continue
            item = {
                "task_id": t.get("task_id", ""),
                "memo_id": "",
                "kind": "task",
                "status": status,
                "title": str(t.get("user_query") or t.get("category", ""))[:120],
                "last_activity": last_act,
                "age_hours": round(age, 1) if age != float("inf") else -1.0,
                "attention": _attention_for(status, age, "task"),
            }
            open_tasks.append(item)

    recent_memos: list[dict[str, Any]] = []
    memos_dir = root / "memos"
    if memos_dir.exists():
        memo_paths = sorted(
            memos_dir.rglob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in memo_paths[:15]:
            if path.name == "latest.json":
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    m = json.load(f)
            except Exception as e:
                log.debug("skip unreadable memo %s: %s", path, e)
                continue
            if not isinstance(m, dict) or not m.get("memo_id"):
                continue
            created = str(m.get("created_at") or m.get("date", ""))
            age = _age_hours(created, now)
            recent_memos.append(
                {
                    "task_id": m.get("task_id", ""),
                    "memo_id": m.get("memo_id", ""),
                    "kind": "memo",
                    "status": str(m.get("status", "")),
                    "title": str(m.get("title", ""))[:120],
                    "last_activity": created,
                    "age_hours": round(age, 1) if age != float("inf") else -1.0,
                    "attention": _attention_for(str(m.get("status", "")), age, "memo"),
                }
            )

    stale_items = [
        x for x in (open_tasks + recent_memos)
        if x["attention"] in ("stale", "blocked", "follow_up")
    ]

    return {
        "open_tasks": open_tasks[:20],
        "recent_memos": recent_memos[:10],
        "stale_items": stale_items[:10],
    }


# === Agent ==============================================================


class SecretaryAgent(BaseAgent):
    output_type = OutputType.secretary_brief

    def build_user_prompt(self, ctx: AgentContext) -> str:
        # 1. News cache
        snapshot = news_cache.read_latest()
        news_block = snapshot.to_llm_block(max_items=25)

        # 2. Progress scan
        progress = scan_progress()
        progress_block = json.dumps(progress, ensure_ascii=False, indent=2, default=str)

        # 3. Period covered
        now = datetime.now(timezone.utc)
        period = f"{(now - timedelta(hours=24)).isoformat(timespec='seconds')} ~ {now.isoformat(timespec='seconds')}"

        # 4. Firm assets the secretary should try to correlate against
        firm_assets = ["XAU", "XAG", "GC=F", "SI=F", "GDX", "DXY", "SPX", "NDX", "UST-10Y", "TIPS-10Y", "BTC"]

        news_flag_note = (
            f"News cache age: {snapshot.age_hours:.1f}h "
            f"({'FRESH' if snapshot.is_fresh else 'STALE — flag with risk_flags:[news_stale]'}); "
            f"source: {snapshot.source}; items: {len(snapshot.items)}."
        )

        return f"""## 任務上下文
- Task ID: {ctx.task_id}
- Generated at: {current_utc_iso()}
- Brief type: daily_morning
- Period covered: {period}
- {news_flag_note}

## 過去 24 小時新聞快取（由 QoderWork browser cron 寫入）
```json
{news_block}
```

## 公司進行中的任務與近期 memo（progress tracking 素材）
```json
{progress_block}
```

## 公司關注的資產清單（用於 news ↔ asset 关联）
{json.dumps(firm_assets, ensure_ascii=False)}

## 你的任務
按照 System Prompt Contract §5，產出 SecretaryBrief：

**Job 1 — News digest**：
- `top_stories`：從新聞快取挑出 5-10 條對資本市場有实质影響的項目。每條填 headline / source / url / published_at / summary（1-2 句提煉）/ sentiment（positive/negative/neutral/mixed）/ materiality（low/medium/high/critical）/ affected_assets / affected_departments。
- `news_by_asset`：以資產為 key，列出相關 headline（每資產 ≤ 3 條）。
- `market_movers`：3-5 條一句話的市場動態總結。

**Job 2 — Progress tracking**：
- `open_tasks`：直接複製上面扫描到的 open_tasks（你可以調整 attention 欄位，但不要改 task_id）。
- `recent_memos`：直接複製上面扫描到的 recent_memos。
- `stale_items`：需要跟進的項目（attention ∈ follow_up/stale/blocked）。

**Job 3 — Info correlation**：
- `cross_references`：把 top_stories 中的每則新聞嘗試關聯到 recent_memos 或 open_tasks。每條 dict 形如 `{{"news_headline": "...", "linked_memo": "memo-...", "linked_task": "task-...", "reason": "..."}}`。若無相關 memo/task，寫 `{{"news_headline": "...", "reason": "無公司內部相關記錄"}}`。
- `follow_ups_needed`：秘書建議 CIO 採取的跟進行動（例：「XX memo 已過 review_date，建議召集 metals 部覆核」）。
- `executive_headline`：一句話 TL;DR，讓 CIO 30 秒內抓到今天重點。

**硬性規則**：
- `stance` 必須是 `no_view`（你不做方向判斷）
- 若新聞快取 `is_fresh=false` 或 items 為空 → `risk_flags` 加 `news_stale` 或 `news_unavailable`
- `confidence` 反映你對這份 brief 完整性的信心（新聞缺失時 ≤ 0.3）
- 不加入你自己的市場觀點或預測
- 若某則新聞的 materiality 為 `critical`，`escalation_required=true`

**輸出紀律**：
- narrative 總長 ≤ 800 中文字，用 Markdown 分三節（新聞 / 進度 / 關聯）
- key_points 3-5 條，每條一句話
- evidence_refs 引用 top_stories 的 url 或 memo_id 作為 source
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
                "evidence_refs": EVIDENCE_REFS_SCHEMA,
                "escalation_required": {"type": "boolean"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "brief_type": {"type": "string"},
                        "period_covered": {"type": "string"},
                        "generated_at": {"type": "string"},
                        "news_source": {"type": "string"},
                        "news_fresh": {"type": "boolean"},
                        "top_stories": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "headline": {"type": "string"},
                                    "source": {"type": "string"},
                                    "url": {"type": "string"},
                                    "published_at": {"type": "string"},
                                    "summary": {"type": "string"},
                                    "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral", "mixed"]},
                                    "materiality": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                                    "affected_assets": {"type": "array", "items": {"type": "string"}},
                                    "affected_departments": {"type": "array", "items": {"type": "string"}},
                                    "linked_memo_ids": {"type": "array", "items": {"type": "string"}},
                                    "linked_task_ids": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["headline", "source", "materiality"],
                            },
                        },
                        "news_by_asset": {"type": "object"},
                        "market_movers": {"type": "array", "items": {"type": "string"}},
                        "open_tasks": {"type": "array", "items": {"type": "object"}},
                        "recent_memos": {"type": "array", "items": {"type": "object"}},
                        "stale_items": {"type": "array", "items": {"type": "object"}},
                        "cross_references": {"type": "array", "items": {"type": "object"}},
                        "follow_ups_needed": {"type": "array", "items": {"type": "string"}},
                        "executive_headline": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["brief_type", "period_covered", "top_stories", "executive_headline"],
                },
            },
            "required": ["narrative", "stance", "confidence", "payload"],
        }

    def parse_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        p = response.get("payload") or {}
        if not isinstance(p, dict):
            p = {}

        # Merge deterministic progress scan into whatever the LLM returned.
        # The LLM is asked to preserve our scan; this is a safety net.
        scan = scan_progress()

        def _pick(key: str, fallback: list) -> list:
            v = p.get(key)
            if isinstance(v, list) and v:
                return v
            return fallback

        return {
            "brief_type": str(p.get("brief_type", "daily_morning")),
            "period_covered": str(p.get("period_covered", "")),
            "generated_at": str(p.get("generated_at", current_utc_iso())),
            "news_source": str(p.get("news_source", "cache")),
            "news_fresh": bool(p.get("news_fresh", False)),
            "top_stories": list(p.get("top_stories") or []),
            "news_by_asset": dict(p.get("news_by_asset") or {}),
            "market_movers": list(p.get("market_movers") or []),
            "open_tasks": _pick("open_tasks", scan["open_tasks"]),
            "recent_memos": _pick("recent_memos", scan["recent_memos"]),
            "stale_items": _pick("stale_items", scan["stale_items"]),
            "cross_references": list(p.get("cross_references") or []),
            "follow_ups_needed": list(p.get("follow_ups_needed") or []),
            "executive_headline": str(p.get("executive_headline", "")),
            "confidence": float(p.get("confidence", response.get("confidence", 0.5))),
        }

    def post_process(self, ctx: AgentContext, response: dict[str, Any]) -> dict[str, Any]:
        """Deterministic guards the LLM cannot be trusted to enforce."""
        payload = response.get("payload") or {}

        # Guard 1: news freshness
        snapshot = news_cache.read_latest()
        payload["news_fresh"] = snapshot.is_fresh
        payload["news_source"] = snapshot.source if snapshot.items else "none"
        if not snapshot.period_covered:
            now = datetime.now(timezone.utc)
            payload["period_covered"] = (
                f"{(now - timedelta(hours=24)).isoformat(timespec='seconds')} ~ "
                f"{now.isoformat(timespec='seconds')}"
            )
        else:
            payload["period_covered"] = snapshot.period_covered

        flags = list(response.get("risk_flags") or [])
        if not snapshot.items:
            if "news_unavailable" not in flags:
                flags.append("news_unavailable")
        elif not snapshot.is_fresh:
            if "news_stale" not in flags:
                flags.append("news_stale")

        # Guard 2: any critical-materiality story → escalate
        for story in payload.get("top_stories", []):
            if isinstance(story, dict) and str(story.get("materiality", "")).lower() == "critical":
                response["escalation_required"] = True
                if "critical_news_present" not in flags:
                    flags.append("critical_news_present")
                break

        response["risk_flags"] = flags
        response["payload"] = payload
        # Stance is always no_view for SEC
        response["stance"] = "no_view"
        return response
