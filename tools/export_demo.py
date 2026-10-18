"""Export an explicitly public, portable copy of local games; never edit originals."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena.storage import summarize, compute_records


def clean(value):
    if isinstance(value, str):
        value = re.sub(r"(?i)\b[A-Z]:[\\/][^\s\"<>]+", "[local path]", value)
        value = re.sub(r"/(?:Users|home|tmp|var|private)/[^\s\"<>]+", "[local path]", value)
        value = re.sub(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "[session]", value, flags=re.I)
        value = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}", "[redacted]", value)
        return value
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def pick(obj, fields):
    return {key: obj[key] for key in fields.split() if key in obj}


def public_turn(turn):
    out = pick(turn, "ply color think_s san uci fen_after plan analysis eval clock_after illegal_after tokens tokens_after book classification")
    out["tool_violations"] = [{"tool": e.get("tool", "unknown")} for e in turn.get("tool_violations", [])]
    out["attempts"] = [pick(a, "n think_s kind move_text accepted reason plan resign_explanation resign_line tokens analysis reasoning raw_output") for a in turn.get("attempts", [])]
    # raw_output here is the model's final response, not the CLI's raw transport.
    return out


def public_game(source, transcript):
    game = pick(source, "id started_at finished_at start_fen start_eval result termination reason winner")
    game["settings"] = pick(source["settings"], "send_legal_moves think_aloud move_cap_plies")
    game["opening"] = pick(source["opening"], "id name eco moves") if source.get("opening") else None
    game["resignation"] = pick(source["resignation"], "color explanation line_san fen") if source.get("resignation") else None
    for side in ("white", "black"):
        game[side] = {"agent": pick(source[side]["agent"], "id display_name cli model reasoning_effort group family")}
    game["plies"] = [public_turn(p) for p in source["plies"]]
    game["final_turn"] = public_turn(source["final_turn"]) if source.get("final_turn") else None
    game["events"] = []
    for event in source.get("events", []):
        public = pick(event, "type ply time color attempt reason warning error")
        if event.get("type") == "tool_use_violation":
            public["events"] = [{"tool": e.get("tool", "unknown")} for e in event.get("events", [])]
        game["events"].append(public)
    turns = game["plies"] + ([game["final_turn"]] if game["final_turn"] else [])
    by_ply = {t["ply"]: t for t in turns}
    active = {}
    for t in turns:
        t["console"] = []
    fields = {
        "prompt": "text attempt", "text": "delta", "message": "text",
        "reasoning": "text", "thinking": "", "thinking_done": "",
        "block_end": "", "result": "attempt accepted move reason think_s tokens",
        "tool": "tool", "notice": "text",
    }
    for event in transcript:
        side = event.get("side")
        if event.get("k") == "turn":
            active[side] = (event.get("ply"), event.get("t", 0))
        if side not in active or event.get("k") not in fields:
            continue
        ply, start = active[side]
        turn = by_ply.get(ply)
        if not turn or turn["color"] != side:
            continue
        turn["console"].append({"k": event["k"], "offset": max(0, event.get("t", start) - start), **pick(event, fields[event["k"]])})
    for turn in turns:
        events = turn["console"]
        turn["console_source"] = "transcript" if events else "saved responses"
        if not events:
            elapsed = 0
            for a in turn["attempts"]:
                elapsed += max(0, a.get("think_s", 0))
                response = a.get("raw_output") or a.get("analysis") or a.get("plan")
                if response:
                    events.append({"k": "message", "offset": elapsed, "text": response})
                events.append({"k": "result", "offset": elapsed, "accepted": a.get("accepted"), "move": a.get("move_text"), "reason": a.get("reason")})
        extent = max([turn.get("think_s", 0), *[e["offset"] for e in events], 0.001])
        for event in events:
            event["at"] = min(1, event.pop("offset") / extent)
        # Stable sort preserves streaming order for simultaneous entries.
        events.sort(key=lambda e: e["at"])
        for attempt in turn["attempts"]:
            attempt.pop("raw_output", None)
    for event in game["events"]:
        turn = by_ply.get(event.get("ply"))
        event["at"] = 1
        if turn and event.get("attempt"):
            result = next((e for e in turn["console"] if e["k"] == "result" and e.get("attempt") == event["attempt"]), None)
            if result:
                event["at"] = result["at"]
    return clean(game)


def export(games_dir, destination):
    destination.mkdir(parents=True, exist_ok=True)
    summaries, manifest = [], []
    for path in sorted(games_dir.glob("*.json"), reverse=True):
        source = json.loads(path.read_text(encoding="utf-8"))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", source["id"]):
            raise ValueError("Unsafe game identifier")
        transcript_path = games_dir / (source["id"] + ".console.jsonl")
        transcript = []
        if transcript_path.exists():
            for line in transcript_path.read_text(encoding="utf-8").splitlines():
                try:
                    transcript.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # tolerate an interrupted final transcript write
        game = public_game(source, transcript)
        write_json(destination / (game["id"] + ".json"), game)
        summary = summarize(source)
        summaries.append(summary)
        manifest.append(clean(pick(summary, "id started_at white_id black_id white_name black_name plies opening legal_moves_sent think_aloud")))
    if not manifest:
        raise ValueError("No saved games found")
    write_json(destination / "manifest.json", manifest)
    for legal in ("all", "on", "off"):
        for think in ("all", "on", "off"):
            rows = [g for g in summaries if (legal == "all" or bool(g["legal_moves_sent"]) == (legal == "on")) and (think == "all" or bool(g["think_aloud"]) == (think == "on"))]
            write_json(destination / f"records-{legal}-{think}.json", clean(compute_records(rows)))
    return len(manifest)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=Path, default=ROOT / "games")
    parser.add_argument("--output", type=Path, default=ROOT / "demo/data")
    args = parser.parse_args()
    print(f"Exported {export(args.games, args.output)} public demo games to {args.output}")
