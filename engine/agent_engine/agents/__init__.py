"""Agents package — BaseAgent, concrete roles, registry."""

from .base import BaseAgent, AgentContext, AgentRunResult
from .registry import AgentRegistry, get_registry

__all__ = ["BaseAgent", "AgentContext", "AgentRunResult", "AgentRegistry", "get_registry"]
