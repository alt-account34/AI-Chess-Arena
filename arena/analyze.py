"""Re-analyses saved games with the current engine settings and classifies every
move, so games played before move classification existed get labels too."""
from __future__ import annotations

import json
from collections import Counter

import chess

from .classify import LABELS
from .config import Settings
from .referee import classify_ply
from .state import GameState, state_from_dict
from .storage import RecordDB, game_paths, load_game, save_game, summarize


def analyze_state(st: GameState, analyzer, progress=None) -> None:
    """Fills start_eval/start_lines and each ply's eval, lines and classification."""
    analyzer.new_game()
    ev = analyzer.analyse(st.start_fen)
    if ev:
        st.start_lines = ev.pop("lines", None)
    st.start_eval = ev
    for rec in st.plies:
        if not rec.fen_after:
            continue
        ev = analyzer.analyse(rec.fen_after)
        if ev:
            rec.lines = ev.pop("lines", None)
        rec.eval = ev
        rec.classification = None if rec.book else classify_ply(st, rec)
        if progress:
            progress(rec.ply, len(st.plies))


def final_board(st: GameState) -> chess.Board:
    board = chess.Board(st.start_fen)
    for rec in st.plies:
        if rec.uci:
            board.push(chess.Move.from_uci(rec.uci))
    return board


def label_counts(st: GameState) -> dict[str, Counter]:
    out = {c: Counter() for c in ("white", "black")}
    for rec in st.plies:
        if rec.classification:
            out[rec.color][rec.classification["label"]] += 1
    return out


def analyze_cli(settings: Settings, ids: list[str], all_games: bool = False, missing_only: bool = False) -> int:
    from .engine import Analyzer
    games_dir = settings.resolve(settings.games_dir)
    if all_games or not ids:
        ids = sorted(p.stem for p in games_dir.glob("*.json"))
    db = RecordDB(settings.resolve(settings.db_path))
    analyzer = Analyzer(settings)
    if not analyzer.available:
        print(f"Stockfish unavailable: {analyzer.error}")
        return 1
    try:
        for gid in ids:
            try:
                data = load_game(games_dir, gid)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"{gid}: skipped ({e!r})")
                continue
            if missing_only and any(p.get("classification") for p in data["plies"]):
                continue
            st = state_from_dict(data)
            print(f"{gid}: {len(st.plies)} plies", flush=True)
            analyze_state(st, analyzer,
                          lambda n, total: print(f"\r  ply {n}/{total}", end="", flush=True))
            save_game(st, final_board(st), games_dir)
            db.upsert(summarize(st.to_dict()))
            counts = label_counts(st)
            print()
            for color in ("white", "black"):
                name = st.side(color).agent.get("display_name") or st.side(color).agent["id"]
                row = ", ".join(f"{lab} {counts[color][lab]}" for lab in LABELS if counts[color][lab])
                print(f"  {color}: {name}: {row or '-'}")
            print(f"  saved {game_paths(games_dir, gid)[1].name}")
    finally:
        analyzer.close()
    return 0
