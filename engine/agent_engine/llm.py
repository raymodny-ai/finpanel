"""LLM client wrapper — thin async layer over OpenAI SDK pointed at OpenRouter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import AsyncOpenAI

from .config import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient:
    """Async OpenAI-compatible client, defaults to OpenRouter.

    Falls back to a deterministic "echo" mode when no API key is present so the
    pipeline can still run end-to-end during local development.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.base_url = base_url or settings.OPENROUTER_BASE_URL
        self.model = settings.LLM_MODEL
        self.temperature = settings.LLM_TEMPERATURE
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.timeout = settings.LLM_TIMEOUT

        self._client: Optional[AsyncOpenAI] = None
        self.echo_mode = not bool(self.api_key)

        if not self.echo_mode:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        else:
            log.warning(
                "LLM running in ECHO MODE (no OPENROUTER_API_KEY). "
                "Agents will produce deterministic placeholder outputs."
            )

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Return the raw text of a single completion."""
        if self.echo_mode:
            return self._echo_response(system_prompt, user_prompt)

        assert self._client is not None
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        try:
            resp = await self._client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as e:
            log.error("LLM call failed: %s", e)
            raise LLMError(f"LLM call failed: {e}") from e

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_hint: Optional[dict] = None,
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        """Ask for JSON output, robustly parse it.

        Strategy:
        1. Prefer OpenAI response_format json_schema/json_object when available.
        2. Otherwise append a strong JSON instruction and extract the first
           {...} block from the response.
        """
        rf: Optional[dict] = {"type": "json_object"}
        if schema_hint:
            rf = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_output",
                    "strict": False,
                    "schema": schema_hint,
                },
            }

        instruction = (
            "\n\nYou MUST respond with a single valid JSON object and nothing else. "
            "No prose outside the JSON. No markdown code fences."
        )
        try:
            raw = await self.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt + instruction,
                response_format=rf,
                temperature=temperature,
            )
        except LLMError:
            # Retry without response_format in case provider rejects it
            raw = await self.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt + instruction,
                response_format=None,
                temperature=temperature,
            )

        return _extract_json(raw)

    def _echo_response(self, system_prompt: str, user_prompt: str) -> str:
        """Deterministic fallback when no API key is present."""
        return json.dumps(
            {
                "narrative": (
                    "[ECHO MODE — LLM API key not configured. "
                    "This is a deterministic placeholder produced by the engine so the "
                    "pipeline can be validated end-to-end.]"
                ),
                "stance": "neutral",
                "confidence": 0.5,
                "key_points": [
                    "Placeholder point 1 — configure OPENROUTER_API_KEY for real analysis",
                    "Placeholder point 2 — engine pipeline is functioning",
                ],
                "risk_flags": [],
                "next_actions": [],
                "evidence_refs": [],
                "escalation_required": False,
                "payload": {},
                "_echo": True,
                "_prompt_hash": f"{len(system_prompt)}:{len(user_prompt)}",
            },
            indent=2,
        )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from an LLM response."""
    if not text:
        raise LLMError("Empty LLM response")

    # 1) Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) Extract from code fence
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3) Find first balanced {...}
    start = text.find("{")
    if start == -1:
        raise LLMError(f"No JSON object in response: {text[:200]}")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    raise LLMError(f"Malformed JSON in response: {e}") from e

    raise LLMError("Unbalanced JSON braces in response")


# Module-level singletons: one default + one per model override.
_llm_client: Any = None
_llm_client_by_model: dict[str, Any] = {}


def get_llm(model_override: Optional[str] = None) -> Any:
    """Return an LLM client for the configured backend.

    ``LLM_BACKEND=openai`` (default) -> :class:`LLMClient` (OpenAI SDK; echo
    mode when no key). ``LLM_BACKEND=cli`` -> :class:`agent_engine.llm_cli.
    CLILLMClient` (Qoder CN CLI subprocess; auth via user's Qoder session).

    ``model_override`` enables per-agent model selection (e.g. metals-da on
    Qwen3.8-Flash for speed while CIO/QM stay on Qwen3.8-Max). Overridden
    clients are cached by model id so we don't rebuild subprocess plumbing
    on every agent turn. ``None`` returns the process-wide default client.
    """
    global _llm_client
    if model_override:
        cached = _llm_client_by_model.get(model_override)
        if cached is not None:
            return cached
        if settings.LLM_BACKEND == "cli":
            from .llm_cli import CLILLMClient  # lazy: llm_cli imports this module

            client = CLILLMClient(model=model_override)
        else:
            client = LLMClient()
            client.model = model_override  # OpenAI SDK: swap model on the instance
        _llm_client_by_model[model_override] = client
        log.info(
            "Built per-agent LLM client (backend=%s model=%s)",
            settings.LLM_BACKEND, model_override,
        )
        return client

    if _llm_client is None:
        if settings.LLM_BACKEND == "cli":
            from .llm_cli import CLILLMClient  # lazy: llm_cli imports this module

            _llm_client = CLILLMClient()
        else:
            _llm_client = LLMClient()
    return _llm_client
