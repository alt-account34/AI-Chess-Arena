"""FastAPI server: setup/live/replay/record pages, JSON API and a WebSocket feed."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from typing import Any

import chess
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import openings
from .agents.registry import create_agent
from .config import ROOT, Settings, load_agents, load_settings
from .console import ConsoleLog, console_path, read_console
from .referee import Referee
from .state import BLACK, WHITE, GameState
from .storage import RecordDB, game_paths, load_game, save_game, summarize

log = logging.getLogger("arena.server")
WEB = ROOT / "web"


def lite_state(state: GameState) -> dict:
    """The live snapshot without the bulky raw outputs (fetched on demand)."""
    d = state.to_dict()

    def strip(rec: dict | None) -> None:
        if not rec:
            return
        for a in rec["attempts"]:
            a["raw_chars"] = len(a.pop("raw_output") or "")
            a["prompt_chars"] = len(a.pop("prompt", None) or "")
            a.pop("stderr", None)
    for rec in d["plies"]:
        strip(rec)
        rec.pop("lines", None)          # engine lines: only the classifier needs them
    strip(d["final_turn"])
    d.pop("start_lines", None)
    return d


class StartRequest(BaseModel):
    white: str
    black: str
    send_legal_moves: bool | None = None
    move_cap: int | None = None
    start_fen: str | None = None
    opening: str | None = "free"          # "free" | "random" | "custom" | an opening id
    opening_moves: str | None = None      # SAN line when opening == "custom"
    think_aloud: bool | None = None       # ask agents for an ANALYSIS block before each move


class MoveRequest(BaseModel):
    uci: str


class ResignRequest(BaseModel):
    color: str | None = None              # None = the human player (or the one to move)


class TerminalRequest(BaseModel):
    side: str
    game: str | None = None               # None = follow the live game


class Arena:
    """Holds the one live game and the connected browsers."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.games_dir = settings.resolve(settings.games_dir)
        self.db = RecordDB(settings.resolve(settings.db_path))
        self.referee: Referee | None = None
        self.task: asyncio.Task | None = None
        self.clients: set[WebSocket] = set()
        self.analyzer = None
        self._analyzer_lock = threading.Lock()
        self.console: ConsoleLog | None = None
        self.console_subs: set[tuple[asyncio.Queue, str]] = set()   # (queue, side)
        self.loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ live agent console
    def publish_console(self, ev: dict) -> None:
        """Called from any thread (CLI reader threads included)."""
        loop = self.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._fanout, ev)

    def _fanout(self, ev: dict) -> None:
        for q, side in list(self.console_subs):
            if side in (ev.get("side"), "*") or ev.get("k") == "new_game":
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    pass

    def get_analyzer(self):
        with self._analyzer_lock:
            if self.analyzer is None:
                from .engine import Analyzer
                self.analyzer = Analyzer(self.settings)
            return self.analyzer

    def engine_info(self) -> dict:
        a = self.analyzer
        if a is None:
            return {"available": None, "error": None, "name": None}
        return {"available": a.available, "error": a.error, "name": a.name}

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def snapshot(self) -> dict | None:
        if self.referee is None:
            return None
        return {**lite_state(self.referee.state), "server_time": time.time(),
                "engine": self.engine_info()}

    async def broadcast(self, type_: str, data: dict) -> None:
        if not self.clients:
            return
        msg = json.dumps({"type": type_, "data": data, "state": self.snapshot()}, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def start(self, req: StartRequest) -> GameState:
        if self.running:
            raise HTTPException(409, "a game is already running")
        agents_cfg = load_agents()
        for aid in (req.white, req.black):
            if aid not in agents_cfg:
                raise HTTPException(400, f"unknown agent '{aid}'")
        if req.start_fen:
            try:
                b = chess.Board(req.start_fen)
                if not b.is_valid():
                    raise ValueError("invalid position")
            except ValueError as e:
                raise HTTPException(400, f"bad FEN: {e}") from e
        try:
            opening = openings.resolve(req.opening, req.opening_moves)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if opening and req.start_fen:
            raise HTTPException(400, "choose either a start FEN or an opening, not both")
        configs = {WHITE: agents_cfg[req.white], BLACK: agents_cfg[req.black]}
        try:
            agents = {c: create_agent(cfg, self.settings) for c, cfg in configs.items()}
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        analyzer = await asyncio.to_thread(self.get_analyzer)
        analyzer.new_game()

        def on_finish(state: GameState, board: chess.Board) -> None:
            save_game(state, board, self.games_dir)
            self.db.upsert(summarize(state.to_dict()))
            if self.console is not None and self.console.game_id == state.id:
                self.console.close()

        self.loop = asyncio.get_running_loop()
        if self.console is not None:
            self.console.close()
        self.referee = Referee(agents, configs, self.settings,
                               send_legal_moves=req.send_legal_moves, move_cap=req.move_cap,
                               think_aloud=req.think_aloud,
                               start_fen=req.start_fen or None, analyzer=analyzer, opening=opening,
                               on_event=self.broadcast,
                               on_finish=lambda s, b: asyncio.to_thread(on_finish, s, b))
        self.referee.state.engine_available = analyzer.available
        gid = self.referee.state.id
        self.console = ConsoleLog(gid, console_path(self.games_dir, gid), self.publish_console)
        self.referee.console = self.console
        self._fanout({"k": "new_game", "game": gid, "side": "*"})
        self.task = asyncio.create_task(self.referee.run())
        return self.referee.state

    def ply_detail(self, game_id: str, n: str) -> dict:
        """Full ply record (with raw outputs). n is a ply number or 'final'."""
        if self.referee is not None and self.referee.state.id == game_id:
            st = self.referee.state.to_dict()
        else:
            try:
                st = load_game(self.games_dir, game_id)
            except FileNotFoundError:
                raise HTTPException(404, "game not found") from None
        if n == "final":
            if not st.get("final_turn"):
                raise HTTPException(404, "no final turn")
            return st["final_turn"]
        try:
            return st["plies"][int(n) - 1]
        except (ValueError, IndexError):
            raise HTTPException(404, "no such ply") from None

    def close(self) -> None:
        if self.referee is not None and self.running:
            self.referee.abort()
        if self.console is not None:
            self.console.close()
        if self.analyzer is not None:
            self.analyzer.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    arena = Arena(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        arena.close()
        if arena.task is not None:
            try:
                await asyncio.wait_for(arena.task, timeout=15)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass

    app = FastAPI(title="AI Chess Arena", lifespan=lifespan)
    app.state.arena = arena
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # the browser revalidates scripts and styles (cheap 304s) instead of running stale copies
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def page(name: str):
        return FileResponse(WEB / name, headers={"Cache-Control": "no-cache"})

    @app.get("/")
    async def live_page():
        return page("index.html")

    @app.get("/setup")
    async def setup_page():
        return page("setup.html")

    @app.get("/replay")
    async def replay_page():
        return page("replay.html")

    @app.get("/record")
    async def record_page():
        return page("record.html")

    @app.get("/api/agents")
    async def api_agents():
        return [{"id": a.id, "display_name": a.display_name, "cli": a.cli, "model": a.model,
                 "reasoning_effort": a.reasoning_effort, "group": a.extra.get("group"),
                 "family": a.extra.get("family")} for a in load_agents().values()]

    @app.get("/console")
    async def console_page():
        return page("console.html")

    @app.post("/api/console/terminal")
    async def api_console_terminal(req: TerminalRequest):
        """Opens a console window on this PC running `python -m arena watch <side>`."""
        if req.side not in (WHITE, BLACK):
            raise HTTPException(400, "side must be white or black")
        argv = [sys.executable, "-m", "arena", "watch", req.side, "--port", str(settings.port)]
        if req.game:
            argv += ["--game", req.game]
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            subprocess.Popen(argv, cwd=str(ROOT), creationflags=flags)
        except OSError as e:
            raise HTTPException(500, f"could not open a terminal: {e}") from e
        return {"ok": True}

    @app.websocket("/ws/console")
    async def ws_console(ws: WebSocket, side: str = WHITE, game: str | None = None):
        """History first, then live events. game=None follows whatever game is live."""
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=20000)
        live_id = arena.referee.state.id if arena.referee else None
        follow = game is None or game == live_id
        entry = (q, side)
        if follow:
            arena.console_subs.add(entry)       # subscribe before reading history: no gaps
        try:
            gid = game or live_id
            sent = 0
            if gid:
                hist = read_console(arena.games_dir, gid, side)
                await ws.send_text(json.dumps({"k": "history", "game": gid, "events": hist,
                                               "live": follow and arena.running}))
                sent = hist[-1]["seq"] if hist else 0
            else:
                await ws.send_text(json.dumps({"k": "history", "game": None, "events": [], "live": False}))
            if not follow:
                while True:
                    await ws.receive_text()
            while True:
                ev = await q.get()
                if ev.get("k") == "new_game":
                    sent = 0
                elif ev.get("seq", 0) <= sent and ev.get("game") == gid:
                    continue                    # already in the history we sent
                gid = ev.get("game", gid)
                await ws.send_text(json.dumps(ev, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            arena.console_subs.discard(entry)

    @app.get("/api/openings")
    async def api_openings():
        return openings.catalog()

    @app.get("/api/settings")
    async def api_settings():
        return {"send_legal_moves": settings.send_legal_moves, "move_cap_plies": settings.move_cap_plies,
                "think_aloud": settings.think_aloud,
                "safety_timeout_s": settings.safety_timeout_s, "running": arena.running,
                "engine": arena.engine_info()}

    @app.post("/api/game/start")
    async def api_start(req: StartRequest):
        st = await arena.start(req)
        return {"id": st.id}

    def _ref() -> Referee:
        if arena.referee is None or not arena.running:
            raise HTTPException(409, "no game is running")
        return arena.referee

    @app.post("/api/game/pause")
    async def api_pause():
        _ref().request_pause()
        await arena.broadcast("pause_requested", {})
        return {"ok": True}

    @app.post("/api/game/resume")
    async def api_resume():
        _ref().resume()
        return {"ok": True}

    @app.post("/api/game/retry")
    async def api_retry():
        _ref().retry()
        return {"ok": True}

    @app.post("/api/game/abort")
    async def api_abort():
        _ref().abort()
        return {"ok": True}

    @app.post("/api/game/move")
    async def api_move(req: MoveRequest):
        """A human player's move, in UCI (e7e8q for promotions)."""
        ref = _ref()
        try:
            san = ref.submit_human_move(req.uci)
        except LookupError as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True, "san": san}

    @app.post("/api/game/resign")
    async def api_resign(req: ResignRequest):
        ref = _ref()
        color = req.color
        if color is None:
            humans = [c for c in (WHITE, BLACK) if ref.agents[c].is_human]
            if not humans:
                raise HTTPException(409, "no human player in this game")
            turn = ref.state.turn["color"] if ref.state.turn else None
            color = turn if turn in humans else humans[0]
        if color not in (WHITE, BLACK):
            raise HTTPException(400, "color must be white or black")
        try:
            ref.human_resign(color)
        except LookupError as e:
            raise HTTPException(409, str(e)) from None
        return {"ok": True}

    @app.get("/api/game/current")
    async def api_current():
        return JSONResponse(arena.snapshot())

    @app.get("/api/game/{game_id}/ply/{n}")
    async def api_ply(game_id: str, n: str):
        return arena.ply_detail(game_id, n)

    @app.get("/api/games")
    async def api_games(legal_moves: str = "all", think_aloud: str = "all"):
        return arena.db.games(legal_moves, think_aloud)

    @app.get("/api/games/{game_id}")
    async def api_game(game_id: str):
        try:
            return load_game(arena.games_dir, game_id)
        except FileNotFoundError:
            raise HTTPException(404, "game not found") from None

    @app.get("/api/games/{game_id}/pgn")
    async def api_game_pgn(game_id: str):
        pgn_path, _ = game_paths(arena.games_dir, game_id)
        if pgn_path.parent.resolve() != arena.games_dir.resolve() or not pgn_path.exists():
            raise HTTPException(404, "game not found")
        return FileResponse(pgn_path, media_type="application/x-chess-pgn", filename=pgn_path.name)

    @app.get("/api/records")
    async def api_records(legal_moves: str = "all", think_aloud: str = "all"):
        return arena.db.records(legal_moves, think_aloud)

    @app.websocket("/ws")
    async def ws_feed(ws: WebSocket):
        await ws.accept()
        arena.clients.add(ws)
        try:
            await ws.send_text(json.dumps({"type": "snapshot", "data": {}, "state": arena.snapshot()},
                                          default=str))
            while True:
                await ws.receive_text()  # keepalive pings from the page; no commands over WS
        except WebSocketDisconnect:
            pass
        finally:
            arena.clients.discard(ws)

    return app


def serve(settings: Settings) -> None:
    import uvicorn
    url = f"http://localhost:{settings.port}"
    if settings.open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"AI Chess Arena running at {url}  (Ctrl+C to stop)")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")
