"""Game state. The same structure is the live UI snapshot and the JSON game log.

Nothing in this module is ever handed to an agent: prompts are built only from
prompts.PromptContext.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

WHITE, BLACK = "white", "black"


def other(color: str) -> str:
    return BLACK if color == WHITE else WHITE


@dataclass
class Attempt:
    """One call to an agent (a move attempt, a retry, or a resignation)."""
    n: int                          # 1-based attempt number within the ply
    think_s: float
    raw_output: str
    kind: str                       # "move" | "resign" | "none"
    move_text: str | None = None    # what the agent wrote after MOVE:
    accepted: bool = False
    reason: str | None = None       # why it was rejected
    plan: str | None = None
    resign_explanation: str | None = None
    resign_line: str | None = None
    session_id: str | None = None
    new_session: bool = False
    tool_events: list[dict] = field(default_factory=list)
    stderr: str = ""
    exit_code: int | None = None
    meta: dict = field(default_factory=dict)
    tokens: dict | None = None      # {input, cached, output, reasoning} as reported by the CLI
    prompt: str | None = None       # exact text sent to the CLI on stdin
    analysis: str | None = None     # ANALYSIS block (think-aloud games)
    reasoning: str | None = None    # reasoning summary reported by the CLI (Codex)


@dataclass
class PlyRecord:
    ply: int                        # 1-based ply number in this game
    color: str
    agent_id: str
    think_s: float                  # includes retries
    attempts: list[Attempt]
    san: str | None = None          # None when the turn ended without a move
    uci: str | None = None
    fen_after: str | None = None
    plan: str | None = None
    analysis: str | None = None     # ANALYSIS of the accepted attempt (think-aloud games)
    eval: dict | None = None        # {"cp": int} or {"mate": int}, White's view
    tool_violations: list[dict] = field(default_factory=list)
    clock_after: dict = field(default_factory=dict)    # {"white": s, "black": s}
    illegal_after: dict = field(default_factory=dict)  # {"white": {"total","streak"}, ...}
    tokens: dict | None = None      # summed over this move's attempts
    tokens_after: dict = field(default_factory=dict)   # running totals per side
    book: bool = False              # played by the referee from the chosen opening
    lines: list[dict] | None = None  # engine's top lines for fen_after (MultiPV, White's view)
    classification: dict | None = None  # {label, loss, rank, best_san, ...}, see classify.py


@dataclass
class SideState:
    agent: dict
    session_id: str | None = None
    session_history: list[str] = field(default_factory=list)
    intro_sent: bool = False
    last_plan: str | None = None
    last_think_s: float | None = None
    think_total: float = 0.0
    moves: int = 0
    illegal_total: int = 0
    illegal_streak: int = 0
    tool_violations: int = 0
    tokens: dict = field(default_factory=lambda: empty_tokens())


TOKEN_KEYS = ("input", "cached", "output", "reasoning")


def empty_tokens() -> dict:
    return {k: 0 for k in TOKEN_KEYS} | {"calls": 0}


def add_tokens(total: dict, t: dict | None) -> None:
    if not t:
        return
    for k in TOKEN_KEYS:
        total[k] = total.get(k, 0) + int(t.get(k) or 0)
    total["calls"] = total.get("calls", 0) + 1


@dataclass
class GameState:
    id: str
    started_at: str
    start_fen: str
    settings: dict
    white: SideState
    black: SideState
    plies: list[PlyRecord] = field(default_factory=list)
    status: str = "starting"        # starting|running|paused|unresponsive|finished
    result: str = "*"
    termination: str | None = None  # machine key, e.g. "checkmate", "forfeit"
    reason: str | None = None       # human-readable
    winner: str | None = None
    finished_at: str | None = None
    resignation: dict | None = None  # {color, explanation, line_san, fen}
    # A turn that ended without a move (resignation, forfeit, abort mid-turn).
    final_turn: PlyRecord | None = None
    start_eval: dict | None = None
    start_lines: list[dict] | None = None
    opening: dict | None = None     # {id, name, eco, moves} when the referee set the opening
    events: list[dict] = field(default_factory=list)
    turn: dict | None = None        # live only: {color, started_at, attempt}
    pause_requested: bool = False
    unresponsive_reason: str | None = None
    engine_available: bool | None = None

    def side(self, color: str) -> SideState:
        return self.white if color == WHITE else self.black

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pick(cls, d: dict) -> dict:
    return {k: v for k, v in d.items() if k in cls.__dataclass_fields__}


def _ply_from_dict(d: dict) -> PlyRecord:
    return PlyRecord(**{**_pick(PlyRecord, d), "attempts": [Attempt(**_pick(Attempt, a)) for a in d["attempts"]]})


def state_from_dict(d: dict) -> GameState:
    """Inverse of GameState.to_dict() (for saved logs; unknown keys are ignored)."""
    return GameState(**{
        **_pick(GameState, d),
        "white": SideState(**_pick(SideState, d["white"])),
        "black": SideState(**_pick(SideState, d["black"])),
        "plies": [_ply_from_dict(p) for p in d["plies"]],
        "final_turn": _ply_from_dict(d["final_turn"]) if d.get("final_turn") else None,
    })
