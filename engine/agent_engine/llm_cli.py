"""LLM backend that shells out to the Qoder CN CLI (qoderclicn) in print mode.

Why this exists
---------------
FinPanel can run on the user's Qoder CN subscription (shared platform credits)
without any third-party API key (no OpenRouter / DashScope key needed). The
CLI is an agent, so we neutralise its agency and use it as a stateless
completion endpoint:

  * ``-p / --print``              non-interactive: print response and exit
  * ``--tools ""``                disable ALL built-in tools (pure LLM, no file ops)
  * ``--no-session-persistence``  do not pollute the user's session library
  * ``--permission-mode dont_ask`` belt-and-braces against interactive hangs
  * ``-o text``                   plain-text stdout we can parse
  * ``-m <model>``                e.g. ``Qwen3.8-Max`` (see ``--list-models``)
  * ``--system-prompt <text>``    REPLACES the CLI's own agent system prompt,
                                  giving us raw-completion behaviour instead of
                                  "Ready." acknowledgements or tool-planning
                                  preambles.
  * ``--attachment <file>``       user prompt is shipped as a temp .md file, so
                                  Windows cmd.exe re-parsing of the ``.CMD``
                                  npm shim cannot mangle newlines / ``=====``
                                  separators / long strings.

Environment hygiene
-------------------
When the Engine is spawned from inside QoderWork, the host injects ~18 env vars
in the ``QODER*`` / ``QODERCN*`` / ``QODERWORK*`` namespaces. Empirically these
break the CLI in different ways:

  * ``QODER_AGENT_SDK_ENTRYPOINT``  -> CLI demands SDK stream-json flags and
    aborts with ``sdk_invalid_args``
  * ``QODER_WORK_INTEGRATION_MODE`` -> CLI assumes desktop-integration auth and
    reports "Not logged in"
  * ``QODER_CONFIG_DIR``            -> points the CLI at QoderWork's config dir
    instead of its own (~/.qoder) where ``qoderclicn login`` stores credentials
  * ``QODERCN_CONFIG_DIR``          -> SAME trap for the CN-flavored binary
    (``qoderclicn`` reads this one, not ``QODER_CONFIG_DIR``)
  * ``QODER_SDK_AUTH_PAYLOAD_FILE`` -> ephemeral SDK auth payload from the host
    process, not the user's interactive login

Rather than chase each new var QoderWork adds, :class:`CLILLMClient` scrubs the
entire ``QODER*`` namespace by prefix (see ``settings.CLI_SCRUB_PREFIXES``).
``PATH`` / ``APPDATA`` / ``USERPROFILE`` / ``HOME`` are preserved so the CLI
can still resolve its npm shim and locate ``~/.qoder-cli``.

Caveats (accepted by design)
----------------------------
* Output is free text carrying the CLI's own agent system prompt; our 8-section
  Prompt Contract is passed as part of the user prompt. JSON is recovered by
  the engine's existing robust ``_extract_json`` fallback chain.
* Each call pays a few seconds of CLI boot latency (fine for post-market use)
  and consumes Qoder platform credits.
* ``-p`` print mode is not an officially slotted API surface; flags may drift
  across CLI versions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any, Optional

from .config import settings
from .llm import LLMError, _extract_json

log = logging.getLogger(__name__)


class CLILLMClient:
    """Drop-in replacement for :class:`agent_engine.llm.LLMClient`.

    Implements the same ``complete()`` / ``complete_json()`` surface so the
    orchestrator and agents need no changes.
    """

    backend = "cli"

    def __init__(
        self,
        binary: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        raw = binary or settings.QODERCLI_BINARY
        # asyncio.create_subprocess_exec does NOT resolve PATHEXT on Windows;
        # the npm shim is qoderclicn.cmd, so a bare name raises WinError 2.
        resolved = shutil.which(raw) or raw

        # On Windows the npm shim is a .CMD batch file. Spawning it goes
        # through cmd.exe /c which re-parses argv and MANGLES multi-line
        # values (verified: --system-prompt with newlines causes rc=42 with
        # empty stdout/stderr). Bypass by invoking node on the JS bundle
        # directly. Layout is standard npm global:
        #   <prefix>/qoderclicn.CMD
        #   <prefix>/node_modules/@qodercn-ai/qoderclicn/bundle/qoderclicn.js
        self._node_binary: Optional[str] = None
        self._js_entry: Optional[str] = None
        self.binary = resolved

        if resolved.lower().endswith((".cmd", ".bat")):
            node = shutil.which("node")
            shim_dir = os.path.dirname(resolved)
            candidate = os.path.join(
                shim_dir,
                "node_modules",
                "@qodercn-ai",
                "qoderclicn",
                "bundle",
                "qoderclicn.js",
            )
            if node and os.path.isfile(candidate):
                self._node_binary = node
                self._js_entry = candidate
                self.binary = candidate  # for logs / display
                log.info(
                    "LLM backend = cli (strategy=node-direct node=%s js=%s model=%s timeout=%ss)",
                    node, candidate, self.model if False else (model or settings.LLM_MODEL),
                    timeout or settings.LLM_TIMEOUT,
                )
            else:
                log.warning(
                    "qoderclicn shim resolved to %s but node=%s / bundle=%s "
                    "(exists=%s). Falling back to .CMD invocation; multi-line "
                    "--system-prompt may fail with rc=42 on Windows.",
                    resolved, node, candidate, os.path.isfile(candidate),
                )
                log.info(
                    "LLM backend = cli (strategy=cmd-shim binary=%s model=%s timeout=%ss)",
                    resolved, model or settings.LLM_MODEL, timeout or settings.LLM_TIMEOUT,
                )
        else:
            log.info(
                "LLM backend = cli (strategy=direct binary=%s model=%s timeout=%ss)",
                resolved, model or settings.LLM_MODEL, timeout or settings.LLM_TIMEOUT,
            )

        self.model = model or settings.LLM_MODEL
        self.timeout = timeout or settings.LLM_TIMEOUT
        # The CLI backend never echoes: no key is required, auth is the user's
        # Qoder CN session. A missing login surfaces as LLMError at call time.
        self.echo_mode = False

    # -- public interface (mirrors LLMClient) -----------------------------

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Single completion. ``response_format``/``temperature`` are accepted
        for interface parity but ignored (the CLI exposes neither)."""
        return await self._run(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
        )

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_hint: Optional[dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        # Qwen3.8-Max (and other CLI models) receive no server-side
        # response_format, so schema_hint MUST be injected into the prompt
        # text or the model has no structural guidance. Empirically, dropping
        # schema_hint produces empty `narrative`, empty `key_points`, and
        # partially-filled `evidence_refs` for most agents.
        parts = [user_prompt]
        if schema_hint:
            import json as _json
            schema_str = _json.dumps(schema_hint, ensure_ascii=False, indent=2)
            parts.append(
                "\n\n## Required Output Schema\n"
                "Respond with a SINGLE JSON object matching this schema exactly.\n"
                "Every key listed at the top level MUST be present.\n"
                "For array fields, every item MUST include ALL of its listed subfields "
                "(do not omit or leave blank any subfield that appears in the item schema).\n"
                "For string fields, do not return empty strings unless the schema explicitly "
                "allows it -- fill with your best real value.\n\n"
                f"{schema_str}\n\n"
                "Emit ONLY the JSON object. No prose before or after. "
                "No markdown code fences. No trailing commentary."
            )
        else:
            parts.append(
                "\n\nYou MUST respond with a single valid JSON object and nothing "
                "else. No prose outside the JSON. No markdown code fences."
            )
        raw = await self.complete(
            system_prompt=system_prompt,
            user_prompt="".join(parts),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _extract_json(raw)

    # -- internals ---------------------------------------------------------

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        dropped: list[str] = []
        for key in list(env.keys()):
            upper = key.upper()
            if key in settings.CLI_SCRUB_ENV or upper in {
                k.upper() for k in settings.CLI_SCRUB_ENV
            }:
                dropped.append(key)
                env.pop(key, None)
                continue
            for prefix in settings.CLI_SCRUB_PREFIXES:
                if upper.startswith(prefix.upper()):
                    dropped.append(key)
                    env.pop(key, None)
                    break
        if dropped:
            log.debug("CLI child env scrubbed %d vars: %s", len(dropped), sorted(dropped))
        return env

    def _child_cwd(self) -> str:
        """Isolated scratch dir so the CLI's implicit workspace is never the
        project tree (it has no tools anyway, but keep the blast radius zero)."""
        d = settings.STORAGE_DIR / "cli-scratch"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _argv(
        self,
        system_prompt: str,
        attachment_path: str,
        max_tokens: Optional[int],
    ) -> list[str]:
        # --system-prompt REPLACES qoderclicn's default agent system prompt,
        # giving us raw-completion behaviour (no "Ready." acknowledgements,
        # no tool-planning preamble). --attachment ships the user prompt as a
        # file so Windows cmd.exe re-parsing of the .CMD shim cannot mangle
        # newlines / equals signs / long strings.
        if self._node_binary and self._js_entry:
            args = [self._node_binary, self._js_entry]
        else:
            args = [self.binary]
        args += [
            "-p",
            "--tools", "",
            "--no-session-persistence",
            "--permission-mode", "dont_ask",
            "-o", "text",
        ]
        if self.model:
            args += ["-m", self.model]
        if max_tokens:
            args += ["--max-output-tokens", str(max_tokens)]
        if system_prompt:
            args += ["--system-prompt", system_prompt]
        args += ["--attachment", attachment_path]
        args += [
            "Follow the instructions in the attached file exactly. "
            "Reply with only the requested output and nothing else."
        ]
        return args

    async def _run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        # Always go through --attachment: bulletproof against argv mangling and
        # Windows' 32,767-char CreateProcess cap. Cost is one small temp file.
        fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="finpanel-prompt-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(user_prompt)

            args = self._argv(system_prompt, tmp_path, max_tokens)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(),
                cwd=self._child_cwd(),
            )
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise LLMError(f"CLI LLM timed out after {self.timeout}s")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        text = out_b.decode("utf-8", "replace").strip()
        stderr_full = err_b.decode("utf-8", "replace").strip()
        stderr_tail = stderr_full[-4000:]
        stdout_tail = text[-4000:]

        # HR-04: never silently substitute model / data source. qoderclicn
        # prints `Model "X" is not available right now; using "auto" instead.`
        # and quietly downgrades -- we refuse that response.
        fallback_marker = "is not available right now"
        if fallback_marker in text or fallback_marker in stderr_tail:
            snippet = text[:400] or stderr_tail
            raise LLMError(
                f"qoderclicn silently substituted the model (HR-04). "
                f"Requested model={self.model!r} is not accepted by the CLI. "
                f"Run `qoderclicn --list-models` and set LLM_MODEL in "
                f"engine/.env to one of the listed ids. CLI said: {snippet}"
            )

        if "Not logged in" in text or "Not logged in" in stderr_tail:
            raise LLMError(
                "qoderclicn is not logged in. Run `qoderclicn login` in a "
                "terminal once, then verify with `qoderclicn --list-models`."
            )
        if proc.returncode != 0 and not text:
            log.warning(
                "CLI rc=%s -- stdout tail:\n%s\n-- stderr tail:\n%s",
                proc.returncode, stdout_tail, stderr_tail,
            )
            raise LLMError(
                f"CLI LLM failed (rc={proc.returncode}).\n"
                f"--- stdout (last 4000) ---\n{stdout_tail}\n"
                f"--- stderr (last 4000) ---\n{stderr_tail}"
            )
        if proc.returncode != 0:
            # Cosmetic CLI bugs on Windows (e.g. a libuv assertion at exit) can
            # set a non-zero rc even when stdout is perfectly good.
            log.warning(
                "CLI rc=%s but stdout non-empty; stderr tail: %s",
                proc.returncode,
                stderr_tail,
            )
        if not text:
            raise LLMError(f"CLI LLM returned empty output. stderr: {stderr_tail}")
        return text
