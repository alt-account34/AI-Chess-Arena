"""Anti-cheating: no prompt may contain engine, clock, material or illegal-move
data, apart from the opponent's last think time."""
import ast
import dataclasses
import re
import subprocess
import sys
from pathlib import Path

from arena import prompts
from arena.state import BLACK, WHITE

from .conftest import run

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_MODULES = {"engine", "state", "storage", "server", "referee", "chess.engine"}


def test_prompts_module_imports_nothing_forbidden():
    tree = ast.parse((ROOT / "arena" / "prompts.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.add(mod)
            imported |= {f"{mod}.{a.name}" if mod else a.name for a in node.names}
    for name in imported:
        parts = set(name.split("."))
        assert not (parts & FORBIDDEN_MODULES), f"prompts.py imports {name}"
    assert imported <= {"__future__", "__future__.annotations", "dataclasses",
                        "dataclasses.dataclass", "chess"}, imported


def test_importing_prompts_does_not_load_engine():
    code = "import sys, arena.prompts; print('arena.engine' in sys.modules, 'chess.engine' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"]


def test_prompt_context_has_only_allowed_fields():
    # Adding a field to PromptContext must be a deliberate decision: update this list.
    names = {f.name for f in dataclasses.fields(prompts.PromptContext)}
    assert names == {"color", "move_number", "fen", "ascii_board", "history_san",
                     "opponent_last_think_s", "last_plan", "legal_moves_san", "retry"}


class SentinelAnalyzer:
    available = True

    def analyse(self, fen):
        return {"cp": 31337, "depth": 4242}


def test_no_engine_clock_material_or_counter_data_in_prompts(make_game):
    """Plays a game with a fake engine and sentinel values planted in every piece of
    UI-only state, then checks every prompt sent to either agent."""
    holder = {}

    async def on_event(t, d):
        st = holder["ref"].state
        for side in (st.white, st.black):
            side.think_total = 98765.25       # total clock
            side.illegal_total = 5151         # illegal counter
            side.tool_violations = 6161
            side.tokens = {"input": 7272727, "cached": 828282, "output": 939393, "reasoning": 515151, "calls": 9}
        st.plies and setattr(st.plies[-1], "eval", {"cp": 31337})

    ref, agents = make_game(white=["random", "illegal", "random"], black=["garbage", "random"],
                            on_event=on_event, move_cap=12, analyzer=SentinelAnalyzer())
    holder["ref"] = ref
    st = run(ref)
    assert st.start_eval == {"cp": 31337, "depth": 4242}   # engine really ran

    all_prompts = agents[WHITE].prompts + agents[BLACK].prompts
    assert len(all_prompts) >= 12
    for p in all_prompts:
        for sentinel in ("31337", "4242", "98765", "5151", "6161", "313.37",
                         "7272727", "828282", "939393", "515151"):
            assert sentinel not in p, (sentinel, p)
        low = p.lower()
        for word in ("centipawn", "stockfish", "evaluation", "total think", "clock:"):
            assert word not in low, (word, p)
        assert not re.search(r"material (count|advantage|balance|score)|material:|\+\d+ material", low), p
        # The only timing number allowed is the opponent's last think time.
        secs = re.findall(r"(\d+(?:\.\d+)?) seconds", p)
        assert len(secs) <= 1
        if secs:
            assert "Your opponent thought for" in p


def test_opponent_think_time_matches_last_move(make_game):
    ref, agents = make_game(move_cap=3)
    st = run(ref)
    black_first = agents[BLACK].prompts[0]
    m = re.search(r"Your opponent thought for (\d+\.\d) seconds", black_first)
    assert m and abs(float(m.group(1)) - st.plies[0].think_s) <= 0.051
    assert "Your opponent has not moved yet." in agents[WHITE].prompts[0]


def test_legal_moves_only_when_enabled(make_game):
    ref, agents = make_game(move_cap=2)
    run(ref)
    assert "Legal moves:" not in agents[WHITE].prompts[0]
    ref, agents = make_game(move_cap=2, send_legal_moves=True)
    run(ref)
    p = agents[WHITE].prompts[0]
    assert "Legal moves:" in p and "Nf3" in p and len(re.search(r"Legal moves: (.*)", p).group(1).split()) == 20


def test_prompt_contents(make_game):
    ref, agents = make_game(move_cap=3)
    run(ref)
    p = agents[BLACK].prompts[0]
    assert "You are Black" in p
    assert "FEN: " in p
    assert "a b c d e f g h" in p and "  8 r n b q k b n r 8" in p
    assert "Move history: 1. " in p
    assert "no engines" in p.lower() or "No engines" in p
    assert "draw offers" in agents[BLACK].prompts[0]
