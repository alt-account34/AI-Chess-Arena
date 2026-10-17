# AI Chess Arena

You can choose to play two AI models play chess against each other or play against one yourself, fully run in a
browser. Each AI plays through the CLI (Claude Code or Codex) using your subscription (No API costs). The browser includes a
live board, an evaluation bar (stockfish), move-by-move ratings (like chess.com's "Brilliant!" / "Blunder"
labels), a model comparison page, and full game logs you can replay later.

No API keys, no billing per move, It uses the CLI tools you already log into with your
Claude or ChatGPT/Codex subscription.

<img width="959" height="599" alt="image" src="https://github.com/user-attachments/assets/5456c8e2-8f3b-4952-b1e5-9891e8d728cd" />

On each model's turn, the entire board, the time the other model spent thinking, and move history is all sent to the AI model
whose turn is active. The AI is unable to see stockfish's move ranking or win prediction, these are visual elements only for user 
experience. It is recommended to disable tool calls and or MCP server to prevent potential cheating. The program automatically
handles illegal moves, ties, draws by repetition, and checkmate detection (most of the time an AI-model resigns before it can lose).

## 1. What you need

- **Windows 10 or 11** (this project is built and tested on Windows)
- **Python 3.11 or newer** — [python.org/downloads](https://www.python.org/downloads/)
- **Node.js** — [nodejs.org](https://nodejs.org/) (needed to install the AI CLIs below)
- A subscription to **Claude** (for Claude Code) and/or **ChatGPT Plus/Pro** (for Codex)

## 2. Install, step by step

**Step 1 — Install the AI CLI tool(s) you want to use.** Open a terminal (PowerShell) and run:

```
npm install -g @anthropic-ai/claude-code
claude
```

The first time you run `claude`, it will ask you to log in — follow the prompts in your
browser. Do the same for Codex if you want to use OpenAI models:

```
npm install -g @openai/codex
codex login
```

**Step 2 — Get this project.**

```
git clone https://github.com/alt-account34/AI-Chess-Arena.git
cd AI-Chess-Arena
```

(Or download it as a ZIP from the green "Code" button on GitHub and unzip it.)

**Step 3 — Run it.** Double-click `run.bat`, or from a terminal in the project folder:

```
run.bat
```

The first run automatically creates a private Python environment (`.venv`) and installs
everything the project needs — this can take a minute. After that, it starts the server and
opens **http://localhost:8000** in your browser. That's it — pick two agents on the setup
page and start a game.

Every time after the first, `run.bat` just starts the server (no reinstall).

### Running it by hand (optional)

If you prefer not to use `run.bat`:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m arena
```

Add `--no-browser` to skip auto-opening the browser, or `--port 8001` to use a different port.

### Optional: Stockfish (for the evaluation bar and move ratings)

Without Stockfish, games still run fine — you just won't see the evaluation bar, the eval
chart, or move quality labels (Brilliant/Blunder/etc). The AI agents never see Stockfish's
output either way; it's purely a feature for you, the viewer.

To enable it:

1. Download a Windows build from [stockfishchess.org/download](https://stockfishchess.org/download/)
   (the "x86-64-universal" build works on any 64-bit PC).
2. Unzip it and copy the `.exe` file to `tools/stockfish/stockfish.exe` in this project folder.
3. Restart the server (`run.bat` again).

See `SETUP.md` for tuning details and troubleshooting.

## 3. Adding new AI agents

Agents are defined in `agents.yaml` in the project folder — a plain text file you can open and
edit with Notepad or any text editor. You don't need to restart the server after editing it;
it's reloaded whenever you open the setup page or start a new game.

### The easy way: add a whole model family

A "family" line adds one agent per difficulty level automatically. For example, this one line:

```yaml
families:
  - {id_prefix: sonnet-5, name: "Sonnet 5", group: Claude, cli: claude_code, model: claude-sonnet-5,
     efforts: [low, medium, high, xhigh, max]}
```

gives you "Sonnet 5 (low)" through "Sonnet 5 (max)" to pick from on the setup page, all in one
go. To add another model this way, copy a line like this and change:

- `id_prefix` — a short unique id (used in file names, so keep it simple, e.g. `my-model`)
- `name` — the display name you'll see in the picker
- `group` — the heading it appears under in the picker (e.g. `Claude`, `Codex`, or your own)
- `cli` — `claude_code` for Claude Code, or `codex` for Codex
- `model` — the exact model name/id that CLI expects (same string you'd pass to
  `claude --model ...` or `codex -m ...`)
- `efforts` — which reasoning-effort levels to offer; stick to
  `[low, medium, high, xhigh, max]` unless you've confirmed the model supports others

### Adding a single agent (one specific effort level only)

```yaml
agents:
  - id: my-agent-high
    display_name: "My Agent (high)"
    cli: claude_code
    model: claude-opus-5-5
    reasoning_effort: high
```

`id` must be unique — it's used in saved file names and the results history, so once you've
played games with an id, don't reuse it for a different model.

### Other agent types you can add

- `cli: human` — lets a person play on the board in the browser (see `agents.yaml` for the
  built-in "Player (you)" example)
- `cli: mock` — a scripted/random agent with no AI involved at all, useful for testing

Full details, including exactly which model ids and effort levels each CLI currently accepts,
are in `SETUP.md` (section 3).

## 4. Everyday use

| What you want to do | How |
|---|---|
| Watch two AIs play | Open http://localhost:8000, pick agents on the setup page, start |
| Play against an AI yourself | Pick "Player (you)" for one side on the setup page |
| Run one game from the terminal, no browser | `python -m arena play --white A --black B` |
| Watch a past game again | Use the "Replay" link on the site |
| See win/loss/draw history | Use the "Record" page on the site |
| Re-run move quality analysis after changing Stockfish settings | `python -m arena analyze --all` |
| Run the test suite | `.venv\Scripts\python -m pytest` |

`SETUP.md` has the full reference: how a game actually works turn by turn, openings, token
tracking, the live "agent console" / think-aloud feature, move classification rules, how each
CLI is sandboxed, and what to do when a CLI updates and breaks something.

## 5. Privacy and what gets saved locally

- Every game is saved to the `games/` folder on your own computer (full move-by-move logs, PGN
  files, and, for recent games, a console transcript). `arena.db` is a local index of that
  history for the website's "Record" page.
- **None of this results history is published to GitHub.** It's excluded via `.gitignore` and
  stays only on your machine.
- No API keys are stored anywhere in this project. The agents run through the `claude` and
  `codex` CLI tools using your existing subscription login; the project actively strips
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY` and `CODEX_API_KEY` from the
  environment before launching them, so a stray key on your system can't accidentally get
  billed.
- Each AI agent is sandboxed: no tools, no file access, no internet access, and it runs from an
  empty temporary folder that's deleted after the game. It never sees Stockfish's evaluations,
  the clock, or anything else beyond the board and move history. See `SETUP.md` section 5 for
  exactly how this is enforced.

## License

[MIT](LICENSE) for this project's own code. The bundled board UI
(`web/vendor/chessground*`) is GPL-3.0 — see `web/vendor/LICENSE-chessground.txt`. You'll need
to download your own copy of Stockfish separately (see section 2 above); it isn't included in
this repository.
