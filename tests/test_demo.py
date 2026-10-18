import json
from pathlib import Path

from arena.storage import save_game, summarize, compute_records
from tools.export_demo import export, public_game, clean
from tools.build_demo import build
from .conftest import run


def test_public_export_preserves_originals_and_statistics(make_game, tmp_path):
    ref, _ = make_game(black=["illegal", "random", "resign"])
    state = run(ref)
    games = tmp_path / "games"
    _, path = save_game(state, ref.board, games)
    original = path.read_bytes()
    destination = tmp_path / "public"
    assert export(games, destination) == 1
    assert path.read_bytes() == original
    public = json.loads((destination / path.name).read_text(encoding="utf-8"))
    assert len(public["plies"]) == len(state.plies)
    assert public["final_turn"]
    assert public["resignation"]
    assert all(p["console_source"] == "saved responses" for p in public["plies"])
    assert public["plies"][0]["console"]
    assert "session_id" not in json.dumps(public)
    assert "meta" not in public["plies"][0]["attempts"][0]
    records = json.loads((destination / "records-all-all.json").read_text())
    assert records == compute_records([summarize(json.loads(original))])
    manifest = json.loads((destination / "manifest.json").read_text())
    assert "result" not in manifest[0]


def test_transcript_timing_and_sanitization(make_game):
    ref, _ = make_game()
    source = run(ref).to_dict()
    transcript = [
        {"k": "turn", "side": "white", "ply": 1, "t": 100},
        {"k": "raw", "side": "white", "t": 101, "line": "PRIVATE METADATA"},
        {"k": "session", "side": "white", "t": 101, "session_id": "PRIVATE SESSION"},
        {"k": "text", "side": "white", "t": 102, "delta": "hello"},
        {"k": "text", "side": "white", "t": 103, "delta": " world"},
        {"k": "result", "side": "white", "t": 104, "accepted": True},
    ]
    public = public_game(source, transcript)
    console = public["plies"][0]["console"]
    assert [e["k"] for e in console] == ["text", "text", "result"]
    assert [e["at"] for e in console] == [0.5, 0.75, 1]
    assert "PRIVATE" not in json.dumps(public)
    text = clean(r"C:\Users\someone\secret.txt /home/someone/private 12345678-1234-1234-1234-123456789abc")
    assert "someone" not in text and "12345678" not in text


def test_static_build_is_self_contained(tmp_path):
    build(tmp_path)
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "static/vendor/LICENSE-chessground.txt").exists()
    assert not (tmp_path / "arena.db").exists()
    assert not (tmp_path / "games").exists()
    assert "/api/records" not in (tmp_path / "static/js/record.js").read_text()
    manifest = json.loads((tmp_path / "data/manifest.json").read_text())
    source = Path(__file__).resolve().parents[1] / "demo/data/manifest.json"
    assert manifest == json.loads(source.read_text())
    for row in manifest:
        assert (tmp_path / "data" / (row["id"] + ".json")).exists()
