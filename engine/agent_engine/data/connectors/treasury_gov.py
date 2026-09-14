"""Treasury.gov connector — daily yield curve + TIPS (real yields).

Endpoints (all CORS-open, no key):
- Daily Treasury Yield Curve:
  https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM
- Daily TIPS (real long rate):
  https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value_month=YYYYMM

We parse the Atom XML feeds and produce NormalizedData for a set of tenors.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Optional

import httpx

from .base import BaseConnector, ConnectorError, NormalizedData, RawData


TREASURY_BASE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
}

# Field names in the nominal yield curve feed
NOMINAL_FIELDS = {
    "1M": "d:BC_1MONTH",
    "3M": "d:BC_3MONTH",
    "6M": "d:BC_6MONTH",
    "1Y": "d:BC_1YEAR",
    "2Y": "d:BC_2YEAR",
    "3Y": "d:BC_3YEAR",
    "5Y": "d:BC_5YEAR",
    "7Y": "d:BC_7YEAR",
    "10Y": "d:BC_10YEAR",
    "20Y": "d:BC_20YEAR",
    "30Y": "d:BC_30YEAR",
}

# Field names in the TIPS (real) yield curve feed
REAL_FIELDS = {
    "5Y_REAL": "d:DFII5",
    "7Y_REAL": "d:DFII7",
    "10Y_REAL": "d:DFII10",
    "20Y_REAL": "d:DFII20",
    "30Y_REAL": "d:DFII30",
}


class TreasuryGovConnector(BaseConnector):
    source_id = "treasury-gov"
    display_name = "US Treasury (Yield Curve + TIPS)"
    requires_proxy = False
    requires_key = False
    rate_limit_per_min = 0

    async def fetch(self, symbols: list[str], start: date, end: date) -> list[RawData]:
        """Fetch nominal + real yield curves covering the given date range.

        `symbols` is a list like ["10Y", "2Y", "10Y_REAL"] or ["ALL"].
        Treasury publishes monthly XML, so we may need multiple month fetches.
        """
        client = await self._get_client()
        results: list[RawData] = []

        months = self._month_range(start, end)
        want_real = any(s.endswith("_REAL") for s in symbols) or symbols == ["ALL"]
        want_nominal = (not want_real) or symbols == ["ALL"] or any(
            not s.endswith("_REAL") for s in symbols
        )

        for ym in months:
            if want_nominal:
                raw = await self._fetch_feed(client, "daily_treasury_yield_curve", ym)
                if raw:
                    results.append(raw)
            if want_real:
                raw = await self._fetch_feed(client, "daily_treasury_real_yield_curve", ym)
                if raw:
                    results.append(raw)

        return results

    async def _fetch_feed(
        self, client: httpx.AsyncClient, dataset: str, ym: str
    ) -> Optional[RawData]:
        url = f"{TREASURY_BASE}?data={dataset}&field_tdr_date_value_month={ym}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return RawData(
                source_id=self.source_id,
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                payload=resp.text,
                symbol=dataset,
                meta={"dataset": dataset, "month": ym, "url": url},
            )
        except httpx.HTTPError as e:
            # Treasury feeds occasionally fail on the current month before it's published
            return None

    def _month_range(self, start: date, end: date) -> list[str]:
        months: list[str] = []
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            months.append(f"{y}{m:02d}")
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return months

    def normalize(self, raw: RawData) -> NormalizedData:
        text = raw.payload
        if not isinstance(text, str) or not text.strip():
            return NormalizedData(source_id=self.source_id, symbol=raw.symbol, observations=[])

        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise ConnectorError(f"Treasury XML parse failed: {e}") from e

        dataset = raw.meta.get("dataset", "")
        is_real = "real" in dataset

        fields = REAL_FIELDS if is_real else NOMINAL_FIELDS
        unit = "percent_real" if is_real else "percent_nominal"

        # Each entry is a business day
        observations: list[dict] = []
        for entry in root.findall("atom:entry", NS):
            props = entry.find("m:properties", NS)
            if props is None:
                continue
            date_el = props.find("d:NEW_DATE", NS)
            date_str = ""
            if date_el is not None and date_el.text:
                # Format: "2026-09-05T00:00:00"
                date_str = date_el.text.strip()[:10]
            if not date_str:
                continue

            for tenor_label, field_name in fields.items():
                # ElementTree doesn't handle prefixed names well via find(); use tag match
                local = field_name.split(":", 1)[1]
                el = props.find(f"d:{local}", NS)
                if el is None or el.text is None:
                    continue
                try:
                    val = float(el.text.strip())
                except ValueError:
                    continue
                observations.append(
                    {
                        "date": date_str,
                        "symbol": tenor_label,
                        "value": val,
                        "field": "yield" if not is_real else "real_yield",
                    }
                )

        return NormalizedData(
            source_id=self.source_id,
            symbol=dataset,
            unit=unit,
            observations=observations,
            meta={"dataset": dataset, "is_real": is_real, "month": raw.meta.get("month", "")},
        )

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            today = datetime.now(timezone.utc)
            ym = f"{today.year}{today.month:02d}"
            r = await client.get(
                f"{TREASURY_BASE}?data=daily_treasury_yield_curve&field_tdr_date_value_month={ym}"
            )
            return r.status_code == 200
        except Exception:
            return False
