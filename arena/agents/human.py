"""A person playing on the board in the browser.

ask() blocks until the server hands over a move (POST /api/game/move) or a
resignation, or the game is aborted. There is no safety timeout for a person.
"""
from __future__ import annotations

import queue
import threading

from .base import Agent, AgentCancelled, AgentReply


class HumanAgent(Agent):
    is_human = True

    def __init__(self, config, settings):
        super().__init__(config, settings)
        self._q: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._cancel = threading.Event()

    def submit_move(self, uci: str) -> None:
        self._q.put(("move", uci))

    def submit_resign(self) -> None:
        self._q.put(("resign", None))

    def ask(self, prompt: str, session_id: str | None, timeout: float) -> AgentReply:
        # cancel() is never cleared: it only comes when the game ends, and it may
        # arrive before this thread has started
        while True:
            if self._cancel.is_set():
                raise AgentCancelled()
            try:
                kind, uci = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            if kind == "resign":
                return AgentReply(text="", session_id=None, meta={"human_resign": True})
            return AgentReply(text=f"MOVE: {uci}", session_id=None)

    def cancel(self) -> None:
        self._cancel.set()
