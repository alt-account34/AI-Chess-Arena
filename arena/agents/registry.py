"""Maps an agents.yaml `cli` value to an adapter class."""
from __future__ import annotations

from ..config import AgentConfig, Settings
from .base import Agent


def create_agent(config: AgentConfig, settings: Settings) -> Agent:
    if config.cli == "mock":
        from .mock import MockAgent
        return MockAgent(config, settings)
    if config.cli == "human":
        from .human import HumanAgent
        return HumanAgent(config, settings)
    if config.cli == "claude_code":
        from .claude_code import ClaudeCodeAgent
        return ClaudeCodeAgent(config, settings)
    if config.cli == "codex":
        from .codex import CodexAgent
        return CodexAgent(config, settings)
    raise ValueError(f"unknown cli '{config.cli}' for agent {config.id}")
