"""A minimal UCI engine for tests. Scores every position as +0.42, plays the
first legal move. With argv[1] == 'crash' it exits on the second 'go'."""
import sys

import chess

board = chess.Board()
gos = 0
multipv = 1
crash = len(sys.argv) > 1 and sys.argv[1] == "crash"
vary = len(sys.argv) > 1 and sys.argv[1] == "vary"   # material-based scores, for UI demos
VALUES = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 320, chess.ROOK: 500, chess.QUEEN: 900}


def score(b):
    if not vary:
        return 42
    s = sum(v * (len(b.pieces(t, b.turn)) - len(b.pieces(t, not b.turn))) for t, v in VALUES.items())
    return s + (hash(b.board_fen()) % 61) - 30
for line in sys.stdin:
    cmd = line.strip()
    if cmd == "uci":
        print("id name FakeFish 1.0\noption name Threads type spin default 1 min 1 max 8\n"
              "option name Hash type spin default 16 min 1 max 1024\n"
              "option name MultiPV type spin default 1 min 1 max 500\nuciok", flush=True)
    elif cmd == "isready":
        print("readyok", flush=True)
    elif cmd.startswith("setoption name MultiPV value "):
        multipv = int(cmd.split()[-1])
    elif cmd.startswith("position fen "):
        fen = cmd[len("position fen "):].split(" moves ")[0]
        board = chess.Board(fen)
    elif cmd.startswith("position startpos"):
        board = chess.Board()
    elif cmd.startswith("go"):
        gos += 1
        if crash and gos >= 2:
            sys.exit(3)
        moves = list(board.legal_moves)[:multipv]
        for k, mv in enumerate(moves):
            print(f"info depth 7 multipv {k + 1} score cp {score(board) - 10 * k} pv {mv.uci()}", flush=True)
        print(f"bestmove {moves[0].uci()}", flush=True)
    elif cmd == "quit":
        break
