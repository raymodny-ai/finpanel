"""Prompt loader — reads 8-section Prompt Contract .md files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config import settings

log = logging.getLogger(__name__)


class PromptLoader:
    """Loads system prompts from `prompts/system/{agent-id}.md`."""

    def __init__(self, prompts_dir: Optional[Path] = None):
        self.prompts_dir = prompts_dir or (settings.PROMPTS_DIR / "system")

    def load(self, agent_id: str) -> str:
        path = self.prompts_dir / f"{agent_id}.md"
        if not path.exists():
            log.warning("Prompt file not found for %s at %s", agent_id, path)
            return self._fallback_prompt(agent_id)
        return path.read_text(encoding="utf-8")

    def load_template(self, name: str) -> str:
        """Load a template from prompts/templates/ (used for output formatting)."""
        path = settings.PROMPTS_DIR / "templates" / f"{name}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _fallback_prompt(self, agent_id: str) -> str:
        return (
            f"You are agent `{agent_id}` of FinPanel Virtual Capital.\n"
            f"Your prompt file has not been configured yet. Produce a JSON response with "
            f"the standard envelope fields and note that the prompt is missing."
        )


_loader: Optional[PromptLoader] = None


def get_prompt_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader
