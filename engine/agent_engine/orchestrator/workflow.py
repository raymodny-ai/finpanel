"""Workflow Orchestrator — the deterministic 'COO' of the firm.

Runs Tasks through the state machine:
    created -> assigned -> collecting_facts -> generating_opinions
      -> risk_review -> meeting_optional -> cio_summary -> archived

Enforces permission rules after every agent output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from ..agents.base import AgentContext, BaseAgent
from ..agents.registry import AgentRegistry, get_registry
from ..config import settings
from ..data.pipeline import DataPipeline
from ..models.decision import ActionItem, DecisionMemo, MemoStatus
from ..models.meeting import Meeting, MeetingRound, MeetingType
from ..models.output import AgentOutputEnvelope, OutputType, Stance
from ..models.task import (
    Task,
    TaskCategory,
    TaskPriority,
    TaskStatus,
    TriggerSource,
    WorkflowMode,
)
from .permissions import (
    PermissionViolation,
    validate_memo_hard_rules,
    validate_output_permissions,
    validate_risk_escalation,
)

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


@dataclass
class TaskResult:
    task: Task
    envelopes: list[AgentOutputEnvelope] = field(default_factory=list)
    memo: Optional[DecisionMemo] = None
    meeting: Optional[Meeting] = None
    market_data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    envelope_timings: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.model_dump(mode="json"),
            "envelopes": [e.model_dump(mode="json") for e in self.envelopes],
            "memo": self.memo.model_dump(mode="json") if self.memo else None,
            "meeting": self.meeting.model_dump(mode="json") if self.meeting else None,
            "errors": self.errors,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class Orchestrator:
    """Deterministic workflow scheduler for the firm."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        pipeline: Optional[DataPipeline] = None,
    ):
        self.registry = registry or get_registry()
        self.pipeline = pipeline or DataPipeline()
        # Per-execute_task memo cache. _load_recent_memos scans up to 30 JSON
        # files and json.dumps each for text search; without caching we redo
        # that work for every agent (5x for daily_run). Reset each task so
        # newly-published memos are picked up on the next run.
        self._memo_cache: dict[tuple, list[dict]] = {}

    def _get_recent_memos_cached(
        self, task_id: str, assets: list[str], limit: int = 3
    ) -> list[dict]:
        key = (task_id, tuple(sorted(a.upper() for a in assets)), limit)
        cached = self._memo_cache.get(key)
        if cached is None:
            cached = _load_recent_memos(assets, limit=limit)
            self._memo_cache[key] = cached
            log.debug(
                "memo cache MISS task=%s assets=%s -> %d memos",
                task_id, list(assets), len(cached),
            )
        else:
            log.debug(
                "memo cache HIT  task=%s assets=%s -> %d memos",
                task_id, list(assets), len(cached),
            )
        return cached

    # === Public entry points ===========================================

    async def run_daily_review(self, target_date: Optional[date] = None) -> TaskResult:
        """Kick off the daily post-close pipeline (data + all agents)."""
        target_date = target_date or datetime.now(timezone.utc).date()
        task = Task(
            task_id=_new_id("task-daily"),
            user_query=None,
            category=TaskCategory.daily_review,
            priority=TaskPriority.normal,
            workflow_mode=WorkflowMode.fast,
            trigger_source=TriggerSource.daily_cron,
            participants=["metals-da", "metals-qm", "macro-strategist", "risk-cro", "cio"],
            departments=["metals", "macro", "risk", "cio-office"],
            assets=["XAU", "XAG"],
        )
        return await self.execute_task(task, target_date=target_date)

    async def run_user_query(self, user_query: str) -> TaskResult:
        """Kick off a user-triggered task: PMO parses -> Orchestrator executes."""
        task = Task(
            task_id=_new_id("task-user"),
            user_query=user_query,
            category=TaskCategory.asset_opinion,      # provisional; PMO may revise
            priority=TaskPriority.normal,
            workflow_mode=WorkflowMode.fast,
            trigger_source=TriggerSource.user_query,
            assets=[],
        )
        task.transition(TaskStatus.assigned, note="awaiting PMO triage")

        # PMO triage
        pmo = self.registry.get("pmo")
        pmo_ctx = AgentContext(
            task_id=task.task_id,
            task_category="triage",
            user_query=user_query,
        )
        pmo_result = await pmo.run(pmo_ctx)
        brief = pmo_result.envelope.payload or {}

        # Apply PMO decisions to the Task
        try:
            task.category = TaskCategory(brief.get("task_category", "asset_opinion"))
        except ValueError:
            pass
        try:
            task.priority = TaskPriority(brief.get("priority", "normal"))
        except ValueError:
            pass
        task.workflow_mode = (
            WorkflowMode.meeting
            if brief.get("recommended_mode") == "meeting"
            else WorkflowMode.fast
        )
        task.participants = list(brief.get("required_agents") or task.participants)
        task.departments = list(brief.get("required_departments") or task.departments)
        task.assets = list(brief.get("detected_assets") or task.assets)
        task.status = TaskStatus.created       # reset for execute_task
        task.status_history.append(
            {"from": "assigned", "to": "created", "at": _utc_now_iso(), "note": "post-PMO reset"}
        )

        return await self.execute_task(task, seed_envelopes=[pmo_result.envelope])

    # === Core executor =================================================

    async def execute_task(
        self,
        task: Task,
        target_date: Optional[date] = None,
        seed_envelopes: Optional[list[AgentOutputEnvelope]] = None,
        market_data: Optional[dict] = None,
    ) -> TaskResult:
        result = TaskResult(task=task, started_at=_utc_now_iso())
        if seed_envelopes:
            result.envelopes.extend(seed_envelopes)

        # Reset per-task memo cache so newly-published memos from previous
        # runs are picked up but agents within THIS run share one scan.
        self._memo_cache.clear()

        try:
            # 1. Data pipeline
            if market_data is None:
                market_data = await self.pipeline.run(target_date=target_date)
            result.market_data = market_data

            # 2. Transition to assigned if still created
            if task.status == TaskStatus.created:
                task.transition(TaskStatus.assigned, note="orchestrator picked up")

            # 3. Determine participants
            participants = self._resolve_participants(task)
            task.participants = participants

            # 4. collecting_facts + macro context in parallel.
            # MACRO reads market_data directly and has NO genuine dependency
            # on DA output, so it belongs at Level 0. QM however reads DA's
            # fact_brief (see agents/desks/metals_qm.py:36) and must wait.
            task.transition(
                TaskStatus.collecting_facts,
                note="DA + Macro gathering facts / regime in parallel",
            )
            level0_envelopes = await self._run_role_group(
                task=task,
                role_filter=("DA", "MACRO"),
                participants=participants,
                market_data=market_data,
                prior=result.envelopes,
                timings_sink=result.envelope_timings,
            )
            result.envelopes.extend(level0_envelopes)

            # 5. generating_opinions (QM only -- depends on DA's fact_brief)
            task.transition(TaskStatus.generating_opinions, note="QM generating quantitative view")
            opinion_envelopes = await self._run_role_group(
                task=task,
                role_filter=("QM",),
                participants=participants,
                market_data=market_data,
                prior=result.envelopes,
                timings_sink=result.envelope_timings,
            )
            result.envelopes.extend(opinion_envelopes)

            # 6. risk_review
            task.transition(TaskStatus.risk_review, note="Risk CRO reviewing")
            if "risk-cro" in participants:
                risk_env = await self._run_single(
                    task=task,
                    agent_id="risk-cro",
                    market_data=market_data,
                    prior=result.envelopes,
                    timings_sink=result.envelope_timings,
                )
                if risk_env:
                    result.envelopes.append(risk_env)

            # 7. meeting_optional
            needs_meeting = validate_risk_escalation(result.envelopes) or task.workflow_mode == WorkflowMode.meeting
            if needs_meeting:
                task.transition(TaskStatus.meeting_optional, note="debate/meeting triggered")
                meeting = await self._convene_meeting(task, result.envelopes, market_data)
                result.meeting = meeting
            else:
                # Skip meeting state (transition table allows risk_review -> cio_summary)
                pass

            # 8. cio_summary
            task.transition(TaskStatus.cio_summary, note="CIO synthesizing")
            if "cio" in participants:
                cio_env = await self._run_single(
                    task=task,
                    agent_id="cio",
                    market_data=market_data,
                    prior=result.envelopes,
                    user_query=task.user_query,
                    timings_sink=result.envelope_timings,
                )
                if cio_env:
                    result.envelopes.append(cio_env)
                    result.memo = self._build_memo_from_envelope(task, cio_env)

            # 9. archived
            task.transition(TaskStatus.archived, note="task complete")

        except Exception as e:
            log.exception("Task %s failed: %s", task.task_id, e)
            result.errors.append(f"{type(e).__name__}: {e}")
            if task.status != TaskStatus.failed:
                try:
                    task.transition(TaskStatus.failed, note=str(e))
                except ValueError:
                    task.status = TaskStatus.failed
                    task.error = str(e)

        result.completed_at = _utc_now_iso()
        return result

    # === Internal helpers ==============================================

    def _resolve_participants(self, task: Task) -> list[str]:
        """Ensure a sensible default participant list."""
        if task.participants:
            # Always include CIO
            if "cio" not in task.participants:
                task.participants.append("cio")
            return list(task.participants)

        # Default: precious metals MVP crew
        return ["metals-da", "metals-qm", "macro-strategist", "risk-cro", "cio"]

    async def _run_role_group(
        self,
        task: Task,
        role_filter: str | tuple[str, ...],
        participants: list[str],
        market_data: dict,
        prior: list[AgentOutputEnvelope],
        timings_sink: Optional[dict[str, int]] = None,
    ) -> list[AgentOutputEnvelope]:
        """Run all agents whose role matches the filter, in parallel."""
        if isinstance(role_filter, str):
            role_filter = (role_filter,)

        targets: list[BaseAgent] = []
        for agent_id in participants:
            try:
                agent = self.registry.get(agent_id)
            except KeyError:
                log.warning("Participant %s not registered; skipping", agent_id)
                continue
            if agent.config.role.value in role_filter:
                targets.append(agent)

        if not targets:
            return []

        tasks = [
            self._run_agent_safe(
                agent=agent,
                task=task,
                market_data=market_data,
                prior=list(prior),
                timings_sink=timings_sink,
            )
            for agent in targets
        ]
        # return_exceptions=True so one sibling's unexpected (non-LLMError)
        # crash does not cancel the whole group; LLMError is already caught
        # inside BaseAgent.run and turned into an empty envelope.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[AgentOutputEnvelope] = []
        for agent, r in zip(targets, results):
            if isinstance(r, BaseException):
                log.error(
                    "Agent %s raised unexpected exception: %s: %s",
                    agent.config.agent_id, type(r).__name__, r,
                )
                continue
            if r is not None:
                out.append(r)
        return out

    async def _run_single(
        self,
        task: Task,
        agent_id: str,
        market_data: dict,
        prior: list[AgentOutputEnvelope],
        user_query: Optional[str] = None,
        timings_sink: Optional[dict[str, int]] = None,
    ) -> Optional[AgentOutputEnvelope]:
        try:
            agent = self.registry.get(agent_id)
        except KeyError:
            log.warning("Agent %s not registered", agent_id)
            return None
        return await self._run_agent_safe(
            agent=agent,
            task=task,
            market_data=market_data,
            prior=list(prior),
            user_query=user_query,
            timings_sink=timings_sink,
        )

    async def _run_agent_safe(
        self,
        agent: BaseAgent,
        task: Task,
        market_data: dict,
        prior: list[AgentOutputEnvelope],
        user_query: Optional[str] = None,
        timings_sink: Optional[dict[str, int]] = None,
    ) -> Optional[AgentOutputEnvelope]:
        ctx = AgentContext(
            task_id=task.task_id,
            task_category=task.category.value,
            task_priority=task.priority.value,
            user_query=user_query if user_query is not None else task.user_query,
            assets=list(task.assets),
            departments=list(task.departments),
            market_data=market_data,
            prior_outputs=[e.model_dump(mode="json") for e in prior],
            historical_memos=self._get_recent_memos_cached(task.task_id, task.assets, limit=3),
        )

        run_result = await agent.run(ctx)
        envelope = run_result.envelope

        if timings_sink is not None:
            timings_sink[agent.config.agent_id] = run_result.elapsed_ms
        log.info(
            "Agent %s finished in %d ms (stance=%s conf=%.2f infra_fail=%s)",
            agent.config.agent_id,
            run_result.elapsed_ms,
            envelope.stance.value,
            envelope.confidence,
            bool(run_result.error),
        )

        # Enforce permissions -- but skip for infrastructure-failure envelopes.
        # Those come from _empty_envelope (timeout / LLM error) with a
        # role-appropriate fallback stance already chosen; running them through
        # validate_output_permissions masks the real cause behind a synthetic
        # "stance_not_allowed" violation.
        is_infra_failure = any(
            f.startswith("agent_failure:") for f in envelope.risk_flags
        )
        if not is_infra_failure:
            try:
                validate_output_permissions(envelope, agent.config.role)
            except PermissionViolation as e:
                log.error("Permission violation blocked: %s", e)
                envelope.risk_flags.append(f"permission_violation:{e.rule}")
                envelope.narrative = (
                    f"[BLOCKED BY PERMISSIONS] {e.rule}. "
                    f"The agent attempted an output outside its authorized scope. "
                    f"Original narrative suppressed."
                )
                envelope.payload = {}
                envelope.confidence = 0.0
                envelope.stance = Stance.no_view

        return envelope

    async def _convene_meeting(
        self,
        task: Task,
        prior_envelopes: list[AgentOutputEnvelope],
        market_data: dict,
    ) -> Meeting:
        """Simple round-robin meeting — MVP version.

        Full debate logic (multi-round challenge/response) lands in Phase 2.
        For Phase 1 we simply collect a summary round from each participant.
        """
        meeting = Meeting(
            meeting_id=_new_id("mtg"),
            task_id=task.task_id,
            meeting_type=(
                MeetingType.risk_committee
                if validate_risk_escalation(prior_envelopes)
                else MeetingType.desk_review
            ),
            title=f"Meeting for task {task.task_id}",
            participants=list(task.participants),
        )

        round_no = 1
        for env in prior_envelopes:
            meeting.rounds.append(
                MeetingRound(
                    round_number=round_no,
                    speaker_agent_id=env.agent_id,
                    speaker_display_name=self._display_name(env.agent_id),
                    output_type=env.output_type.value,
                    narrative=env.narrative[:2000],
                    structured_output=env.model_dump(mode="json"),
                )
            )
            round_no += 1

        meeting.summary = (
            f"Meeting convened due to "
            f"{'Risk CRO red light' if validate_risk_escalation(prior_envelopes) else 'complex task mode'}. "
            f"{len(prior_envelopes)} participants contributed."
        )
        meeting.concluded_at = _utc_now_iso()
        return meeting

    def _display_name(self, agent_id: str) -> str:
        try:
            return self.registry.get_config(agent_id).display_name
        except KeyError:
            return agent_id

    def _build_memo_from_envelope(
        self, task: Task, cio_envelope: AgentOutputEnvelope
    ) -> DecisionMemo:
        payload = cio_envelope.payload or {}
        action_items: list[ActionItem] = []
        for raw in payload.get("action_items") or []:
            if not isinstance(raw, dict):
                continue
            try:
                action_items.append(
                    ActionItem(
                        asset=str(raw.get("asset", "")),
                        action=str(raw.get("action", "watch")),
                        urgency=str(raw.get("urgency", "monitoring")),
                        size=raw.get("size"),
                        condition=raw.get("condition"),
                        note=str(raw.get("note", "")),
                    )
                )
            except Exception as e:
                log.warning("Skipped malformed action_item: %s", e)

        hard_rule_issues = validate_memo_hard_rules(payload)
        status = MemoStatus.published if not hard_rule_issues else MemoStatus.draft

        review_date_default = (
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone()
            .strftime("%Y-%m-%d")
        )

        memo = DecisionMemo(
            memo_id=_new_id("memo"),
            task_id=task.task_id,
            date=_today(),
            title=str(payload.get("title") or f"CIO Memo for {task.task_id}"),
            owner_agent_id="cio",
            participating_depts=list(task.departments),
            participating_agents=list(task.participants),
            executive_summary=str(payload.get("executive_summary", "")),
            supporting_evidence=list(payload.get("supporting_evidence") or []),
            dissenting_views=list(payload.get("dissenting_views") or []),
            final_conclusion=str(payload.get("final_conclusion", "")),
            action_items=action_items,
            invalidation_conditions=list(payload.get("invalidation_conditions") or []),
            next_watchpoints=list(payload.get("next_watchpoints") or []),
            review_date=str(payload.get("review_date", review_date_default)),
            confidence=float(payload.get("confidence", cio_envelope.confidence)),
            status=status,
        )
        if hard_rule_issues:
            log.warning(
                "Memo %s failed hard-rule checks: %s (published as draft)",
                memo.memo_id,
                hard_rule_issues,
            )
        return memo


# === Simple JSON-based memo retrieval ====================================


def _load_recent_memos(assets: list[str], limit: int = 3) -> list[dict]:
    """Load recent memos from frontend/public/memos/ that mention any of the assets.

    This is a Phase-1 stand-in for ChromaDB RAG (Phase 3+).
    """
    memos_dir = settings.FRONTEND_PUBLIC / "memos"
    if not memos_dir.exists():
        return []

    all_memos: list[dict] = []
    for path in sorted(memos_dir.rglob("*.json"), reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                m = json.load(f)
            if isinstance(m, dict) and m.get("memo_id"):
                all_memos.append(m)
        except Exception:
            continue
        if len(all_memos) >= 30:
            break

    if not assets:
        return all_memos[:limit]

    asset_upper = {a.upper() for a in assets}
    matched: list[dict] = []
    for m in all_memos:
        text = json.dumps(m, ensure_ascii=False).upper()
        if any(a in text for a in asset_upper):
            matched.append(m)
        if len(matched) >= limit:
            break
    return matched
