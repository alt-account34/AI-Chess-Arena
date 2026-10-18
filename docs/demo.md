# Public replay demo

The demo is a static website hosted at <https://jackstroup.github.io/AI-Chess-Arena/>.
It reuses the local app's styles, board renderer, evaluation chart, and record tables.
It needs no Python server, model subscription, or Stockfish installation in the browser.
The local app still uses its existing setup, live, replay, and record pages.

## Build and preview

From the repository root:

```powershell
python tools/build_demo.py
python -m http.server 8080 --directory _site --bind 127.0.0.1
```

Open <http://localhost:8080/>. The build uses committed public data, so it works in a
fresh clone without private game logs or Python dependencies. Relative links also work
when the output is served beneath `/AI-chess-arena/`.

## Update the public games

After installing the normal project requirements:

```powershell
python tools/export_demo.py
python tools/build_demo.py
```

The exporter reads `games/*.json` and available `.console.jsonl` transcripts without
modifying them. It writes a public manifest, per-game data, and nine filtered comparison
datasets to `demo/data/`. It does not publish `arena.db` or the original logs.

Review the exported text before committing it: responses and game prompts are public.
Session identifiers, local machine paths, raw CLI transport, and diagnostic metadata
are removed. Games missing transcripts use saved model responses, visibly labeled.
The exporter retains illegal attempts and final resignation turns. New games only become
public when their exported data is committed and deployed. To remove an exported game,
remove it from the export input, rerun the exporter, and remove its obsolete public JSON.
Only manifest-listed games are copied into a fresh site build.

At 1×, non-book turns are scaled around a 3-second median and clamped to 2–5 seconds.
Opening book moves take 0.3 seconds. The speed control multiplies that compressed
timeline; displayed thinking times are the original recorded times. Hidden tabs pause.
Seeking changes the board, console history, and evaluations together. Outcomes appear
at completion; Head-to-head intentionally shows full historical results.

## Publish

The `Deploy replay demo` workflow builds and deploys on pushes to the default branch
of `jackstroup/AI-Chess-Arena`, and can be run manually on that branch. Forks do not deploy.
In that repository's **Settings → Pages**, select **GitHub Actions** as the build source.
The workflow uploads only `_site`, using the `github-pages` deployment environment.

This checkout's existing remote may point to another account. The deployment target and
star links intentionally use `jackstroup/AI-Chess-Arena`; the build does not change remotes.
Keep its Pages project path and the README/demo links in sync if the repository is renamed.

## Checks

```powershell
python -m pytest
node --test tests/demo-timeline.test.mjs
python tools/build_demo.py
```

Check the picker, a full replay including its final turn, speed/pause/seek controls,
both inline consoles, all record filters, and the completion dialog on desktop and mobile.
The bundled Chessground license is retained at `static/vendor/LICENSE-chessground.txt`.
