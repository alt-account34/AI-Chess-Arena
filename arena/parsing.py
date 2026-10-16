"""Extracts MOVE / RESIGN / PLAN blocks from agent output and resolves moves.

The agent may reason freely; only the LAST `MOVE:` line or bare `RESIGN` line
counts. Moves are accepted in SAN or UCI, and the parser is forgiving about
markdown, check/annotation symbols and move-number prefixes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import chess

FIELD_RE = re.compile(
    r"^[\s>#*_`\-]*(MOVE|RESIGN|PLAN|EXPLANATION|LINE|ANALYSIS)\b[\s*_`]*(?:[:=\-]\s*)?(.*)$",
    re.IGNORECASE,
)
RESULT_TOKENS = {"1-0", "0-1", "1/2-1/2", "½-½", "*"}
PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}
UCI_RE = re.compile(r"^([a-h][1-8])[-x]?([a-h][1-8])=?([qrbnQRBN])?$")
SAN_RE = re.compile(r"^([NBRQK])?([a-h])?([1-8])?x?([a-h][1-8])(?:=?([NBRQnbrq]))?$")


@dataclass
class ParsedResponse:
    kind: str                       # "move" | "resign" | "none"
    move: str | None = None         # cleaned move token
    move_raw: str | None = None     # text after MOVE:
    plan: str | None = None
    explanation: str | None = None
    line_raw: str | None = None
    line: list[str] = field(default_factory=list)
    analysis: str | None = None     # ANALYSIS block (think-aloud games), if any


def _strip_markdown(s: str) -> str:
    return re.sub(r"[*`_~]", "", s).strip()


def parse_response(text: str) -> ParsedResponse:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    # Locate every field line: (line index, key, inline value)
    fields: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        m = FIELD_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).upper(), _strip_markdown(m.group(2))
        if key == "RESIGN":
            # RESIGN must stand alone ("RESIGN" / "**RESIGN**" / "RESIGN."),
            # so prose like "I won't resign yet" doesn't count.
            if value.strip(" .!:") or line.strip(" >#*_`-").upper().rstrip(".!:") != "RESIGN":
                continue
        elif key == "MOVE":
            if ":" not in line and "=" not in line:
                continue
        elif ":" not in line:
            continue
        fields.append((i, key, value))

    anchors = [f for f in fields if f[1] in ("MOVE", "RESIGN")]
    if not anchors:
        plan = _field_value(lines, fields, "PLAN", start=-1)
        return ParsedResponse(kind="none", plan=plan)

    anchor_idx, anchor_key, anchor_val = anchors[-1]
    plan = _field_value(lines, fields, "PLAN", start=anchor_idx)
    # ANALYSIS normally comes just before MOVE; take the last one before the
    # anchor, or else the first one after it.
    before = [f[0] for f in fields if f[1] == "ANALYSIS" and f[0] < anchor_idx]
    analysis = _field_value(lines, fields, "ANALYSIS", start=before[-1] - 1 if before else anchor_idx)
    if anchor_key == "MOVE":
        return ParsedResponse(kind="move", move=clean_move_token(anchor_val),
                              move_raw=anchor_val, plan=plan, analysis=analysis)

    explanation = _field_value(lines, fields, "EXPLANATION", start=anchor_idx)
    line_raw = _field_value(lines, fields, "LINE", start=anchor_idx)
    return ParsedResponse(kind="resign", plan=plan, explanation=explanation, analysis=analysis,
                          line_raw=line_raw, line=tokenize_line(line_raw or ""))


def _field_value(lines: list[str], fields: list[tuple[int, str, str]], key: str,
                 start: int) -> str | None:
    """Value of the first `key` field after line `start`, including continuation
    lines up to the next field line."""
    field_lines = sorted(f[0] for f in fields)
    for idx, k, value in fields:
        if k != key or idx <= start:
            continue
        nxt = next((j for j in field_lines if j > idx), len(lines))
        parts = [value] + [_strip_markdown(l) for l in lines[idx + 1:nxt]]
        joined = "\n".join(parts).strip()
        return joined or None
    return None


def clean_move_token(s: str | None) -> str | None:
    """'**12... Nxf3+!**' -> 'Nxf3'; '0-0' -> 'O-O'; 'e7e8=q' kept for UCI parsing."""
    if not s:
        return None
    s = _strip_markdown(s).strip().strip("\"'“”‘’()[]{}")
    if not s:
        return None
    tokens = s.split()
    # Drop move-number tokens like "12." / "12..." / "..."
    tokens = [t for t in tokens if not re.fullmatch(r"\d+\.*|\.{2,}", t)]
    if not tokens:
        return None
    tok = re.sub(r"^\d+\.+", "", tokens[0])          # "12.Nf3"
    tok = tok.strip(".,;:\"'“”‘’()[]{}")
    tok = re.sub(r"[+#!?]+$", "", tok)
    tok = re.sub(r"[+#!?]", "", tok)
    tok = re.sub(r"(?i)e\.?p\.?$", "", tok)           # "exd6e.p."
    if re.fullmatch(r"[0oO]-[0oO]-[0oO]", tok):
        tok = "O-O-O"
    elif re.fullmatch(r"[0oO]-[0oO]", tok):
        tok = "O-O"
    return tok or None


def tokenize_line(s: str) -> list[str]:
    s = _strip_markdown(s).replace("→", " ").replace("->", " ").replace(",", " ")
    out: list[str] = []
    for raw in s.split():
        if raw in RESULT_TOKENS or re.fullmatch(r"\d+\.*|\.{2,}", raw):
            continue
        tok = clean_move_token(raw)
        if tok and tok not in RESULT_TOKENS:
            out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Move resolution with human-readable reasons
# ---------------------------------------------------------------------------

def resolve_move(board: chess.Board, token: str | None) -> tuple[chess.Move | None, str | None]:
    """Returns (move, None) if legal, else (None, reason)."""
    if not token:
        return None, "could not parse a move (expected a line like 'MOVE: Nf3')"

    # UCI first (e2e4, e7e8q, e2-e4). A square-to-square token means the same
    # thing whether read as UCI or as fully disambiguated SAN.
    m = UCI_RE.match(token)
    if m:
        return _resolve_uci(board, token, m)

    candidates = [token]
    if token[:1] in "nbrqk" and len(token) >= 3 and not re.fullmatch(r"[a-h]x?[a-h][1-8].*", token):
        candidates.append(token[0].upper() + token[1:])
    if re.fullmatch(r"[a-h][1-8][qrbnQRBN]", token[-3:] or "") and "=" not in token:
        candidates.append(token[:-1] + "=" + token[-1].upper())
    last_err: Exception | None = None
    for cand in candidates:
        try:
            return board.parse_san(cand), None
        except chess.AmbiguousMoveError:
            return None, _ambiguous_reason(board, cand)
        except (chess.IllegalMoveError, chess.InvalidMoveError) as e:
            last_err = e
    if isinstance(last_err, chess.IllegalMoveError) or SAN_RE.match(token) or token in ("O-O", "O-O-O"):
        return None, explain_illegal_san(board, token)
    return None, f"could not understand '{token}' as a move in SAN (e.g. Nf3) or UCI (e.g. g1f3)"


def _resolve_uci(board: chess.Board, token: str, m: re.Match) -> tuple[chess.Move | None, str | None]:
    from_sq = chess.parse_square(m.group(1))
    to_sq = chess.parse_square(m.group(2))
    promo = chess.Piece.from_symbol(m.group(3).lower()).piece_type if m.group(3) else None
    piece = board.piece_at(from_sq)
    if piece is None:
        return None, f"{token} is illegal: there is no piece on {m.group(1)}"
    if piece.color != board.turn:
        return None, f"{token} is illegal: the piece on {m.group(1)} belongs to your opponent"
    # Castling written as king-takes-rook or king two squares: python-chess accepts both.
    move = chess.Move(from_sq, to_sq, promotion=promo)
    if piece.piece_type == chess.PAWN and promo is None and chess.square_rank(to_sq) in (0, 7):
        if chess.Move(from_sq, to_sq, promotion=chess.QUEEN) in board.legal_moves:
            return None, f"{token} is incomplete: a pawn reaching the last rank must promote (e.g. {token}q)"
    if move in board.legal_moves:
        return move, None
    try:
        parsed = board.parse_uci(move.uci())
        return parsed, None
    except (chess.IllegalMoveError, chess.InvalidMoveError):
        pass
    name = PIECE_NAMES[piece.piece_type]
    if move in board.pseudo_legal_moves:
        return None, f"{token} is illegal: {_check_reason(board)}"
    return None, f"{token} is illegal: your {name} on {m.group(1)} cannot move to {m.group(2)}"


def _check_reason(board: chess.Board) -> str:
    if board.is_check():
        return "you are in check and this move does not get out of check"
    return "it would leave your king in check"


def _ambiguous_reason(board: chess.Board, token: str) -> str:
    m = SAN_RE.match(token)
    options = []
    if m:
        target = chess.parse_square(m.group(4))
        piece_type = chess.Piece.from_symbol(m.group(1)).piece_type if m.group(1) else chess.PAWN
        for mv in board.legal_moves:
            p = board.piece_at(mv.from_square)
            if mv.to_square == target and p and p.piece_type == piece_type:
                options.append(board.san(mv))
    hint = f" (could be {' or '.join(options)})" if options else ""
    return f"{token} is ambiguous{hint}; say which piece moves"


def explain_illegal_san(board: chess.Board, token: str) -> str:
    if token in ("O-O", "O-O-O"):
        side = "kingside" if token == "O-O" else "queenside"
        why = "you are in check" if board.is_check() else (
            "the king or rook has moved, the squares between are not empty, "
            "or the king would pass through or land on an attacked square")
        return f"{token} is illegal: castling {side} is not possible here ({why})"
    m = SAN_RE.match(token)
    if not m:
        return f"could not understand '{token}' as a move"
    piece_sym, file_hint, rank_hint, target_name, _promo = m.groups()
    piece_type = chess.Piece.from_symbol(piece_sym).piece_type if piece_sym else chess.PAWN
    target = chess.parse_square(target_name)
    name = PIECE_NAMES[piece_type]

    own = board.piece_at(target)
    if own and own.color == board.turn:
        return f"{token} is illegal: {target_name} is occupied by your own {PIECE_NAMES[own.piece_type]}"

    pseudo = [mv for mv in board.pseudo_legal_moves
              if mv.to_square == target
              and board.piece_type_at(mv.from_square) == piece_type
              and (file_hint is None or chess.square_file(mv.from_square) == "abcdefgh".index(file_hint))
              and (rank_hint is None or chess.square_rank(mv.from_square) == int(rank_hint) - 1)]
    if not pseudo:
        if piece_type == chess.PAWN and "x" in token and board.piece_at(target) is None:
            return f"{token} is illegal: there is nothing to capture on {target_name}"
        qualifier = "" if not (file_hint or rank_hint) else f" from {file_hint or ''}{rank_hint or ''}"
        return f"{token} is illegal: no {name}{qualifier} can reach {target_name}"
    return f"{token} is illegal: {_check_reason(board)}"


def validate_resignation_line(board: chess.Board, tokens: list[str],
                              min_plies: int = 2) -> tuple[bool, str | None, list[str]]:
    """Plays the LINE on a copy of the board. Returns (ok, reason, san_list)."""
    if not tokens:
        return False, "the resignation has no LINE (a sequence of at least 2 legal moves is required)", []
    b = board.copy(stack=False)
    sans: list[str] = []
    for i, tok in enumerate(tokens):
        mv, err = resolve_move(b, tok)
        if mv is None:
            return False, f"LINE move {i + 1} ('{tok}') is illegal in that line: {err}", sans
        sans.append(b.san(mv))
        b.push(mv)
    if len(sans) < min_plies:
        return False, f"the LINE must contain at least {min_plies} legal moves (plies); it had {len(sans)}", sans
    return True, None, sans
