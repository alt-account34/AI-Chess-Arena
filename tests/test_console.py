"""Live agent console and think-aloud mode."""
import json
import re

from fastapi.testclient import TestClient

from arena import prompts
from arena.agents.claude_code import console_events as claude_events
from arena.agents.codex import console_events as codex_events
from arena.console import ConsoleLog, console_path, read_console
from arena.parsing import parse_response
from arena.state import BLACK, WHITE

from .conftest import run


# --------------------------------------------------------------------------- think aloud

def test_analysis_is_parsed_before_or_after_move():
    p = parse_response("ANALYSIS: Nf3 or d4.\nBoth fine; d4 grabs space.\nMOVE: d4\nPLAN: c4 next")
    assert p.move == "d4" and p.analysis == "Nf3 or d4.\nBoth fine; d4 grabs space." and p.plan == "c4 next"
    p = parse_response("MOVE: e4\nANALYSIS: central\nPLAN: x")
    assert p.analysis == "central"
    assert parse_response("MOVE: e4\nPLAN: x").analysis is None


def test_think_aloud_prompts(make_game):
    ref, agents = make_game(move_cap=4, think_aloud=True)
    st = run(ref)
    first, later = agents[WHITE].prompts[0], agents[WHITE].prompts[1]
    assert "ANALYSIS: <3-6 sentences" in first and "ANALYSIS" in later
    assert st.settings["think_aloud"] is True
    ref, agents = make_game(move_cap=2)
    run(ref)
    assert "ANALYSIS" not in agents[WHITE].prompts[0]


def test_analysis_is_stored(make_game):
    ref, _ = make_game(white=["move:e4"], move_cap=1)
    agent = ref.agents[WHITE]
    orig = agent.ask

    def ask(prompt, sid, timeout):
        r = orig(prompt, sid, timeout)
        r.text = "ANALYSIS: e4 controls the centre.\n" + r.text
        return r
    agent.ask = ask
    st = run(ref)
    assert st.plies[0].analysis == "e4 controls the centre."
    assert st.plies[0].attempts[0].analysis == "e4 controls the centre."


# --------------------------------------------------------------------------- console feed

def test_referee_writes_console_feed(make_game, tmp_path):
    ref, agents = make_game(black=["illegal", "random", "tool"], move_cap=4)
    published = []
    ref.console = ConsoleLog(ref.state.id, console_path(tmp_path, ref.state.id), published.append)
    st = run(ref)
    ref.console.close()
    evs = read_console(tmp_path, ref.state.id, BLACK)
    kinds = [e["k"] for e in evs]
    for k in ("turn", "prompt", "session", "thinking", "text", "result", "status"):
        assert k in kinds, k
    # the prompt in the feed is exactly what the agent received
    prompts_sent = [e["text"] for e in evs if e["k"] == "prompt"]
    assert prompts_sent == agents[BLACK].prompts
    results = [e for e in evs if e["k"] == "result"]
    assert results[0]["accepted"] is False and "no king can reach" in results[0]["reason"]
    assert results[1]["accepted"] is True and results[1]["tokens"]["output"] > 0
    assert any(e["k"] == "tool" for e in evs)
    assert [e["seq"] for e in published] == sorted(e["seq"] for e in published)
    # streamed text reassembles to the raw reply
    assert "".join(e["delta"] for e in evs if e["k"] == "text").startswith("I'll go for it.")
    # stored attempts carry the exact prompt
    assert st.plies[1].attempts[0].prompt == agents[BLACK].prompts[0]


def test_console_never_reaches_prompts(make_game, tmp_path):
    """The console sees prompts; prompts never see the console."""
    ref, agents = make_game(move_cap=6)
    ref.console = ConsoleLog(ref.state.id, console_path(tmp_path, ref.state.id))
    ref.console.emit(WHITE, "notice", text="SENTINEL-CONSOLE-4711")
    run(ref)
    for p in agents[WHITE].prompts + agents[BLACK].prompts:
        assert "SENTINEL-CONSOLE-4711" not in p


