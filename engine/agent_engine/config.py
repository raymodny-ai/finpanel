"""Global configuration for FinPanel Agent Engine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from engine/ root if present
_ENGINE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ENGINE_ROOT / ".env", override=False)


class Settings:
    """Runtime settings, populated from environment variables."""

    # === Paths ===
    ENGINE_ROOT: Path = _ENGINE_ROOT
    PROJECT_ROOT: Path = _ENGINE_ROOT.parent
    FRONTEND_PUBLIC: Path = _ENGINE_ROOT.parent.parent / "frontend" / "public"

    PROMPTS_DIR: Path = _ENGINE_ROOT / "agent_engine" / "prompts"
    AGENT_CONFIGS_DIR: Path = _ENGINE_ROOT / "agent_engine" / "agents" / "configs"
    STORAGE_DIR: Path = _ENGINE_ROOT / "storage"

    # === LLM ===
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "120"))

    # === LLM backend selection ===
    # "openai" = OpenAI-SDK against OPENROUTER_BASE_URL (echo mode if no key)
    # "cli"    = shell out to Qoder CN CLI (qoderclicn -p); auth = user's
    #            Qoder CN session, billed to platform credits, no API key.
    LLM_BACKEND: str = os.getenv("LLM_BACKEND", "openai").strip().lower()
    QODERCLI_BINARY: str = os.getenv("QODERCLI_BINARY", "qoderclicn")
    # Env vars that must NOT leak into the qoderclicn subprocess. When the
    # Engine runs inside QoderWork the host injects ~18 QODER*/QODERCN*/
    # QODERWORK* vars; empirically the following break the CLI:
    #   QODER_AGENT_SDK_ENTRYPOINT   -> CLI aborts with sdk_invalid_args
    #   QODER_WORK_INTEGRATION_MODE  -> CLI reports "Not logged in"
    #   QODER_CONFIG_DIR             -> points at .qoderworkcn, not ~/.qoder
    #   QODERCN_CONFIG_DIR           -> same trap for the CN-flavored binary
    #   QODER_SDK_AUTH_PAYLOAD_FILE  -> ephemeral SDK auth socket, not user login
    # Rather than enumerate every current and future var, we ALSO strip any
    # name matching CLI_SCRUB_PREFIXES (see _child_env in llm_cli.py).
    CLI_SCRUB_ENV: list = [
        "QODER_AGENT_SDK_ENTRYPOINT",
        "QODER_WORK_INTEGRATION_MODE",
        "QODER_CONFIG_DIR",
    ]
    # Case-insensitive prefixes. `QODER` alone covers QODER_*, QODERCN_*,
    # QODERWORK_*. Keep narrow -- PATH/APPDATA/USERPROFILE must survive or the
    # CLI cannot find its own npm shim or ~/.qoder-cli.
    CLI_SCRUB_PREFIXES: list = ["QODER"]

    # === Data Source Keys ===
    FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
    FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

    # === Runtime ===
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    FIRM_NAME: str = os.getenv("FIRM_NAME", "FinPanel Virtual Capital")
    TIMEZONE: str = os.getenv("TIMEZONE", "America/New_York")

    # === Feature flags ===
    ENABLE_CHROMADB: bool = os.getenv("ENABLE_CHROMADB", "false").lower() == "true"
    ENABLE_DEBATE: bool = os.getenv("ENABLE_DEBATE", "true").lower() == "true"

    @classmethod
    def ensure_dirs(cls) -> None:
        """Create required directories if missing."""
        for p in [cls.STORAGE_DIR, cls.FRONTEND_PUBLIC]:
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
