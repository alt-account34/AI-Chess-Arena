"""Builds every piece of text an agent ever sees.

ISOLATION RULE: prompts are built only from PromptContext, which carries the
position, the move history, the opponent's last think time and the agent's own
last plan. This module must never import arena.engine or arena.state (a test
enforces this), so engine evals, clocks, material and illegal-move counters
cannot leak into a prompt.
"""
from __future__ import annotations

from dataclasses import dataclass

import chess

# Short system-level text given to every CLI (Claude: --system-prompt,
# Codex: developer_instructions). The rules arrive in the first user message.
SYSTEM_PROMPT = (
    "You are a chess player taking part in a game run by a referee program. "
    "You have no tools; think it through yourself and reply in plain text, "
    "following the response format the referee gives you."
)

RESPONSE_FORMAT = """\
RESPONSE FORMAT (required). You may think and reason first, in any form. Your reply
must END with exactly one of these two blocks:

To play a move:
MOVE: <your move in SAN, e.g. Nf3, exd5, O-O, e8=Q; or UCI, e.g. g1f3>
PLAN: <1-5 sentences of strategy for the coming turns>

To resign:
RESIGN
EXPLANATION: <why your position is lost>
LINE: <a sequence of legal moves in SAN, starting from the current position, showing how you expect the game to go; at least 2 moves>
PLAN: <optional>"""

RESPONSE_FORMAT_THINK_ALOUD = RESPONSE_FORMAT.replace(
    "To play a move:\nMOVE:",
    "To play a move:\nANALYSIS: <3-6 sentences: the candidate moves you considered, the main threats "
    "for both sides, and why you chose your move>\nMOVE:")

FORMAT_REMINDER_THINK_ALOUD = (
    "Reminder: end your reply with `ANALYSIS: <candidates, threats, why>`, `MOVE: <move>` and "
    "`PLAN: <strategy>` (or RESIGN / EXPLANATION / LINE). No engines, no tools: use only your own reasoning."
)

FORMAT_REMINDER = (
    "Reminder: end your reply with `MOVE: <move>` and `PLAN: <strategy>` "
    "(or RESIGN / EXPLANATION / LINE). No engines, no tools: use only your own reasoning."
)


@dataclass(frozen=True)
class RetryInfo:
    attempted: str | None
    reason: str


@dataclass(frozen=True)
class PromptContext:
    color: str                          # "White" | "Black"
    move_number: int                    # full-move number
    fen: str
    ascii_board: str
    history_san: str                    # "1. e4 e5 2. Nf3 ..." or ""
    opponent_last_think_s: float | None
    last_plan: str | None
    legal_moves_san: tuple[str, ...] | None = None
    retry: RetryInfo | None = None


def make_context(board: chess.Board, *, opponent_last_think_s: float | None,
                 last_plan: str | None, send_legal_moves: bool,
                 retry: RetryInfo | None = None) -> PromptContext:
    legal = None
    if send_legal_moves:
        legal = tuple(sorted(board.san(m) for m in board.legal_moves))
    return PromptContext(
        color="White" if board.turn == chess.WHITE else "Black",
        move_number=board.fullmove_number,
        fen=board.fen(),
        ascii_board=ascii_board(board),
        history_san=history_san(board),
        opponent_last_think_s=opponent_last_think_s,
        last_plan=last_plan,
        legal_moves_san=legal,
        retry=retry,
    )


def ascii_board(board: chess.Board) -> str:
    files = "    a b c d e f g h"
    rows = [files]
    for rank in range(7, -1, -1):
        cells = []
        for f in range(8):
            p = board.piece_at(chess.square(f, rank))
            cells.append(p.symbol() if p else ".")
        rows.append(f"  {rank + 1} {' '.join(cells)} {rank + 1}")
    rows.append(files)
    return "\n".join(rows)


def history_san(board: chess.Board) -> str:
    """Numbered SAN history of the moves on the board's stack."""
    root = board.root()
    if not board.move_stack:
        return ""
    return root.variation_san(board.move_stack)


