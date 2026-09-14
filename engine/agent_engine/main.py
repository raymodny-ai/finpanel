"""FastAPI application — exposes the Agent Engine to the frontend.

Endpoints:
    GET  /api/health             — Engine + LLM status
    GET  /api/registry           — Full agent + department registry (JSON)
    GET  /api/tasks/{task_id}    — Task snapshot
    GET  /api/memos/latest       — Most recent Decision Memo
    POST /api/query              — Run a user query through the full pipeline
    POST /api/daily-run          — Manually trigger the daily post-close run
    POST /api/secretary-brief    — Generate a Corporate Secretary brief
    GET  /api/briefs/latest      — Most recent Secretary brief envelope
    GET  /api/news/cache         — Latest news cache snapshot metadata
    POST /api/news/cache         — Overwrite today's news cache (cron bridge)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agents.base import AgentContext
from .agents.registry import get_registry
from .config import settings
from .data import news_cache
from .llm import get_llm
from .models.output import OutputType
from .orchestrator.permissions import validate_output_permissions, PermissionViolation
from .orchestrator.workflow import Orchestrator, TaskResult
from .output.archivist import get_archivist
from .output.writer import OutputWriter

log = logging.getLogger(__name__)
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


# === Schemas ==============================================================


class HealthResponse(BaseModel):
    status: str
    firm_name: str
    llm_echo_mode: bool
    llm_model: str
    agents_registered: int
    timestamp: str


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    force_mode: Optional[str] = Field(default=None, pattern="^(fast|meeting)$")


class QueryResponse(BaseModel):
    task_id: str
    status: str
    memo_id: Optional[str] = None
    memo_title: Optional[str] = None
    participants: list[str] = []
    envelopes: list[dict] = []
    errors: list[str] = []


class DailyRunResponse(BaseModel):
    task_id: str
    status: str
    memo_id: Optional[str] = None
    manifest: dict = Field(default_factory=dict)
    errors: list[str] = []


class SecretaryBriefRequest(BaseModel):
    """Trigger a Corporate Secretary brief.

    ``user_query`` is optional — the secretary runs even without one (it is
    primarily a scheduled / on-demand digest, not a Q&A). ``target_date``
    picks a specific news cache file (YYYY-MM-DD); when None the newest
    available cache is used.
    """

    user_query: Optional[str] = Field(default=None, max_length=2000)
    target_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class SecretaryBriefResponse(BaseModel):
    task_id: str
    status: str
    brief_id: Optional[str] = None
    executive_headline: str = ""
    news_fresh: bool = False
    top_stories_count: int = 0
    stale_items_count: int = 0
    risk_flags: list[str] = Field(default_factory=list)
    envelope: dict = Field(default_factory=dict)
    brief_path: Optional[str] = None
    errors: list[str] = Field(default_factory=list)


class NewsCacheWriteRequest(BaseModel):
    """Payload written by the QoderWork browser cron bridge."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    target_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    source: str = "qoderwork-browser"
    period_covered: str = ""


class NewsCacheWriteResponse(BaseModel):
    path: str
    item_count: int
    date: str


# === App factory ==========================================================


def _build_org_payload() -> dict[str, Any]:
    registry = get_registry()
    agents = [c.model_dump(mode="json") for c in registry.all_configs()]
    departments_map: dict[str, dict[str, Any]] = {}
    for c in registry.all_configs():
        d = departments_map.setdefault(
            c.department, {"id": c.department, "agents": [], "display_name": c.department}
        )
        d["agents"].append(c.agent_id)
    return {"agents": agents, "departments": list(departments_map.values())}