def test_claude_console_translation():
    sd = lambda e: json.dumps(e)
    assert claude_events(sd({"type": "stream_event", "event": {"type": "content_block_start",
                                                               "content_block": {"type": "thinking"}}})) == [("thinking", {})]
    assert claude_events(sd({"type": "stream_event", "event": {"type": "content_block_delta",
                                                               "delta": {"type": "text_delta", "text": "MOVE"}}})) == [("text", {"delta": "MOVE"})]
    ev = claude_events(sd({"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": ""}, {"type": "tool_use", "name": "Read", "input": {"p": 1}}]}}))
    assert ev[0] == ("thinking_done", {"redacted": True}) and ev[1][0] == "tool"
    assert claude_events("not json") == []


def test_codex_console_translation():
    sd = lambda e: json.dumps(e)
    assert codex_events(sd({"type": "turn.started"})) == [("thinking", {})]
    assert codex_events(sd({"type": "item.completed", "item": {"type": "reasoning", "text": "**Choosing Bb5**"}})) == \
        [("reasoning", {"text": "**Choosing Bb5**"})]
    assert codex_events(sd({"type": "item.completed", "item": {"type": "agent_message", "text": "MOVE: e4"}})) == \
        [("message", {"text": "MOVE: e4"})]
    assert codex_events(sd({"type": "item.completed", "item": {"type": "command_execution", "command": "dir"}}))[0][0] == "tool"


def test_codex_reasoning_summary_flag_and_storage(tmp_path):
    from arena.agents.codex import parse_events
    from .test_adapters import jl, make_codex
    a = make_codex(tmp_path)
    assert 'model_reasoning_summary="detailed"' in a.build_argv(None)
    r = parse_events(jl({"type": "item.completed", "item": {"type": "reasoning", "text": "**Choosing Bb5**"}},
                        {"type": "item.completed", "item": {"type": "agent_message", "text": "MOVE: Bb5"}}))
    assert r.meta["reasoning"] == ["**Choosing Bb5**"] and r.text == "MOVE: Bb5"


def test_claude_streams_partial_messages(tmp_path):
    from .test_adapters import make_claude
    assert "--include-partial-messages" in make_claude(tmp_path).build_argv(None, "N")


# --------------------------------------------------------------------------- server

def test_console_websocket_history_then_live(settings, tmp_path, monkeypatch):
    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text("agents:\n  - {id: r1, cli: mock}\n  - {id: r2, cli: mock, script: [resign]}\n",
                           encoding="utf-8")
    monkeypatch.setenv("ARENA_AGENTS", str(agents_yaml))
    from arena.server import create_app
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/console?side=black") as ws:
            hist = ws.receive_json()
            assert hist["k"] == "history" and hist["events"] == []
            gid = client.post("/api/game/start", json={"white": "r1", "black": "r2", "think_aloud": True}).json()["id"]
            kinds = []
            while True:
                ev = ws.receive_json()
                kinds.append(ev["k"])
                if ev["k"] == "status" and ev.get("final"):
                    break
            assert kinds[0] == "new_game" and "prompt" in kinds and "result" in kinds
        import time
        for _ in range(100):                     # wait until the game is fully saved
            if not client.get("/api/settings").json()["running"]:
                break
            time.sleep(0.05)
        # a late viewer of the finished game gets the whole log as history
        with client.websocket_connect(f"/ws/console?side=black&game={gid}") as ws:
            hist = ws.receive_json()
            assert hist["game"] == gid and not hist["live"]
            assert [e["k"] for e in hist["events"]].count("prompt") == 1
        snap = client.get("/api/game/current").json()
        assert snap["settings"]["think_aloud"] is True
        assert "prompt" not in json.dumps(snap["plies"]) or all("prompt" not in a for p in snap["plies"] for a in p["attempts"])
        full = client.get(f"/api/game/{gid}/ply/1").json()
        assert full["attempts"][0]["prompt"].startswith("You are playing a game of chess")
        assert client.get("/api/games?think_aloud=on").json()[0]["id"] == gid
        assert client.get("/api/games?think_aloud=off").json() == []
        assert client.get("/console?side=white").status_code == 200
        assert client.post("/api/console/terminal", json={"side": "green"}).status_code == 400


def test_adapter_console_lines_send_raw_and_translated(tmp_path):
    from .test_adapters import make_codex
    a = make_codex(tmp_path)
    got = []
    a.console = lambda kind, **d: got.append((kind, d))
    on_line = a._console_lines(codex_events)
    on_line(json.dumps({"type": "turn.started"}))
    assert got[0] == ("raw", {"line": '{"type": "turn.started"}'}) and got[1] == ("thinking", {})
    a.console = None
    assert a._console_lines(codex_events) is None
