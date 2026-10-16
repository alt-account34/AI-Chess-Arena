import chess

from arena.referee import check_game_end
from arena.state import BLACK, WHITE

from .conftest import run


# --------------------------------------------------------------------------- illegal moves

def test_three_illegal_in_a_row_forfeits(make_game):
    ref, agents = make_game(white=["random"], black=["illegal", "garbage", "bad_resign"])
    st = run(ref)
    assert st.termination == "forfeit" and st.result == "1-0" and st.winner == WHITE
    assert st.black.illegal_total == 3 and st.black.illegal_streak == 3
    assert len(st.plies) == 1
    assert len(st.final_turn.attempts) == 3
    assert "3 illegal attempts in a row" in st.reason


def test_streak_resets_after_legal_move(make_game):
    ref, _ = make_game(black=["illegal", "illegal", "random", "garbage", "illegal", "random", "resign"])
    st = run(ref)
    assert st.termination == "resignation"
    assert st.black.illegal_total == 4
    assert st.black.illegal_streak == 0
    # per-ply snapshot of counters for the replay viewer
    assert st.plies[1].illegal_after[BLACK] == {"total": 2, "streak": 0}


def test_retry_prompt_has_reason_and_same_session(make_game):
    ref, agents = make_game(black=["illegal", "random", "resign"])
    run(ref)
    b = agents[BLACK]
    assert b.session_ids[0] is None                    # first call starts a session
    assert b.session_ids[1] is not None                # retry resumes it
    assert b.session_ids[1] == b.session_ids[2]
    assert "REJECTED" in b.prompts[1] and "no king can reach" in b.prompts[1]
    assert "REJECTED" not in b.prompts[2]


def test_rules_intro_only_on_first_prompt(make_game):
    ref, agents = make_game(move_cap=6)
    run(ref)
    w = agents[WHITE]
    assert "RESPONSE FORMAT" in w.prompts[0]
    assert all("RESPONSE FORMAT" not in p for p in w.prompts[1:])
    assert all("Reminder: end your reply" in p for p in w.prompts)


def test_retry_time_counts_toward_move(make_game):
    ref, _ = make_game(black=["illegal", "random"], move_cap=2)
    st = run(ref)
    rec = st.plies[1]
    assert len(rec.attempts) == 2
    assert abs(rec.think_s - sum(a.think_s for a in rec.attempts)) < 0.01


# --------------------------------------------------------------------------- resignation

def test_valid_resignation(make_game):
    ref, _ = make_game(white=["resign"])
    st = run(ref)
    assert st.termination == "resignation" and st.result == "0-1"
    r = st.resignation
    assert r["color"] == WHITE and len(r["line_san"]) == 2
    b = chess.Board(r["fen"])
    for san in r["line_san"]:
        b.push_san(san)


def test_bad_and_short_resignations_count_as_illegal(make_game):
    ref, agents = make_game(white=["bad_resign", "short_resign", "random"], move_cap=1)
    st = run(ref)
    assert st.termination == "move_cap"
    assert st.white.illegal_total == 2
    reasons = [a.reason for a in st.plies[0].attempts]
    assert "resignation rejected" in reasons[0]
    assert "at least 2" in reasons[1]
    assert "REJECTED" in agents[WHITE].prompts[1]


# --------------------------------------------------------------------------- sessions

def test_session_resumed_across_turns(make_game):
    ref, agents = make_game(move_cap=8)
    st = run(ref)
    ids = agents[WHITE].session_ids
    assert ids[0] is None and len(set(ids[1:])) == 1
    assert st.white.session_history == [ids[1]]


def test_resume_failure_starts_new_session_with_recovery_prompt(make_game):
    ref, agents = make_game(white=["random", "fail_resume", "random"], move_cap=6)
    st = run(ref)
    w = agents[WHITE]
    assert w.session_ids[1] is not None and w.session_ids[2] is None
    assert "could not be restored" in w.prompts[2]
    assert "RESPONSE FORMAT" in w.prompts[2]
    assert "Move history: 1. " in w.prompts[2]
    assert any(e["type"] == "session_resume_failure" for e in st.events)
    assert len(st.white.session_history) == 2
    assert st.white.illegal_total == 0


# --------------------------------------------------------------------------- timeout / pause / abort

def test_timeout_pauses_then_retry_continues(make_game, settings):
    settings.safety_timeout_s = 0.3
    holder = {}

    async def on_event(t, d):
        if t == "unresponsive":
            holder["seen"] = d["reason"]
            holder["ref"].retry()

    ref, _ = make_game(white=["timeout", "random"], on_event=on_event, move_cap=2)
    holder["ref"] = ref
    st = run(ref)
    assert "safety timeout" in holder["seen"]
    assert st.termination == "move_cap"
    assert st.plies[0].think_s >= 0.3
    assert any(e["type"] == "agent_unresponsive" for e in st.events)


