"""Codex adapter (`codex exec`, headless, subscription login, strictest setup).

Flags verified against `codex exec --help` and `codex exec resume --help` (Codex CLI 0.162.1):
  exec --json                         JSONL events on stdout
  exec resume <thread_id> --json      continue the same thread
  -                                   read the prompt from stdin
  --ignore-user-config                skip ~/.codex/config.toml (your MCP servers, effort, ...)
  --ignore-rules --skip-git-repo-check
  -s read-only / -c sandbox_mode=...  read-only sandbox (resume has no -s flag)
  -c approval_policy="never"          never ask for approval
  -m / -c model_reasoning_effort=...  from agents.yaml, passed verbatim
  --disable <feature>                 shell, browser, computer use, apps, plugins, ...
  -c web_search="disabled"
  -c skills.include_instructions=false -c skills.bundled.enabled=false
  -c project_doc_max_bytes=0          no project AGENTS.md
  -c developer_instructions=...       the same short system text Claude gets
Tool detection: `exec --json` items, plus the thread's rollout log in
CODEX_HOME/sessions, which also records code-mode `exec` calls that the JSON
stream omits (with code_mode_host disabled those calls fail closed).
Known gap: Codex always loads ~/.codex/AGENTS.md (global user instructions);
no flag disables it. See SETUP.md.
"""
from __future__ import annotations

import functools
import json
import os
import subprocess
from pathlib import Path

from ..procutil import ProcRunner, child_env, resolve_cli
from ..prompts import SYSTEM_PROMPT
from .base import Agent, AgentCancelled, AgentFailure, AgentReply, AgentTimeout

# Every feature that gives the model a tool or outside access. Only the ones the
# installed Codex knows are passed (an unknown name is a hard error).
DISABLED_FEATURES = ["shell_tool", "unified_exec", "unified_exec_tty", "apps", "plugins", "browser_use",
                     "browser_use_external", "browser_use_full_cdp_access", "browser_annotation_api",
                     "computer_use", "in_app_browser", "in_app_local_automation", "image_generation",
                     "multi_agent", "multi_agent_v2", "view_image", "goals", "hooks", "memories",
                     "skill_search", "skill_mcp_dependency_install", "tool_suggest", "sleep_tool",
                     "code_mode", "code_mode_host", "js_repl", "standalone_web_search",
                     "request_permissions_tool", "realtime_conversation", "worktrees"]
