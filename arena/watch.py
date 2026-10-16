"""`python -m arena watch white|black`: the live agent console in a terminal.

Connects to the running server's /ws/console feed and prints what that side's
CLI is doing: the prompt it was sent, its streamed reply, Codex reasoning
summaries, tool-call attempts, the referee's verdict and token use. Read-only.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from datetime import datetime

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

RESET, DIM, BOLD = "\x1b[0m", "\x1b[2m", "\x1b[1m"
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN = (f"\x1b[{c}m" for c in (31, 32, 33, 34, 35, 36))


def _enable_ansi() -> None:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _title(text: str) -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(text)
    else:
        sys.stdout.write(f"\x1b]0;{text}\x07")


def _ts(ev: dict) -> str:
    return datetime.fromtimestamp(ev.get("t", time.time())).strftime("%H:%M:%S")


def _fmt_tokens(t: dict | None) -> str:
    if not t:
        return ""
    k = lambda n: f"{n / 1000:.1f}k" if n >= 1000 else str(n)
    return f"{k(t.get('input', 0))} in ({k(t.get('cached', 0))} cached) / {k(t.get('output', 0))} out"


class Printer:
    def __init__(self, side: str, raw: bool):
        self.side = side
        self.raw = raw
        self.in_text = False      # currently streaming reply text
        self.width = min(110, max(60, os.get_terminal_size().columns - 2)) if sys.stdout.isatty() else 100

    def _end_text(self) -> None:
        if self.in_text:
            sys.stdout.write(RESET + "\n")
            self.in_text = False

    def line(self, ev: dict, color: str, text: str) -> None:
        self._end_text()
        print(f"{DIM}[{_ts(ev)}]{RESET} {color}{text}{RESET}")

    def block(self, text: str, color: str = "", indent: str = "    ") -> None:
        for para in (text or "").splitlines() or [""]:
            for chunk in textwrap.wrap(para, self.width - len(indent)) or [""]:
                print(f"{color}{indent}{chunk}{RESET}")

    def handle(self, ev: dict) -> None:
        k = ev.get("k")
        if k == "turn":
            self._end_text()
            print(f"\n{BOLD}{'━' * 8} ply {ev.get('ply')} · move {ev.get('move_number')} · "
                  f"{self.side.capitalize()} to play {'━' * 8}{RESET}")
        elif k == "prompt":
            new = " · new session" if ev.get("new_session") else ""
            self.line(ev, CYAN, f"→ prompt (attempt {ev.get('attempt')}, {len(ev.get('text', ''))} chars{new})")
            self.block(ev.get("text", ""), DIM)
        elif k == "session":
            sid = str(ev.get("session_id") or "")[:13]
            extra = f" · model {ev['model']}" if ev.get("model") else ""
            tools = ev.get("tools")
            if tools is not None:
                extra += f" · tools {tools or 'none'}"
            self.line(ev, DIM, f"session {sid}{extra}")
        elif k == "thinking":
            self.line(ev, MAGENTA, "… thinking")
        elif k == "thinking_done":
            note = " (content redacted by the CLI)" if ev.get("redacted") else ""
            self.line(ev, MAGENTA, f"… thinking finished{note}")
        elif k == "reasoning":
            self.line(ev, MAGENTA, "◆ reasoning summary")
            self.block(ev.get("text", ""), MAGENTA)
        elif k == "text":
            if not self.in_text:
                print(f"{DIM}[{_ts(ev)}]{RESET} {BOLD}▌ reply{RESET}")
                sys.stdout.write("    ")
                self.in_text = True
            sys.stdout.write(ev.get("delta", "").replace("\n", "\n    "))
            sys.stdout.flush()
        elif k == "block_end":
            self._end_text()
        elif k == "message":
            self.line(ev, BOLD, "▌ reply")
            self.block(ev.get("text", ""))
        elif k == "tool":
            what = ev.get("input") or ev.get("detail") or ""
            self.line(ev, YELLOW, f"⚠ tool call attempt: {ev.get('tool')} {str(what)[:200]}")
        elif k == "notice":
            self.line(ev, YELLOW, f"! {ev.get('text')}")
        elif k == "result":
            tok = _fmt_tokens(ev.get("tokens"))
            if ev.get("accepted"):
                self.line(ev, GREEN, f"✓ {ev.get('move')} accepted · {ev.get('think_s', 0):.1f}s · {tok}")
            else:
                self.line(ev, RED, f"✗ {ev.get('move')} rejected: {ev.get('reason')} · {ev.get('think_s', 0):.1f}s · {tok}")
            for t in ev.get("tools") or []:
                self.line(ev, YELLOW, f"⚠ tool-use violation: {t.get('tool')} {str(t.get('input') or t.get('detail') or '')[:160]}")
        elif k == "status":
            self.line(ev, YELLOW if not ev.get("final") else BOLD, f"■ {ev.get('text')}")
        elif k == "raw" and self.raw:
            self._end_text()
            print(f"{DIM}  raw: {ev.get('line', '')[:300]}{RESET}")
        sys.stdout.flush()


def watch(side: str, port: int, game: str | None = None, raw: bool = False) -> int:
    _enable_ansi()
    label = f"Arena · {side.capitalize()} agent console" + (f" · {game}" if game else "")
    _title(label)
    url = f"ws://127.0.0.1:{port}/ws/console?side={side}" + (f"&game={game}" if game else "")
    p = Printer(side, raw)
    print(f"{BOLD}{label}{RESET}\n{DIM}{url}  (Ctrl+C to close){RESET}")
    while True:
        try:
            with connect(url, max_size=None, open_timeout=5) as ws:
                for msg in ws:
                    ev = json.loads(msg)
                    if ev.get("k") == "history":
                        gid = ev.get("game")
                        print(f"\n{BOLD}== game {gid or '(none yet)'} =={RESET}"
                              f"{'  (live)' if ev.get('live') else ''}")
                        if gid and not ev.get("events"):
                            print(f"{DIM}no console log for this game (games before the console existed have none){RESET}")
                        for e in ev.get("events", []):
                            p.handle(e)
                        if not ev.get("live"):
                            print(f"{DIM}-- waiting for the next game --{RESET}" if not game else
                                  f"{DIM}-- end of recorded log --{RESET}")
                    elif ev.get("k") == "new_game":
                        print(f"\n{BOLD}== new game {ev.get('game')} =={RESET}")
                    else:
                        p.handle(ev)
            if game:
                return 0
        except KeyboardInterrupt:
            return 0
        except (OSError, ConnectionClosed, TimeoutError):
            print(f"{DIM}(server not reachable on port {port}; retrying in 3 s){RESET}")
            try:
                time.sleep(3)
            except KeyboardInterrupt:
                return 0
