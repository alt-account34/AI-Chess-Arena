import json
import time

import chess
import chess.pgn
from fastapi.testclient import TestClient

from arena.config import AgentConfig
from arena.state import BLACK, WHITE
from arena.storage import RecordDB, compute_records, save_game, summarize

from .conftest import run


def finished(make_game, **kw):
    ref, _ = make_game(**kw)
    st = run(ref)
    return ref, st


def test_save_game_writes_pgn_and_json(make_game, tmp_path):
    ref, st = finished(make_game, black=["illegal", "random", "resign"])
    pgn_path, json_path = save_game(st, ref.board, tmp_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["termination"] == "resignation" and data["resignation"]["line_san"]
    assert data["plies"][1]["attempts"][0]["reason"]          # illegal attempt kept
    assert data["plies"][0]["attempts"][0]["raw_output"]      # raw output kept
    game = chess.pgn.read_game(pgn_path.open(encoding="utf-8"))
    h = game.headers
    assert h["WhiteAgent"] == "w" and h["BlackAgent"] == "b"
    assert h["LegalMovesSent"] == "false" and h["Result"] == "1-0"
    assert h["Termination"] == "Black resigned"
    assert len(list(game.mainline_moves())) == len(st.plies)


def test_pgn_from_custom_fen(make_game, tmp_path):
    ref, st = finished(make_game, white=["move:Qf7"], start_fen="7k/8/6K1/5Q2/8/8/8/8 w - - 0 1")
    pgn_path, _ = save_game(st, ref.board, tmp_path)
    game = chess.pgn.read_game(pgn_path.open(encoding="utf-8"))
    assert game.headers["FEN"] == "7k/8/6K1/5Q2/8/8/8/8 w - - 0 1"
    assert game.headers["Result"] == "1/2-1/2"


def g(id_, w, b, result, winner, legal=0, plies=10):
    return {"id": id_, "white_id": w, "black_id": b, "white_name": w.upper(), "black_name": b.upper(),
            "result": result, "winner": winner, "legal_moves_sent": legal, "plies": plies,
            "white_think_total": 10.0, "black_think_total": 20.0, "white_moves": 5, "black_moves": 5,
            "white_illegal": 1, "black_illegal": 0, "white_tool_violations": 0, "black_tool_violations": 2}


def test_compute_records():
    games = [g("1", "a", "b", "1-0", "white"), g("2", "b", "a", "1-0", "white"),
             g("3", "a", "b", "1/2-1/2", None), g("4", "a", "b", "*", None)]
    rec = compute_records(games)
    a, b = rec["agents"]["a"], rec["agents"]["b"]
    assert a["total"] == {"w": 1, "l": 1, "d": 1} and a["games"] == 3 and a["unfinished"] == 1
    assert a["as_white"] == {"w": 1, "l": 0, "d": 1} and a["as_black"] == {"w": 0, "l": 1, "d": 0}
    assert b["total"] == {"w": 1, "l": 1, "d": 1}
    # a: white in games 1,3 (10s, 5 moves each), black in game 2 (20s, 5 moves)
    assert a["avg_think_per_move"] == round(40 / 15, 2)
    assert a["avg_illegal_per_game"] == round(2 / 3, 2)
    assert a["avg_game_plies"] == 10
    assert b["tool_violations"] == 4
    m = {(x["white_id"], x["black_id"]): x for x in rec["matchups"]}
    assert m[("a", "b")]["games"] == 2 and m[("a", "b")]["white_wins"] == 1 and m[("a", "b")]["draws"] == 1
    assert m[("b", "a")]["white_wins"] == 1


def test_record_db_filter_and_rebuild(make_game, tmp_path):
    db = RecordDB(tmp_path / "x.db")
    ref1, st1 = finished(make_game, move_cap=2)
    ref2, st2 = finished(make_game, move_cap=2, send_legal_moves=True, game_id="g2")
    for ref, st in ((ref1, st1), (ref2, st2)):
        save_game(st, ref.board, tmp_path / "games")
        db.upsert(summarize(st.to_dict()))
    assert len(db.games("all")) == 2
    assert [x["id"] for x in db.games("on")] == ["g2"]
    assert len(db.games("off")) == 1
    assert db.rebuild(tmp_path / "games") == 2
    assert db.records("off")["agents"]["w"]["total"]["d"] == 1


def test_server_end_to_end(settings, tmp_path, monkeypatch):
    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text("agents:\n  - {id: r1, cli: mock}\n  - {id: r2, cli: mock, script: [resign]}\n",
                           encoding="utf-8")
    monkeypatch.setenv("ARENA_AGENTS", str(agents_yaml))
    from arena.server import create_app
    app = create_app(settings)
    with TestClient(app) as client:
        assert [a["id"] for a in client.get("/api/agents").json()] == ["r1", "r2"]
        assert client.post("/api/game/start", json={"white": "nope", "black": "r2"}).status_code == 400
        assert client.post("/api/game/start", json={"white": "r1", "black": "r2", "start_fen": "garbage"}).status_code == 400
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            gid = client.post("/api/game/start", json={"white": "r1", "black": "r2"}).json()["id"]
            types = []
            while "saved" not in types:
                msg = ws.receive_json()
                types.append(msg["type"])
                assert "raw_output" not in json.dumps(msg["state"]["plies"])  # snapshots are lite
        assert "move" in types and "game_over" in types
        snap = client.get("/api/game/current").json()
        assert snap["termination"] == "resignation" and snap["resignation"]["line"]
        ply = client.get(f"/api/game/{gid}/ply/1").json()
        assert ply["attempts"][0]["raw_output"]
        assert client.get(f"/api/game/{gid}/ply/final").json()["attempts"][0]["kind"] == "resign"
        assert client.get("/api/games").json()[0]["id"] == gid
        assert client.get(f"/api/games/{gid}").json()["result"] == "1-0"
        assert client.get(f"/api/games/{gid}/pgn").text.startswith("[Event")
        assert client.get("/api/games/..%2F..%2Fsettings").status_code == 404
        rec = client.get("/api/records?legal_moves=off").json()
        assert rec["agents"]["r1"]["total"]["w"] == 1
        assert client.post("/api/game/pause").status_code == 409   # nothing running
        for page in ("/", "/setup", "/replay", "/record"):
            assert client.get(page).status_code == 200


def test_server_openings_api(settings, tmp_path, monkeypatch):
    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text("agents:\n  - {id: r1, cli: mock}\n  - {id: r2, cli: mock}\n", encoding="utf-8")
    monkeypatch.setenv("ARENA_AGENTS", str(agents_yaml))
    from arena.server import create_app
    with TestClient(create_app(settings)) as client:
        cat = client.get("/api/openings").json()
        assert any(o["id"] == "najdorf" for o in cat)
        bad = client.post("/api/game/start", json={"white": "r1", "black": "r2", "opening": "custom",
                                                   "opening_moves": "1. e4 e4"})
        assert bad.status_code == 400 and "illegal" in bad.json()["detail"]
        both = client.post("/api/game/start", json={"white": "r1", "black": "r2", "opening": "e4",
                                                    "start_fen": chess.STARTING_FEN})
        assert both.status_code == 400
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/game/start", json={"white": "r1", "black": "r2", "opening": "italian",
                                                 "move_cap": 7})
            while ws.receive_json()["type"] != "saved":
                pass
        snap = client.get("/api/game/current").json()
        assert snap["opening"]["name"] == "Italian Game" and [p["book"] for p in snap["plies"]][:6] == [True] * 5 + [False]
        assert snap["white"]["tokens"]["calls"] == 1 and snap["black"]["tokens"]["calls"] == 1
        assert client.get("/api/games").json()[0]["opening"] == "Italian Game"
