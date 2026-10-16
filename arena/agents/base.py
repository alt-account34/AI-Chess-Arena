"""Agent interface. The referee calls `ask` once per attempt; agents never poll."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AgentConfig, Settings


class AgentTimeout(Exception):
    """The safety timeout expired; the process has been killed."""


class AgentCancelled(Exception):
    """cancel() was called (game aborted) while the agent was thinking."""


class AgentFailure(Exception):
    """The CLI failed in a way that isn't the agent's fault (crash, auth, rate limit)."""


@dataclass
class AgentReply:
    text: str
    session_id: str | None
    tool_events: list[dict] = field(default_factory=list)
    raw_stdout: str = ""
    stderr: str = ""
    exit_code: int | None = 0
    resume_failed: bool = False
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    # Normalized usage for this call: {input, cached, output, reasoning}. "input"
    # includes cached input; "reasoning" is the part of "output" spent thinking.
    tokens: dict | None = None


class Agent(ABC):
    is_human = False    # a person moving on the board in the browser (agents/human.py)

    def __init__(self, config: AgentConfig, settings: Settings):
        self.config = config
        self.settings = settings
        self.workdir: Path | None = None
        # Live console sink, set by the referee: console(kind, **data). Optional.
        self.console = None

    def _console_lines(self, translate):
        """on_line callback for ProcRunner: raw line + translated events to the console."""
        if self.console is None:
            return None

        def on_line(line: str) -> None:
            self.console("raw", line=line)
            for kind, data in translate(line):
                self.console(kind, **data)
        return on_line

    def setup(self, workdir: Path) -> None:
        """Called once per game with a new, empty working folder."""
        self.workdir = workdir

    @abstractmethod
    def ask(self, prompt: str, session_id: str | None, timeout: float) -> AgentReply:
        """Blocking. Start a new session if session_id is None, else resume it."""

    def cancel(self) -> None:
        """Kill any in-flight request (called from another thread)."""

    def teardown(self) -> None:
        """Called once when the game ends."""
