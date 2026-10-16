"""chess.com-style move classification (UI and logs only; never shown to agents).

classify() compares the move that was played with Stockfish's MultiPV lines for
the position before it, using win percentages from the mover's point of view:

  brilliant   a sound sacrifice: engine's top move (or #2 within 2%), material left
              en prise, not already completely winning, not losing afterwards
  great       engine's top move that is not obvious and is tactically critical:
              the only good move, a fork, or a trap (material left en prise
              that the opponent must not take)
  best        the engine's #1 move, or its #2 move when that loses under 2%
  excellent / good / inaccuracy / mistake / blunder
              by win% lost: < 2, < 5, < 10, < 20, >= 20

Scores in analyses are White's view, like the eval bar: {"cp": int} or
{"mate": int} (mate 0 = game over, with "winner").
"""
from __future__ import annotations

import math

import chess

LABELS = ("brilliant", "great", "best", "excellent", "good", "inaccuracy", "mistake", "blunder")
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}

# win% lost -> label (upper bounds, exclusive)
BANDS = ((2.0, "excellent"), (5.0, "good"), (10.0, "inaccuracy"), (20.0, "mistake"))
BEST_RANK2_MAX_LOSS = 2.0
BRILLIANT_MAX_WIN_BEFORE = 90.0   # already completely winning: no brilliant
BRILLIANT_MIN_WIN_AFTER = 50.0    # still losing after the sacrifice: no brilliant
GREAT_MAX_WIN_BEFORE = 95.0
ONLY_MOVE_GAP = 10.0              # best line vs second line
TRAP_GAP = 8.0                    # taking the bait costs the opponent at least this much
SACRIFICE_MIN = 2                 # material (pawns) left en prise, net of what the move took


def win_pct(score: dict | None, color: bool) -> float | None:
    """Win% for `color` (chess.WHITE/BLACK) from a White-view score, lichess curve."""
    if not score:
        return None
    if score.get("mate") is not None:
        m = score["mate"]
        white_wins = (score.get("winner") == "white") if m == 0 else m > 0
        return 100.0 if white_wins == color else 0.0
    if score.get("cp") is None:
        return None
    cp = max(-1500, min(1500, score["cp"]))
    w = 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)
    return w if color == chess.WHITE else 100 - w


def band(loss: float) -> str:
    for limit, label in BANDS:
        if loss < limit:
            return label
    return "blunder"


# ---------------------------------------------------------------- tactics helpers
def see(board: chess.Board, square: chess.Square, side: bool) -> int:
    """Static exchange evaluation: material `side` wins by capturing on `square`
    (0 if it shouldn't capture). Pins are ignored; x-rays are seen because each
    capture really moves the piece on a scratch board."""
    target = board.piece_at(square)
    if target is None:
        return 0
    attackers = board.attackers(side, square)
    if not attackers:
        return 0
    frm = min(attackers, key=lambda s: VALUE[board.piece_type_at(s)])
    b = board.copy(stack=False)
    attacker = b.remove_piece_at(frm)
    b.set_piece_at(square, attacker)
    return max(0, VALUE[target.piece_type] - see(b, square, not side))


def _legal_capture_on(board: chess.Board, square: chess.Square) -> bool:
    return any(m.to_square == square for m in board.legal_moves)


