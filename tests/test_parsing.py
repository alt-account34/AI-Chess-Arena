import chess
import pytest

from arena.parsing import (clean_move_token, parse_response, resolve_move, tokenize_line,
                           validate_resignation_line)


def test_simple_move_and_plan():
    p = parse_response("I like e4.\nMOVE: e4\nPLAN: Control the center.")
    assert p.kind == "move" and p.move == "e4" and p.plan == "Control the center."


@pytest.mark.parametrize("text,expected", [
    ("**MOVE:** `Nf3+`\nPLAN: x", "Nf3"),
    ("MOVE: 12... Nxf3+!", "Nxf3"),
    ("MOVE: 7.Bb5", "Bb5"),
    ("### MOVE: d4", "d4"),
    ("> MOVE: Qh4#", "Qh4"),
    ("move: e2e4", "e2e4"),
    ("MOVE: 0-0", "O-O"),
    ("MOVE: o-o-o", "O-O-O"),
    ("MOVE: exd6 e.p.", "exd6"),
    ("MOVE: \"Rxe8?!\"", "Rxe8"),
    ("MOVE: e8=Q+", "e8=Q"),
    ("MOVE: Nf3\r\nPLAN: crlf\r\n", "Nf3"),
])
def test_forgiving_move_cleanup(text, expected):
    p = parse_response(text)
    assert p.kind == "move"
    assert p.move == expected


def test_last_block_wins():
    text = "Maybe MOVE: e4?\nMOVE: e4\nPLAN: a\n\nOn reflection...\nMOVE: d4\nPLAN: b"
    p = parse_response(text)
    assert p.move == "d4" and p.plan == "b"


def test_resign_after_move_wins_and_fields_parse():
    text = ("MOVE: e4\nPLAN: x\nActually it's hopeless.\n**RESIGN**\n"
            "EXPLANATION: I am down a queen\nand a rook.\nLINE: 1. e4 e5 2. Nf3\nPLAN: none")
    p = parse_response(text)
    assert p.kind == "resign"
    assert p.explanation == "I am down a queen\nand a rook."
    assert p.line == ["e4", "e5", "Nf3"]
    assert p.plan == "none"


def test_prose_mentioning_resign_is_not_a_resignation():
    p = parse_response("I won't resign yet, even though I could resign.\nMOVE: Kh1\nPLAN: hold")
    assert p.kind == "move"
    p2 = parse_response("Should I RESIGN now? No.")
    assert p2.kind == "none"


def test_no_block_is_none():
    p = parse_response("I think the position is balanced.")
    assert p.kind == "none" and p.move is None


def test_move_after_resign_wins():
    p = parse_response("RESIGN\nEXPLANATION: x\nLINE: e4 e5\nWait, no.\nMOVE: d4\nPLAN: fight")
    assert p.kind == "move" and p.move == "d4"


def test_tokenize_line():
    assert tokenize_line("1. e4 e5 2. Nf3+ Nc6!? 3... a6 1-0") == ["e4", "e5", "Nf3", "Nc6", "a6"]
    assert tokenize_line("Qxf7#, Kxf7 -> O-O") == ["Qxf7", "Kxf7", "O-O"]


def test_clean_none():
    assert clean_move_token("") is None
    assert clean_move_token("**") is None


# --------------------------------------------------------------------------- resolve_move

def test_resolve_san_and_uci():
    b = chess.Board()
    assert resolve_move(b, "Nf3")[0] == chess.Move.from_uci("g1f3")
    assert resolve_move(b, "g1f3")[0] == chess.Move.from_uci("g1f3")
    assert resolve_move(b, "e2-e4")[0] == chess.Move.from_uci("e2e4")
    assert resolve_move(b, "nf3")[0] == chess.Move.from_uci("g1f3")  # lowercase piece letter
    assert resolve_move(b, "bc4")[0] is None  # read as a b-pawn move, not a bishop move


def test_no_piece_can_reach():
    mv, reason = resolve_move(chess.Board(), "Nf4")
    assert mv is None and reason == "Nf4 is illegal: no knight can reach f4"


def test_in_check_reason():
    b = chess.Board("4k3/4r3/8/8/8/8/P7/4K3 w - - 0 1")  # rook on e7 checks Ke1
    assert b.is_check()
    mv, reason = resolve_move(b, "a3")
    assert mv is None and "you are in check" in reason


def test_pinned_piece_reason():
    b = chess.Board("4k3/4r3/8/8/8/8/4N3/4K3 w - - 0 1")  # knight on e2 pinned
    mv, reason = resolve_move(b, "Nc3")
    assert mv is None and "leave your king in check" in reason


def test_ambiguous():
    b = chess.Board("4k3/8/8/8/8/5N2/8/1N2K3 w - - 0 1")
    mv, reason = resolve_move(b, "Nd2")
    assert mv is None and "ambiguous" in reason and "Nbd2" in reason and "Nfd2" in reason
    assert resolve_move(b, "Nbd2")[0] == chess.Move.from_uci("b1d2")


def test_unparsable_and_empty():
    assert "could not understand" in resolve_move(chess.Board(), "hello")[1]
    assert "could not parse" in resolve_move(chess.Board(), None)[1]


def test_uci_reasons():
    b = chess.Board()
    assert "no piece on e4" in resolve_move(b, "e4e5")[1]
    assert "belongs to your opponent" in resolve_move(b, "e7e5")[1]
    assert "cannot move to" in resolve_move(b, "g1g3")[1]


def test_promotion_forms():
    b = chess.Board("8/4P3/8/8/8/8/k7/4K3 w - - 0 1")
    for tok in ("e8=Q", "e8Q", "e8q", "e7e8q", "e7e8Q"):
        mv, _ = resolve_move(b, tok)
        assert mv == chess.Move.from_uci("e7e8q"), tok
    mv, reason = resolve_move(b, "e7e8")
    assert mv is None and "must promote" in reason


def test_castling_reason():
    mv, reason = resolve_move(chess.Board(), "O-O")
    assert mv is None and "castling kingside is not possible" in reason


# --------------------------------------------------------------------------- resignation lines

def test_resignation_line_valid():
    ok, why, sans = validate_resignation_line(chess.Board(), ["e4", "e5", "Nf3"])
    assert ok and why is None and sans == ["e4", "e5", "Nf3"]


def test_resignation_line_too_short():
    ok, why, _ = validate_resignation_line(chess.Board(), ["e4"])
    assert not ok and "at least 2" in why


def test_resignation_line_illegal_move():
    ok, why, sans = validate_resignation_line(chess.Board(), ["e4", "e4"])
    assert not ok and "LINE move 2" in why and sans == ["e4"]


def test_resignation_line_empty():
    ok, why, _ = validate_resignation_line(chess.Board(), [])
    assert not ok and "no LINE" in why


def test_resignation_line_does_not_touch_board():
    b = chess.Board()
    validate_resignation_line(b, ["e4", "e5"])
    assert b.fen() == chess.STARTING_FEN
