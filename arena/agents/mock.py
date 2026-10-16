"""Mock agents for testing. Like real agents, they only see the prompt text.

Script actions (one per ask() call, retries included):
  random       a random legal move (with some chatter before it)
  move:<SAN>   a specific move
  garbage      text with no MOVE/RESIGN block
  illegal      a well-formed but illegal move
  bad_resign   RESIGN with an illegal LINE
  short_resign RESIGN with a 1-ply LINE
  resign       a valid resignation with a 2-ply LINE
  timeout      sleep until the safety timeout fires
  fail_resume  report a resume failure if a session id was given, else random
  fail         raise AgentFailure (a CLI crash)
  tool         a random move, but report a fake tool call
When the script runs out, the agent plays random legal moves.
"""
from __future__ import annotations

import random
import re
import threading
import time
import uuid

import chess

from ..config import AgentConfig, Settings
from .base import Agent, AgentCancelled, AgentFailure, AgentReply, AgentTimeout


class MockAgent(Agent):
    def __init__(self, config: AgentConfig, settings: Settings):
        super().__init__(config, settings)
        extra = config.extra
        self.script: list[str] = list(extra.get("script") or [])
        self.delay_s: float = float(extra.get("delay_s", 0.0))
        self.rng = random.Random(extra.get("seed"))
        self._cancel = threading.Event()
        self._calls = 0
        self.prompts: list[str] = []       # kept for tests
        self.session_ids: list[str | None] = []

    def ask(self, prompt: str, session_id: str | None, timeout: float) -> AgentReply:
        self._cancel.clear()
        self.prompts.append(prompt)
        self.session_ids.append(session_id)
        action = self.script[self._calls] if self._calls < len(self.script) else "random"
        self._calls += 1
        sid = session_id or f"mock-{uuid.uuid4()}"

        if self.console is not None:
            self.console("session", session_id=sid, model="mock")
            self.console("thinking")
        if action == "timeout":
            self._sleep(timeout + 1, timeout)
        if self.delay_s:
            self._sleep(self.delay_s, timeout)

        board = board_from_prompt(prompt)
        if action == "fail_resume" and session_id:
            return AgentReply(text="", session_id=None, resume_failed=True,
                              stderr="mock: session not found", exit_code=1)
        if action == "fail":
            raise AgentFailure("mock CLI crashed")

        tool_events: list[dict] = []
        if action == "garbage":
            text = "Hmm, interesting position. I like my chances here."
        elif action == "illegal":
            text = f"I'll go for it.\nMOVE: {illegal_move(board)}\nPLAN: test the referee"
        elif action == "bad_resign":
            text = f"RESIGN\nEXPLANATION: testing\nLINE: {illegal_move(board)} {illegal_move(board)}"
        elif action == "short_resign":
            mv = self.rng.choice(list(board.legal_moves))
            text = f"RESIGN\nEXPLANATION: testing\nLINE: {board.san(mv)}"
        elif action == "resign":
            text = (f"My position is hopeless.\nRESIGN\nEXPLANATION: I am lost (mock).\n"
                    f"LINE: {resign_line(board, self.rng)}\nPLAN: none")
        elif action.startswith("move:"):
            text = f"MOVE: {action[5:]}\nPLAN: scripted"
        else:
            mv = self.rng.choice(list(board.legal_moves))
            text = (f"Let me look at the position... I'll play {board.san(mv)}.\n\n"
                    f"**MOVE:** {board.san(mv)}\nPLAN: keep playing random moves (mock #{self._calls}).")
            if action == "tool":
                tool_events = [{"tool": "Read", "input": {"file_path": "x"}}]
        if self.console is not None:            # stream the reply like a real CLI would
            for i in range(0, len(text), 12):
                self.console("text", delta=text[i:i + 12])
                time.sleep(0.01)
            self.console("block_end")
            if action == "tool":
                self.console("tool", tool="Read", input='{"file_path": "x"}')
        # rough fake usage (~4 characters per token) so the token UI has data
        tokens = {"input": len(prompt) // 4, "cached": 0, "output": len(text) // 4, "reasoning": 0}
        return AgentReply(text=text, session_id=sid, tool_events=tool_events,
                          raw_stdout=text, meta={"action": action}, tokens=tokens)

    def _sleep(self, seconds: float, timeout: float) -> None:
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            if self._cancel.wait(0.05):
                raise AgentCancelled()
            if time.monotonic() - start >= timeout:
                raise AgentTimeout()

    def cancel(self) -> None:
        self._cancel.set()


def board_from_prompt(prompt: str) -> chess.Board:
    fens = re.findall(r"^FEN: (.+)$", prompt, re.MULTILINE)
    if not fens:
        raise AgentFailure("mock: no FEN in prompt")
    return chess.Board(fens[-1].strip())


def illegal_move(board: chess.Board) -> str:
    """A well-formed SAN king move that is not legal."""
    king = board.king(board.turn)
    for sq in chess.SQUARES:
        if chess.square_distance(king, sq) >= 2 and board.piece_at(sq) is None:
            return "K" + chess.square_name(sq)
    return "Ka9"


def resign_line(board: chess.Board, rng: random.Random) -> str:
    moves = list(board.legal_moves)
    rng.shuffle(moves)
    for m1 in moves:
        b = board.copy(stack=False)
        san1 = b.san(m1)
        b.push(m1)
        replies = list(b.legal_moves)
        if replies:
            return f"{san1} {b.san(replies[0])}"
    return board.san(moves[0]) if moves else ""
