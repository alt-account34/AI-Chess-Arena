"""The referee: runs the game loop and enforces the rules.

Each turn: build the prompt -> start timer -> call the side's CLI (resuming its
session) -> stop timer -> parse -> validate -> push or retry -> broadcast ->
check for game end. Agents never poll; the referee calls them.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import chess

from . import classify, parsing, prompts
from .agents.base import Agent, AgentCancelled, AgentFailure, AgentReply, AgentTimeout
from .config import AgentConfig, Settings
from .state import BLACK, WHITE, Attempt, GameState, PlyRecord, SideState, add_tokens, other

log = logging.getLogger("arena.referee")

EventCallback = Callable[[str, dict], Awaitable[None]]

UNPARSABLE_REASON = ("could not parse a move: your reply must end with 'MOVE: <move>' "
                     "and 'PLAN: <plan>', or with a RESIGN block")


class Analyzer(Protocol):
    """Anything with a blocking analyse(fen) -> {"cp": int} | {"mate": int} | None."""
    available: bool

    def analyse(self, fen: str) -> dict | None: ...


def color_name(color: str) -> str:
    return "White" if color == WHITE else "Black"


def check_game_end(board: chess.Board, plies_played: int, move_cap: int
                   ) -> tuple[str, str, str, str | None] | None:
    """Returns (result, termination, reason, winner) or None if the game goes on.

    Draws by threefold repetition and the 50-move rule are claimed automatically
    as soon as the condition is actually reached (not when it merely could be
    reached by the next move).
    """
    if board.is_checkmate():
        winner = BLACK if board.turn == chess.WHITE else WHITE
        return ("1-0" if winner == WHITE else "0-1", "checkmate",
                f"{color_name(winner)} wins by checkmate", winner)
    if board.is_stalemate():
        return "1/2-1/2", "stalemate", "Draw by stalemate", None
    if board.is_insufficient_material():
        return "1/2-1/2", "insufficient_material", "Draw by insufficient material", None
    if board.is_fivefold_repetition():
        return "1/2-1/2", "fivefold_repetition", "Draw by fivefold repetition", None
    if board.is_seventyfive_moves():
        return "1/2-1/2", "seventyfive_moves", "Draw by the 75-move rule", None
    if board.is_repetition(3):
        return "1/2-1/2", "threefold_repetition", "Draw by threefold repetition", None
    if board.halfmove_clock >= 100:
        return "1/2-1/2", "fifty_moves", "Draw by the 50-move rule", None
    if plies_played >= move_cap:
        return "1/2-1/2", "move_cap", f"Draw: move limit of {move_cap} plies reached", None
    return None


async def _noop_event(_type: str, _data: dict) -> None:
    return None


def _line_steps(board: chess.Board, sans: list[str]) -> list[dict]:
    """FEN after each move of a validated line, so the UI can animate it."""
    b = board.copy(stack=False)
    steps = []
    for san in sans:
        mv = b.parse_san(san)
        b.push(mv)
        steps.append({"san": san, "uci": mv.uci(), "fen": b.fen(), "check": b.is_check()})
    return steps


class Referee:
    def __init__(self, agents: dict[str, Agent], configs: dict[str, AgentConfig],
                 settings: Settings, *, send_legal_moves: bool | None = None,
                 move_cap: int | None = None, start_fen: str | None = None,
                 game_id: str | None = None, analyzer: Analyzer | None = None,
                 opening: dict | None = None, think_aloud: bool | None = None,
                 console: Any = None,
                 on_event: EventCallback | None = None,
                 on_finish: Callable[[GameState, chess.Board], Any] | None = None):
        self.agents = agents
        self.configs = configs
        self.settings = settings
        self.send_legal = settings.send_legal_moves if send_legal_moves is None else send_legal_moves
        self.think_aloud = settings.think_aloud if think_aloud is None else think_aloud
        self.console = console      # ConsoleLog (live agent console), optional
        self.move_cap = settings.move_cap_plies if move_cap is None else move_cap
        self.timeout = settings.safety_timeout_s
        self.max_streak = settings.max_illegal_streak
        self.analyzer = analyzer
        self.on_event = on_event or _noop_event
        self.on_finish = on_finish

        self.board = chess.Board(start_fen) if start_fen else chess.Board()
        now = datetime.now()
        gid = game_id or f"{now:%Y%m%d-%H%M%S}_{configs[WHITE].id}_vs_{configs[BLACK].id}"
        self.state = GameState(
            id=gid,
            started_at=now.isoformat(timespec="seconds"),
            start_fen=self.board.fen(),
            settings={"send_legal_moves": self.send_legal, "move_cap_plies": self.move_cap,
                      "safety_timeout_s": self.timeout, "max_illegal_streak": self.max_streak},
            white=SideState(agent=configs[WHITE].model_dump()),
            black=SideState(agent=configs[BLACK].model_dump()),
            opening=opening,
        )
        self.state.settings["opening"] = opening["name"] if opening else "free"
        self.state.settings["think_aloud"] = self.think_aloud
        self._wake = asyncio.Event()
        self._aborted = False
        self._decision: str | None = None
        self._eval_tasks: list[asyncio.Task] = []
        self._last_eval: asyncio.Task | None = None   # evals run in ply order (classification)
        self._resign_color: str | None = None        # a human resigned
        self._workdirs: dict[str, Path] = {}
        self._seen_warnings: set[tuple[str, str]] = set()
        self._partial_think = 0.0

    # ------------------------------------------------------------------ controls
    def request_pause(self) -> None:
        if self.state.status in ("running", "starting"):
            self.state.pause_requested = True
            self._fire("pause_requested", {})

    def resume(self) -> None:
        self.state.pause_requested = False
        self._wake.set()

    def retry(self) -> None:
        self._decision = "retry"
        self._wake.set()

    def abort(self) -> None:
        self._aborted = True
        self._decision = "abort"
        for agent in self.agents.values():
            try:
                agent.cancel()
            except Exception:  # noqa: BLE001
                log.exception("cancel failed")
        self._wake.set()

    # ------------------------------------------------------------------ human player
    def human_turn_color(self) -> str | None:
        t = self.state.turn
        if t and t.get("human") and self.state.status == "running" and not t.get("submitted"):
            return t["color"]
        return None

    def submit_human_move(self, uci: str) -> str:
        """Hands a move to the human whose turn it is. Returns its SAN.
        Raises LookupError (not a human's turn) or ValueError (illegal move)."""
        color = self.human_turn_color()
        if color is None:
            raise LookupError("it is not a human player's turn")
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise ValueError(f"not a move: {uci!r}") from None
        if move not in self.board.legal_moves:
            raise ValueError(f"illegal move: {uci}")
        self.state.turn["submitted"] = True
        self.agents[color].submit_move(uci)
        return self.board.san(move)

    def human_resign(self, color: str) -> None:
        """A human player resigns, on its own turn or while the opponent thinks."""
        if not self.agents[color].is_human or self.state.status == "finished":
            raise LookupError(f"{color} is not a human player in a running game")
        self._resign_color = color
        t = self.state.turn
        if t and t["color"] == color:
            self.agents[color].submit_resign()
        elif t:
            self.agents[t["color"]].cancel()
        if self.state.status == "unresponsive":
            self._decision = "abort"
        self._wake.set()

    async def _finish_resign(self) -> None:
        color = self._resign_color
        winner = other(color)
        await self._finish("1-0" if winner == WHITE else "0-1", "resignation",
                           f"{color_name(color)} resigned", winner)

    def _fire(self, type_: str, data: dict) -> None:
        asyncio.ensure_future(self.on_event(type_, data))

    async def _emit(self, type_: str, data: dict | None = None) -> None:
        try:
            await self.on_event(type_, data or {})
        except Exception:  # noqa: BLE001 - the UI must never break the game
            log.exception("event callback failed")

    def _event(self, type_: str, **data: Any) -> dict:
        ev = {"type": type_, "time": datetime.now().isoformat(timespec="seconds"),
              "ply": len(self.state.plies), **data}
        self.state.events.append(ev)
        return ev

    # ------------------------------------------------------------------ main loop
    async def run(self) -> GameState:
        st = self.state
        try:
            await self._setup()
            st.status = "running"
            await self._emit("game_start")
            if self.analyzer is not None:
                self._queue_eval(self._eval_start())
            if st.opening:
                await self._play_book_moves()
            while st.status != "finished":
                if self._resign_color:
                    await self._finish_resign()
                    break
                end = check_game_end(self.board, len(st.plies), self.move_cap)
                if end:
                    await self._finish(*end)
                    break
                if st.pause_requested and not self._aborted:
                    st.status = "paused"
                    await self._emit("paused")
                    while st.pause_requested and not self._aborted and not self._resign_color:
                        self._wake.clear()
                        await self._wake.wait()
                    st.status = "running"
                    await self._emit("resumed")
                if self._resign_color:
                    await self._finish_resign()
                    break
                if self._aborted:
                    await self._finish("*", "abort", "Game aborted", None)
                    break
                await self._play_turn()
                if self._resign_color and st.status != "finished":
                    await self._finish_resign()
                if self._aborted and st.status != "finished":
                    await self._finish("*", "abort", "Game aborted", None)
        except Exception as e:  # noqa: BLE001
            log.exception("referee crashed")
            self._event("referee_error", error=repr(e))
            if st.status != "finished":
                await self._finish("*", "error", f"Referee error: {e!r}", None)
        finally:
            await self._wrap_up()
        return st

    async def _setup(self) -> None:
        root = Path(self.settings.agent_temp_root or Path(tempfile.gettempdir()) / "ai-chess-arena")
        for color in (WHITE, BLACK):
            # Random names: some CLIs show the model its working folder, and the
            # game id would reveal the opponent.
            wd = root / f"game-{uuid.uuid4().hex[:12]}"
            wd.mkdir(parents=True, exist_ok=False)
            self._workdirs[color] = wd
            if self.console is not None:
                self.agents[color].console = self.console.sink(color)
            await asyncio.to_thread(self.agents[color].setup, wd)

    async def _wrap_up(self) -> None:
        if self._eval_tasks:
            done, pending = await asyncio.wait(self._eval_tasks, timeout=10)
            for t in pending:
                t.cancel()
        for color, agent in self.agents.items():
            try:
                await asyncio.to_thread(agent.teardown)
            except Exception:  # noqa: BLE001
                log.exception("teardown failed")
            wd = self._workdirs.get(color)
            if wd is not None:
                shutil.rmtree(wd, ignore_errors=True)
        if self.on_finish is not None:
            try:
                res = self.on_finish(self.state, self.board)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001
                log.exception("on_finish failed")
        await self._emit("saved")

    async def _finish(self, result: str, termination: str, reason: str, winner: str | None) -> None:
        st = self.state
        st.result, st.termination, st.reason, st.winner = result, termination, reason, winner
        st.status = "finished"
        st.turn = None
        st.pause_requested = False
        st.finished_at = datetime.now().isoformat(timespec="seconds")
        for color in (WHITE, BLACK):
            self._con(color, "status", text=f"Game over: {result} ({reason})", final=True)
        await self._emit("game_over", {"result": result, "termination": termination, "reason": reason})

    # ------------------------------------------------------------------ one turn
    async def _play_turn(self) -> None:
        st, board = self.state, self.board
        color = WHITE if board.turn == chess.WHITE else BLACK
        side, opp = st.side(color), st.side(other(color))
        attempts: list[Attempt] = []
        violations: list[dict] = []
        think = 0.0
        retry: prompts.RetryInfo | None = None
        st.turn = {"color": color, "agent_id": self.configs[color].id, "attempt": 0,
                   "think_before": 0.0, "attempt_started_at": None}
        if self.agents[color].is_human:
            dests: dict[str, list[str]] = {}
            for m in board.legal_moves:
                dests.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
            st.turn.update(human=True, dests=dests, submitted=False)
        self._con(color, "turn", ply=len(st.plies) + 1, move_number=board.fullmove_number)

        while True:
            ctx = prompts.make_context(board, opponent_last_think_s=opp.last_think_s,
                                       last_plan=side.last_plan,
                                       send_legal_moves=self.send_legal, retry=retry)
            got = await self._call_agent(color, ctx, think, len(attempts) + 1)
            if got is None:  # aborted mid-turn
                think += self._partial_think
                self._close_turn(color, attempts, violations, think, final=True)
                return
            reply, elapsed, new_session, prompt_text = got
            think += elapsed
            if reply.meta.get("human_resign"):
                self._close_turn(color, attempts, violations, think, final=True)
                return
            parsed = parsing.parse_response(reply.text)
            att = Attempt(
                n=len(attempts) + 1, think_s=round(elapsed, 3), raw_output=reply.text,
                kind=parsed.kind, move_text=parsed.move_raw, plan=parsed.plan,
                resign_explanation=parsed.explanation, resign_line=parsed.line_raw,
                session_id=reply.session_id, new_session=new_session,
                tool_events=reply.tool_events, stderr=reply.stderr[-4000:],
                exit_code=reply.exit_code, meta=reply.meta, tokens=reply.tokens,
                prompt=prompt_text, analysis=parsed.analysis,
                reasoning="\n".join(reply.meta.get("reasoning") or []) or None,
            )
            attempts.append(att)
            add_tokens(side.tokens, reply.tokens)
            for w in reply.warnings:
                if (color, w) not in self._seen_warnings:  # CLIs repeat some notices every turn
                    self._seen_warnings.add((color, w))
                    self._event("agent_warning", color=color, warning=w)
            if reply.tool_events:
                v = {"color": color, "attempt": att.n, "events": reply.tool_events}
                violations.append(v)
                side.tool_violations += 1
                ev = self._event("tool_use_violation", color=color, attempt=att.n,
                                 events=reply.tool_events)
                await self._emit("tool_violation", ev)
            if parsed.plan:
                side.last_plan = parsed.plan

            if parsed.kind == "move":
                move, reason = parsing.resolve_move(board, parsed.move)
                if move is not None:
                    att.accepted = True
                    san = board.san(move)
                    board.push(move)
                    side.illegal_streak = 0
                    rec = self._close_turn(color, attempts, violations, think)
                    rec.san, rec.uci, rec.fen_after = san, move.uci(), board.fen()
                    rec.plan = parsed.plan
                    rec.analysis = parsed.analysis
                    side.moves += 1
                    self._con_result(color, att, san)
                    st.plies.append(rec)
                    await self._emit("move", {"ply": rec.ply, "san": san, "color": color})
                    if self.analyzer is not None:
                        self._queue_eval(self._eval_ply(rec))
                    return
                reason = reason or "illegal move"
            elif parsed.kind == "resign":
                ok, why, sans = parsing.validate_resignation_line(board, parsed.line)
                if ok:
                    att.accepted = True
                    self._con_result(color, att, "RESIGN")
                    self._close_turn(color, attempts, violations, think, final=True)
                    winner = other(color)
                    st.resignation = {"color": color, "explanation": parsed.explanation,
                                      "line_san": sans, "line_raw": parsed.line_raw,
                                      "fen": board.fen(), "line": _line_steps(board, sans)}
                    await self._finish("1-0" if winner == WHITE else "0-1", "resignation",
                                       f"{color_name(color)} resigned", winner)
                    return
                reason = f"resignation rejected: {why}"
            else:
                reason = UNPARSABLE_REASON

            att.reason = reason
            self._con_result(color, att, parsed.move_raw or parsed.kind)
            side.illegal_total += 1
            side.illegal_streak += 1
            ev = self._event("illegal_attempt", color=color, attempt=att.n,
                             move=parsed.move_raw, reason=reason,
                             streak=side.illegal_streak, total=side.illegal_total)
            await self._emit("illegal", ev)
            if side.illegal_streak >= self.max_streak:
                self._close_turn(color, attempts, violations, think, final=True)
                winner = other(color)
                await self._finish("1-0" if winner == WHITE else "0-1", "forfeit",
                                   f"{color_name(color)} forfeits: {self.max_streak} illegal attempts in a row "
                                   f"(last: {reason})", winner)
                return
            if st.turn is not None and st.turn.get("human"):
                st.turn["submitted"] = False
            attempted = parsed.move_raw if parsed.kind == "move" else ("RESIGN" if parsed.kind == "resign" else None)
            retry = prompts.RetryInfo(attempted=attempted, reason=reason)

    def _close_turn(self, color: str, attempts: list[Attempt], violations: list[dict],
                    think: float, final: bool = False) -> PlyRecord:
        st = self.state
        side = st.side(color)
        side.think_total += think
        side.last_think_s = round(think, 3)
        rec = PlyRecord(
            ply=len(st.plies) + 1, color=color, agent_id=self.configs[color].id,
            think_s=round(think, 3), attempts=attempts, tool_violations=violations,
            clock_after={WHITE: round(st.white.think_total, 3), BLACK: round(st.black.think_total, 3)},
            illegal_after={c: {"total": st.side(c).illegal_total, "streak": st.side(c).illegal_streak}
                           for c in (WHITE, BLACK)},
            tokens_after={c: dict(st.side(c).tokens) for c in (WHITE, BLACK)},
        )
        if any(a.tokens for a in attempts):
            rec.tokens = {}
            for a in attempts:
                add_tokens(rec.tokens, a.tokens)
        st.turn = None
        if final:
            st.final_turn = rec
        return rec

    async def _call_agent(self, color: str, ctx: prompts.PromptContext, think_before: float,
                          attempt_no: int) -> tuple[AgentReply, float, bool] | None:
        """Calls the agent, handling resume failure, timeouts and CLI failures.
        Returns (reply, seconds spent, started_new_session) or None if aborted."""
        st = self.state
        side = st.side(color)
        agent = self.agents[color]
        spent = 0.0
        self._partial_think = 0.0
        while True:
            if self._aborted:
                return None
            new_session = side.session_id is None
            if agent.is_human:
                prompt = None
            elif not new_session:
                prompt = prompts.turn_prompt(ctx, self.think_aloud)
            elif side.intro_sent:
                prompt = prompts.recovery_prompt(ctx, self.send_legal, opening=self._opening_note(),
                                                 think_aloud=self.think_aloud)
            else:
                prompt = prompts.first_prompt(ctx, self.send_legal, opening=self._opening_note(),
                                              think_aloud=self.think_aloud)
            if prompt is not None:
                self._con(color, "prompt", attempt=attempt_no, text=prompt, session=side.session_id,
                          new_session=new_session)
            st.status = "running"
            st.unresponsive_reason = None
            st.turn.update(attempt=attempt_no, think_before=round(think_before + spent, 3),
                           attempt_started_at=time.time())
            await self._emit("turn_start", {"color": color, "attempt": attempt_no})
            t0 = time.monotonic()
            failure: str | None = None
            try:
                reply = await asyncio.to_thread(agent.ask, prompt, side.session_id, self.timeout)
            except AgentCancelled:
                self._partial_think = spent + time.monotonic() - t0
                return None
            except AgentTimeout:
                failure = f"no response within the safety timeout ({self.timeout:.0f}s)"
            except AgentFailure as e:
                failure = f"CLI failure: {e}"
            except Exception as e:  # noqa: BLE001
                log.exception("agent.ask raised")
                failure = f"unexpected error: {e!r}"
            elapsed = time.monotonic() - t0
            spent += elapsed
            self._partial_think = spent
            if self._aborted:
                return None
            if failure is None and reply.resume_failed:
                if new_session:
                    failure = f"CLI failed to start a session: {reply.stderr[-500:]}"
                else:
                    self._event("session_resume_failure", color=color, session_id=side.session_id,
                                detail=reply.stderr[-1000:])
                    log.warning("%s: resume of %s failed; starting a new session", color, side.session_id)
                    side.session_id = None
                    self._con(color, "status", text="Session resume failed; starting a new session")
                    continue
            if failure is not None:
                self._con(color, "status", text=f"Agent unresponsive: {failure}")
                st.turn.update(think_before=round(think_before + spent, 3), attempt_started_at=None)
                self._event("agent_unresponsive", color=color, reason=failure)
                decision = await self._await_decision(failure)
                if decision == "abort":
                    return None
                continue
            side.intro_sent = True
            if reply.session_id and reply.session_id != side.session_id:
                side.session_id = reply.session_id
                side.session_history.append(reply.session_id)
            elif not reply.session_id and not agent.is_human:
                self._event("agent_warning", color=color, warning="CLI did not report a session id")
            return reply, spent, new_session, prompt

    async def _await_decision(self, reason: str) -> str:
        st = self.state
        st.status = "unresponsive"
        st.unresponsive_reason = reason
        self._decision = None
        await self._emit("unresponsive", {"reason": reason})
        while self._decision is None:
            self._wake.clear()
            await self._wake.wait()
        decision = self._decision
        self._decision = None
        st.status = "running"
        st.unresponsive_reason = None
        return decision

    # ------------------------------------------------------------------ live console
    def _con(self, color: str, kind: str, **data: Any) -> None:
        if self.console is not None:
            self.console.emit(color, kind, **data)

    def _con_result(self, color: str, att: Attempt, move: str | None) -> None:
        self._con(color, "result", attempt=att.n, accepted=att.accepted, move=move, reason=att.reason,
                  think_s=att.think_s, tokens=att.tokens, tools=att.tool_events,
                  analysis=att.analysis, plan=att.plan)

    # ------------------------------------------------------------------ opening
    def _opening_note(self) -> str | None:
        op = self.state.opening
        if not op:
            return None
        line = chess.Board().variation_san([m for m in self._book_moves()])
        return f"{op['name']} ({line})"

    def _book_moves(self) -> list[chess.Move]:
        b = chess.Board(self.state.start_fen)
        out = []
        for san in self.state.opening["moves"]:
            mv = b.parse_san(san)
            out.append(mv)
            b.push(mv)
        return out

    async def _play_book_moves(self) -> None:
        """Plays the chosen opening for both sides before the agents take over."""
        st, board = self.state, self.board
        for mv in self._book_moves():
            color = WHITE if board.turn == chess.WHITE else BLACK
            san = board.san(mv)
            board.push(mv)
            rec = PlyRecord(
                ply=len(st.plies) + 1, color=color, agent_id="book", think_s=0.0, attempts=[],
                san=san, uci=mv.uci(), fen_after=board.fen(), book=True,
                clock_after={WHITE: 0.0, BLACK: 0.0},
                illegal_after={c: {"total": 0, "streak": 0} for c in (WHITE, BLACK)},
                tokens_after={c: dict(st.side(c).tokens) for c in (WHITE, BLACK)},
            )
            st.plies.append(rec)
            await self._emit("move", {"ply": rec.ply, "san": san, "color": color, "book": True})
            if self.analyzer is not None:
                self._queue_eval(self._eval_ply(rec))

    # ------------------------------------------------------------------ engine (UI only)
    def _queue_eval(self, coro) -> None:
        """Runs analyses one after another in ply order: classifying a ply needs the
        lines of the position before it."""
        prev = self._last_eval

        async def chained():
            if prev is not None:
                await asyncio.wait([prev])
            await coro
        self._last_eval = asyncio.create_task(chained())
        self._eval_tasks.append(self._last_eval)

    async def _eval_start(self) -> None:
        ev = await asyncio.to_thread(self.analyzer.analyse, self.state.start_fen)
        if ev:
            self.state.start_lines = ev.pop("lines", None)
        self.state.start_eval = ev
        self.state.engine_available = self.analyzer.available
        await self._emit("eval", {"ply": 0, "eval": ev})

    async def _eval_ply(self, rec: PlyRecord) -> None:
        ev = await asyncio.to_thread(self.analyzer.analyse, rec.fen_after)
        if ev:
            rec.lines = ev.pop("lines", None)
        rec.eval = ev
        self.state.engine_available = self.analyzer.available
        if not rec.book:
            try:
                rec.classification = classify_ply(self.state, rec)
            except Exception:  # noqa: BLE001 - classification is cosmetic
                log.exception("classification failed for ply %s", rec.ply)
        await self._emit("eval", {"ply": rec.ply, "eval": ev, "classification": rec.classification})


def classify_ply(state: GameState, rec: PlyRecord) -> dict | None:
    """Classifies one played ply from the stored engine lines (see classify.py)."""
    i = rec.ply - 1
    prev = state.plies[i - 1] if i > 0 else None
    before_fen = prev.fen_after if prev else state.start_fen
    before_lines = prev.lines if prev else state.start_lines
    if not rec.uci or rec.eval is None or not before_lines:
        return None
    after = {**rec.eval, "lines": rec.lines}
    prev_move = chess.Move.from_uci(prev.uci) if prev and prev.uci else None
    return classify.classify(chess.Board(before_fen), chess.Move.from_uci(rec.uci), before_lines,
                             after, prev_move)
