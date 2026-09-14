"""Daily post-close pipeline runner — CLI entry point.

Usage:
    python -m agent_engine.scripts.daily_run            # run for today
    python -m agent_engine.scripts.daily_run --date 2026-09-05
    python -m agent_engine.scripts.daily_run --skip-data   # reuse last snapshot
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from ..agents.registry import get_registry
from ..config import settings
from ..orchestrator.workflow import Orchestrator
from ..output.archivist import get_archivist
from ..output.writer import OutputWriter

log = logging.getLogger("daily_run")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FinPanel daily post-close pipeline")
    p.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD (default: today UTC)")
    p.add_argument("--skip-data", action="store_true", help="Reuse existing /public/data/latest.json")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    target_date: date | None = None
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    log.info("FinPanel daily run starting for %s", target_date or "today (UTC)")

    registry = get_registry()
    log.info("Registered agents: %s", [c.agent_id for c in registry.all_configs()])

    orchestrator = Orchestrator()
    writer = OutputWriter()
    archivist = get_archivist()

    market_data = None
    if args.skip_data:
        latest = settings.FRONTEND_PUBLIC / "data" / "latest.json"
        if latest.exists():
            with open(latest, "r", encoding="utf-8") as f:
                market_data = json.load(f)
            log.info("Reusing cached market data from %s", latest)
        else:
            log.warning("--skip-data given but no cached snapshot; running full pipeline")

    result = await orchestrator.run_daily_review(target_date=target_date)

    log.info("Task %s finished with status=%s", result.task.task_id, result.task.status.value)
    if result.errors:
        log.error("Task errors: %s", result.errors)

    # Publish org.json (registry may have been lazy-loaded)
    org_agents = [c.model_dump(mode="json") for c in registry.all_configs()]
    dept_map: dict[str, dict] = {}
    for c in registry.all_configs():
        d = dept_map.setdefault(c.department, {"id": c.department, "agents": []})
        d["agents"].append(c.agent_id)
    writer.write_agent_registry(org_agents, list(dept_map.values()))

    # Build lobby snapshot
    lobby = {
        "firm_name": settings.FIRM_NAME,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "date": (target_date or datetime.utcnow().date()).isoformat(),
        "cio_main_line": "",
        "risk_status": "unknown",
        "latest_memo_id": None,
        "latest_task_id": result.task.task_id,
        "departments": list(dept_map.values()),
        "agents": [
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
            for c in registry.all_configs()
        ],
        "task_summary": {
            "task_id": result.task.task_id,
            "status": result.task.status.value,
            "participants": result.task.participants,
            "assets": result.task.assets,
            "envelope_count": len(result.envelopes),
        },
    }

    if result.memo:
        lobby["cio_main_line"] = (
            result.memo.executive_summary or result.memo.final_conclusion
        )[:400]
        lobby["latest_memo_id"] = result.memo.memo_id

    for env in result.envelopes:
        if env.agent_id == "risk-cro":
            lobby["risk_status"] = (env.payload or {}).get("status", "unknown")
            break

    manifest = writer.write_task_result(
        task=result.task,
        envelopes=result.envelopes,
        memo=result.memo,
        meeting=result.meeting,
        lobby=lobby,
    )
    log.info("Wrote %d artifacts", len(manifest))

    if result.memo:
        archivist.archive_memo(result.memo)
    if result.meeting:
        archivist.archive_meeting(result.meeting)

    # Print summary
    print()
    print("=" * 72)
    print(f"  FinPanel Daily Run — {lobby['date']}")
    print("=" * 72)
    print(f"  Task ID       : {result.task.task_id}")
    print(f"  Status        : {result.task.status.value}")
    print(f"  Participants  : {', '.join(result.task.participants)}")
    print(f"  Envelopes     : {len(result.envelopes)}")
    if result.memo:
        print(f"  Memo          : {result.memo.memo_id} — {result.memo.title}")
        print(f"  Conclusion    : {result.memo.final_conclusion[:200]}")
    print(f"  Risk status   : {lobby['risk_status']}")
    if result.envelope_timings:
        # Wall-clock total (start -> finish) vs sum of per-agent LLM time.
        # The gap shows how much parallelism + data pipeline + memo build
        # contributed vs the raw model inference budget.
        try:
            from datetime import datetime as _dt
            t0 = _dt.fromisoformat(result.started_at.replace("Z", "+00:00"))
            t1 = _dt.fromisoformat(result.completed_at.replace("Z", "+00:00"))
            wall_s = (t1 - t0).total_seconds()
        except Exception:
            wall_s = 0.0
        sum_ms = sum(result.envelope_timings.values())
        max_ms = max(result.envelope_timings.values())
        print(f"  Wall clock    : {wall_s:.1f}s")
        print(f"  LLM total     : {sum_ms / 1000:.1f}s (sum) / {max_ms / 1000:.1f}s (slowest)")
        print(f"  Per-agent     :")
        for aid, ms in sorted(
            result.envelope_timings.items(), key=lambda kv: -kv[1]
        ):
            print(f"    {aid:20s} {ms / 1000:6.1f}s")
    if result.errors:
        print(f"  Errors        : {result.errors}")
    print("=" * 72)
    print()

    return 0 if not result.errors else 1


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
