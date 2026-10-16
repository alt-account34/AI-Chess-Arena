"""Game logs (one PGN + one JSON per game) and the SQLite record index."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import chess
import chess.pgn

from .state import BLACK, WHITE, GameState

LOG_VERSION = 1
# PGN annotation glyphs for move classifications (NAG numbers)
CLASS_NAGS = {"brilliant": 3, "great": 1, "inaccuracy": 6, "mistake": 2, "blunder": 4}


def game_paths(games_dir: Path, game_id: str) -> tuple[Path, Path]:
    return games_dir / f"{game_id}.pgn", games_dir / f"{game_id}.json"


def format_eval(ev: dict | None) -> str | None:
    if not ev:
        return None
    if ev.get("mate") is not None:
        return f"#{ev['mate']}"
    return f"{ev['cp'] / 100:+.2f}"


def build_pgn(state: GameState, board: chess.Board) -> str:
    game = chess.pgn.Game.from_board(board)
    started = datetime.fromisoformat(state.started_at)
    w, b = state.white.agent, state.black.agent
    game.headers["Event"] = "AI Chess Arena"
    game.headers["Site"] = "local"
    game.headers["Date"] = started.strftime("%Y.%m.%d")
    game.headers["Round"] = "-"
    game.headers["White"] = w.get("display_name") or w["id"]
    game.headers["Black"] = b.get("display_name") or b["id"]
    game.headers["Result"] = state.result
    game.headers["WhiteAgent"] = w["id"]
    game.headers["BlackAgent"] = b["id"]
    game.headers["LegalMovesSent"] = "true" if state.settings.get("send_legal_moves") else "false"
    game.headers["ThinkAloud"] = "true" if state.settings.get("think_aloud") else "false"
    game.headers["Termination"] = state.reason or state.termination or "unterminated"
    if state.opening:
        game.headers["Opening"] = state.opening["name"]
        if state.opening.get("eco"):
            game.headers["ECO"] = state.opening["eco"]
    game.headers["Time"] = started.strftime("%H:%M:%S")
    game.headers["PlyCount"] = str(len(state.plies))

    node = game
    for rec in state.plies:
        node = node.variations[0]
        if rec.book:
            node.comment = "book (opening set by the referee)"
            continue
        parts = [f"think {rec.think_s:.1f}s"]
        if len(rec.attempts) > 1:
            parts.append(f"{len(rec.attempts) - 1} rejected")
        ev = format_eval(rec.eval)
        if ev:
            parts.append(f"eval {ev}")
        cls = rec.classification
        if cls:
            parts.append(cls["label"] + (f", best {cls['best_san']}" if cls["label"] in CLASS_NAGS
                                         and cls.get("best_san") and cls["rank"] != 1 else ""))
            if cls["label"] in CLASS_NAGS:
                node.nags.add(CLASS_NAGS[cls["label"]])
        node.comment = "; ".join(parts)
    if state.resignation:
        r = state.resignation
        node.comment = (node.comment + " " if node.comment else "") + \
            f"{r['color'].capitalize()} resigns. Expected line: {' '.join(r['line_san'])}"
    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter) + "\n"


def save_game(state: GameState, board: chess.Board, games_dir: Path) -> tuple[Path, Path]:
    games_dir.mkdir(parents=True, exist_ok=True)
    pgn_path, json_path = game_paths(games_dir, state.id)
    data = state.to_dict()
    data["log_version"] = LOG_VERSION
    data["turn"] = None
    data["pgn_file"] = pgn_path.name
    pgn_path.write_text(build_pgn(state, board), encoding="utf-8")
    tmp = json_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(json_path)
    return pgn_path, json_path


def load_game(games_dir: Path, game_id: str) -> dict:
    _, json_path = game_paths(games_dir, game_id)
    if json_path.resolve().parent != games_dir.resolve() or not json_path.exists():
        raise FileNotFoundError(game_id)
    return json.loads(json_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Record index (SQLite). The JSON logs are the source of truth; the index can be
# rebuilt from them at any time with `python -m arena rebuild-records`.
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    started_at TEXT, finished_at TEXT,
    white_id TEXT, black_id TEXT, white_name TEXT, black_name TEXT,
    result TEXT, termination TEXT, reason TEXT, winner TEXT,
    legal_moves_sent INTEGER,
    plies INTEGER,
    white_think_total REAL, black_think_total REAL,
    white_moves INTEGER, black_moves INTEGER,
    white_illegal INTEGER, black_illegal INTEGER,
    white_tool_violations INTEGER, black_tool_violations INTEGER,
    opening TEXT,
    white_tokens_in INTEGER, white_tokens_out INTEGER,
    black_tokens_in INTEGER, black_tokens_out INTEGER,
    think_aloud INTEGER
);
"""
# Columns added after the first release; ALTERed into older databases.
ADDED_COLUMNS = {"opening": "TEXT", "white_tokens_in": "INTEGER", "white_tokens_out": "INTEGER",
                 "black_tokens_in": "INTEGER", "black_tokens_out": "INTEGER", "think_aloud": "INTEGER"}