def rules_intro(color: str, send_legal_moves: bool, opening: str | None = None,
                think_aloud: bool = False) -> str:
    opening_note = ("\n- The referee chose the opening for this game and played its first moves "
                    f"for both sides: {opening}. The game continues from the current position."
                    if opening else "")
    legal_note = ("Each turn you will also be given the list of legal moves."
                  if send_legal_moves else
                  "You will NOT be given a list of legal moves; work them out yourself.")
    return f"""\
You are playing a game of chess as {color} against another AI. A referee program
runs the game: each turn it sends you the position and you reply with one move.

RULES
- Standard chess rules (FIDE). You play {color}.
- Use only your own reasoning. Do not use chess engines, tools, files, code,
  web searches or any outside help.
- There are no draw offers: you cannot offer, accept or claim a draw. Draws happen
  automatically on stalemate, insufficient material, threefold repetition, the
  50-move rule, or when the game reaches its move limit.
- An illegal or unreadable move is rejected and you will be asked again, with the
  reason. Three rejected attempts in a row lose the game by forfeit.
- You may resign. A resignation must include a LINE of at least 2 legal moves from
  the current position showing how you expect the game to go; if any move in it is
  illegal, the resignation is rejected (and counts as a rejected attempt).
- The only timing information you get is how long your opponent thought on their
  last move. There is no clock and no time penalty.
- {legal_note}{opening_note}
- Board notation: uppercase letters are White pieces (K Q R B N P), lowercase are
  Black, '.' is an empty square. The board is shown from White's side.

{RESPONSE_FORMAT_THINK_ALOUD if think_aloud else RESPONSE_FORMAT}

Keep your own plan in mind between turns. Your PLAN is shown back to you next turn."""


def turn_prompt(ctx: PromptContext, think_aloud: bool = False) -> str:
    parts: list[str] = []
    if ctx.retry is not None:
        attempted = f" '{ctx.retry.attempted}'" if ctx.retry.attempted else ""
        parts.append(f"Your previous reply{attempted} was REJECTED: {ctx.retry.reason}.\n"
                     f"The position is unchanged. Please send a legal move (or a valid resignation).")
    parts.append(f"You are {ctx.color}. It is move {ctx.move_number}, {ctx.color} to play.")
    parts.append(f"FEN: {ctx.fen}")
    parts.append("Board:\n" + ctx.ascii_board)
    parts.append("Move history: " + (ctx.history_san or "(none yet - this is the first move)"))
    if ctx.opponent_last_think_s is None:
        parts.append("Your opponent has not had a turn yet (any moves so far were set by the referee)."
                     if ctx.history_san else "Your opponent has not moved yet.")
    else:
        parts.append(f"Your opponent thought for {ctx.opponent_last_think_s:.1f} seconds on their last move.")
    parts.append("Your plan from last turn: " + (ctx.last_plan or "(none)"))
    if ctx.legal_moves_san is not None:
        parts.append("Legal moves: " + " ".join(ctx.legal_moves_san))
    parts.append(FORMAT_REMINDER_THINK_ALOUD if think_aloud else FORMAT_REMINDER)
    return "\n\n".join(parts)


def first_prompt(ctx: PromptContext, send_legal_moves: bool, opening: str | None = None,
                 think_aloud: bool = False) -> str:
    return (rules_intro(ctx.color, send_legal_moves, opening, think_aloud) + "\n\n---\n\n"
            + turn_prompt(ctx, think_aloud))


def recovery_prompt(ctx: PromptContext, send_legal_moves: bool, opening: str | None = None,
                    think_aloud: bool = False) -> str:
    """For a new session after resuming the old one failed."""
    note = ("NOTE: your previous conversation for this game could not be restored, so "
            "this is a fresh start. The full game so far and your last plan are below.")
    return note + "\n\n" + first_prompt(ctx, send_legal_moves, opening, think_aloud)
