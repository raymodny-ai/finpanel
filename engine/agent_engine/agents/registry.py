"""Agent Registry — loads YAML configs, instantiates concrete Agent classes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from ..models.agent import AgentConfig, RoleTemplate
from .base import BaseAgent

log = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry: agent_id -> (AgentConfig, BaseAgent instance)."""

    def __init__(self, configs_dir: Optional[Path] = None):
        self.configs_dir = configs_dir or Path(__file__).parent / "configs"
        self._configs: dict[str, AgentConfig] = {}
        self._instances: dict[str, BaseAgent] = {}

    def load_all(self) -> None:
        """Scan configs_dir for *.yaml and register every agent."""
        if not self.configs_dir.exists():
            log.warning("Agent configs dir does not exist: %s", self.configs_dir)
            return

        for path in sorted(self.configs_dir.glob("*.yaml")):
            try:
                self.load_file(path)
            except Exception as e:
                log.error("Failed to load agent config %s: %s", path, e)

        log.info("Registered %d agents: %s", len(self._configs), list(self._configs))

    def load_file(self, path: Path) -> AgentConfig:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = AgentConfig.model_validate(data)
        self._configs[config.agent_id] = config
        self._instances[config.agent_id] = self._instantiate(config)
        return config

    def _instantiate(self, config: AgentConfig) -> BaseAgent:
        """Map role+department to concrete Agent subclass."""
        # Import here to avoid circular imports at module load
        from .cio import CIOAgent
        from .pmo import PMOAgent
        from .risk_cro import RiskCROAgent
        from .macro_strategist import MacroStrategistAgent
        from .secretary import SecretaryAgent
        from .desks.metals_da import MetalsDAAgent
        from .desks.metals_qm import MetalsQMAgent

        key = (config.role, config.department, config.agent_id)

        # Agent-ID overrides (specific implementations)
        by_id = {
            "cio": CIOAgent,
            "pmo": PMOAgent,
            "risk-cro": RiskCROAgent,
            "macro-strategist": MacroStrategistAgent,
            "secretary": SecretaryAgent,
            "metals-da": MetalsDAAgent,
            "metals-qm": MetalsQMAgent,
        }
        cls = by_id.get(config.agent_id)
        if cls is None:
            # Fallback by role
            role_cls = {
                RoleTemplate.CIO: CIOAgent,
                RoleTemplate.PMO: PMOAgent,
                RoleTemplate.RA: RiskCROAgent,
                RoleTemplate.MACRO: MacroStrategistAgent,
                RoleTemplate.SEC: SecretaryAgent,
            }.get(config.role)
            if role_cls is None:
                raise ValueError(
                    f"No implementation for agent {config.agent_id} (role={config.role.value})"
                )
            cls = role_cls
        return cls(config)

    # --- Accessors -----------------------------------------------------

    def get_config(self, agent_id: str) -> AgentConfig:
        return self._configs[agent_id]

    def get(self, agent_id: str) -> BaseAgent:
        return self._instances[agent_id]

    def all_configs(self) -> list[AgentConfig]:
        return list(self._configs.values())

    def by_department(self, department: str) -> list[AgentConfig]:
        return [c for c in self._configs.values() if c.department == department]

    def by_role(self, role: RoleTemplate) -> list[AgentConfig]:
        return [c for c in self._configs.values() if c.role == role]

    def departments(self) -> list[str]:
        return sorted({c.department for c in self._configs.values()})


_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
        _registry.load_all()
    return _registry
