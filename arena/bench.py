"""Stockfish settings benchmark: `python -m arena bench-engine`.

Compares engine settings (threads, hash, time per position, MultiPV) against a
long reference search on the same positions:

  positions   consecutive middlegame positions from games/*.json (so the move
              played between two of them can be classified), plus WAC tactics
  reference   a long, wide search of every position (REF_* below)
  metrics     depth, speed (knps), agreement of the top move with the reference,
              win% lost by the chosen move (judged by the reference), eval error,
              WAC tactics solved, and how often classify() gives the same label
              as it does with the reference analyses (the number the UI shows)
"""
from __future__ import annotations

import json
import statistics
import subprocess
import time
from dataclasses import dataclass, field

import chess
import chess.engine

from .classify import LABELS, classify, win_pct
from .config import Settings
from .engine import _line

REF_TIME, REF_THREADS, REF_HASH = 6.0, 12, 1024
WINDOW = 13                  # consecutive positions taken from each saved game
WAC = [  # Win At Chess: (FEN, best move in SAN)
    ("2rr3k/pp3pp1/1nnqbN1p/3pN3/2pP4/2P3Q1/PPB4P/R4RK1 w - - 0 1", "Qg6"),
    ("8/7p/5k2/5p2/p1p2P2/Pr1pPK2/1P1R3P/8 b - - 0 1", "Rxb2"),
    ("5rk1/1ppb3p/p1pb4/6q1/3P1p1r/2P1R2P/PP1BQ1P1/5RKN w - - 0 1", "Rg3"),
    ("r1bq2rk/pp3pbp/2p1p1pQ/7P/3P4/2PB1N2/PP3PPR/2KR4 w - - 0 1", "Qxh7+"),
    ("5k2/6pp/p1qN4/1p1p4/3P4/2PKP2Q/PP3r2/3R4 b - - 0 1", "Qc4+"),
    ("7k/p7/1R5K/6r1/6p1/6P1/8/8 w - - 0 1", "Rb7"),
    ("rnbqkb1r/pppp1ppp/8/4P3/6n1/7P/PPPNPPP1/R1BQKBNR b KQkq - 0 1", "Ne3"),
    ("r4q1k/p2bR1rp/2p2Q1N/5p2/5p2/2P5/PP3PPP/R5K1 w - - 0 1", "Rf7"),
    ("3q1rk1/p4pp1/2pb3p/3p4/6Pr/1PNQ4/P1PB1PP1/4RRK1 b - - 0 1", "Bh2+"),
    ("2br2k1/2q3rn/p2NppQ1/2p1P3/Pp5R/4P3/1P3PPP/3R2K1 w - - 0 1", "Rxh7"),
]


@dataclass
class Sample:
    fen: str
    seq: int                       # positions with the same seq come from one game, in order
    played: str | None = None      # uci of the move played from this position (to the next one)
    bm: str | None = None          # WAC best move (uci)


@dataclass
class Result:
    label: str
    threads: int
    hash_mb: int
    time_s: float
    rows: list[dict] = field(default_factory=list)