def summarize(data: dict) -> dict[str, Any]:
    w, b = data["white"], data["black"]
    return {
        "id": data["id"], "started_at": data["started_at"], "finished_at": data.get("finished_at"),
        "white_id": w["agent"]["id"], "black_id": b["agent"]["id"],
        "white_name": w["agent"].get("display_name") or w["agent"]["id"],
        "black_name": b["agent"].get("display_name") or b["agent"]["id"],
        "result": data["result"], "termination": data.get("termination"), "reason": data.get("reason"),
        "winner": data.get("winner"),
        "legal_moves_sent": 1 if data["settings"].get("send_legal_moves") else 0,
        "plies": len(data["plies"]),
        "white_think_total": w["think_total"], "black_think_total": b["think_total"],
        "white_moves": w["moves"], "black_moves": b["moves"],
        "white_illegal": w["illegal_total"], "black_illegal": b["illegal_total"],
        "white_tool_violations": w["tool_violations"], "black_tool_violations": b["tool_violations"],
        "opening": (data.get("opening") or {}).get("name") or "free",
        "think_aloud": 1 if data["settings"].get("think_aloud") else 0,
        "white_tokens_in": (w.get("tokens") or {}).get("input"),
        "white_tokens_out": (w.get("tokens") or {}).get("output"),
        "black_tokens_in": (b.get("tokens") or {}).get("input"),
        "black_tokens_out": (b.get("tokens") or {}).get("output"),
    }


