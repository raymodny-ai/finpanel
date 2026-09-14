"""gold-api.com connector — free spot prices for XAU, XAG, BTC.

Endpoint: https://api.gold-api.com/price/{SYMBOL}
No auth, no key, unlimited rate, CORS-open.
Response format: {"name": "Gold", "price": 2645.3, "symbol": "XAU", "updatedAt": "...", "updatedAtReadable": "..."}
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import httpx

from .base import BaseConnector, ConnectorError, NormalizedData, RawData


GOLD_API_BASE = "https://api.gold-api.com/price"

# Symbols supported by gold-api.com
SUPPORTED_SYMBOLS = {"XAU", "XAG", "BTC"}


class GoldApiConnector(BaseConnector):
    source_id = "gold-api"
    display_name = "Gold-API.com (Spot)"
    requires_proxy = False
    requires_key = False
    rate_limit_per_min = 0     # unlimited per docs

    async def fetch(self, symbols: list[str], start: date, end: date) -> list[RawData]:
        """Note: gold-api only returns current spot price, no history.

        The `start`/`end` parameters are accepted for interface compliance but
        the connector always returns the latest snapshot.
        """
        client = await self._get_client()
        results: list[RawData] = []
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for sym in symbols:
            sym_upper = sym.upper()
            if sym_upper not in SUPPORTED_SYMBOLS:
                continue
            try:
                resp = await client.get(f"{GOLD_API_BASE}/{sym_upper}")
                resp.raise_for_status()
                payload = resp.json()
                results.append(
                    RawData(
                        source_id=self.source_id,
                        fetched_at=fetched_at,
                        payload=payload,
                        symbol=sym_upper,
                        meta={"endpoint": f"{GOLD_API_BASE}/{sym_upper}"},
                    )
                )
            except httpx.HTTPError as e:
                raise ConnectorError(f"gold-api fetch failed for {sym_upper}: {e}") from e

        return results

    def normalize(self, raw: RawData) -> NormalizedData:
        payload = raw.payload or {}
        price = payload.get("price")
        updated_at = payload.get("updatedAt", raw.fetched_at)

        # Derive date portion for observation
        obs_date = updated_at[:10] if isinstance(updated_at, str) and len(updated_at) >= 10 else ""

        observations = []
        if price is not None:
            try:
                observations.append(
                    {
                        "date": obs_date or date.today().isoformat(),
                        "value": float(price),
                        "field": "spot",
                        "timestamp": updated_at,
                    }
                )
            except (TypeError, ValueError):
                pass

        unit = "USD/oz" if raw.symbol in ("XAU", "XAG") else "USD"

        return NormalizedData(
            source_id=self.source_id,
            symbol=raw.symbol,
            unit=unit,
            observations=observations,
            meta={
                "name": payload.get("name", ""),
                "updated_at_readable": payload.get("updatedAtReadable", ""),
                "snapshot_only": True,
            },
        )

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            r = await client.get(f"{GOLD_API_BASE}/XAU")
            return r.status_code == 200
        except Exception:
            return False
