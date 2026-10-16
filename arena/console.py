"""Live agent console: everything each agent's CLI does, as it happens.

The referee and the adapters emit small events ("prompt", "thinking", "text",
"reasoning", "tool", "result", "raw", ...). ConsoleLog writes them to
games/<game_id>.console.jsonl and forwards them to a publisher (the server's
WebSocket hub), from any thread. Viewers (browser window or terminal) only read
this feed; nothing here is ever sent to an agent.

Event kinds (all carry: game, side, seq, t):
  turn       {ply, move_number}                      the referee hands this side the move
  prompt     {attempt, text, session, new_session}   exact text sent on stdin
  session    {session_id, model?}                    the CLI reported its session
  thinking   {}                                       model started working / thinking
  thinking_done {}                                    (Claude) thinking block finished
  text       {delta}                                  streamed reply text (Claude)
  message    {text}                                   complete reply text (Codex)
  reasoning  {text}                                   reasoning summary (Codex)
  tool       {tool, input?, detail?, source?}         a tool call attempt
  notice     {text}                                   CLI warning or error
  raw        {line}                                   one raw stdout line from the CLI
  result     {attempt, accepted, move, reason, think_s, tokens, tools}
  status     {text}                                   referee status (timeout, game over, ...)
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

Publisher = Callable[[dict], None]


class ConsoleLog:
    def __init__(self, game_id: str, path: Path | None, publish: Publisher | None = None):
        self.game_id = game_id
        self.path = path
        self.publish = publish
        self._lock = threading.Lock()
        self._seq = 0
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def emit(self, side: str, kind: str, **data) -> None:
        """Thread-safe: called from the referee loop and from CLI reader threads."""
        with self._lock:
            self._seq += 1
            ev = {"game": self.game_id, "side": side, "seq": self._seq, "t": round(time.time(), 3),
                  "k": kind, **data}
            if self._fh is not None:
                try:
                    self._fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    self._fh.flush()
                except (OSError, ValueError):
                    pass
        if self.publish is not None:
            try:
                self.publish(ev)
            except Exception:  # noqa: BLE001 - viewers must never break a game
                pass

    def sink(self, side: str) -> Callable[..., None]:
        """A per-side emit function, handed to that side's agent adapter."""
        return lambda kind, **data: self.emit(side, kind, **data)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def console_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / f"{game_id}.console.jsonl"


def read_console(games_dir: Path, game_id: str, side: str | None = None) -> list[dict]:
    p = console_path(games_dir, game_id)
    if p.resolve().parent != games_dir.resolve() or not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if side is None or ev.get("side") == side:
            out.append(ev)
    return out
