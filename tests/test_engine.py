import sys
from pathlib import Path

import chess

from arena.config import Settings
from arena.engine import Analyzer

FAKE = str(Path(__file__).with_name("fake_uci.py"))


def make(*args):
    return Analyzer(Settings(engine_time_s=0.05), command=[sys.executable, FAKE, *args])


def test_analyse_with_engine():
    a = make()
    try:
        assert a.available and a.name == "FakeFish 1.0"
        ev = a.analyse(chess.STARTING_FEN)
        assert ev["cp"] == 42 and ev["depth"] == 7
        # Black to move: score is still reported from White's side
        b = chess.Board(); b.push_san("e4")
        assert a.analyse(b.fen())["cp"] == -42
    finally:
        a.close()


def test_game_over_positions_need_no_engine():
    a = Analyzer(Settings(stockfish_path=None))
    mated = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
    assert a.analyse(mated) == {"mate": 0, "winner": "black", "final": True}
    assert a.analyse("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")["cp"] == 0


def test_missing_binary_falls_back():
    a = Analyzer(Settings(stockfish_path="does/not/exist.exe"))
    assert not a.available and "not found" in a.error
    assert a.analyse(chess.STARTING_FEN) is None


def test_crash_is_survived_and_restarted():
    a = make("crash")
    try:
        assert a.analyse(chess.STARTING_FEN)["cp"] == 42
        assert a.analyse(chess.STARTING_FEN) is None      # engine dies on 2nd go
        assert not a.available
        assert a.analyse(chess.STARTING_FEN)["cp"] == 42  # restarted
    finally:
        a.close()


def test_referee_records_evals(make_game):
    from .conftest import run
    a = make()
    try:
        ref, _ = make_game(move_cap=4, analyzer=a)
        st = run(ref)
        assert st.start_eval["cp"] == 42
        assert all(p.eval is not None for p in st.plies)
        assert st.engine_available is True
    finally:
        a.close()


def test_referee_without_engine(make_game):
    from .conftest import run
    a = Analyzer(Settings(stockfish_path=None))
    ref, _ = make_game(move_cap=2, analyzer=a)
    st = run(ref)
    assert len(st.plies) == 2 and all(p.eval is None for p in st.plies)
    assert st.engine_available is False


def test_invalid_position_is_skipped():
    a = make()
    try:
        # side not to move is in check: illegal, and real Stockfish exits on it
        assert a.analyse("4k3/8/8/8/8/8/8/r3K3 b - - 0 1") is None
        assert a.available and a.analyse(chess.STARTING_FEN)["cp"] == 42
    finally:
        a.close()
