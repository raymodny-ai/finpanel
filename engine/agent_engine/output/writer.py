"""OutputWriter — writes Engine artifacts into frontend/public/ as static files.

Directory layout (mirrors the frontend's expectations):
    frontend/public/
      data/{YYYY-MM-DD}_metals_rates.json
      data/latest.json
      agents/{agent_id}/latest.json
      tasks/{task_id}.json
      memos/{YYYY-MM-DD}/{memo_id}.json
      memos/latest.json
      meetings/{YYYY-MM-DD}/{meeting_id}.json
      meetings/latest.json
      lobby.json                     # aggregated snapshot for the home page
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import settings
from ..models.decision import DecisionMemo
from ..models.meeting import Meeting
from ..models.output import AgentOutputEnvelope
from ..models.task import Task

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OutputWriter:
    def __init__(self, public_root: Path | None = None):
        self.root = public_root or settings.FRONTEND_PUBLIC
        self.root.mkdir(parents=True, exist_ok=True)

    # --- Directory helpers ---------------------------------------------

    def _ensure(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        log.debug("Wrote %s", path)

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        log.debug("Wrote %s", path)

    # --- Individual artifacts ------------------------------------------

    def write_task(self, task: Task) -> Path:
        d = self._ensure("tasks")
        path = d / f"{task.task_id}.json"
        self._write_json(path, task.model_dump(mode="json"))
        return path

    def write_envelope(self, env: AgentOutputEnvelope) -> Path:
        d = self._ensure("agents", env.agent_id)
        # Latest per agent
        self._write_json(d / "latest.json", env.model_dump(mode="json"))
        # Per-task copy
        per_task = self._ensure("tasks", env.task_id, "outputs")
        self._write_json(per_task / f"{env.agent_id}.json", env.model_dump(mode="json"))
        return d / "latest.json"

    def write_memo(self, memo: DecisionMemo) -> Path:
        d = self._ensure("memos", memo.date)
        path = d / f"{memo.memo_id}.json"
        self._write_json(path, memo.model_dump(mode="json"))
        # Also refresh the "latest" pointer
        self._write_json(self.root / "memos" / "latest.json", memo.model_dump(mode="json"))
        # Markdown companion for humans
        md_path = d / f"{memo.memo_id}.md"
        self._write_text(md_path, memo_to_markdown(memo))
        return path

    def write_meeting(self, meeting: Meeting) -> Path:
        date_str = meeting.created_at[:10]
        d = self._ensure("meetings", date_str)
        path = d / f"{meeting.meeting_id}.json"
        self._write_json(path, meeting.model_dump(mode="json"))
        self._write_json(self.root / "meetings" / "latest.json", meeting.model_dump(mode="json"))
        md_path = d / f"{meeting.meeting_id}.md"
        self._write_text(md_path, meeting_to_markdown(meeting))
        return path

    def write_lobby(self, lobby: dict[str, Any]) -> Path:
        path = self.root / "lobby.json"
        self._write_json(path, lobby)
        return path

    def write_agent_registry(self, agents: list[dict[str, Any]], departments: list[dict[str, Any]]) -> Path:
        path = self.root / "org.json"
        self._write_json(path, {"agents": agents, "departments": departments, "generated_at": _utc_now_iso()})
        return path

    def write_brief(self, envelope: AgentOutputEnvelope) -> Path:
        """Persist a Secretary brief envelope.

        Layout:
            briefs/{YYYY-MM-DD}/{timestamp}_{agent_id}.json
            briefs/latest.json
        Both files contain the full envelope so the frontend can read either
        without additional joins. ``YYYY-MM-DD`` is derived from the envelope
        timestamp (UTC).
        """
        ts = envelope.timestamp or _utc_now_iso()
        date_str = ts[:10]
        # Compact timestamp for the filename, e.g. 20260908T113000Z
        stamp = ts.replace("-", "").replace(":", "").replace("+0000", "Z").replace("Z", "Z")
        d = self._ensure("briefs", date_str)
        path = d / f"{stamp}_{envelope.agent_id}.json"
        payload = envelope.model_dump(mode="json")
        self._write_json(path, payload)
        # Refresh the "latest" pointer regardless of date
        self._write_json(self.root / "briefs" / "latest.json", payload)
        return path

    def write_news_cache(
        self,
        items: list[dict[str, Any]],
        *,
        target_date: str | None = None,
        source: str = "qoderwork-browser",
        period_covered: str = "",
    ) -> Path:
        """Thin wrapper around :func:`agent_engine.data.news_cache.write_snapshot`.

        Kept here so external callers (e.g. a QoderWork cron bridge that only
        has access to the OutputWriter) can persist news without importing
        the data module directly.
        """
        from ..data import news_cache as _nc

        return _nc.write_snapshot(
            items, target_date=target_date, source=source, period_covered=period_covered
        )

    # --- Bulk ------------------------------------------------------------

    def write_task_result(
        self,
        task: Task,
        envelopes: Iterable[AgentOutputEnvelope],
        memo: DecisionMemo | None,
        meeting: Meeting | None,
        lobby: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Write everything about a finished task. Returns a manifest of paths."""
        manifest: dict[str, str] = {}
        manifest["task"] = str(self.write_task(task))
        for env in envelopes:
            self.write_envelope(env)
            manifest[f"envelope:{env.agent_id}"] = str(self.root / "agents" / env.agent_id / "latest.json")
        if memo:
            manifest["memo"] = str(self.write_memo(memo))
        if meeting:
            manifest["meeting"] = str(self.write_meeting(meeting))
        if lobby:
            manifest["lobby"] = str(self.write_lobby(lobby))
        return manifest


