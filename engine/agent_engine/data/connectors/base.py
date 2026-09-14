"""Base connector — abstract interface for all data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx


class ConnectorError(Exception):
    """Raised when a data source cannot be reached or returns malformed data."""


@dataclass
class RawData:
    """Raw response from a data source, before normalization."""

    source_id: str
    fetched_at: str
    payload: Any
    symbol: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class NormalizedData:
    """Normalized data point — unified schema across sources.

    Every observation is a (timestamp, symbol, field, value) tuple.
    """

    source_id: str
    symbol: str
    unit: str = ""
    observations: list[dict] = field(default_factory=list)
    # Each observation: {"date": "YYYY-MM-DD", "value": float, "field": "close|price|yield|..."}
    meta: dict = field(default_factory=dict)

    def latest(self, field_name: str = "value") -> Optional[dict]:
        """Return the most recent observation."""
        if not self.observations:
            return None
        return max(self.observations, key=lambda o: o.get("date", ""))

    def value_at(self, target_date: str) -> Optional[float]:
        for obs in self.observations:
            if obs.get("date") == target_date:
                v = obs.get("value")
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BaseConnector(ABC):
    """Abstract base for all data source connectors."""

    #: Unique identifier used in agent configs
    source_id: str = "base"

    #: Human-readable label
    display_name: str = "Base Connector"

    #: Whether this source needs a Next.js Route Handler proxy from the browser.
    #: The engine itself can always hit sources directly (server-side, no CORS).
    requires_proxy: bool = False

    #: Whether an API key is required
    requires_key: bool = False

    #: Approximate rate limit (requests per minute); 0 = unlimited
    rate_limit_per_min: int = 0

    #: Default timeout in seconds
    timeout: float = 15.0

    def __init__(self, api_key: str = "", client: Optional[httpx.AsyncClient] = None):
        self.api_key = api_key
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "User-Agent": "FinPanel-Agent-Engine/0.1 (research-use)",
                    "Accept": "application/json, text/xml, */*",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @abstractmethod
    async def fetch(self, symbols: list[str], start: date, end: date) -> list[RawData]:
        """Fetch raw data for given symbols and date range."""

    @abstractmethod
    def normalize(self, raw: RawData) -> NormalizedData:
        """Convert raw response into unified NormalizedData schema."""

    async def fetch_and_normalize(
        self, symbols: list[str], start: date, end: date
    ) -> list[NormalizedData]:
        raw_list = await self.fetch(symbols, start, end)
        return [self.normalize(r) for r in raw_list]

    async def health_check(self) -> bool:
        """Return True if the source is reachable. Override for real probes."""
        return True
