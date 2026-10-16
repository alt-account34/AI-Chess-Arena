"""Loads agents.yaml and settings.yaml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent


class AgentConfig(BaseModel):
    # Extra keys (e.g. a mock agent's `script` or `delay_s`) are kept.
    model_config = ConfigDict(extra="allow")

    id: str
    display_name: str = ""
    cli: str
    model: str | None = None
    reasoning_effort: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.display_name:
            self.display_name = self.id

    @property
    def extra(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True

    # Game defaults (the setup page can override the first two per game).
    send_legal_moves: bool = False
    think_aloud: bool = False        # ask agents for an ANALYSIS block before each move
    move_cap_plies: int = 200
    max_illegal_streak: int = 3
    safety_timeout_s: float = 1200.0

    # Storage
    games_dir: str = "games"
    db_path: str = "arena.db"

    # Engine (UI/logs only)
    stockfish_path: str | None = "tools/stockfish/stockfish.exe"
    engine_time_s: float = 1.6       # tuned with `python -m arena bench-engine` (SETUP.md, section 2)
    engine_depth: int | None = None
    engine_threads: int = 5
    engine_hash_mb: int = 384
    engine_multipv: int = 3          # lines per position, used by the move classifier

    # CLI locations. None = auto-detect via PATH.
    claude_path: str | None = None
    codex_path: str | None = None
    # Parent folder for per-agent empty working dirs. None = %TEMP%\ai-chess-arena
    agent_temp_root: str | None = None

    def resolve(self, p: str | None) -> Path | None:
        if not p:
            return None
        path = Path(p)
        return path if path.is_absolute() else ROOT / path


def load_settings(path: Path | None = None) -> Settings:
    """settings.yaml, or the file named by the ARENA_SETTINGS environment variable."""
    path = path or Path(os.environ.get("ARENA_SETTINGS") or ROOT / "settings.yaml")
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings(**data)


def expand_family(fam: dict) -> list[dict]:
    """One agent entry per effort level: id `<id_prefix>-<effort>`, name `<name> (<effort>)`.
    A family without efforts gives a single agent with id `<id_prefix>`."""
    fam = dict(fam)
    prefix, name = fam.pop("id_prefix"), fam.pop("name")
    efforts = fam.pop("efforts", None) or [None]
    group = fam.pop("group", None)
    out = []
    for e in efforts:
        out.append({**fam, "id": f"{prefix}-{e}" if e else prefix,
                    "display_name": f"{name} ({e})" if e else name,
                    "reasoning_effort": e, "family": name, "group": group or fam.get("cli")})
    return out


def load_agents(path: Path | None = None) -> dict[str, AgentConfig]:
    """agents.yaml, or the file named by the ARENA_AGENTS environment variable."""
    path = path or Path(os.environ.get("ARENA_AGENTS") or ROOT / "agents.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    agents: dict[str, AgentConfig] = {}
    entries = [e for fam in data.get("families", []) for e in expand_family(fam)]
    for entry in entries + list(data.get("agents", [])):
        cfg = AgentConfig(**entry)
        if cfg.id in agents:
            raise ValueError(f"duplicate agent id in {path.name}: {cfg.id}")
        agents[cfg.id] = cfg
    return agents
