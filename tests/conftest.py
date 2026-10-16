import asyncio

import pytest

from arena.agents.mock import MockAgent
from arena.config import AgentConfig, Settings
from arena.referee import Referee
from arena.state import BLACK, WHITE


@pytest.fixture
def settings(tmp_path):
    return Settings(agent_temp_root=str(tmp_path / "agents"), games_dir=str(tmp_path / "games"),
                    db_path=str(tmp_path / "arena.db"), stockfish_path=None,
                    safety_timeout_s=30)


@pytest.fixture
def make_game(settings):
    """make_game(white_script, black_script, **referee_kwargs) -> (referee, agents)."""
    def _make(white=None, black=None, on_event=None, **kw):
        cfgs = {
            WHITE: AgentConfig(id="w", cli="mock", script=white or [], seed=1),
            BLACK: AgentConfig(id="b", cli="mock", script=black or [], seed=2),
        }
        agents = {c: MockAgent(cfg, settings) for c, cfg in cfgs.items()}
        ref = Referee(agents, cfgs, settings, on_event=on_event, **kw)
        return ref, agents
    return _make


def run(ref):
    return asyncio.run(ref.run())