# === Markdown serializers =================================================


def memo_to_markdown(memo: DecisionMemo) -> str:
    lines = [
        f"# {memo.title}",
        "",
        f"**Memo ID**: `{memo.memo_id}`  ",
        f"**Date**: {memo.date}  ",
        f"**Owner**: {memo.owner_agent_id}  ",
        f"**Status**: {memo.status.value}  ",
        f"**Confidence**: {memo.confidence:.2f}  ",
        f"**Version**: v{memo.version}",
        "",
        "## Executive Summary",
        "",
        memo.executive_summary or "_(empty)_",
        "",
        "## Final Conclusion",
        "",
        memo.final_conclusion or "_(empty)_",
        "",
        "## Supporting Evidence",
        "",
    ]
    if memo.supporting_evidence:
        for ev in memo.supporting_evidence:
            if isinstance(ev, dict):
                agent = ev.get("agent_id", "?")
                point = ev.get("point", "")
                lines.append(f"- **{agent}**: {point}")
            else:
                lines.append(f"- {ev}")
    else:
        lines.append("_(none)_")

    lines += ["", "## Dissenting Views", ""]
    if memo.dissenting_views:
        for dv in memo.dissenting_views:
            if isinstance(dv, dict):
                agent = dv.get("agent_id", "?")
                obj = dv.get("objection", "")
                lines.append(f"- **{agent}**: {obj}")
            else:
                lines.append(f"- {dv}")
    else:
        lines.append("_(no dissent recorded)_")

    lines += ["", "## Action Items", ""]
    if memo.action_items:
        lines.append("| Asset | Action | Urgency | Size | Condition | Note |")
        lines.append("|-------|--------|---------|------|-----------|------|")
        for ai in memo.action_items:
            lines.append(
                f"| {ai.asset} | {ai.action} | {ai.urgency} | {ai.size or '—'} | "
                f"{ai.condition or '—'} | {ai.note or '—'} |"
            )
    else:
        lines.append("_(none)_")

    lines += ["", "## Invalidation Conditions", ""]
    for c in memo.invalidation_conditions or ["_(none)_"]:
        lines.append(f"- {c}")

    lines += ["", "## Next Watchpoints", ""]
    for w in memo.next_watchpoints or ["_(none)_"]:
        lines.append(f"- {w}")

    lines += ["", f"**Review date**: {memo.review_date}", ""]
    lines.append("---")
    lines.append(f"_Generated at {_utc_now_iso()}_")
    return "\n".join(lines)


def meeting_to_markdown(meeting: Meeting) -> str:
    lines = [
        f"# Meeting — {meeting.title or meeting.meeting_id}",
        "",
        f"**Meeting ID**: `{meeting.meeting_id}`  ",
        f"**Task**: `{meeting.task_id}`  ",
        f"**Type**: {meeting.meeting_type.value}  ",
        f"**Created**: {meeting.created_at}  ",
        f"**Concluded**: {meeting.concluded_at or '—'}",
        "",
        f"**Participants**: {', '.join(meeting.participants)}",
        "",
        "## Rounds",
        "",
    ]
    for r in meeting.rounds:
        lines += [
            f"### Round {r.round_number} — {r.speaker_display_name or r.speaker_agent_id}",
            f"_{r.output_type} · {r.timestamp}_",
            "",
            r.narrative or "_(no narrative)_",
            "",
        ]
    lines += ["## Summary", "", meeting.summary or "_(empty)_", ""]
    return "\n".join(lines)
