import json
import random

import chess
import chess.pgn
import pytest

from arena import openings
from arena.agents.claude_code import claude_tokens
from arena.agents.codex import codex_tokens
from arena.state import BLACK, WHITE
from arena.storage import RecordDB, compute_records, save_game, summarize

from .conftest import run


# --------------------------------------------------------------------------- openings

def test_every_book_line_is_legal_and_catalog_renders():
    for o in openings.OPENINGS.values():
        b = chess.Board()
        for san in o["moves"]:
            b.push_san(san)
    cat = openings.catalog()
    assert len(cat) == len(openings.OPENINGS)
    ruy = next(c for c in cat if c["id"] == "ruy-lopez")
    assert ruy["line"] == "1. e4 e5 2. Nf3 Nc6 3. Bb5" and ruy["eco"] == "C60"


def test_resolve_choices():
    assert openings.resolve("free") is None and openings.resolve(None) is None
    assert openings.resolve("e4")["moves"] == ["e4"]
    r = openings.resolve("random", rng=random.Random(3))
    assert r["random"] and r["id"] in openings.OPENINGS and len(r["moves"]) >= 2
    c = openings.resolve("custom", "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4")
    assert c["moves"] == ["d4", "Nf6", "c4", "e6", "Nc3", "Bb4"]
    with pytest.raises(ValueError, match="illegal"):
        openings.resolve("custom", "1. e4 e4")
    with pytest.raises(ValueError, match="unknown"):
        openings.resolve("no-such")
    with pytest.raises(ValueError, match="at least one"):
        openings.resolve("custom", "")


def test_referee_plays_book_moves_then_agents(make_game, tmp_path):
    ref, agents = make_game(opening=openings.resolve("ruy-lopez"), move_cap=7)
    st = run(ref)
    assert [p.san for p in st.plies[:5]] == ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    assert all(p.book and p.attempts == [] and p.agent_id == "book" for p in st.plies[:5])
    assert not st.plies[5].book and st.plies[5].color == BLACK   # Black moves first after 3.Bb5
    # agents never moved during the book phase
    assert st.white.moves == 1 and st.black.moves == 1
    # the first prompt names the opening; Black's first prompt is at move 3
    bp = agents[BLACK].prompts[0]
    assert "Ruy Lopez (1. e4 e5 2. Nf3 Nc6 3. Bb5)" in bp and "It is move 3, Black to play" in bp
    assert "not had a turn yet" in bp
    assert st.settings["opening"] == "Ruy Lopez"
    pgn_path, _ = save_game(st, ref.board, tmp_path)
    game = chess.pgn.read_game(pgn_path.open(encoding="utf-8"))
    assert game.headers["Opening"] == "Ruy Lopez" and game.headers["ECO"] == "C60"
    assert game.next().comment.startswith("book")


def test_free_opening_has_no_book_moves(make_game):
    ref, agents = make_game(move_cap=2)
    st = run(ref)
    assert st.opening is None and not any(p.book for p in st.plies)
    assert "referee chose the opening" not in agents[WHITE].prompts[0]
    assert st.settings["opening"] == "free"


# --------------------------------------------------------------------------- tokens

def test_token_normalization():
    assert claude_tokens({"input_tokens": 2, "cache_creation_input_tokens": 627, "cache_read_input_tokens": 100,
                          "output_tokens": 127, "output_tokens_details": {"thinking_tokens": 86}}) == \
        {"input": 729, "cached": 100, "output": 127, "reasoning": 86}
    assert codex_tokens({"input_tokens": 20149, "cached_input_tokens": 9728, "output_tokens": 80,
                         "reasoning_output_tokens": 14}) == \
        {"input": 20149, "cached": 9728, "output": 80, "reasoning": 14}
    assert claude_tokens(None) is None and codex_tokens({}) is None


def test_tokens_accumulate_per_attempt_move_and_side(make_game):
    ref, _ = make_game(black=["illegal", "random"], move_cap=4)
    st = run(ref)
    rec = st.plies[1]
    assert len(rec.attempts) == 2 and all(a.tokens for a in rec.attempts)
    assert rec.tokens["input"] == sum(a.tokens["input"] for a in rec.attempts)
    assert rec.tokens["calls"] == 2
    assert st.black.tokens["calls"] == 3 and st.white.tokens["calls"] == 2
    assert st.black.tokens["input"] == sum(a.tokens["input"] for p in st.plies if p.color == BLACK
                                           for a in p.attempts)
    assert st.plies[-1].tokens_after[BLACK] == st.black.tokens


