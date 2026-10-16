"""Entry point.

  python -m arena                          start the web server (http://localhost:8000)
  python -m arena play --white A --black B run one game headless in the terminal
  python -m arena rebuild-records          rebuild arena.db from games/*.json
  python -m arena check-clis               check that the CLIs still accept our flags
  python -m arena analyze --all            re-analyse saved games and classify every move
  python -m arena bench-engine             measure Stockfish settings (threads/hash/time)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import load_agents, load_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arena")
    sub = ap.add_subparsers(dest="cmd")
    play = sub.add_parser("play", help="run one game in the terminal")
    play.add_argument("--white", required=True)
    play.add_argument("--black", required=True)
    play.add_argument("--legal-moves", action="store_true", help="send the legal-move list")
    play.add_argument("--think-aloud", action="store_true", help="ask agents for an ANALYSIS block")
    play.add_argument("--move-cap", type=int, default=None)
    play.add_argument("--fen", default=None, help="start from this position")
    play.add_argument("--opening", default="free",
                      help="free | random | custom | an opening id (see arena/openings.py)")
    play.add_argument("--opening-moves", default=None, help="SAN moves for --opening custom")
    play.add_argument("--no-engine", action="store_true")
    play.add_argument("--no-save", action="store_true")
    srv = sub.add_parser("serve", help="start the web server (default)")
    for parser in (ap, srv):
        parser.add_argument("--port", type=int, default=None)
        parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    watch = sub.add_parser("watch", help="live agent console in this terminal (needs the server)")
    watch.add_argument("side", choices=["white", "black"])
    watch.add_argument("--game", default=None, help="a past game id (default: follow the live game)")
    watch.add_argument("--port", type=int, default=None)
    watch.add_argument("--raw", action="store_true", help="also print the CLI's raw JSON lines")
    sub.add_parser("rebuild-records", help="rebuild arena.db from games/*.json")
    sub.add_parser("fix-tokens", help="recompute token counts in saved game logs, then rebuild arena.db")
    sub.add_parser("check-clis", help="verify the claude/codex CLIs still support our flags")
    an = sub.add_parser("analyze", help="re-analyse saved games with Stockfish and classify every move")
    an.add_argument("games", nargs="*", help="game ids (default with --all: every saved game)")
    an.add_argument("--all", action="store_true")
    an.add_argument("--missing", action="store_true", help="only games without classifications")
    bench = sub.add_parser("bench-engine", help="benchmark Stockfish threads/hash/time settings")
    bench.add_argument("--quick", action="store_true", help="fewer positions and settings (smoke test)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = load_settings()

    if args.cmd == "watch":
        from .watch import watch
        return watch(args.side, port=args.port or settings.port, game=args.game, raw=args.raw)
    if args.cmd == "play":
        return asyncio.run(_play(args, settings))
    if args.cmd == "rebuild-records":
        from .storage import RecordDB
        db = RecordDB(settings.resolve(settings.db_path))
        n = db.rebuild(settings.resolve(settings.games_dir))
        print(f"indexed {n} games into {settings.db_path}")
        return 0
    if args.cmd == "fix-tokens":
        from .storage import RecordDB, fix_token_logs
        games_dir = settings.resolve(settings.games_dir)
        for gid, before, after in fix_token_logs(games_dir):
            print(gid)
            for c in ("white", "black"):
                b, a = before.get(c) or {}, after[c]
                print(f"  {c}: input {b.get('input', '-')} -> {a['input']}, output {b.get('output', '-')} -> {a['output']}")
        n = RecordDB(settings.resolve(settings.db_path)).rebuild(games_dir)
        print(f"re-indexed {n} games")
        return 0
    if args.cmd == "analyze":
        from .analyze import analyze_cli
        return analyze_cli(settings, args.games, all_games=args.all, missing_only=args.missing)
    if args.cmd == "bench-engine":
        from .bench import bench_cli
        return bench_cli(settings, quick=args.quick)
    if args.cmd == "check-clis":
        from .clicheck import check_all
        return 0 if check_all(settings, verbose=True) else 1
    from .server import serve
    if getattr(args, "port", None):
        settings.port = args.port
    if getattr(args, "no_browser", False):
        settings.open_browser = False
    serve(settings)
    return 0


async def _play(args: argparse.Namespace, settings) -> int:
    from .agents.registry import create_agent
    from .referee import Referee
    from .state import BLACK, WHITE
    from .storage import save_game

    agents_cfg = load_agents()
    for aid in (args.white, args.black):
        if aid not in agents_cfg:
            print(f"unknown agent '{aid}'. Known: {', '.join(agents_cfg)}", file=sys.stderr)
            return 2
    from . import openings
    try:
        opening = openings.resolve(args.opening, args.opening_moves)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    configs = {WHITE: agents_cfg[args.white], BLACK: agents_cfg[args.black]}
    if any(cfg.cli == "human" for cfg in configs.values()):
        print("a human player needs the board in the browser: start the server and use the setup page",
              file=sys.stderr)
        return 2
    agents = {c: create_agent(cfg, settings) for c, cfg in configs.items()}

    analyzer = None
    if not args.no_engine:
        from .engine import Analyzer
        analyzer = Analyzer(settings)

    referee: Referee | None = None

    async def on_event(type_: str, data: dict) -> None:
        st = referee.state
        if type_ == "move":
            rec = st.plies[-1]
            if rec.book:
                print(f"{rec.ply:>3}. {rec.color:<5} {rec.san:<8} (book)")
                return
            tok = rec.tokens or {}
            print(f"{rec.ply:>3}. {rec.color:<5} {rec.san:<8} think {rec.think_s:6.2f}s "
                  f"attempts {len(rec.attempts)}  tokens in {tok.get('input', '-')} out {tok.get('output', '-')}")
        elif type_ == "illegal":
            print(f"     ! {data['color']} attempt {data['attempt']}: {data['reason']} "
                  f"(streak {data['streak']}, total {data['total']})")
        elif type_ == "tool_violation":
            print(f"     ! TOOL USE by {data['color']}: {data['events']}")
        elif type_ == "unresponsive":
            print(f"     ! agent unresponsive: {data['reason']} -> aborting (headless mode)")
            referee.abort()
        elif type_ == "game_over":
            print(f"\nResult: {data['result']}  ({data['reason']})")

    def on_finish(state, board):
        if args.no_save:
            return
        pgn, js = save_game(state, board, settings.resolve(settings.games_dir))
        try:
            from .storage import RecordDB, summarize
            RecordDB(settings.resolve(settings.db_path)).upsert(summarize(state.to_dict()))
        except Exception as e:  # noqa: BLE001
            print(f"record index update failed: {e!r}")
        print(f"saved {pgn.name} and {js.name}")

    referee = Referee(agents, configs, settings, send_legal_moves=args.legal_moves or None,
                      think_aloud=args.think_aloud or None,
                      move_cap=args.move_cap, start_fen=args.fen, analyzer=analyzer, opening=opening,
                      on_event=on_event, on_finish=on_finish)
    if not args.no_save:
        from .console import ConsoleLog, console_path
        gid = referee.state.id
        referee.console = ConsoleLog(gid, console_path(settings.resolve(settings.games_dir), gid))
    print(f"{configs[WHITE].display_name} (White) vs {configs[BLACK].display_name} (Black)")
    try:
        st = await referee.run()
    finally:
        if analyzer is not None:
            analyzer.close()
    if st.resignation:
        r = st.resignation
        print(f"Resignation: {r['explanation']}\nExpected line: {' '.join(r['line_san'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