def test_abort_while_unresponsive(make_game, settings):
    settings.safety_timeout_s = 0.2
    holder = {}

    async def on_event(t, d):
        if t == "unresponsive":
            holder["ref"].abort()

    ref, _ = make_game(white=["timeout"], on_event=on_event)
    holder["ref"] = ref
    st = run(ref)
    assert st.termination == "abort" and st.result == "*"


def test_cli_failure_goes_unresponsive_not_illegal(make_game):
    holder = {}

    async def on_event(t, d):
        if t == "unresponsive":
            holder["ref"].retry()

    ref, _ = make_game(white=["fail", "random"], on_event=on_event, move_cap=1)
    holder["ref"] = ref
    st = run(ref)
    assert st.white.illegal_total == 0 and len(st.plies) == 1


def test_pause_and_resume(make_game):
    seen = []
    holder = {}

    async def on_event(t, d):
        seen.append(t)
        if t == "move" and d["ply"] == 1:
            holder["ref"].request_pause()
        if t == "paused":
            holder["ref"].resume()

    ref, _ = make_game(on_event=on_event, move_cap=3)
    holder["ref"] = ref
    st = run(ref)
    assert "paused" in seen and "resumed" in seen
    assert seen.index("paused") > seen.index("move")
    assert len(st.plies) == 3


def test_tool_use_is_recorded_not_fatal(make_game):
    ref, _ = make_game(white=["tool"], move_cap=2)
    st = run(ref)
    assert st.white.tool_violations == 1
    assert st.plies[0].tool_violations[0]["events"][0]["tool"] == "Read"
    assert any(e["type"] == "tool_use_violation" for e in st.events)
    assert len(st.plies) == 2


# --------------------------------------------------------------------------- end conditions

def test_checkmate_fools_mate(make_game):
    ref, _ = make_game(white=["move:f3", "move:g4"], black=["move:e5", "move:Qh4#"])
    st = run(ref)
    assert st.termination == "checkmate" and st.result == "0-1" and st.winner == BLACK


def test_stalemate(make_game):
    ref, _ = make_game(white=["move:Qf7"], start_fen="7k/8/6K1/5Q2/8/8/8/8 w - - 0 1")
    st = run(ref)
    assert st.termination == "stalemate" and st.result == "1/2-1/2"


def test_insufficient_material(make_game):
    ref, _ = make_game(white=["move:Kxd2"], start_fen="8/8/8/4k3/8/8/3p4/2K1B3 w - - 0 1")
    st = run(ref)
    assert st.termination == "insufficient_material"


def test_threefold_repetition(make_game):
    ref, _ = make_game(white=["move:Nf3", "move:Ng1"] * 2, black=["move:Nf6", "move:Ng8"] * 2)
    st = run(ref)
    assert st.termination == "threefold_repetition" and len(st.plies) == 8


def test_threefold_not_triggered_by_mere_possibility(make_game):
    # After 7 plies the side to move *could* repeat a third time; the game must go on.
    ref, _ = make_game(white=["move:Nf3", "move:Ng1", "move:Nf3", "move:Ng1"],
                       black=["move:Nf6", "move:Ng8", "move:Nf6", "move:Nc6"], move_cap=20)
    st = run(ref)
    assert len(st.plies) > 8


def test_fifty_move_rule(make_game):
    ref, _ = make_game(white=["move:Ra3"], start_fen="7k/8/8/8/8/8/R7/K7 w - - 99 80")
    st = run(ref)
    assert st.termination == "fifty_moves"


def test_seventyfive_move_rule():
    # In play the 50-move draw always triggers first; the 75-move rule is still
    # checked (and reported as such) for positions that are already past it.
    b = chess.Board("7k/8/8/8/8/8/R7/K7 b - - 150 120")
    assert check_game_end(b, 0, 200)[1] == "seventyfive_moves"


def test_fivefold_check():
    b = chess.Board()
    for _ in range(4):
        for san in ("Nf3", "Nf6", "Ng1", "Ng8"):
            b.push_san(san)
    assert check_game_end(b, 16, 200)[1] == "fivefold_repetition"


def test_move_cap(make_game):
    ref, _ = make_game(move_cap=10)
    st = run(ref)
    assert len(st.plies) == 10 and st.termination == "move_cap" and st.result == "1/2-1/2"


def test_game_in_progress_is_not_over():
    assert check_game_end(chess.Board(), 0, 200) is None


def test_temp_workdirs_removed(make_game, settings, tmp_path):
    ref, agents = make_game(move_cap=2)
    run(ref)
    root = tmp_path / "agents"
    assert not any(root.rglob("*")) if root.exists() else True
    w, b = agents[WHITE].workdir, agents[BLACK].workdir
    assert w != b and not w.exists() and not b.exists()
    # folder names must not reveal the game id (which contains both agents' names)
    assert ref.state.id not in str(w) and w.name.startswith("game-")
