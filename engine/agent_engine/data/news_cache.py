"""News cache reader — bridges QoderWork-driven browser scraping and the engine.

Design:
-------
The engine cannot drive `builtin_browser` (that tool lives only inside a
QoderWork session). Instead, a QoderWork-side cron job fetches financial news
each morning and writes it to a shared JSON cache under
``frontend/public/news/``. The Secretary agent then reads from that cache.

Cache contract (``frontend/public/news/YYYY-MM-DD.json``):

.. code-block:: json

    {
      "date": "2026-09-08",
      "generated_at": "2026-09-08T11:30:00Z",
      "source": "qoderwork-browser",
      "period_covered": "2026-09-07T11:30:00Z ~ 2026-09-08T11:30:00Z",
      "items": [
        {
          "headline": "Fed's Powell signals patience on rate cuts",
          "source": "Reuters",
          "url": "https://...",
          "published_at": "2026-09-08T09:15:00Z",
          "summary": "Optional 1-2 sentence distillation.",
          "sentiment": "neutral",
          "affected_assets": ["XAU", "DXY"],
          "tags": ["fed", "rates"]
        }
      ]
    }

Only ``items[].headline`` and ``items[].source`` are strictly required; every
other field has a sensible default. Files older than ``STALE_HOURS`` (24 by
default) cause the secretary to emit a ``news_stale`` risk flag.

Writers (external to this module):
  * QoderWork cron task using ``builtin_browser`` — recommended
  * Manual ``curl`` + jq pipeline
  * Any future Finnhub / RSS connector can also write this format

Readers:
  * :class:`agent_engine.agents.secretary.SecretaryAgent`
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import settings

log = logging.getLogger(__name__)

STALE_HOURS = 24


@dataclass
class NewsCacheSnapshot:
    """Result of one cache read."""

    date: str = ""                    # YYYY-MM-DD of the cache file
    generated_at: str = ""            # ISO timestamp of when cache was written
    source: str = "none"              # qoderwork-browser / manual / none
    period_covered: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    age_hours: float = float("inf")   # how old the cache is
    is_fresh: bool = False            # age < STALE_HOURS
    path: Optional[Path] = None

    def to_llm_block(self, max_items: int = 25) -> str:
        """Render as a compact JSON block for prompt injection."""
        if not self.items:
            return (
                "(no news cache available — ask QoderWork to run the morning "
                "browser fetch, or write a JSON file to "
                "frontend/public/news/YYYY-MM-DD.json following the schema in "
                "agent_engine/data/news_cache.py)"
            )
        payload = {
            "date": self.date,
            "generated_at": self.generated_at,
            "source": self.source,
            "period_covered": self.period_covered,
            "age_hours": round(self.age_hours, 1),
            "is_fresh": self.is_fresh,
            "items": self.items[:max_items],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _news_dir() -> Path:
    return settings.FRONTEND_PUBLIC / "news"


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Accept trailing Z or +HH:MM
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def read_latest(target_date: Optional[str] = None) -> NewsCacheSnapshot:
    """Read the newest cache file, or the one for ``target_date`` if given.

    ``target_date`` is ``YYYY-MM-DD``. When None, picks the file with the
    newest ``date`` in its filename.
    """
    d = _news_dir()
    if not d.exists():
        log.info("news cache dir missing: %s", d)
        return NewsCacheSnapshot()

    if target_date:
        path = d / f"{target_date}.json"
        candidates = [path] if path.exists() else []
    else:
        candidates = sorted(d.glob("*.json"), reverse=True)

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            log.warning("failed to read news cache %s: %s", path, e)
            continue

        if not isinstance(raw, dict):
            continue

        items = raw.get("items") or []
        if not isinstance(items, list):
            items = []

        generated_at = str(raw.get("generated_at", ""))
        gen_dt = _parse_iso(generated_at)
        now = datetime.now(timezone.utc)
        age_hours = ((now - gen_dt).total_seconds() / 3600.0) if gen_dt else float("inf")

        return NewsCacheSnapshot(
            date=str(raw.get("date") or path.stem),
            generated_at=generated_at,
            source=str(raw.get("source", "unknown")),
            period_covered=str(raw.get("period_covered", "")),
            items=[i for i in items if isinstance(i, dict)],
            age_hours=age_hours,
            is_fresh=age_hours <= STALE_HOURS,
            path=path,
        )

    return NewsCacheSnapshot()


def write_snapshot(
    items: list[dict[str, Any]],
    *,
    target_date: Optional[str] = None,
    source: str = "qoderwork-browser",
    period_covered: str = "",
) -> Path:
    """Persist a news snapshot. Called by the QoderWork-side cron bridge.

    ``target_date`` defaults to today (UTC). Existing file is overwritten.
    """
    d = _news_dir()
    d.mkdir(parents=True, exist_ok=True)
    date_str = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not period_covered:
        start = (datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)).isoformat(
            timespec="seconds"
        )
        period_covered = f"{start} ~ {now_iso}"

    path = d / f"{date_str}.json"
    payload = {
        "date": date_str,
        "generated_at": now_iso,
        "source": source,
        "period_covered": period_covered,
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("wrote news cache %s (%d items)", path, len(items))
    return path


def list_available_dates(limit: int = 30) -> list[str]:
    """Return available cache dates, newest first."""
    d = _news_dir()
    if not d.exists():
        return []
    dates = [p.stem for p in d.glob("*.json")]
    dates.sort(reverse=True)
    return dates[:limit]
