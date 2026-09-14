"""Connector registry — lookup by source_id."""

from __future__ import annotations

from typing import Optional

from .base import BaseConnector
from .gold_api import GoldApiConnector
from .treasury_gov import TreasuryGovConnector


CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    GoldApiConnector.source_id: GoldApiConnector,
    TreasuryGovConnector.source_id: TreasuryGovConnector,
}


def get_connector(source_id: str, api_key: str = "") -> BaseConnector:
    """Instantiate a connector by source_id."""
    cls = CONNECTOR_REGISTRY.get(source_id)
    if cls is None:
        raise KeyError(f"Unknown connector: {source_id}. Available: {list(CONNECTOR_REGISTRY)}")
    return cls(api_key=api_key)
