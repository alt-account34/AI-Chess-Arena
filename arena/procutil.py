"""Windows-friendly subprocess helpers for the CLI adapters."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Never let a stray API key switch a CLI from the subscription to API billing.
SCRUBBED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY")


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in SCRUBBED_ENV}
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    return env


def resolve_cli(name: str, override: str | None = None) -> list[str]:
    """Returns the argv prefix that runs a CLI without going through cmd.exe.

    npm installs CLIs as .cmd shims; running those with shell=False goes through
    cmd.exe argument re-parsing, which mangles empty args and quotes. We read the
    shim and call its real target (a native .exe, or node + a .js file).
    """
    path = override or shutil.which(name)
    if not path:
        raise FileNotFoundError(f"'{name}' CLI not found on PATH (set {name}_path in settings.yaml)")
    p = Path(path)
    if p.suffix.lower() in (".exe", ""):
        if p.suffix == "" and p.with_suffix(".cmd").exists():
            p = p.with_suffix(".cmd")
        else:
            return [str(p)]
    if p.suffix.lower() == ".js":
        return [_node(), str(p)]
    if p.suffix.lower() in (".cmd", ".bat"):
        text = p.read_text(encoding="utf-8", errors="replace")
        targets = re.findall(r'"%dp0%\\([^"]+?\.(?:exe|js|cjs|mjs))"', text, re.IGNORECASE)
        if targets:
            target = p.parent / targets[-1]
            if target.exists():
                if target.suffix.lower() == ".exe":
                    return [str(target)]
                return [_node(p.parent), str(target)]
        return [str(p)]  # last resort: run the shim itself
    return [str(p)]


def _node(near: Path | None = None) -> str:
    if near is not None and (near / "node.exe").exists():
        return str(near / "node.exe")
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("node.exe not found on PATH")
    return node


@dataclass
class ProcResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    cancelled: bool


class ProcRunner:
    """Runs one process at a time, feeding stdin, with a timeout and cancel()."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    def run(self, argv: list[str], stdin_text: str, cwd: Path, timeout: float,
            on_line: Callable[[str], None] | None = None) -> ProcResult:
        """Runs the process to completion. Each stdout line is passed to on_line as
        soon as it arrives (for the live agent console); the full output is returned."""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with self._lock:
            self._cancelled = False
            self._proc = subprocess.Popen(
                argv, cwd=str(cwd), env=child_env(), shell=False,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=flags,
            )
        proc = self._proc
        out_lines: list[str] = []
        err_chunks: list[bytes] = []

        def feed_stdin() -> None:
            try:
                proc.stdin.write(stdin_text.encode("utf-8"))
                proc.stdin.close()
            except OSError:
                pass  # process died early; its exit code tells the story

        def read_stdout() -> None:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                out_lines.append(line)
                if on_line is not None:
                    try:
                        on_line(line)
                    except Exception:  # noqa: BLE001 - a viewer must never break a game
                        pass

        def read_stderr() -> None:
            err_chunks.append(proc.stderr.read())

        threads = [threading.Thread(target=f, daemon=True) for f in (feed_stdin, read_stdout, read_stderr)]
        for t in threads:
            t.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            proc.wait()
        finally:
            for t in threads:
                t.join(timeout=10)
            with self._lock:
                self._proc = None
        err = b"".join(err_chunks).decode("utf-8", errors="replace").replace("\r\n", "\n")
        stdout = "\n".join(out_lines) + ("\n" if out_lines else "")
        return ProcResult(stdout, err, proc.returncode, timed_out, self._cancelled)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._proc is not None and self._proc.poll() is None:
                _kill_tree(self._proc)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and its children (node spawns codex.exe, etc.)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        proc.kill()
    except OSError:
        pass