def _build_lobby_payload(last_result: Optional[TaskResult]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    registry = get_registry()
    lobby: dict[str, Any] = {
        "firm_name": settings.FIRM_NAME,
        "generated_at": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "departments": [],
        "cio_main_line": "",
        "risk_status": "unknown",
        "latest_memo_id": None,
        "latest_task_id": None,
        "agents": [],
    }

    for c in registry.all_configs():
        lobby["agents"].append(
            {
                "agent_id": c.agent_id,
                "display_name": c.display_name,
                "title": c.title,
                "department": c.department,
                "role": c.role.value,
                "seniority": c.seniority.value,
                "avatar": c.avatar,
                "specialty": c.specialty,
                "status": "idle",
            }
        )

    dept_ids = sorted({c.department for c in registry.all_configs()})
    for d in dept_ids:
        lobby["departments"].append(
            {"id": d, "agents": [c.agent_id for c in registry.by_department(d)]}
        )

    if last_result and last_result.memo:
        memo = last_result.memo
        payload_summary = memo.executive_summary or memo.final_conclusion
        lobby["cio_main_line"] = payload_summary[:400]
        lobby["latest_memo_id"] = memo.memo_id
        lobby["latest_task_id"] = memo.task_id

    if last_result:
        for env in last_result.envelopes:
            if env.agent_id == "risk-cro":
                lobby["risk_status"] = (env.payload or {}).get("status", "unknown")
                break

    return lobby


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("FinPanel Engine starting up")
    settings.ensure_dirs()
    # Warm the registry
    get_registry()
    yield
    log.info("FinPanel Engine shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinPanel Agent Engine",
        version="0.1.0",
        description="AI Agent virtual hedge fund company — Engine API",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    writer = OutputWriter()
    orchestrator = Orchestrator()
    archivist = get_archivist()

    # Publish org.json on startup
    try:
        org = _build_org_payload()
        writer.write_agent_registry(org["agents"], org["departments"])
    except Exception as e:
        log.warning("Failed to write org.json on startup: %s", e)

    # === Endpoints =====================================================

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        llm = get_llm()
        registry = get_registry()
        return HealthResponse(
            status="ok",
            firm_name=settings.FIRM_NAME,
            llm_echo_mode=llm.echo_mode,
            llm_model=llm.model,
            agents_registered=len(registry.all_configs()),
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    @app.get("/api/registry")
    async def registry_endpoint() -> dict[str, Any]:
        return _build_org_payload()

    @app.get("/api/memos/latest")
    async def latest_memo() -> dict[str, Any]:
        recent = archivist.recent_memos(limit=1)
        if not recent:
            raise HTTPException(status_code=404, detail="No memos archived yet")
        return recent[0]

    @app.get("/api/memos")
    async def list_memos(limit: int = 20) -> dict[str, Any]:
        return {"memos": archivist.recent_memos(limit=limit)}

    @app.post("/api/query", response_model=QueryResponse)
    async def query(req: QueryRequest) -> QueryResponse:
        try:
            result = await orchestrator.run_user_query(req.query)
        except Exception as e:
            log.exception("Query failed")
            raise HTTPException(status_code=500, detail=f"Query failed: {e}") from e

        if req.force_mode and result.task.workflow_mode.value != req.force_mode:
            log.info(
                "force_mode=%s requested but task ran as %s",
                req.force_mode,
                result.task.workflow_mode.value,
            )

        # Persist artifacts
        lobby = _build_lobby_payload(result)
        writer.write_task_result(
            task=result.task,
            envelopes=result.envelopes,
            memo=result.memo,
            meeting=result.meeting,
            lobby=lobby,
        )
        if result.memo:
            archivist.archive_memo(result.memo)
        if result.meeting:
            archivist.archive_meeting(result.meeting)

        return QueryResponse(
            task_id=result.task.task_id,
            status=result.task.status.value,
            memo_id=result.memo.memo_id if result.memo else None,
            memo_title=result.memo.title if result.memo else None,
            participants=list(result.task.participants),
            envelopes=[e.model_dump(mode="json") for e in result.envelopes],
            errors=result.errors,
        )

    @app.post("/api/daily-run", response_model=DailyRunResponse)
    async def daily_run() -> DailyRunResponse:
        try:
            result = await orchestrator.run_daily_review()
        except Exception as e:
            log.exception("Daily run failed")
            raise HTTPException(status_code=500, detail=f"Daily run failed: {e}") from e

        lobby = _build_lobby_payload(result)
        manifest = writer.write_task_result(
            task=result.task,
            envelopes=result.envelopes,
            memo=result.memo,
            meeting=result.meeting,
            lobby=lobby,
        )
        if result.memo:
            archivist.archive_memo(result.memo)
        if result.meeting:
            archivist.archive_meeting(result.meeting)

        return DailyRunResponse(
            task_id=result.task.task_id,
            status=result.task.status.value,
            memo_id=result.memo.memo_id if result.memo else None,
            manifest=manifest,
            errors=result.errors,
        )

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        task_path = settings.FRONTEND_PUBLIC / "tasks" / f"{task_id}.json"
        if not task_path.exists():
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        with open(task_path, "r", encoding="utf-8") as f:
            task_json = json.load(f)

        outputs_dir = settings.FRONTEND_PUBLIC / "tasks" / task_id / "outputs"
        outputs = []
        if outputs_dir.exists():
            for p in sorted(outputs_dir.glob("*.json")):
                with open(p, "r", encoding="utf-8") as f:
                    outputs.append(json.load(f))

        return {"task": task_json, "outputs": outputs}

    # === Corporate Secretary ===========================================

    @app.post("/api/secretary-brief", response_model=SecretaryBriefResponse)
    async def secretary_brief(req: SecretaryBriefRequest) -> SecretaryBriefResponse:
        """Run the Corporate Secretary standalone (outside the daily DAG).

        Produces one ``secretary_brief`` envelope, persists it to
        ``frontend/public/briefs/``, and returns a compact summary. This is
        the endpoint the QoderWork morning cron calls after writing the news
        cache.
        """
        registry = get_registry()
        try:
            agent = registry.get("secretary")
        except KeyError:
            raise HTTPException(status_code=500, detail="Secretary agent not registered")

        task_id = f"brief-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        errors: list[str] = []

        ctx = AgentContext(
            task_id=task_id,
            task_category="daily_review",
            task_priority="normal",
            user_query=req.user_query,
            assets=["ALL"],
            departments=["secretary-office"],
        )

        result = await agent.run(ctx)
        env = result.envelope
        if result.error:
            errors.append(result.error)

        # Enforce governance (SEC may only emit secretary_brief / no_view).
        # Skip validation when the envelope is an agent_failure placeholder so
        # the real error surfaces instead of a fake stance_not_allowed.
        has_failure_flag = any(str(f).startswith("agent_failure") for f in (env.risk_flags or []))
        if not has_failure_flag:
            try:
                validate_output_permissions(env, agent.config.role)
            except PermissionViolation as e:
                errors.append(str(e))
                log.error("Secretary permission violation: %s", e)

        brief_path = writer.write_brief(env)

        payload = env.payload or {}
        return SecretaryBriefResponse(
            task_id=task_id,
            status="completed" if not errors else "completed_with_errors",
            brief_id=brief_path.name,
            executive_headline=str(payload.get("executive_headline", "")),
            news_fresh=bool(payload.get("news_fresh", False)),
            top_stories_count=len(payload.get("top_stories") or []),
            stale_items_count=len(payload.get("stale_items") or []),
            risk_flags=list(env.risk_flags or []),
            envelope=env.model_dump(mode="json"),
            brief_path=str(brief_path),
            errors=errors,
        )

    @app.get("/api/briefs/latest")
    async def latest_brief() -> dict[str, Any]:
        path = settings.FRONTEND_PUBLIC / "briefs" / "latest.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="No secretary brief generated yet")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @app.get("/api/briefs")
    async def list_briefs(limit: int = 20) -> dict[str, Any]:
        root = settings.FRONTEND_PUBLIC / "briefs"
        if not root.exists():
            return {"briefs": []}
        paths = sorted(
            (p for p in root.rglob("*.json") if p.name != "latest.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        briefs: list[dict[str, Any]] = []
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    briefs.append(json.load(f))
            except Exception as e:
                log.debug("skip unreadable brief %s: %s", p, e)
        return {"briefs": briefs}

    # === News cache bridge (for QoderWork browser cron) =================

    @app.get("/api/news/cache")
    async def get_news_cache(target_date: Optional[str] = None) -> dict[str, Any]:
        snap = news_cache.read_latest(target_date=target_date)
        return {
            "date": snap.date,
            "generated_at": snap.generated_at,
            "source": snap.source,
            "period_covered": snap.period_covered,
            "age_hours": None if snap.age_hours == float("inf") else round(snap.age_hours, 2),
            "is_fresh": snap.is_fresh,
            "item_count": len(snap.items),
            "items": snap.items,
            "available_dates": news_cache.list_available_dates(limit=14),
        }

    @app.post("/api/news/cache", response_model=NewsCacheWriteResponse)
    async def put_news_cache(req: NewsCacheWriteRequest) -> NewsCacheWriteResponse:
        if not req.items:
            raise HTTPException(status_code=400, detail="items must not be empty")
        path = writer.write_news_cache(
            req.items,
            target_date=req.target_date,
            source=req.source,
            period_covered=req.period_covered,
        )
        date_str = req.target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return NewsCacheWriteResponse(path=str(path), item_count=len(req.items), date=date_str)

    return app


app = create_app()


if __name__ == "__main__":
    import os
    import uvicorn
    # Windows Hyper-V / WinNAT reserves large TCP port ranges (see
    # `netsh interface ipv4 show excludedportrange protocol=tcp`). The
    # default 8000 frequently falls inside 7932-8031 and fails with
    # WinError 10013. Override with ENGINE_PORT when that happens.
    port = int(os.getenv("ENGINE_PORT", "8000"))
    uvicorn.run("agent_engine.main:app", host="0.0.0.0", port=port, reload=True)
