# AI Chess Arena — setup and reference

Two AI models play chess against each other through their own subscription CLIs
(Claude Code and Codex). A Python referee runs the game. A local web page shows it live.

## 1. Install

Requirements: Windows 10/11, Python 3.11+ (tested on 3.14), Node.js (for the CLIs), and both
CLIs installed and logged in with your subscriptions:

```
npm install -g @anthropic-ai/claude-code      # then run `claude` once and log in
npm install -g @openai/codex                  # then run `codex login`
```

No API keys are used. The arena removes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`OPENAI_API_KEY` and `CODEX_API_KEY` from the CLIs' environment, so a stray key can't switch
billing away from your subscription.

Then, from this folder:

```
run.bat
```

The first run creates `.venv` and installs `requirements.txt`. It then starts the server and
opens http://localhost:8000. To do it by hand:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m arena                 # web UI (add --no-browser / --port N)
```

Other commands:

| Command | What it does |
|---|---|
| `python -m arena play --white A --black B` | one game in the terminal (`--legal-moves`, `--move-cap N`, `--fen ...`, `--no-engine`, `--no-save`) |
| `python -m arena check-clis` | checks that the installed CLIs still accept every flag the adapters use (no model calls) |
| `python -m arena rebuild-records` | rebuilds `arena.db` from `games/*.json` |
| `.venv\Scripts\python -m pytest` | runs the test suite (no CLI calls, no subscription use) |

## 2. Stockfish (optional: eval bar, eval chart and evals in the logs)

Stockfish is used **only** for the UI and the logs. Agents never see its output.

**Current install:** Stockfish 19, the `x86-64-universal` Windows build (it picks the best
instruction set for the CPU at runtime; on this PC it runs the `x86-64-avxvnni` code). The arena uses `tools\stockfish\stockfish.exe`, which
is a copy of `tools\stockfish\stockfish\stockfish-windows-x86-64-universal.exe`. The rest of
`tools\stockfish\stockfish\` (source, docs, scripts) isn't used. You can delete it, apart
from the copied `stockfish.exe`.

To install or upgrade:

1. Download the Windows build from https://stockfishchess.org/download/ (or the GitHub
   releases page, https://github.com/official-stockfish/Stockfish/releases).
   * The **x86-64-universal** build works on any 64-bit CPU. Otherwise pick **x86-64-avx2** for most CPUs from 2015 on.
   * If that build crashes or the eval bar says "unavailable", use **x86-64-sse41-popcnt** instead.
2. Unzip it, and copy the `.exe` to `tools\stockfish\stockfish.exe` in this folder.
   Alternatively, point `stockfish_path` in `settings.yaml` at wherever you put it.
3. Restart the server. The Evaluation panel header shows the engine name when it's working.

Engine settings (`settings.yaml`): **5 threads, 384 MB hash, 1.6 s per position, MultiPV 3**
(the top 3 moves, which the move classifier needs). You can set `engine_depth` instead of a
time. If Stockfish is missing or crashes, games still run. The eval bar shows "eval
unavailable", and the engine is restarted up to 3 times per server run.

**How these were chosen** (`python -m arena bench-engine`, 2026-10-15, Core Ultra 7 155H):
* **Positions:** 84 consecutive middlegame positions from the saved games, plus 10 Win-At-Chess
  tactics.
* **Reference:** every position searched for 6 s with 12 threads, 1024 MB and MultiPV 3.
* **Measured** for each setting:
  * depth and speed;
  * how often the top move matches the reference;
  * win chance lost by the chosen move (judged by the reference);
  * eval error;
  * WAC tactics solved;
  * most importantly, how often each played move gets the **same classification label** as it
    does with the reference analysis.
* **Bar:** at least 90% same label and 85% top-move agreement, at the shortest time. Ties go to
  fewer threads, then the smallest hash with no measurable gain.

| threads | hash MB | time s | depth | knps | top-1 % | move loss % | eval err % | WAC | same label % | within 1 label % |
|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 256 | 0.5 | 15.2 | 1759 | 80 | 0.14 | 1.01 | 9/10 | 81 | 96 |
| 4 | 256 | 0.8 | 16.5 | 1805 | 76 | 0.12 | 0.72 | 9/10 | 79 | 96 |
| 4 | 256 | 1.2 | 17.8 | 1951 | 82 | 0.14 | 0.71 | 9/10 | 81 | 97 |
| 4 | 256 | 1.6 | 19.0 | 1970 | 81 | 0.09 | 0.63 | 9/10 | 86 | 97 |
| 5 | 256 | 0.5 | 15.3 | 2055 | 80 | 0.13 | 0.96 | 9/10 | 78 | 95 |
| 5 | 256 | 0.8 | 16.4 | 2037 | 77 | 0.17 | 0.90 | 9/10 | 82 | 96 |
| 5 | 256 | 1.2 | 17.3 | 2009 | 74 | 0.21 | 0.66 | 9/10 | 83 | 96 |
| **5** | 256 | **1.6** | 18.4 | 1927 | 85 | 0.08 | 0.59 | 9/10 | **90** | 99 |
| 6 | 256 | 0.5 | 14.8 | 2245 | 79 | 0.15 | 0.96 | 10/10 | 78 | 95 |
| 6 | 256 | 0.8 | 16.2 | 2173 | 70 | 0.24 | 0.74 | 9/10 | 83 | 96 |
| 6 | 256 | 1.2 | 17.4 | 2229 | 80 | 0.14 | 0.62 | 9/10 | 83 | 97 |
| 6 | 256 | 1.6 | 18.2 | 2236 | 76 | 0.14 | 0.61 | 9/10 | 88 | 97 |

Hash at 5 threads / 1.6 s (2 runs each, averaged):

| hash MB | top-1 % | same label % | within 1 label % |
|---|---|---|---|
| 256 | 79 | 85 | 99 |
| **384** | 77 | **90** | 99 |
| 512 | 79 | 89 | 99 |

What this showed:
* On this CPU (6 performance cores plus efficiency cores), Stockfish barely speeds up past
  2 threads: about 1.0 M nodes/s on 1 thread and 1.8–2.2 M on 4–6. **Time per position matters
  much more than the thread count.**
* Below 1.6 s, no setting reached 90% label agreement.
* Results move by about ±5 points between runs, because multithreaded search isn't
  deterministic. That's why the hash comparison was run twice.
* 384 MB and 512 MB were equal, and both slightly better than 256 MB.
* Labels were almost never off by more than one step (99%).
* With these settings a move is classified about 1.7 s after it's played.
* Re-run `python -m arena bench-engine` (about 45 min) on a different PC. `--quick` runs a
  2-minute smoke test.

## 3. Choosing and adding agents (`agents.yaml`)

Each entry is one agent setup. Pick any two on the setup page, or the same setup twice. The
setup page's model picker has a search box: type any words from the name, model or effort
(`sonnet high`, `luna`, `player`) and every word must match. Arrow keys and Enter work too.

**Families** give one agent per effort level, so one line covers a model:

```yaml
families:
  - {id_prefix: sonnet-5, name: "Sonnet 5", group: Claude, cli: claude_code, model: claude-sonnet-5,
     efforts: [low, medium, high, xhigh, max]}
```

This makes `sonnet-5-low` / "Sonnet 5 (low)" … `sonnet-5-max`. `group` is the heading in the
picker. Shipped families:
* **Claude:** Fable 5.1, Opus 5.5, Sonnet 5, Haiku 4.5.
* **Codex:** GPT-6.1 Sol, GPT-6 Sol, GPT-6 Astra (ids `astra-*`), GPT-6 Luna, GPT-5.6 Sol,
  GPT-5.6 Terra, GPT-5.6 Luna.

Every model id was checked with a real call on 2026-10-15. Fable 5.1 is recognized, but it
answered "out of usage credits" on this account. Haiku 4.5 accepts all five effort levels.
The Opus and Astra ids are the same as before families existed, so the record keeps their
history.

**Single entries** go under `agents:`:

```yaml
agents:
  - id: opus-5.5-high              # unique; used in file names and the record table
    display_name: "Opus 5.5 (high)"
    cli: claude_code               # claude_code | codex | mock | human
    model: claude-opus-5-5         # passed verbatim: claude --model / codex -m
    reasoning_effort: high         # passed verbatim: claude --effort / codex -c model_reasoning_effort=
```

* **Claude Code** efforts: `low | medium | high | xhigh | max`. The CLI silently ignores anything else, so the adapter rejects other values at game start. Model aliases
  (`opus`, `sonnet`, `fable`) or full names (`claude-opus-5-5`).
* **Codex** model slugs are listed in `~/.codex/models_cache.json`, e.g. `gpt-6-astra`,
  `gpt-6-sol`, `gpt-6.1-sol`. Efforts: `low | medium | high | xhigh | max` (the API rejects others, e.g. `ultra` on gpt-6-astra).
  New models may need a newer Codex CLI. 0.147 refused `gpt-6-astra` with "requires a newer
  version of Codex"; 0.162.1 works.
* **human** (`id: human`, "Player (you)"): you play on the board in the browser. See 4d.
* **mock** agents need no CLI. Optional fields: `delay_s`, `seed`, and `script` (a list of
  actions: `random`, `move:<SAN>`, `garbage`, `illegal`, `bad_resign`, `short_resign`,
  `resign`, `timeout`, `fail_resume`, `fail`, `tool`). See `arena/agents/mock.py`.

`agents.yaml` is re-read whenever you open the setup page or start a game. You don't need to
restart the server.

## 4. How a game works

* **The referee drives every turn.** It builds the prompt (`arena/prompts.py`, the only
  module that writes agent-facing text), times the CLI call, parses the reply, validates
  the move with python-chess, and handles retries, forfeits and resignations.
* **What an agent sees:** its color, the move number, the FEN, an ASCII board, the full SAN
  history, the opponent's last think time, its own last PLAN, the legal moves (only when that
  option is on), and, on a retry, the rejected move and the exact reason. Nothing else.
  `tests/test_isolation.py` enforces this. It checks that `prompts.py` imports neither the
  engine nor the game state, that `PromptContext` has exactly the allowed fields, and that
  sentinel eval, clock, material and counter values never appear in any prompt.
* **Memory:** each agent keeps one CLI session for the whole game (Claude `--session-id`
  then `--resume`; Codex `exec` then `exec resume <thread_id>`). Retries go into the same
  session. Every reply ends with a PLAN, and the referee shows it back next turn. If a resume
  fails, a new session gets the rules, the full history and the last PLAN, and the referee
  log records a `session_resume_failure`.
* **Illegal moves:** unreadable replies and rejected resignations count as illegal.
  3 in a row is a forfeit (`max_illegal_streak`). The counters reset after any legal move.
* **Resignation:** the agent's LINE is played out on a copy of the board. It must have at
  least 2 legal plies, otherwise the resignation is rejected and counts as illegal.
* **Game end:** checkmate, stalemate, insufficient material, fivefold, the 75-move rule,
  threefold (as soon as the position actually occurs a 3rd time), the 50-move rule, the move
  cap (default 200 plies, a draw), forfeit, resignation, or abort. There are no draw offers.
* **Timing is display-only.** Think time includes the CLI's start-up time (a few seconds per
  call). Retries count toward the move's time.
* **Safety timeout** (`safety_timeout_s`, default 20 min). It isn't a penalty: the game pauses
  with *Agent unresponsive: Retry / Abort*. CLI errors (rate limits, crashes, auth) do the same
  and never count as illegal moves.
* **Pause** takes effect after the agent that's currently thinking replies.

## 4b. Openings and token tracking

**Openings** (setup page, or `play --opening <id>`):
* **Free** (default): White's agent picks its own first move, and nothing is pre-played.
* **White's first move** (1.e4, 1.d4, 1.c4, 1.Nf3, 1.f4, 1.b3, 1.g3): only White's first
  move is fixed. Black and everything after are the agents' choice.
* **Named openings** (Ruy Lopez, Italian, Sicilian Najdorf, French, Caro-Kann, QGD,
  King's Indian, Nimzo-Indian, …): the referee plays the book line for both sides.
* **Random named opening**: one is picked when the game starts.
* **Custom moves**: any legal SAN line, e.g. `1. d4 Nf6 2. c4 e6`
  (`play --opening custom --opening-moves "..."`).

Book moves are played instantly, with no agent call, no think time and no tokens. They're
shown in italics marked "book", and they still get engine evals. Each agent's first prompt
names the opening and its moves. Book moves count toward the ply cap but not toward an agent's
move count or averages. The opening is saved in the logs (`opening`, plus PGN `Opening`/`ECO`
headers), in the replay list and in the record index. An opening can't be combined with a
custom start FEN. The book lines live in `arena/openings.py`, and a test checks they're all legal.

**Tokens:** each CLI call's usage is recorded per attempt, per move and per side.
* Input includes cached input. Claude's figure adds cache reads and writes back in; Codex
  reports it that way already.
* Output includes reasoning ("thinking") tokens. The breakdown is in the tooltip.
* Shown on the player cards (running totals, also at each point in a replay), in the agent
  console, and on the record page (average per move and totals).
* Claude also reports an API list-price cost per call. It's kept in the JSON log (`meta.cost_usd`)
  for reference, but your subscription isn't billed per call.
* Codex quirk: `codex exec --json` reports usage as a **running total for the whole thread**,
  not per call. The adapter subtracts the previous total for that thread to get per-call usage.
  Games saved before this fix overstated Codex's tokens by roughly 10×.
  `python -m arena fix-tokens` recomputes all saved logs from the raw CLI usage stored in them
  (keeping a `.json.bak` copy of each changed file) and rebuilds `arena.db`.
* Verified against the CLIs' own logs. For Codex, the sum of `last_token_usage` in
  `~/.codex/sessions/.../rollout-*.jsonl`. For Claude, per-response usage in
  `~/.claude/projects/<folder>/<session>.jsonl`. Both matched to the token.
* Each Codex call starts with about 9.9k tokens of fixed overhead: its own instructions, its
  built-in tool definitions, and the environment and AGENTS.md context. Claude's first call is
  about 1.5k, because the arena replaces Claude Code's system prompt and gives it no tools. Most
  of that overhead is cached input.

Token counts, like engine evals and clocks, are never shown to the agents. The isolation test
plants token sentinels to check this.

## 4c. Live agent console and "think aloud"

**Agent console.** The live page (and each replay) has an *Agent consoles* panel. Choose
**Browser window** or **Terminal window** (the choice is remembered), then click **Watch White**
or **Watch Black**.
* **Browser window:** `/console?side=white` opens as a pop-out. It has switches for
  auto-scroll, prompts and raw JSON.
* **Terminal window:** the server opens a Windows console running
  `python -m arena watch white`. You can also run that yourself; add `--raw` for the CLI's raw
  JSON lines, or `--game <id>` for a past game.
* **What you see, as it happens:** the exact prompt sent to the CLI, the session and tool status,
  "thinking…", the reply (Claude streams it word by word; Codex shows it when complete), Codex
  reasoning summaries, any tool-call attempts, and the referee's verdict (accepted, or rejected
  with the reason) with think time and tokens.
* A live window follows whatever game is running and switches over when a new game starts.
* Every event is also saved to `games/<id>.console.jsonl`, so you can replay a past game's
  console. Games from before this feature have no console log.
* Read-only: the console only observes. Nothing in it is ever sent to an agent, and a test
  checks this.

**What each CLI exposes** (checked against the real CLIs):
* **Claude Code** never shows its thinking text in headless mode. The thinking block arrives
  redacted, and the `showThinkingSummaries` setting has no effect in `-p` mode. You see when it
  thinks and how many tokens it used, plus everything it writes in its reply.
* **Codex** keeps raw reasoning encrypted. The arena turns on `model_reasoning_summary="detailed"`
  (display only), so Codex adds a short summary when it reasons, usually at medium effort and
  above. At low effort there is often none.

**Think aloud** (setup-page checkbox, or `play --think-aloud`). Both agents are asked to end
each reply with `ANALYSIS: <candidate moves, threats, why this move>` before MOVE and PLAN. It's
the only way to see Claude's actual reasoning about a move. A missing ANALYSIS is *not* counted
as illegal. It changes the game conditions (more output tokens, possibly different play), so,
like "legal moves sent", it's saved with each game (`think_aloud`, PGN `ThinkAloud` header).
The replay list and record page can filter on it. The analysis appears in the console and in
the JSON log.

**Stored per attempt** (JSON log): the exact prompt sent (`prompt`), the `analysis`, and the
Codex `reasoning` summary.

## 4d. Playing yourself ("Player (you)")

Pick **Player (you)** for either color (or both) on the setup page. On your turn:
* Click a piece, or start dragging it. Its legal destinations show as dots, and captures as
  rings, as on chess.com. Click or drop on one to move.
* Promotions open a picker with queen, knight, rook and bishop on the promotion file. Esc or
  clicking elsewhere cancels.
* If you play Black, the board, eval bar and player cards are flipped so you play from the
  bottom.
* The eval bar and chart stay visible, so you can see how you're doing (it's a training
  setup, not a fair game).
* **Resign** (two clicks) works on your turn or while the AI is thinking. Pause and Abort work
  as in AI games.
* There's no safety timeout for a person, and your think time is recorded like an agent's.
* While it's your turn you can step back through the game (← → Home End, or click a move).
  The board becomes view-only until you return to the current position.

The server side is `arena/agents/human.py` and two endpoints: `POST /api/game/move {"uci": "e7e8q"}`
and `POST /api/game/resign`. `python -m arena play` refuses human players because the terminal
has no board.

## 4e. Move classification (chess.com style)

Every move an agent or player makes (not book moves) is rated as soon as Stockfish has analysed
the position after it, usually 1–2 s after the move:

| Label | Symbol | Rule (`arena/classify.py`) |
|---|---|---|
| Brilliant | !! (light blue) | A sound sacrifice: the engine's top move (or its #2 within 2% win chance) that leaves material en prise, net of what it took. The mover wasn't already ≥90% winning and is ≥50% after it. |
| Great | ! (teal) | The engine's top move, not obvious (no recapture, free-material grab, promotion or escape from attack), and tactical: the only good move (≥10% better than #2), a fork, or a trap (a piece left en prise that costs the opponent ≥8% to take). |
| Best | ★ (dark green) | The engine's #1 move, or its #2 when that loses under 2% win chance |
| Excellent | 👍 | loses < 2% win chance |
| Good | ✓ | < 5% |
| Inaccuracy | ?! (yellow) | < 10% |
| Mistake | ✕ (red) | < 20% |
| Blunder | ?? (dark red) | ≥ 20% |

* Win chance uses the same curve as lichess, from the mover's side. The loss is compared with
  Stockfish's MultiPV lines for the position before the move (`engine_multipv`, 3).
* **On the board:** when a move is rated, its square flashes the label's colour with a large
  symbol, which fades into a small badge on the square's top-right corner. The badge stays
  until the next move. Replays show the same: stepping forward (→ or Play) animates, and
  jumping shows the badge directly.
* **Moves panel:** each move's badge follows its think time. The tooltip has the label, the win
  chance lost and, for weaker moves, the engine's best move.
* **Logs:** `classification` and the engine `lines` are saved per ply in the JSON. The PGN gets
  NAGs (!! ! ?! ? ??) and the label in each move's comment.
* **Older games:** `python -m arena analyze --all` re-analyses every saved game with the current
  engine settings and adds classifications. Use `analyze <game-id>` for one game, or
  `--missing` to skip games that already have them. It takes about 1 s per ply.

## 5. Isolation: how each CLI is locked down

Each agent runs with its working folder set to a new, empty, randomly named folder under
`%TEMP%\ai-chess-arena`. It gets one folder per agent per game, and the folder is deleted
when the game ends.

**Claude Code** (`arena/agents/claude_code.py`):
`-p --output-format stream-json --verbose --model … --effort … --session-id/--resume …
--tools "" --strict-mcp-config --safe-mode --disable-slash-commands --no-chrome
--permission-mode dontAsk --permission-prompts none --system-prompt "<short chess-player text>"`.
Verified: the `init` event reports `tools: []`, `mcp_servers: []` and `apiKeySource: "none"`.
Asked which tools it has, the model answers "NONE".

**Codex** (`arena/agents/codex.py`):
`exec --json --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only -C <tmp>
-m … -c model_reasoning_effort=… -c approval_policy="never" -c sandbox_mode="read-only"
-c web_search="disabled" -c skills.include_instructions=false -c skills.bundled.enabled=false
-c project_doc_max_bytes=0 -c developer_instructions="<same short text>"`. The command also
passes `--disable` for every tool feature the installed version has (shell, unified exec,
browser, computer use, apps, plugins, image generation, multi-agent, code mode, sleep, …).
`--ignore-user-config` matters here: your `~/.codex/config.toml` adds a playwright MCP server
and `reasoning_effort = "ultra"`.

**Tool-use detection:**
* Claude: any `tool_use` / `server_tool_use` block in the stream.
* Codex: tool items in `exec --json`, **plus** the thread's rollout log in
  `~/.codex/sessions`. The JSON stream doesn't show code-mode `exec` calls, but the rollout
  log does.

Either way, the referee records a `tool_use_violation` in the log, and the UI shows a ⚠
badge. The game continues.

**Known limits (Codex):**
* Codex still offers the model its built-in code-mode tools (`exec`, `wait`,
  `request_user_input`). With `code_mode_host` disabled, every `exec` call **fails closed**
  ("code-mode host is disabled"); this was tested. Any attempt is still logged as a violation.
  Each game also logs one notice: "Code Mode is unavailable because code-mode host is disabled".
* Codex always loads your global `~/.codex/AGENTS.md` (currently your Context7 note). No flag
  or config key turns this off, and `--ignore-user-config` doesn't cover it. It contains no
  game information, and the agent has no MCP access to act on it. If you want Codex agents to
  see nothing of it, you can temporarily rename that file while a match runs.
* Codex also tells the model its working folder (a random temp path), today's date and the
  shell. These are harmless and don't identify the opponent.

## 6. Logs, replay, record

* `games/<YYYYmmdd-HHMMSS>_<white>_vs_<black>.json` holds the full log:
  * agent configs, settings and session IDs;
  * per ply: SAN/UCI, the FEN after the move, think time, the clocks and illegal counters at
    that point, each attempt (raw output, parsed move, rejection reason, PLAN, session, tool
    events, CLI usage and cost), the engine eval and top lines, and the move classification;
  * the result, the reason, the resignation explanation and line, and the referee events.
* `games/….pgn` has standard headers plus `WhiteAgent`, `BlackAgent`, `LegalMovesSent` and
  `Termination`, and a comment per move with the think time, rejected attempts, eval and
  classification (plus NAGs such as `!!` and `??`).
* `arena.db` (SQLite) indexes finished games for the replay list and the record page.
  It can be rebuilt from the JSON files at any time.
* **Record rules:**
  * Aborted or errored games (`*`) are listed but not counted as W/L/D.
  * A forfeit is a loss.
  * The page defaults to games *without* legal moves sent. Switch the filter to see
    "assisted" games or all games.

## 7. When a CLI changes

Run `python -m arena check-clis` after updating either CLI. It reads `--help` and reports
any flag the adapters use that has disappeared. Then:

1. Run `claude --help`, `codex exec --help` and `codex exec resume --help`, and update
   `build_argv` / `common_flags` in `arena/agents/claude_code.py` or `codex.py`. Also update
   the `REQUIRED_*` lists there.
2. For Codex, run `codex features list`. Add any new feature that gives the model a tool to
   `DISABLED_FEATURES` in `codex.py`. Only names the installed version knows are passed;
   an unknown name makes Codex exit with "Unknown feature flag".
3. Run `codex debug prompt-input -c … --disable …` with the adapter's flags to see exactly
   what context Codex adds.
4. Smoke-test without a full game: `python -m arena play --white <agent> --black mock-random
   --move-cap 4`. Then open the JSON log and check that the session ID stays the same, that
   `tool_events` is empty, and that the raw outputs end with MOVE/PLAN.
5. Signs that something changed:
   * every move is rejected with "could not parse a move" (the output format changed);
   * `session_resume_failure` events every turn (the resume flag or session store changed);
   * the game goes "unresponsive" with a CLI error message;
   * an `agent_warning` saying tools or MCP servers were available.

## 8. Defaults chosen (not in the original spec)

* Only one live game runs at a time. The live page shows the latest game until a new one starts.
* Board: chessground 9.2.1 (GPL-3.0). Charts: Chart.js 4.5.1. Both are saved in
  `web/vendor`, so the UI works offline.
* The server listens on 127.0.0.1:8000 only.
* Both agents get the same short system text, saying they are a chess player with no tools
  (`prompts.SYSTEM_PROMPT`). For Claude it replaces the default coding-agent system prompt.
  The rules and response format arrive in the first user message, and every later turn
  repeats a one-line format reminder.
* The prompt is sent on stdin, and npm `.cmd` shims are resolved to the real `claude.exe` /
  `node codex.js`, which is then run with `shell=False`.
* Agent work folders get random names, so the folder name can't reveal the opponent.
* An agent's PLAN is updated from every reply that contains one, including rejected attempts.
* Repeated identical CLI notices are logged once per game per side.
* The setup page can start a game from a custom FEN (under "Advanced"). This is useful for
  testing draws and endings.
* Config files can be swapped with the environment variables `ARENA_SETTINGS` and `ARENA_AGENTS`.