class RecordDB:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(SCHEMA)
            have = {row[1] for row in con.execute("PRAGMA table_info(games)")}
            for col, typ in ADDED_COLUMNS.items():
                if col not in have:
                    con.execute(f"ALTER TABLE games ADD COLUMN {col} {typ}")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def upsert(self, summary: dict) -> None:
        cols = list(summary)
        sql = (f"INSERT OR REPLACE INTO games ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        with self._lock, self._connect() as con:
            con.execute(sql, [summary[c] for c in cols])

    def rebuild(self, games_dir: Path) -> int:
        n = 0
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM games")
        for p in sorted(games_dir.glob("*.json")):
            try:
                self.upsert(summarize(json.loads(p.read_text(encoding="utf-8"))))
                n += 1
            except (KeyError, ValueError) as e:
                print(f"skipping {p.name}: {e!r}")
        return n

    def games(self, legal_moves: str = "all", think_aloud: str = "all") -> list[dict]:
        conds, args = [], []
        if legal_moves in ("on", "off"):
            conds.append("legal_moves_sent = ?")
            args.append(1 if legal_moves == "on" else 0)
        if think_aloud in ("on", "off"):
            conds.append("COALESCE(think_aloud, 0) = ?")
            args.append(1 if think_aloud == "on" else 0)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with self._lock, self._connect() as con:
            rows = con.execute(f"SELECT * FROM games {where} ORDER BY started_at DESC", args).fetchall()
        return [dict(r) for r in rows]

    def records(self, legal_moves: str = "all", think_aloud: str = "all") -> dict:
        return compute_records(self.games(legal_moves, think_aloud))


def compute_records(games: list[dict]) -> dict:
    """Per-agent and per-matchup W/L/D split by color, plus averages.
    Aborted/errored games (result '*') are excluded from W/L/D."""
    agents: dict[str, dict] = {}
    matchups: dict[tuple[str, str], dict] = {}

    def blank(name: str) -> dict:
        z = {"w": 0, "l": 0, "d": 0}
        return {"name": name, "games": 0, "as_white": dict(z), "as_black": dict(z), "total": dict(z),
                "think_total": 0.0, "moves": 0, "illegal": 0, "plies": 0, "tool_violations": 0,
                "unfinished": 0, "tokens_in": 0, "tokens_out": 0, "token_moves": 0}

    for g in games:
        for color in (WHITE, BLACK):
            aid = g[f"{color}_id"]
            a = agents.setdefault(aid, blank(g[f"{color}_name"]))
            if g["result"] == "*":
                a["unfinished"] += 1
                continue
            outcome = "d" if g["result"] == "1/2-1/2" else ("w" if g["winner"] == color else "l")
            a["games"] += 1
            a[f"as_{color}"][outcome] += 1
            a["total"][outcome] += 1
            a["think_total"] += g[f"{color}_think_total"] or 0
            a["moves"] += g[f"{color}_moves"] or 0
            a["illegal"] += g[f"{color}_illegal"] or 0
            a["tool_violations"] += g[f"{color}_tool_violations"] or 0
            a["plies"] += g["plies"]
            if g.get(f"{color}_tokens_in") is not None:   # older logs have no token data
                a["tokens_in"] += g[f"{color}_tokens_in"] or 0
                a["tokens_out"] += g[f"{color}_tokens_out"] or 0
                a["token_moves"] += g[f"{color}_moves"] or 0
        if g["result"] == "*":
            continue
        key = (g["white_id"], g["black_id"])
        m = matchups.setdefault(key, {"white_id": key[0], "black_id": key[1],
                                      "white_name": g["white_name"], "black_name": g["black_name"],
                                      "games": 0, "white_wins": 0, "black_wins": 0, "draws": 0,
                                      "plies": 0})
        m["games"] += 1
        m["plies"] += g["plies"]
        if g["result"] == "1-0":
            m["white_wins"] += 1
        elif g["result"] == "0-1":
            m["black_wins"] += 1
        else:
            m["draws"] += 1

    for a in agents.values():
        a["avg_think_per_move"] = round(a["think_total"] / a["moves"], 2) if a["moves"] else None
        a["avg_illegal_per_game"] = round(a["illegal"] / a["games"], 2) if a["games"] else None
        a["avg_game_plies"] = round(a["plies"] / a["games"], 1) if a["games"] else None
        tm = a["token_moves"]
        a["avg_tokens_in_per_move"] = round(a["tokens_in"] / tm) if tm else None
        a["avg_tokens_out_per_move"] = round(a["tokens_out"] / tm) if tm else None
    for m in matchups.values():
        m["avg_game_plies"] = round(m["plies"] / m["games"], 1) if m["games"] else None
    return {"agents": agents, "matchups": list(matchups.values())}


# ---------------------------------------------------------------------------
# Token repair. Logs written before the Codex fix summed Codex's thread-wide
# running totals; this recomputes every attempt's tokens from the raw CLI usage
# kept in attempt.meta["usage"], then the per-move and per-side totals.
# ---------------------------------------------------------------------------

def recompute_tokens(data: dict) -> dict:
    from .agents.claude_code import claude_tokens
    from .agents.codex import ThreadUsage, codex_tokens
    from .state import add_tokens, empty_tokens

    threads = ThreadUsage()
    totals = {WHITE: empty_tokens(), BLACK: empty_tokens()}
    turns = list(data["plies"]) + ([data["final_turn"]] if data.get("final_turn") else [])
    for rec in turns:
        cli = data[rec["color"]]["agent"].get("cli")
        rec_tokens = None
        for a in rec.get("attempts") or []:
            usage = (a.get("meta") or {}).get("usage")
            if usage and cli == "claude_code":
                a["tokens"] = claude_tokens(usage)
            elif usage and cli == "codex":
                t = codex_tokens(usage)
                a["tokens"] = threads.per_call(a.get("session_id") or "?", t) if t else None
            if a.get("tokens"):
                rec_tokens = rec_tokens or {}
                add_tokens(rec_tokens, a["tokens"])
                add_tokens(totals[rec["color"]], a["tokens"])
        rec["tokens"] = rec_tokens
        rec["tokens_after"] = {c: dict(totals[c]) for c in (WHITE, BLACK)}
    for c in (WHITE, BLACK):
        data[c]["tokens"] = totals[c]
    return data


def fix_token_logs(games_dir: Path) -> list[tuple[str, dict, dict]]:
    """Rewrites every game log with recomputed tokens (a .json.bak copy is kept
    the first time). Returns (game id, old side totals, new side totals)."""
    changed = []
    for p in sorted(games_dir.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        before = {c: dict(data[c].get("tokens") or {}) for c in (WHITE, BLACK)}
        recompute_tokens(data)
        after = {c: data[c]["tokens"] for c in (WHITE, BLACK)}
        if before == after:
            continue
        bak = p.with_suffix(".json.bak")
        if not bak.exists():
            bak.write_bytes(p.read_bytes())
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        changed.append((data["id"], before, after))
    return changed
