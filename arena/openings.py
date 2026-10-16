"""Opening choices for the setup page.

"free" lets the agents play their own opening. Any other choice makes the referee
play the listed moves as book moves (both sides, no agent calls) before handing
the game to the agents. "white_first" entries only fix White's first move.
"""
from __future__ import annotations

import random

import chess

from .parsing import tokenize_line

FREE, RANDOM, CUSTOM = "free", "random", "custom"

# (id, group, name, ECO, moves)
_BOOK = [
    # White's first move only: Black and everything after are the agents' choice
    ("e4", "White's first move", "King's Pawn (1.e4)", "B00", "1. e4"),
    ("d4", "White's first move", "Queen's Pawn (1.d4)", "A40", "1. d4"),
    ("c4", "White's first move", "English (1.c4)", "A10", "1. c4"),
    ("nf3", "White's first move", "Réti (1.Nf3)", "A04", "1. Nf3"),
    ("f4", "White's first move", "Bird (1.f4)", "A02", "1. f4"),
    ("b3", "White's first move", "Larsen (1.b3)", "A01", "1. b3"),
    ("g3", "White's first move", "King's Fianchetto (1.g3)", "A00", "1. g3"),
    # Open games
    ("ruy-lopez", "1.e4 e5", "Ruy Lopez", "C60", "1. e4 e5 2. Nf3 Nc6 3. Bb5"),
    ("italian", "1.e4 e5", "Italian Game", "C50", "1. e4 e5 2. Nf3 Nc6 3. Bc4"),
    ("scotch", "1.e4 e5", "Scotch Game", "C45", "1. e4 e5 2. Nf3 Nc6 3. d4"),
    ("petrov", "1.e4 e5", "Petrov Defence", "C42", "1. e4 e5 2. Nf3 Nf6"),
    ("kings-gambit", "1.e4 e5", "King's Gambit", "C30", "1. e4 e5 2. f4"),
    ("vienna", "1.e4 e5", "Vienna Game", "C25", "1. e4 e5 2. Nc3"),
    # Semi-open games
    ("sicilian", "1.e4, other", "Sicilian Defence", "B20", "1. e4 c5"),
    ("najdorf", "1.e4, other", "Sicilian Najdorf", "B90", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6"),
    ("dragon", "1.e4, other", "Sicilian Dragon", "B70", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 g6"),
    ("french", "1.e4, other", "French Defence", "C00", "1. e4 e6 2. d4 d5"),
    ("caro-kann", "1.e4, other", "Caro-Kann Defence", "B12", "1. e4 c6 2. d4 d5"),
    ("pirc", "1.e4, other", "Pirc Defence", "B07", "1. e4 d6 2. d4 Nf6 3. Nc3 g6"),
    ("scandinavian", "1.e4, other", "Scandinavian Defence", "B01", "1. e4 d5"),
    ("alekhine", "1.e4, other", "Alekhine's Defence", "B02", "1. e4 Nf6"),
    # Closed games
    ("qgd", "1.d4", "Queen's Gambit Declined", "D30", "1. d4 d5 2. c4 e6"),
    ("qga", "1.d4", "Queen's Gambit Accepted", "D20", "1. d4 d5 2. c4 dxc4"),
    ("slav", "1.d4", "Slav Defence", "D10", "1. d4 d5 2. c4 c6"),
    ("london", "1.d4", "London System", "D02", "1. d4 d5 2. Nf3 Nf6 3. Bf4"),
    ("kings-indian", "1.d4", "King's Indian Defence", "E60", "1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6"),
    ("nimzo-indian", "1.d4", "Nimzo-Indian Defence", "E20", "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4"),
    ("grunfeld", "1.d4", "Grünfeld Defence", "D80", "1. d4 Nf6 2. c4 g6 3. Nc3 d5"),
    ("benoni", "1.d4", "Modern Benoni", "A60", "1. d4 Nf6 2. c4 c5 3. d5 e6"),
    ("dutch", "1.d4", "Dutch Defence", "A80", "1. d4 f5"),
    # Flank
    ("english-sym", "Flank", "English, Symmetrical", "A30", "1. c4 c5"),
    ("kia", "Flank", "King's Indian Attack", "A07", "1. Nf3 d5 2. g3"),
]

OPENINGS: dict[str, dict] = {
    oid: {"id": oid, "group": group, "name": name, "eco": eco, "moves": tokenize_line(moves)}
    for oid, group, name, eco, moves in _BOOK
}


def catalog() -> list[dict]:
    """For the setup page."""
    return [{"id": o["id"], "group": o["group"], "name": o["name"], "eco": o["eco"],
             "line": line_text(o["moves"])} for o in OPENINGS.values()]


def line_text(sans: list[str]) -> str:
    """'1. e4 e5 2. Nf3' from a SAN list."""
    b = chess.Board()
    moves = []
    for s in sans:
        moves.append(b.parse_san(s))
        b.push(moves[-1])
    return chess.Board().variation_san(moves)


def _replay(sans: list[str]) -> list[str]:
    b = chess.Board()
    out = []
    for s in sans:
        mv = b.parse_san(s)
        out.append(b.san(mv))
        b.push(mv)
    return out


def resolve(choice: str | None, custom_moves: str | None = None,
            rng: random.Random | None = None) -> dict | None:
    """Turns a setup-page choice into {id, name, eco, moves: [SAN]}, or None for 'free'.
    Raises ValueError for unknown ids or illegal custom moves."""
    choice = (choice or FREE).strip()
    if choice == FREE:
        return None
    if choice == RANDOM:
        named = [o for o in OPENINGS.values() if o["group"] != "White's first move"]
        o = (rng or random.Random()).choice(named)
        return {"id": o["id"], "name": o["name"], "eco": o["eco"], "moves": _replay(o["moves"]),
                "random": True}
    if choice == CUSTOM:
        tokens = tokenize_line(custom_moves or "")
        if not tokens:
            raise ValueError("custom opening: enter at least one move, e.g. '1. e4 c5 2. Nf3'")
        from .parsing import resolve_move
        b = chess.Board()
        sans = []
        for i, tok in enumerate(tokens):
            mv, why = resolve_move(b, tok)
            if mv is None:
                raise ValueError(f"custom opening: move {i + 1} ('{tok}') is illegal: {why}")
            sans.append(b.san(mv))
            b.push(mv)
            if b.is_game_over():
                raise ValueError("custom opening: the moves end the game")
        return {"id": CUSTOM, "name": "Custom opening", "eco": None, "moves": sans}
    if choice not in OPENINGS:
        raise ValueError(f"unknown opening '{choice}'")
    o = OPENINGS[choice]
    return {"id": o["id"], "name": o["name"], "eco": o["eco"], "moves": _replay(o["moves"])}
