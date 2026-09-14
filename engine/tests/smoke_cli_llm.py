"""Smoke test CLILLMClient end-to-end from QoderWork's shell.

Proves:
  1. env scrub (QODER_AGENT_SDK_ENTRYPOINT / _WORK_INTEGRATION_MODE / _CONFIG_DIR)
     lets qoderclicn find the user's real login.
  2. shutil.which resolves qoderclicn.CMD on Windows.
  3. Qwen3.8-Max answers a trivial prompt.
  4. HR-04 guard rejects an unknown model id (no silent fallback).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_engine.config import settings  # noqa: E402
from agent_engine.llm import LLMError  # noqa: E402
from agent_engine.llm_cli import CLILLMClient  # noqa: E402


async def main() -> int:
    print(f"LLM_BACKEND={settings.LLM_BACKEND} LLM_MODEL={settings.LLM_MODEL}")
    print(f"scrub list={settings.CLI_SCRUB_ENV}")
    print(
        "host env has "
        f"QODER_AGENT_SDK_ENTRYPOINT={'yes' if os.getenv('QODER_AGENT_SDK_ENTRYPOINT') else 'no'}, "
        f"QODER_WORK_INTEGRATION_MODE={'yes' if os.getenv('QODER_WORK_INTEGRATION_MODE') else 'no'}, "
        f"QODER_CONFIG_DIR={'yes' if os.getenv('QODER_CONFIG_DIR') else 'no'}"
    )

    client = CLILLMClient()
    print(f"resolved binary: {client.binary}")
    print(f"model: {client.model} | timeout: {client.timeout}s | echo: {client.echo_mode}")

    # --- positive test 1: plain complete() ----------------------------------
    t0 = time.perf_counter()
    try:
        out = await client.complete(
            system_prompt=(
                "You are a raw completion endpoint. Emit only what the user "
                "asks for, verbatim, with no preamble, no acknowledgement, "
                "no trailing commentary."
            ),
            user_prompt='Reply with exactly the four characters: PONG',
        )
    except LLMError as e:
        print(f"[FAIL] LLMError after {time.perf_counter() - t0:.1f}s: {e}")
        return 1
    dt = time.perf_counter() - t0
    print(f"[OK] complete() in {dt:.1f}s -> {out!r}")
    if "PONG" not in out:
        print("[FAIL] response did not contain PONG")
        return 2

    # --- positive test 2: complete_json() -----------------------------------
    t0 = time.perf_counter()
    try:
        data = await client.complete_json(
            system_prompt=(
                "You are a strict JSON emitter. Reply with a single JSON "
                "object only. No prose, no markdown fences, no leading text."
            ),
            user_prompt=(
                'Return a JSON object exactly of the form '
                '{"status": "ok", "answer": <int>} where <int> is 6 * 7.'
            ),
        )
    except LLMError as e:
        print(f"[FAIL] complete_json LLMError after {time.perf_counter() - t0:.1f}s: {e}")
        return 5
    except Exception as e:
        print(f"[FAIL] complete_json parse error: {type(e).__name__}: {e}")
        return 6
    dt = time.perf_counter() - t0
    print(f"[OK] complete_json() in {dt:.1f}s -> {data!r}")
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("answer") != 42:
        print("[FAIL] JSON did not match expected shape/values")
        return 7

    # --- HR-04 guard test ----------------------------------------------------
    bad = CLILLMClient(model="definitely-not-a-real-model-id")
    t0 = time.perf_counter()
    try:
        await bad.complete(
            system_prompt="Terse.",
            user_prompt='Reply with exactly: PONG',
        )
        print("[FAIL] HR-04 guard did NOT trip on unknown model")
        return 3
    except LLMError as e:
        dt = time.perf_counter() - t0
        msg = str(e)
        if "HR-04" in msg or "silently substituted" in msg:
            print(f"[OK] HR-04 guard tripped in {dt:.1f}s")
            print(f"     -> {msg[:200]}")
        else:
            print(f"[WARN] failed but not via HR-04: {msg[:200]}")
            return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