def load_samples(settings: Settings, window: int = WINDOW, wac: bool = True) -> list[Sample]:
    games_dir = settings.resolve(settings.games_dir)
    out: list[Sample] = []
    for seq, path in enumerate(sorted(games_dir.glob("*.json"))):
        data = json.loads(path.read_text(encoding="utf-8"))
        plies = [p for p in data["plies"] if p.get("fen_after") and p.get("uci")]
        if len(plies) < window + 2:
            continue
        start = max(0, min(len(plies) - window - 1, len(plies) // 3))   # early middlegame
        for i in range(start, start + window):
            out.append(Sample(fen=plies[i]["fen_after"], seq=seq, played=plies[i + 1]["uci"]))
        out.append(Sample(fen=plies[start + window]["fen_after"], seq=seq))
    if wac:
        for k, (fen, san) in enumerate(WAC):
            b = chess.Board(fen)
            if b.is_valid():
                out.append(Sample(fen=fen, seq=1000 + k, bm=b.parse_san(san).uci()))
    return out


def run_config(path: str, samples: list[Sample], threads: int, hash_mb: int, time_s: float,
               multipv: int = 3, progress=None) -> list[dict]:
    """Analyses every sample; one engine process, ucinewgame per game sequence."""
    eng = chess.engine.SimpleEngine.popen_uci([path], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        eng.configure({"Threads": threads, "Hash": hash_mb})
        rows, game, last_seq = [], object(), None
        for i, s in enumerate(samples):
            if s.seq != last_seq:
                game, last_seq = object(), s.seq
            b = chess.Board(s.fen)
            t0 = time.monotonic()
            infos = eng.analyse(b, chess.engine.Limit(time=time_s), multipv=multipv, game=game)
            ms = (time.monotonic() - t0) * 1000
            top = infos[0]
            rows.append({"lines": [ln for ln in (_line(b, x) for x in infos) if ln],
                         "depth": top.get("depth") or 0, "nodes": top.get("nodes") or 0, "ms": ms})
            if progress:
                progress(i + 1, len(samples))
        return rows
    finally:
        eng.quit()


def _score(ln: dict) -> dict:
    return {k: ln[k] for k in ("cp", "mate") if k in ln}


def compare(samples: list[Sample], cand: list[dict], ref: list[dict]) -> dict:
    top1, loss, err, solved, n_wac, depth, knps = [], [], [], [], 0, [], []
    for s, c, r in zip(samples, cand, ref):
        depth.append(c["depth"])
        knps.append(c["nodes"] / max(c["ms"], 1))
        if not c["lines"] or not r["lines"]:
            continue
        color = chess.Board(s.fen).turn
        best_c, ref_lines = c["lines"][0]["uci"], r["lines"]
        if s.bm:
            n_wac += 1
            solved.append(best_c == s.bm)
            continue
        top1.append(best_c == ref_lines[0]["uci"])
        w_ref = win_pct(ref_lines[0], color)
        match = next((ln for ln in ref_lines if ln["uci"] == best_c), ref_lines[-1])
        loss.append(max(0.0, w_ref - win_pct(match, color)))
        err.append(abs(win_pct(c["lines"][0], color) - w_ref))
    # classification of each played move, candidate vs reference analyses
    same, near, n = 0, 0, 0
    for i in range(len(samples) - 1):
        s, nxt = samples[i], samples[i + 1]
        if not s.played or nxt.seq != s.seq:
            continue
        board, mv = chess.Board(s.fen), chess.Move.from_uci(s.played)

        def lab(rows):
            after_lines = rows[i + 1]["lines"]
            if not rows[i]["lines"] or not after_lines:
                return None
            after = {**_score(after_lines[0]), "lines": after_lines}
            c = classify(board, mv, rows[i]["lines"], after)
            return c and c["label"]
        a, b = lab(cand), lab(ref)
        if a is None or b is None:
            continue
        n += 1
        same += a == b
        near += abs(LABELS.index(a) - LABELS.index(b)) <= 1
    pct = lambda xs: 100 * sum(xs) / len(xs) if xs else float("nan")  # noqa: E731
    return {"depth": statistics.mean(depth), "knps": statistics.mean(knps), "top1": pct(top1),
            "loss": statistics.mean(loss) if loss else float("nan"),
            "eval_err": statistics.mean(err) if err else float("nan"),
            "wac": f"{sum(solved)}/{n_wac}", "cls": 100 * same / n if n else float("nan"),
            "cls_near": 100 * near / n if n else float("nan"), "pairs": n}


HEAD = ("| threads | hash MB | time s | depth | knps | top-1 % | move loss % | eval err % | WAC | "
        "same label % | within 1 label % |\n|---|---|---|---|---|---|---|---|---|---|---|")


def fmt(threads, hash_mb, time_s, m) -> str:
    return (f"| {threads} | {hash_mb} | {time_s} | {m['depth']:.1f} | {m['knps']:.0f} | {m['top1']:.0f} | "
            f"{m['loss']:.2f} | {m['eval_err']:.2f} | {m['wac']} | {m['cls']:.0f} | {m['cls_near']:.0f} |")


def mean_metrics(ms: list[dict]) -> dict:
    out = {}
    for k in ms[0]:
        out[k] = ms[0][k] if isinstance(ms[0][k], str) else statistics.mean(m[k] for m in ms)
    if len(ms) > 1:
        out["wac"] = "/".join(m["wac"].split("/")[0] for m in ms) + "/" + ms[0]["wac"].split("/")[1]
    return out


def bench_cli(settings: Settings, quick: bool = False) -> int:
    path = str(settings.resolve(settings.stockfish_path))
    samples = load_samples(settings, window=5 if quick else WINDOW, wac=not quick)
    print(f"{len(samples)} positions; reference: {REF_TIME}s, {REF_THREADS} threads, {REF_HASH} MB, MultiPV 3",
          flush=True)
    prog = lambda i, n: print(f"\r  {i}/{n}", end="", flush=True)  # noqa: E731
    ref = run_config(path, samples, REF_THREADS, REF_HASH, 1.0 if quick else REF_TIME, progress=prog)
    print()
    threads_opts, times = ((4, 6), (0.3,)) if quick else ((4, 5, 6), (0.5, 0.8, 1.2, 1.6))
    lines_out = ["Phase A: threads x time (hash 256 MB)", HEAD]
    results = {}
    for t in threads_opts:
        for tm in times:
            print(f"threads {t}, {tm}s", flush=True)
            m = compare(samples, run_config(path, samples, t, 256, tm, progress=prog), ref)
            print()
            results[(t, tm)] = m
            lines_out.append(fmt(t, 256, tm, m))
            print(lines_out[-1], flush=True)
    # shortest time that meets the bar; then the fewest threads with the best label agreement at that time
    ok = [k for k, m in results.items() if m["cls"] >= 90 and m["top1"] >= 85]
    if ok:
        tm = min(k[1] for k in ok)
        t = max((k for k in ok if k[1] == tm), key=lambda k: (round(results[k]["cls"]), -k[0]))[0]
    else:
        t, tm = max(results, key=lambda k: (results[k]["cls"], -k[1]))
    print(f"phase A pick: {t} threads, {tm}s", flush=True)
    lines_out += ["", f"Phase B: hash at {t} threads, {tm}s (2 runs each, averaged)", HEAD]
    for h in ((256, 512) if quick else (256, 384, 512)):
        print(f"hash {h}", flush=True)
        runs = [compare(samples, run_config(path, samples, t, h, tm, progress=prog), ref) for _ in range(2)]
        print()
        lines_out.append(fmt(t, h, tm, mean_metrics(runs)))
        print(lines_out[-1], flush=True)
    print("\n" + "\n".join(lines_out))
    return 0
