import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arena.agents.human import HumanAgent
from arena.agents.mock import MockAgent
from arena.config import AgentConfig
from arena.engine import Analyzer
from arena.referee import Referee
from arena.state import BLACK, WHITE

FAKE = str(Path(__file__).with_name("fake_uci.py"))


def make_ref(settings, human=WHITE, black_script=None, analyzer=None):
    cfgs = {WHITE: AgentConfig(id="w", cli="mock", script=[], seed=1, delay_s=0.01),
            BLACK: AgentConfig(id="b", cli="mock", script=black_script or [], seed=2, delay_s=0.01)}
    cfgs[human] = AgentConfig(id="human", cli="human")
    agents = {c: (HumanAgent(cfg, settings) if cfg.cli == "human" else MockAgent(cfg, settings))
              for c, cfg in cfgs.items()}
    return Referee(agents, cfgs, settings, analyzer=analyzer)


async def human_turn(ref, timeout=5.0):
    for _ in range(int(timeout / 0.01)):
        if ref.human_turn_color():
            return ref.human_turn_color()
        await asyncio.sleep(0.01)
    raise AssertionError("no human turn")


def test_human_moves_and_resigns(settings):
    async def main():
        ref = make_ref(settings)
        task = asyncio.create_task(ref.run())
        assert await human_turn(ref) == WHITE
        t = ref.state.turn
        assert t["human"] and sorted(t["dests"]["e2"]) == ["e3", "e4"] and "e1" not in t["dests"]
        with pytest.raises(ValueError):
            ref.submit_human_move("e2e5")
        with pytest.raises(ValueError):
            ref.submit_human_move("zz")
        assert ref.submit_human_move("e2e4") == "e4"
        with pytest.raises(LookupError):      # already submitted
            ref.submit_human_move("d2d4")
        await human_turn(ref)                 # mock Black replied
        assert [p.san for p in ref.state.plies][0] == "e4" and len(ref.state.plies) == 2
        ref.human_resign(WHITE)
        st = await asyncio.wait_for(task, 5)
        assert st.result == "0-1" and st.termination == "resignation" and st.resignation is None
        assert st.plies[0].attempts[0].prompt is None
        assert not [e for e in st.events if e["type"] == "agent_warning"]
    asyncio.run(main())


def test_human_promotion(settings):
    async def main():
        ref = make_ref(settings)
        ref.board.set_fen("8/4P2k/8/8/8/8/8/K7 w - - 0 1")
        ref.state.start_fen = ref.board.fen()
        task = asyncio.create_task(ref.run())
        await human_turn(ref)
        assert ref.submit_human_move("e7e8n") == "e8=N"
        await asyncio.sleep(0.2)
        ref.abort()
        st = await asyncio.wait_for(task, 5)
        assert st.plies[0].san == "e8=N"
    asyncio.run(main())


def test_human_resigns_while_the_engine_side_thinks(settings):
    async def main():
        ref = make_ref(settings, human=BLACK, black_script=None)
        ref.agents[WHITE].delay_s = 30          # White "thinks" for a long time
        task = asyncio.create_task(ref.run())
        await asyncio.sleep(0.2)
        assert ref.state.turn["color"] == WHITE
        ref.human_resign(BLACK)
        st = await asyncio.wait_for(task, 5)
        assert st.result == "1-0" and st.reason == "Black resigned"
    asyncio.run(main())


def test_resign_while_paused(settings):
    async def main():
        ref = make_ref(settings, human=BLACK)
        task = asyncio.create_task(ref.run())
        ref.request_pause()
        for _ in range(500):
            if ref.state.status == "paused":
                break
            await asyncio.sleep(0.01)
        assert ref.state.status == "paused"
        plies = len(ref.state.plies)
        ref.human_resign(BLACK)
        st = await asyncio.wait_for(task, 5)
        assert st.result == "1-0" and st.termination == "resignation" and len(st.plies) == plies
    asyncio.run(main())


def test_abort_while_human_thinks(settings):
    async def main():
        ref = make_ref(settings)
        task = asyncio.create_task(ref.run())
        await human_turn(ref)
        ref.abort()
        st = await asyncio.wait_for(task, 5)
        assert st.result == "*" and st.termination == "abort"
    asyncio.run(main())


def test_moves_get_classified_live(settings):
    async def main():
        a = Analyzer(settings.model_copy(update={"engine_time_s": 0.05}), command=[sys.executable, FAKE])
        try:
            ref = make_ref(settings, analyzer=a)
            task = asyncio.create_task(ref.run())
            await human_turn(ref)
            first = next(iter(ref.board.legal_moves)).uci()     # the fake engine's top line
            ref.submit_human_move(first)
            await human_turn(ref)
            ref.abort()
            st = await asyncio.wait_for(task, 10)
            assert st.start_lines and st.start_lines[0]["uci"] == first
            c = st.plies[0].classification
            assert c and c["rank"] == 1 and c["label"] in ("best", "great", "brilliant")
            assert st.plies[0].lines and "lines" not in (st.plies[0].eval or {})
        finally:
            a.close()
    asyncio.run(main())


def test_move_and_resign_endpoints(settings, tmp_path, monkeypatch):
    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text("agents:\n  - {id: human, cli: human}\n  - {id: r, cli: mock, delay_s: 0.01}\n",
                           encoding="utf-8")
    monkeypatch.setenv("ARENA_AGENTS", str(agents_yaml))
    from arena.server import create_app
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/game/move", json={"uci": "e2e4"}).status_code == 409   # no game
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/game/start", json={"white": "human", "black": "r"})
            while not ((msg := ws.receive_json())["state"]["turn"] or {}).get("human"):
                pass
            assert msg["state"]["turn"]["dests"]["g1"] == ["h3", "f3"] or sorted(msg["state"]["turn"]["dests"]["g1"]) == ["f3", "h3"]
            assert client.post("/api/game/move", json={"uci": "e2e5"}).status_code == 400
            r = client.post("/api/game/move", json={"uci": "e2e4"})
            assert r.status_code == 200 and r.json()["san"] == "e4"
            while len(ws.receive_json()["state"]["plies"]) < 2:
                pass
            assert client.post("/api/game/resign", json={}).status_code == 200
            while ws.receive_json()["type"] != "saved":
                pass
        snap = client.get("/api/game/current").json()
        assert snap["result"] == "0-1" and snap["termination"] == "resignation"
        assert "start_lines" not in snap and all("lines" not in p for p in snap["plies"])
        saved = client.get(f"/api/games/{snap['id']}").json()
        assert saved["white"]["agent"]["cli"] == "human"
        assert json.dumps(saved)
