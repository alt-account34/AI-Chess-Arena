"""`python -m arena check-clis`: do the installed CLIs still accept our flags?

Reads each CLI's --help and reports any flag the adapters rely on that has
disappeared. It makes no model calls and uses no subscription quota.
"""
from __future__ import annotations

import subprocess

from .agents import claude_code, codex
from .config import Settings
from .procutil import child_env, resolve_cli


def _help(argv: list[str]) -> str:
    out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=child_env(), timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout + out.stderr


def check_all(settings: Settings, verbose: bool = False) -> bool:
    ok = True
    checks = [
        ("claude", settings.claude_path, [["--help"]], [claude_code.REQUIRED_FLAGS]),
        ("codex", settings.codex_path, [["exec", "--help"], ["exec", "resume", "--help"]],
         [codex.REQUIRED_EXEC_FLAGS, codex.REQUIRED_RESUME_FLAGS]),
    ]
    for name, override, help_cmds, required_lists in checks:
        try:
            argv0 = resolve_cli(name, override)
        except FileNotFoundError as e:
            print(f"[{name}] NOT FOUND: {e}")
            ok = False
            continue
        try:
            version = _help([*argv0, "--version"]).strip().splitlines()[0]
        except Exception as e:  # noqa: BLE001
            version = f"? ({e!r})"
        print(f"[{name}] {version}  ({' '.join(argv0)})")
        for cmd, required in zip(help_cmds, required_lists):
            text = _help([*argv0, *cmd])
            missing = [f for f in required if f not in text]
            label = " ".join(cmd)
            if missing:
                ok = False
                print(f"  {label}: MISSING {', '.join(missing)}")
            elif verbose:
                print(f"  {label}: all {len(required)} flags present")
    print("OK: all flags present" if ok else
          "Some flags changed. Update arena/agents/*.py (see SETUP.md, 'When a CLI changes').")
    return ok