RESUME_MISSING = ("no rollout found", "thread not found", "No conversation found")
# Item types that are the model talking/thinking; anything else is a tool call.
NON_TOOL_ITEMS = {"agent_message", "reasoning", "error"}
REQUIRED_EXEC_FLAGS = ["--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                       "--sandbox", "--cd", "--model", "--config", "--disable"]
REQUIRED_RESUME_FLAGS = ["--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                         "--model", "--config", "--disable"]


def _toml_str(s: str) -> str:
    return json.dumps(s)  # a JSON string literal is a valid TOML basic string


@functools.lru_cache(maxsize=4)
def installed_features(argv0: tuple[str, ...]) -> frozenset[str] | None:
    """Feature names from `codex features list`, or None if that fails."""
    try:
        out = subprocess.run([*argv0, "features", "list"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=child_env(), timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    names = {line.split()[0] for line in out.stdout.splitlines() if line.strip()}
    return frozenset(names) or None


class CodexAgent(Agent):
    def __init__(self, config, settings, features: frozenset[str] | None = None):
        super().__init__(config, settings)
        self.argv0 = resolve_cli("codex", settings.codex_path)
        self.runner = ProcRunner()
        known = features if features is not None else installed_features(tuple(self.argv0))
        if known is None:
            raise FileNotFoundError("could not run `codex features list`; is the Codex CLI installed?")
        self.disabled = [f for f in DISABLED_FEATURES if f in known]
        self.rollout = RolloutWatcher(codex_home())
        self.usage = ThreadUsage()

    def common_flags(self) -> list[str]:
        flags = ["--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"]
        if self.config.model:
            flags += ["-m", self.config.model]
        if self.config.reasoning_effort:
            flags += ["-c", f"model_reasoning_effort={_toml_str(self.config.reasoning_effort)}"]
        flags += ["-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"',
                  "-c", 'web_search="disabled"',
                  "-c", "skills.include_instructions=false", "-c", "skills.bundled.enabled=false",
                  "-c", "project_doc_max_bytes=0",
                  "-c", f"developer_instructions={_toml_str(SYSTEM_PROMPT)}",
                  # display only: also emit a short summary of the model's reasoning
                  "-c", 'model_reasoning_summary="detailed"']
        for feature in self.disabled:
            flags += ["--disable", feature]
        return flags

    def build_argv(self, session_id: str | None) -> list[str]:
        if session_id:
            return [*self.argv0, "exec", "resume", session_id, *self.common_flags(), "-"]
        return [*self.argv0, "exec", *self.common_flags(), "-s", "read-only",
                "-C", str(self.workdir), "-"]

    def ask(self, prompt: str, session_id: str | None, timeout: float) -> AgentReply:
        res = self.runner.run(self.build_argv(session_id), prompt, self.workdir, timeout,
                              on_line=self._console_lines(console_events))
        if res.cancelled:
            raise AgentCancelled()
        if res.timed_out:
            raise AgentTimeout()
        reply = parse_events(res.stdout)
        reply.stderr, reply.exit_code = res.stderr, res.returncode
        reply.session_id = reply.session_id or session_id
        if reply.tokens and reply.session_id:
            reply.tokens = self.usage.per_call(reply.session_id, reply.tokens)
        if reply.session_id:
            # `exec --json` does not report code-mode tool calls (even blocked ones);
            # the session's rollout file does.
            logged = self.rollout.new_tool_calls(reply.session_id)
            if logged is None:
                reply.warnings.append("Codex rollout log not found; blocked tool attempts could not be checked")
            else:
                reply.tool_events += logged
        if session_id and any(s in res.stderr for s in RESUME_MISSING):
            reply.resume_failed = True
            return reply
        if reply.meta.get("failed") or (res.returncode != 0 and not reply.text):
            detail = reply.meta.get("error") or res.stderr.strip().splitlines()[-1:] or res.stdout[-400:]
            raise AgentFailure(f"codex exited {res.returncode}: {str(detail)[-500:]}")
        return reply


ROLLOUT_TOOL_TYPES = {"function_call", "custom_tool_call", "local_shell_call", "web_search_call",
                      "tool_search_call", "mcp_tool_call", "image_generation_call"}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


class RolloutWatcher:
    """Reads tool calls from Codex's per-thread rollout log
    (CODEX_HOME/sessions/YYYY/MM/DD/rollout-...-<thread_id>.jsonl), remembering how
    far it has read so each turn only reports its own calls."""

    def __init__(self, home: Path):
        self.home = home
        self.paths: dict[str, Path] = {}
        self.offsets: dict[str, int] = {}

    def find(self, thread_id: str) -> Path | None:
        if thread_id not in self.paths:
            hits = sorted((self.home / "sessions").glob(f"*/*/*/rollout-*{thread_id}.jsonl"))
            if not hits:
                return None
            self.paths[thread_id] = hits[-1]
        return self.paths[thread_id]

    def new_tool_calls(self, thread_id: str) -> list[dict] | None:
        """Tool calls logged since the last call, or None if the log can't be found."""
        path = self.find(thread_id)
        if path is None:
            return None
        try:
            with path.open("rb") as f:
                f.seek(self.offsets.get(thread_id, 0))
                data = f.read()
                self.offsets[thread_id] = f.tell()
        except OSError:
            return []
        events = []
        for raw in data.decode("utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            p = rec.get("payload") or {}
            if rec.get("type") == "response_item" and p.get("type") in ROLLOUT_TOOL_TYPES:
                events.append({"tool": p.get("name") or p.get("type"), "type": p.get("type"),
                               "source": "rollout",
                               "input": _short(p.get("input") or p.get("arguments") or p.get("action") or "")})
        return events


def parse_events(stdout: str) -> AgentReply:
    """Reads `codex exec --json` JSONL: thread id, final message, tool calls."""
    thread_id = None
    tokens: dict | None = None
    messages: list[str] = []
    tool_events: list[dict] = []
    warnings: list[str] = []
    meta: dict = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "thread.started":
            thread_id = ev.get("thread_id")
        elif t == "item.completed":
            item = ev.get("item") or {}
            it = item.get("type")
            if it == "agent_message":
                messages.append(item.get("text", ""))
            elif it == "error":
                warnings.append(f"codex: {item.get('message')}")
            elif it == "reasoning":
                if item.get("text"):
                    meta.setdefault("reasoning", []).append(item["text"])
            elif it not in NON_TOOL_ITEMS:
                tool_events.append({"tool": it, "detail": _short({k: v for k, v in item.items()
                                                                  if k not in ("id", "type")})})
        elif t == "turn.completed":
            meta["usage"] = ev.get("usage")
            tokens = codex_tokens(ev.get("usage"))
        elif t in ("turn.failed", "error"):
            meta["failed"] = True
            err = ev.get("error") or {}
            meta["error"] = err.get("message") if isinstance(err, dict) else ev.get("message")
            meta["error"] = meta["error"] or ev.get("message")
    return AgentReply(text=messages[-1] if messages else "", session_id=thread_id,
                      tool_events=tool_events, raw_stdout=stdout, warnings=warnings, meta=meta,
                      tokens=tokens)


class ThreadUsage:
    """`codex exec --json` reports usage as a running total for the whole thread
    (verified against the rollout log's last_token_usage / total_token_usage).
    This turns those running totals into per-call usage."""

    def __init__(self) -> None:
        self.seen: dict[str, dict] = {}

    def per_call(self, thread_id: str, cumulative: dict) -> dict:
        prev = self.seen.get(thread_id)
        self.seen[thread_id] = dict(cumulative)
        if prev is None:
            return dict(cumulative)          # first call of a new thread starts from zero
        delta = {k: cumulative.get(k, 0) - prev.get(k, 0) for k in cumulative}
        if any(v < 0 for v in delta.values()):
            return dict(cumulative)          # counter reset (shouldn't happen): don't go negative
        return delta


def console_events(line: str) -> list[tuple[str, dict]]:
    """One `exec --json` line -> live console events."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return []
    t = ev.get("type")
    if t == "thread.started":
        return [("session", {"session_id": ev.get("thread_id")})]
    if t == "turn.started":
        return [("thinking", {})]
    if t == "item.completed":
        item = ev.get("item") or {}
        it = item.get("type")
        if it == "agent_message":
            return [("message", {"text": item.get("text", "")})]
        if it == "reasoning":
            return [("reasoning", {"text": item.get("text", "")})] if item.get("text") else []
        if it == "error":
            msg = item.get("message", "")
            if "Code Mode is unavailable" in msg:
                return []        # expected: code mode is switched off on purpose (logged once by the referee)
            return [("notice", {"text": msg})]
        return [("tool", {"tool": it, "detail": _short({k: v for k, v in item.items() if k not in ("id", "type")})})]
    if t in ("turn.failed", "error"):
        err = ev.get("error")
        msg = err.get("message") if isinstance(err, dict) else ev.get("message")
        return [("notice", {"text": f"CLI error: {str(msg)[:300]}"})]
    return []


def codex_tokens(u: dict | None) -> dict | None:
    """OpenAI usage -> {input, cached, output, reasoning}. input_tokens already
    includes cached input; reasoning tokens are part of output. NOTE: for
    `exec --json` these are thread-wide running totals; see ThreadUsage."""
    if not u:
        return None
    return {"input": int(u.get("input_tokens") or 0), "cached": int(u.get("cached_input_tokens") or 0),
            "output": int(u.get("output_tokens") or 0), "reasoning": int(u.get("reasoning_output_tokens") or 0)}


def _short(v, n: int = 300) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"
