"""Daily data pipeline — fetches, computes indicators, writes JSON snapshots.

This is the deterministic Phase A of the daily workflow.
Runs before any LLM call so agents have fresh data to reason about.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from .connectors import get_connector
from .connectors.base import NormalizedData
from .indicators import metals as metals_ind
from .indicators import rates as rates_ind
from .indicators import technical as tech_ind

log = logging.getLogger(__name__)


class DataPipeline:
    """Runs the full daily data fetch + indicator calculation."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or (settings.FRONTEND_PUBLIC / "data")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, target_date: Optional[date] = None) -> dict[str, Any]:
        """Run the full pipeline and write snapshots.

        Returns a dict of all normalized data + indicators, keyed by asset group.
        """
        target_date = target_date or datetime.now(timezone.utc).date()
        log.info("Pipeline run for %s", target_date.isoformat())

        # Lookback window for indicator computation
        start = target_date - timedelta(days=90)

        results: dict[str, Any] = {
            "date": target_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "spot": {},
            "yields": {},
            "indicators": {},
            "macro_context": {},
            "_meta": {"lookback_days": 90},
        }

        # === Fetch gold-api spot (XAU, XAG) ===
        gold_api = get_connector("gold-api")
        try:
            spot_data = await gold_api.fetch_and_normalize(["XAU", "XAG"], start, target_date)
            for nd in spot_data:
                latest = nd.latest()
                if latest:
                    results["spot"][nd.symbol] = {
                        "price": latest["value"],
                        "unit": nd.unit,
                        "as_of": latest.get("timestamp", latest["date"]),
                        "source": "gold-api",
                    }
        except Exception as e:
            log.warning("gold-api fetch failed: %s", e)
        finally:
            await gold_api.close()

        # === Fetch Treasury yields (nominal + real) ===
        treasury = get_connector("treasury-gov")
        try:
            ylds = await treasury.fetch_and_normalize(["ALL"], start, target_date)
            nominal_obs: dict[str, list[dict]] = {}
            real_obs: dict[str, list[dict]] = {}
            for nd in ylds:
                is_real = nd.meta.get("is_real", False)
                bucket = real_obs if is_real else nominal_obs
                for obs in nd.observations:
                    sym = obs["symbol"]
                    bucket.setdefault(sym, []).append(obs)

            # Sort each series by date
            for bucket in (nominal_obs, real_obs):
                for sym in bucket:
                    bucket[sym].sort(key=lambda o: o["date"])

            # Latest yields
            latest_yields: dict[str, float] = {}
            for sym, series in nominal_obs.items():
                if series:
                    latest_yields[sym] = float(series[-1]["value"])
            results["yields"]["nominal"] = latest_yields

            latest_real: dict[str, float] = {}
            for sym, series in real_obs.items():
                if series:
                    latest_real[sym] = float(series[-1]["value"])
            results["yields"]["real"] = latest_real

            # Term spreads
            if "10Y" in latest_yields and "2Y" in latest_yields:
                results["indicators"]["spread_10y_2y"] = round(
                    rates_ind.term_spread(latest_yields["10Y"], latest_yields["2Y"]), 3
                )
            if "10Y" in latest_yields and "3M" in latest_yields:
                results["indicators"]["spread_10y_3m"] = round(
                    rates_ind.term_spread(latest_yields["10Y"], latest_yields["3M"]), 3
                )

            # Curve shape
            spreads = {
                "10Y_2Y": results["indicators"].get("spread_10y_2y"),
                "10Y_3M": results["indicators"].get("spread_10y_3m"),
            }
            results["indicators"]["curve_shape"] = rates_ind.curve_shape(
                {k: v for k, v in spreads.items() if v is not None}
            )

            # === Gold-silver ratio ===
            if "XAU" in results["spot"] and "XAG" in results["spot"]:
                results["indicators"]["gold_silver_ratio"] = round(
                    metals_ind.gold_silver_ratio(
                        [results["spot"]["XAU"]["price"]],
                        [results["spot"]["XAG"]["price"]],
                    ) or 0.0,
                    2,
                )

            # === Real-rate regression (gold vs 10Y TIPS) ===
            # We need history; use nominal_obs for gold price history if available, else
            # fall back to spot-only stub. gold-api doesn't provide history, so we build
            # a short series from what we have plus treasury observations aligned by date.
            tips_10y_series = real_obs.get("10Y_REAL", [])
            if tips_10y_series and "XAU" in results["spot"]:
                # With only spot available, we can't run a real regression.
                # Emit a placeholder with the current single-point relationship.
                results["indicators"]["gold_real_rate_note"] = (
                    "Historical regression requires Yahoo Finance proxy (Phase 3+). "
                    "Current 10Y TIPS = "
                    f"{latest_real.get('10Y_REAL', 'n/a')}%, gold spot = "
                    f"{results['spot']['XAU']['price']} USD/oz."
                )
                results["indicators"]["gold_real_rate_correlation_60d"] = None

            # === Macro context snapshot ===
            results["macro_context"] = {
                "tips_10y": latest_real.get("10Y_REAL"),
                "ust_10y": latest_yields.get("10Y"),
                "ust_2y": latest_yields.get("2Y"),
                "ust_30y": latest_yields.get("30Y"),
                "curve_shape": results["indicators"].get("curve_shape"),
            }
        except Exception as e:
            log.warning("treasury-gov fetch failed: %s", e)
        finally:
            await treasury.close()

        # === Write snapshot ===
        out_path = self.output_dir / f"{target_date.isoformat()}_metals_rates.json"
        # Also write "latest.json" for frontend convenience
        latest_path = self.output_dir / "latest.json"

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)

        log.info("Snapshot written: %s and %s", out_path, latest_path)
        results["_meta"]["snapshot_path"] = str(out_path)
        results["_meta"]["latest_path"] = str(latest_path)
        return results


async def run_pipeline(target_date: Optional[date] = None) -> dict[str, Any]:
    """Convenience wrapper."""
    pipeline = DataPipeline()
    return await pipeline.run(target_date=target_date)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_pipeline())