def _captured_value(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return 1
    pt = board.piece_type_at(move.to_square)
    return VALUE[pt] if pt else 0


def en_prise(board: chess.Board, move: chess.Move, pawns: bool = False) -> dict[chess.Square, int]:
    """Mover's pieces the opponent can win material from right after this move:
    {square: SEE gain}. Only squares the opponent can legally capture on."""
    mover = board.turn
    after = board.copy(stack=False)
    after.push(move)
    out = {}
    for sq in chess.SquareSet(after.occupied_co[mover]):
        pt = after.piece_type_at(sq)
        if pt == chess.KING or (pt == chess.PAWN and not pawns):
            continue
        s = see(after, sq, not mover)
        if s > 0 and _legal_capture_on(after, sq):
            out[sq] = s
    return out


def sacrificed_material(board: chess.Board, move: chess.Move) -> int:
    """Material (not counting pawns left en prise) the mover gives up with this
    move, net of what it captured."""
    return max(en_prise(board, move).values(), default=0) - _captured_value(board, move)


def fork_targets(board: chess.Board, move: chess.Move) -> int:
    """How many enemy pieces the moved piece attacks that are worth more than it,
    undefended (minor piece or better), or the king. 0 if the piece itself hangs."""
    mover = board.turn
    after = board.copy(stack=False)
    after.push(move)
    sq = move.to_square
    pt = after.piece_type_at(sq)
    if pt is None or see(after, sq, not mover) > 0:
        return 0
    v = VALUE[pt]
    n = 0
    for t in after.attacks(sq):
        tp = after.piece_at(t)
        if tp is None or tp.color == mover:
            continue
        tv = VALUE[tp.piece_type]
        if tp.piece_type == chess.KING or tv > v or (tv >= 3 and not after.attackers(not mover, t)):
            n += 1
    return n


def is_obvious(board: chess.Board, move: chess.Move, prev_move: chess.Move | None) -> bool:
    """Recaptures, grabbing free material and promotions are not 'great'."""
    if move.promotion:
        return True
    if not board.is_capture(move):
        return False
    if prev_move is not None and prev_move.to_square == move.to_square:
        return True
    after = board.copy(stack=False)
    after.push(move)
    return _captured_value(board, move) - see(after, move.to_square, not board.turn) >= 2


def escapes_attack(board: chess.Board, move: chess.Move) -> bool:
    """The moved piece was en prise where it stood (moving it away is obvious)."""
    return see(board, move.from_square, not board.turn) > 0 or (
        board.is_check() and board.piece_type_at(move.from_square) == chess.KING)


def is_trap(board: chess.Board, move: chess.Move, opp_lines: list[dict]) -> bool:
    """Something is left en prise (more than the move took), but taking it costs the
    opponent at least TRAP_GAP win%: a poisoned piece. opp_lines are the engine's
    lines for the position after the move. A capture outside those lines is at
    least as bad as the last of them."""
    bait = en_prise(board, move, pawns=True)
    if not bait or max(bait.values()) <= _captured_value(board, move) or len(opp_lines) < 2:
        return False
    opp = not board.turn
    w_best = win_pct(opp_lines[0], opp)
    if w_best is None:
        return False
    for ln in opp_lines:
        if chess.Move.from_uci(ln["uci"]).to_square in bait:
            w = win_pct(ln, opp)
            if w is None or w_best - w < TRAP_GAP:
                return False
    w_last = win_pct(opp_lines[-1], opp)
    return w_last is not None and w_best - w_last >= TRAP_GAP


# ---------------------------------------------------------------- classifier
def classify(board: chess.Board, move: chess.Move, before_lines: list[dict] | None,
             after: dict | None, prev_move: chess.Move | None = None) -> dict | None:
    """board: position before the move. before_lines: MultiPV lines for it (best
    first, each {uci, san, cp|mate, pv}). after: analysis of the position after the
    move ({cp|mate, lines?}). Returns None when there's not enough data."""
    if not before_lines or after is None:
        return None
    mover = board.turn
    uci = move.uci()
    best = before_lines[0]
    w_best = win_pct(best, mover)
    w_after = win_pct(after, mover)
    if w_best is None or w_after is None:
        return None
    rank = next((i + 1 for i, ln in enumerate(before_lines) if ln.get("uci") == uci), None)
    if rank is not None:
        w_played = win_pct(before_lines[rank - 1], mover)
    else:
        # not among the top lines, so no better than the last of them
        w_last = win_pct(before_lines[-1], mover)
        w_played = w_after if w_last is None else min(w_after, w_last)
    loss = max(0.0, w_best - w_played)
    n_legal = board.legal_moves.count()
    tags: list[str] = []
    if n_legal == 1:
        tags.append("forced")

    is_top = rank == 1 or (rank == 2 and loss < BEST_RANK2_MAX_LOSS)
    label = "best" if is_top else band(loss)

    if is_top and n_legal > 1:
        # brilliant: a sound sacrifice
        sac = sacrificed_material(board, move)
        if (sac >= SACRIFICE_MIN and not move.promotion and w_best < BRILLIANT_MAX_WIN_BEFORE
                and w_after >= BRILLIANT_MIN_WIN_AFTER):
            tags.append("sacrifice")
            label = "brilliant"
        # great: not obvious, and critical or tactical
        elif (rank == 1 and w_best < GREAT_MAX_WIN_BEFORE and not is_obvious(board, move, prev_move)
              and not (board.is_check() and n_legal <= 2)):
            if len(before_lines) >= 2 and not escapes_attack(board, move):
                w2 = win_pct(before_lines[1], mover)
                if w2 is not None and w_best - w2 >= ONLY_MOVE_GAP:
                    tags.append("only_move")
            if fork_targets(board, move) >= 2:
                tags.append("fork")
            if is_trap(board, move, after.get("lines") or []):
                tags.append("trap")
            if any(t in tags for t in ("only_move", "fork", "trap")):
                label = "great"

    return {"label": label, "loss": round(loss, 2), "rank": rank,
            "best_uci": best.get("uci"), "best_san": best.get("san"),
            "win_before": round(w_best, 1), "win_after": round(w_after, 1), "tags": tags}
