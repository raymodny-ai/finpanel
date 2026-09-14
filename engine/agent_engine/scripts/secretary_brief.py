"""Corporate Secretary brief runner — CLI entry point.

Standalone counterpart to ``POST /api/secretary-brief``. Runs the Secretary
agent once, persists the envelope to ``frontend/public/briefs/``, and prints a
human-readable summary. Designed to be called by an external scheduler (e.g.
QoderWork ``qoder_cron``) each morning after the browser news cache is written.

Usage:
    python -m agent_engine.scripts.secretary_brief                 # newest news cache
    python -m agent_engine.scripts.secretary_brief --date 2026-09-14
    python -m agent_engine.scripts.secretary_brief --query "特別關注聯準會"
    python -m agent_engine.scripts.secretary_brief --max-output-tokens 2500
    python -m agent_engine.scripts.secretary_brief --verbose

Exit codes:
    0  brief produced cleanly
    1  brief produced but with errors / permission violations
    2  agent failed to produce any output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from ..agents.base import AgentContext
from ..agents.registry import get_registry
from ..config import settings
from ..data import news_cache
from ..orchestrator.permissions import PermissionViolation, validate_output_permissions
from ..output.writer import OutputWriter

log = logging.getLogger("secretary_brief")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FinPanel Corporate Secretary brief")
    p.add_argument(
        "--date",
        type=str,
        default=None,
        help="News cache date YYYY-MM-DD (default: newest available)",
    )
    p.add_argument(
        "--query",
        type=str,
        default=None,
        help="Optional user instruction passed to the secretary",
    )
    p.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Override governance.max_output_length for this run",
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    registry = get_registry()
    try:
        agent = registry.get("secretary")
    except KeyError:
        log.error("Secretary agent not registered — check agents/configs/secretary.yaml")
        return 2

    if args.max_output_tokens:
        agent.config.governance.max_output_length = args.max_output_tokens

    # Surface news cache freshness before we spend tokens on the LLM.
    snap = news_cache.read_latest(target_date=args.date)
    if not snap.items:
        log.warning(
            "No news cache available (looked in %s). Secretary will emit "
            "news_unavailable and rely on progress tracking only.",
            settings.FRONTEND_PUBLIC / "news",
        )
    else:
        log.info(
            "News cache: date=%s age=%.1fh fresh=%s items=%d source=%s",
            snap.date,
            snap.age_hours if snap.age_hours != float("inf") else -1,
            snap.is_fresh,
            len(snap.items),
            snap.source,
        )

    task_id = f"brief-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    ctx = AgentContext(
        task_id=task_id,
        task_category="daily_review",
        task_priority="normal",
        user_query=args.query,
        assets=["ALL"],
        departments=["secretary-office"],
    )

    log.info("Running secretary brief task %s ...", task_id)
    result = await agent.run(ctx)
    env = result.envelope
    errors: list[str] = []
    if result.error:
        errors.append(result.error)

    has_failure_flag = any(str(f).startswith("agent_failure") for f in (env.risk_flags or []))
    if not has_failure_flag:
        try:
            validate_output_permissions(env, agent.config.role)
        except PermissionViolation as e:
            errors.append(str(e))
            log.error("Secretary permission violation: %s", e)

    writer = OutputWriter()
    brief_path = writer.write_brief(env)
    log.info("Wrote brief -> %s", brief_path)

    payload = env.payload or {}
    top_stories = payload.get("top_stories") or []
    stale_items = payload.get("stale_items") or []

    print()
    print("=" * 72)
    print(f"  FinPanel Secretary Brief — {payload.get('period_covered') or 'n/a'}")
    print("=" * 72)
    print(f"  Task ID         : {task_id}")
    print(f"  Elapsed         : {result.elapsed_ms / 1000:.1f}s")
    print(f"  News fresh      : {payload.get('news_fresh', False)} (source={payload.get('news_source', 'none')})")
    print(f"  Top stories     : {len(top_stories)}")
    print(f"  Open tasks      : {len(payload.get('open_tasks') or [])}")
    print(f"  Stale / follow  : {len(stale_items)}")
    print(f"  Risk flags      : {', '.join(env.risk_flags) or '(none)'}")
    print(f"  Escalation      : {env.escalation_required}")
    headline = payload.get("executive_headline") or "(no executive headline)"
    print(f"  TL;DR           : {headline}")
    if top_stories:
        print("  --- Top stories ---")
        for s in top_stories[:8]:
            if isinstance(s, dict):
                mat = s.get("materiality", "?")
                src = s.get("source", "?")
                print(f"    [{mat:8s}] ({src}) {s.get('headline', '')}")
    if errors:
        print(f"  Errors          : {errors}")
    print("=" * 72)
    print()

    if has_failure_flag and not top_stories and not payload.get("open_tasks"):
        return 2
    return 0 if not errors else 1


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