def test_token_totals_in_records(make_game, tmp_path):
    ref, _ = make_game(move_cap=4)
    st = run(ref)
    db = RecordDB(tmp_path / "r.db")
    db.upsert(summarize(st.to_dict()))
    row = db.games()[0]
    assert row["white_tokens_in"] == st.white.tokens["input"] and row["opening"] == "free"
    rec = db.records()["agents"]["w"]
    assert rec["avg_tokens_in_per_move"] == round(st.white.tokens["input"] / 2)


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE games (id TEXT PRIMARY KEY, started_at TEXT, white_id TEXT, black_id TEXT, "
                "white_name TEXT, black_name TEXT, result TEXT, termination TEXT, reason TEXT, winner TEXT, "
                "legal_moves_sent INTEGER, plies INTEGER, white_think_total REAL, black_think_total REAL, "
                "white_moves INTEGER, black_moves INTEGER, white_illegal INTEGER, black_illegal INTEGER, "
                "white_tool_violations INTEGER, black_tool_violations INTEGER, finished_at TEXT)")
    con.execute("INSERT INTO games (id, started_at, white_id, black_id, white_name, black_name, result, winner, "
                "legal_moves_sent, plies, white_think_total, black_think_total, white_moves, black_moves, "
                "white_illegal, black_illegal, white_tool_violations, black_tool_violations) "
                "VALUES ('g', 't', 'a', 'b', 'A', 'B', '1-0', 'white', 0, 10, 1, 1, 5, 5, 0, 0, 0, 0)")
    con.commit(); con.close()
    db = RecordDB(path)
    rec = db.records()["agents"]["a"]
    assert rec["total"]["w"] == 1 and rec["avg_tokens_in_per_move"] is None   # no token data: not zero


# --------------------------------------------------------------------------- Codex running totals

# Real values from a game: `codex exec --json` turn.completed usage for one thread.
# They are running totals (cross-checked against the rollout log's last_token_usage).
CODEX_CUMULATIVE = [
    {"input_tokens": 9900, "cached_input_tokens": 2432, "output_tokens": 39, "reasoning_output_tokens": 0},
    {"input_tokens": 20151, "cached_input_tokens": 12160, "output_tokens": 76, "reasoning_output_tokens": 0},
    {"input_tokens": 30761, "cached_input_tokens": 22272, "output_tokens": 122, "reasoning_output_tokens": 0},
]
PER_CALL_INPUT = [9900, 10251, 10610]      # rollout last_token_usage.input_tokens for the same calls


def test_codex_running_totals_become_per_call(tmp_path, monkeypatch):
    from .test_adapters import fake_run, jl, make_codex
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nohome"))
    a = make_codex(tmp_path)
    got = []
    for i, usage in enumerate(CODEX_CUMULATIVE):
        events = ([{"type": "thread.started", "thread_id": "T"}] if i == 0 else []) + [
            {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "MOVE: e4"}},
            {"type": "turn.completed", "usage": usage}]
        a.runner.run = fake_run(jl(*events))
        got.append(a.ask("p", None if i == 0 else "T", 10).tokens)
    assert [t["input"] for t in got] == PER_CALL_INPUT
    assert [t["output"] for t in got] == [39, 37, 46]
    assert sum(t["input"] for t in got) == CODEX_CUMULATIVE[-1]["input_tokens"]
    # a new thread (e.g. after a failed resume) starts counting from zero again
    a.runner.run = fake_run(jl({"type": "thread.started", "thread_id": "T2"},
                               {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "x"}},
                               {"type": "turn.completed", "usage": CODEX_CUMULATIVE[0]}))
    assert a.ask("p", None, 10).tokens["input"] == 9900


def test_recompute_tokens_repairs_old_logs():
    from arena.storage import recompute_tokens

    def att(sid, usage, tokens):
        return {"session_id": sid, "meta": {"usage": usage}, "tokens": tokens}
    claude_usage = {"input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0,
                    "output_tokens": 50}
    data = {
        "white": {"agent": {"cli": "codex"}, "tokens": {"input": 60812}},
        "black": {"agent": {"cli": "claude_code"}, "tokens": {}},
        "final_turn": None,
        "plies": [
            {"color": "white", "attempts": [att("T", CODEX_CUMULATIVE[0], {"input": 9900})]},
            {"color": "black", "attempts": [att("S", claude_usage, None)]},
            # a rejected attempt and its retry inside one move, same thread
            {"color": "white", "attempts": [att("T", CODEX_CUMULATIVE[1], {"input": 20151}),
                                            att("T", CODEX_CUMULATIVE[2], {"input": 30761})]},
        ],
    }
    recompute_tokens(data)
    assert data["white"]["tokens"]["input"] == 30761 and data["white"]["tokens"]["calls"] == 3
    assert data["plies"][2]["tokens"]["input"] == 10251 + 10610
    assert data["black"]["tokens"]["input"] == 1002
    assert data["plies"][2]["tokens_after"]["white"]["input"] == 30761
