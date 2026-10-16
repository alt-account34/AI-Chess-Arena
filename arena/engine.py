"""Stockfish analysis for the UI and logs ONLY.

Nothing from this module may reach an agent; prompts.py never imports it.
If Stockfish is missing or crashes, analyse() returns None and the game goes on.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

import chess
import chess.engine

from .config import Settings

log = logging.getLogger("arena.engine")
MAX_RESTARTS = 3


def _score(score: chess.engine.Score) -> dict:
    return {"mate": score.mate()} if score.is_mate() else {"cp": score.score()}


def _line(board: chess.Board, info: dict) -> dict | None:
    pv = info.get("pv") or []
    if not pv or "score" not in info:
        return None
    return {"uci": pv[0].uci(), "san": board.san(pv[0]), **_score(info["score"].white()),
            "pv": [m.uci() for m in pv[:8]]}


class Analyzer:
    def __init__(self, settings: Settings, command: list[str] | None = None):
        path = settings.resolve(settings.stockfish_path)
        self.command = command or ([str(path)] if path else None)
        self.limit = (chess.engine.Limit(depth=settings.engine_depth) if settings.engine_depth
                      else chess.engine.Limit(time=settings.engine_time_s))
        self.options = {"Threads": settings.engine_threads, "Hash": settings.engine_hash_mb}
        self.multipv = max(1, settings.engine_multipv)
        self._game: object = object()   # python-chess sends ucinewgame when this changes
        self._engine: chess.engine.SimpleEngine | None = None
        self._lock = threading.Lock()
        self._restarts = 0
        self.available = False
        self.error: str | None = None
        self.name: str | None = None
        with self._lock:
            self._start()

    def _start(self) -> None:
        if not self.command:
            self.error = "no stockfish_path configured"
            return
        if len(self.command) == 1 and not Path(self.command[0]).exists():
            self.error = f"Stockfish not found at {self.command[0]}"
            log.warning("%s; eval bar disabled", self.error)
            return
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(
                self.command, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            opts = {k: v for k, v in self.options.items() if k in self._engine.options}
            if opts:
                self._engine.configure(opts)
            self.name = self._engine.id.get("name")
            self.available = True
            self.error = None
        except Exception as e:  # noqa: BLE001
            self._engine = None
            self.available = False
            self.error = f"failed to start engine: {e!r}"
            log.warning(self.error)

    def new_game(self) -> None:
        """The next analyse() starts a new game for the engine (clears its hash)."""
        self._game = object()

    def analyse(self, fen: str) -> dict | None:
        """{"cp"|"mate", "depth", "ms", "lines": [{uci, san, cp|mate, pv}]}, White's view.
        "lines" holds the engine's top `engine_multipv` moves, best first."""
        board = chess.Board(fen)
        if not board.is_valid():
            return None  # Stockfish exits on illegal positions; don't spend a restart on one
        outcome = board.outcome(claim_draw=False)
        if outcome is not None:
            if outcome.winner is None:
                return {"cp": 0, "final": True}
            return {"mate": 0, "winner": "white" if outcome.winner else "black", "final": True}
        with self._lock:
            if self._engine is None:
                if not self.available and self._restarts == 0 and self.error and "not found" in self.error:
                    return None
                if self._restarts >= MAX_RESTARTS or self.command is None:
                    return None
                self._restarts += 1
                self._start()
                if self._engine is None:
                    return None
            try:
                t0 = time.monotonic()
                mpv = self.multipv if "MultiPV" in self._engine.options else None
                infos = self._engine.analyse(board, self.limit, multipv=mpv, game=self._game)
                if isinstance(infos, dict):
                    infos = [infos]
                lines = [ln for ln in (_line(board, i) for i in infos) if ln]
                ev: dict = _score(infos[0]["score"].white())
                ev["depth"] = infos[0].get("depth")
                ev["ms"] = int((time.monotonic() - t0) * 1000)
                ev["lines"] = lines
                return ev
            except Exception as e:  # noqa: BLE001 - engine crashed or hung
                log.warning("engine analyse failed: %r", e)
                self.error = f"engine error: {e!r}"
                self.available = False
                self._quit()
                return None

    def _quit(self) -> None:
        eng, self._engine = self._engine, None
        if eng is not None:
            try:
                eng.quit()
            except Exception:  # noqa: BLE001
                try:
                    eng.close()
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        with self._lock:
            self._quit()
