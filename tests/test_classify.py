import chess

from arena import classify as C
from arena.classify import classify, en_prise, sacrificed_material, win_pct


def line(uci, cp=None, mate=None, board=None):
    d = {"uci": uci, "san": board.san(chess.Move.from_uci(uci)) if board else uci, "pv": [uci]}
    d.update({"mate": mate} if mate is not None else {"cp": cp})
    return d


def run(fen, uci, lines, after, prev=None):
    b = chess.Board(fen)
    return classify(b, chess.Move.from_uci(uci), [line(*ln, board=b) if isinstance(ln, tuple) else ln for ln in lines],
                    after, chess.Move.from_uci(prev) if prev else None)


START = chess.STARTING_FEN
TOP3 = [("e2e4", 30), ("d2d4", 25), ("g1f3", 20)]


def test_win_pct():
    assert win_pct({"cp": 0}, chess.WHITE) == 50
    assert win_pct({"cp": 300}, chess.WHITE) + win_pct({"cp": 300}, chess.BLACK) == 100
    assert win_pct({"mate": 3}, chess.WHITE) == 100 and win_pct({"mate": -2}, chess.WHITE) == 0
    assert win_pct({"mate": 0, "winner": "black"}, chess.BLACK) == 100
    assert win_pct({"cp": 5000}, chess.WHITE) == win_pct({"cp": 1500}, chess.WHITE)


def test_top_move_is_best():
    c = run(START, "e2e4", TOP3, {"cp": 30})
    assert c["label"] == "best" and c["rank"] == 1 and c["loss"] == 0 and c["best_san"] == "e4"


def test_second_line_is_best_when_close():
    c = run(START, "d2d4", TOP3, {"cp": 25})
    assert c["label"] == "best" and c["rank"] == 2


def test_second_line_is_not_best_when_clearly_worse():
    c = run(START, "d2d4", [("e2e4", 300), ("d2d4", 60)], {"cp": 60})
    assert c["rank"] == 2 and c["label"] in ("inaccuracy", "mistake") and c["loss"] >= 10


def test_third_line_bands():
    assert run(START, "g1f3", TOP3, {"cp": 20})["label"] == "excellent"


def test_bands_for_moves_outside_the_top_lines():
    # the loss is measured against the best line; a move outside the top lines is
    # never rated better than the last of them
    labels = {cp: run(START, "a2a3", TOP3, {"cp": cp})["label"] for cp in (15, -20, -70, -180, -600)}
    assert labels == {15: "excellent", -20: "good", -70: "inaccuracy", -180: "mistake", -600: "blunder"}
    assert run(START, "a2a3", TOP3, {"cp": 100})["loss"] > 0      # capped by the 3rd line


def test_black_moves_use_blacks_point_of_view():
    b = chess.Board(); b.push_san("e4")
    fen = b.fen()
    lines = [("e7e5", 30), ("c7c5", 35), ("e7e6", 40)]    # White's view: lower is better for Black
    assert run(fen, "e7e5", lines, {"cp": 30})["label"] == "best"
    assert run(fen, "g7g5", lines, {"cp": 500})["label"] == "blunder"


def test_missing_data_gives_none():
    assert run(START, "e2e4", [], {"cp": 30}) is None
    assert classify(chess.Board(), chess.Move.from_uci("e2e4"), [line("e2e4", 30)], None) is None


GREEK_GIFT = "r1bq1rk1/pppn1ppp/2n1p3/3pP3/1b1P4/2NB1N2/PPP2PPP/R1BQK2R w KQ - 0 8"


def test_sacrifice_detection():
    b = chess.Board(GREEK_GIFT)
    assert b.is_valid()
    mv = chess.Move.from_uci("d3h7")
    assert sacrificed_material(b, mv) == 2            # bishop for a pawn
    assert sacrificed_material(b, chess.Move.from_uci("a2a3")) == 0


def test_sound_sacrifice_is_brilliant():
    c = run(GREEK_GIFT, "d3h7", [("d3h7", 150), ("e1g1", 60)], {"cp": 150})
    assert c["label"] == "brilliant" and "sacrifice" in c["tags"]


def test_unsound_or_unneeded_sacrifice_is_not_brilliant():
    # not the engine's choice
    assert run(GREEK_GIFT, "d3h7", [("e1g1", 150), ("a2a3", 140), ("h2h3", 130)], {"cp": -200})["label"] != "brilliant"
    # already completely winning
    assert run(GREEK_GIFT, "d3h7", [("d3h7", 900), ("e1g1", 850)], {"cp": 900})["label"] == "best"


FORK = "r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1"


def test_fork_is_great():
    c = run(FORK, "b5c7", [("b5c7", 500), ("e1d2", 0), ("e1e2", 0)], {"cp": 500})
    assert c["label"] == "great" and "fork" in c["tags"] and "only_move" in c["tags"]


def test_recapture_is_not_great():
    fen = "4k3/8/8/3n4/4P3/8/8/4K3 w - - 0 1"   # Black's knight just took on d5
    c = run(fen, "e4d5", [("e4d5", 400), ("e1d2", -50)], {"cp": 400}, prev="f6d5")
    assert c["label"] == "best"


def test_retreating_an_attacked_piece_is_not_great():
    # 1. e4 e5 2. Nf3 Nc6 3. Bb5 a6: the bishop must move
    b = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]:
        b.push_san(san)
    c = run(b.fen(), "b5a4", [("b5a4", 30), ("b5c6", 0), ("e1g1", -250)], {"cp": 30}, prev="a7a6")
    assert c["label"] == "best" and "only_move" not in c["tags"]


def test_trap_is_great():
    # White pushes e4 next to Black's rook on d4: the pawn looks free, but none of
    # Black's top replies takes it
    b = chess.Board("4k3/8/8/8/3r4/8/4P3/4K3 w - - 0 1")
    mv = chess.Move.from_uci("e2e4")
    assert en_prise(b, mv, pawns=True)
    before = [line("e2e4", 200, board=b), line("e1e2", 0, board=b)]
    after = {"cp": 200, "lines": [line("d4d1", 200), line("e8e7", 450)]}
    c = classify(b, mv, before, after)
    assert c["label"] == "great" and "trap" in c["tags"]
    # taking is just one of several fine replies (e.g. the Open Spanish): no trap
    close = {"cp": 200, "lines": [line("d4d1", 200), line("e8e7", 220)]}
    assert "trap" not in classify(b, mv, before, close)["tags"]
    # if the engine's reply takes the pawn, it was no trap
    after_takes = {"cp": 200, "lines": [line("d4e4", 200), line("e8e7", 220)]}
    assert "trap" not in classify(b, mv, before, after_takes)["tags"]


def test_forced_move_is_best():
    b = chess.Board("k7/8/8/8/8/8/1q6/K7 w - - 0 1")
    assert b.legal_moves.count() == 1
    m = next(iter(b.legal_moves)).uci()
    c = run(b.fen(), m, [(m, -100)], {"cp": -100})
    assert c["label"] == "best" and "forced" in c["tags"]


def test_mate_scores():
    # keeping a forced mate (even a slower one) loses nothing
    c = run(START, "d2d4", [("e2e4", None, 3), ("d2d4", None, 5)], {"mate": 5})
    assert c["label"] == "best" and c["loss"] == 0
    # letting a mate slip to an equal position is a blunder
    assert run(START, "a2a3", [("e2e4", None, 3), ("d2d4", None, 5), ("g1f3", None, 6)], {"cp": 0})["label"] == "blunder"


def test_thresholds_are_ordered():
    assert [b[0] for b in C.BANDS] == sorted(b[0] for b in C.BANDS)
