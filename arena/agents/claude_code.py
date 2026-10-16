"""Claude Code adapter (`claude -p`, headless, subscription login, no tools).

Flags verified against `claude --help` (Claude Code 2.1.280):
  -p --output-format stream-json --verbose   headless, one JSON event per line
  --model / --effort                          from agents.yaml, passed verbatim
  --session-id <uuid> | --resume <uuid>       new session with a known id / resume it
  --tools ""                                  no built-in tools at all
  --strict-mcp-config (no --mcp-config)       no MCP servers
  --safe-mode                                 no CLAUDE.md, skills, plugins, hooks, memory
  --disable-slash-commands --no-chrome
  --permission-mode dontAsk --permission-prompts none   never prompt; deny anything else
  --system-prompt                             replaces the coding-agent system prompt
The prompt is sent on stdin. Run `python -m arena check-clis` after a CLI update.
"""
from __future__ import annotations

import json
import uuid

from ..procutil import ProcRunner, resolve_cli
from ..prompts import SYSTEM_PROMPT
from .base import Agent, AgentCancelled, AgentFailure, AgentReply, AgentTimeout

REQUIRED_FLAGS = ["--print", "--output-format", "--verbose", "--model", "--effort", "--session-id",
                  "--resume", "--tools", "--strict-mcp-config", "--safe-mode",
                  "--disable-slash-commands", "--no-chrome", "--permission-mode",
                  "--permission-prompts", "--system-prompt"]
RESUME_MISSING = ("No conversation found", "session not found", "Session not found")


# The CLI silently accepts any --effort string, so a typo would quietly run at the
# default effort. These are the choices `claude --help` lists.
EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ClaudeCodeAgent(Agent):
    def __init__(self, config, settings):
        if config.reasoning_effort and config.reasoning_effort not in EFFORTS:
            raise ValueError(f"agent {config.id}: reasoning_effort '{config.reasoning_effort}' is not one of "
                             f"{', '.join(EFFORTS)} (Claude Code would silently ignore it)")
        super().__init__(config, settings)
        self.argv0 = resolve_cli("claude", settings.claude_path)
        self.runner = ProcRunner()

    def build_argv(self, session_id: str | None, new_id: str | None) -> list[str]:
        # --include-partial-messages streams the reply text live for the agent console;
        # it changes nothing the model sees.
        argv = [*self.argv0, "-p", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages"]
        if self.config.model:
            argv += ["--model", self.config.model]
        if self.config.reasoning_effort:
            argv += ["--effort", self.config.reasoning_effort]
        argv += ["--resume", session_id] if session_id else ["--session-id", new_id]
        argv += ["--tools", "", "--strict-mcp-config", "--safe-mode", "--disable-slash-commands",
                 "--no-chrome", "--permission-mode", "dontAsk", "--permission-prompts", "none",
                 "--system-prompt", SYSTEM_PROMPT]
        return argv

    def ask(self, prompt: str, session_id: str | None, timeout: float) -> AgentReply:
        new_id = None if session_id else str(uuid.uuid4())
        res = self.runner.run(self.build_argv(session_id, new_id), prompt, self.workdir, timeout,
                              on_line=self._console_lines(console_events))
        if res.cancelled:
            raise AgentCancelled()
        if res.timed_out:
            raise AgentTimeout()
        reply = parse_stream(res.stdout)
        reply.stderr, reply.exit_code = res.stderr, res.returncode
        reply.session_id = reply.session_id or session_id or new_id
        if session_id and any(s in res.stderr for s in RESUME_MISSING):
            reply.resume_failed = True
            return reply
        if reply.meta.get("is_error") or (res.returncode != 0 and not reply.text):
            detail = (reply.meta.get("result_text") or res.stderr or res.stdout or "").strip()
            raise AgentFailure(f"claude exited {res.returncode}: {detail[-400:]}")
        return reply


def parse_stream(stdout: str) -> AgentReply:
    """Reads Claude Code stream-json output: final text, session id, tool use."""
    session_id = None
    texts: list[str] = []
    final: str | None = None
    tool_events: list[dict] = []
    warnings: list[str] = []
    meta: dict = {}
    tokens: dict | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        session_id = ev.get("session_id") or session_id
        if t == "system" and ev.get("subtype") == "init":
            meta["model"] = ev.get("model")
            meta["api_key_source"] = ev.get("apiKeySource")
            if ev.get("tools"):
                warnings.append(f"tools were available to the agent: {ev['tools']}")
            if ev.get("mcp_servers"):
                warnings.append(f"MCP servers were connected: {ev['mcp_servers']}")
            if ev.get("apiKeySource") not in (None, "none"):
                warnings.append(f"CLI used an API key ({ev.get('apiKeySource')}) instead of the subscription")
        elif t == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                bt = block.get("type")
                if bt == "text":
                    texts.append(block.get("text", ""))
                elif bt in ("tool_use", "server_tool_use", "mcp_tool_use"):
                    tool_events.append({"tool": block.get("name"), "type": bt,
                                        "input": _short(block.get("input"))})
        elif t == "result":
            final = ev.get("result")
            meta.update(is_error=bool(ev.get("is_error")), result_subtype=ev.get("subtype"),
                        cost_usd=ev.get("total_cost_usd"), duration_ms=ev.get("duration_ms"),
                        num_turns=ev.get("num_turns"), usage=ev.get("usage"))
            if ev.get("is_error"):
                meta["result_text"] = str(ev.get("result") or ev.get("errors") or ev.get("subtype"))
            tokens = claude_tokens(ev.get("usage"))
    text = final if isinstance(final, str) and final else "\n".join(texts)
    return AgentReply(text=text, session_id=session_id, tool_events=tool_events,
                      raw_stdout=stdout, warnings=warnings, meta=meta, tokens=tokens)


def console_events(line: str) -> list[tuple[str, dict]]:
    """One stream-json line -> live console events."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return []
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return [("session", {"session_id": ev.get("session_id"), "model": ev.get("model"),
                             "tools": ev.get("tools"), "mcp_servers": ev.get("mcp_servers")})]
    if t == "stream_event":
        e = ev.get("event") or {}
        et = e.get("type")
        if et == "content_block_start" and (e.get("content_block") or {}).get("type") == "thinking":
            return [("thinking", {})]
        if et == "content_block_delta":
            d = e.get("delta") or {}
            if d.get("type") == "text_delta":
                return [("text", {"delta": d.get("text", "")})]
            if d.get("type") == "thinking_delta" and d.get("thinking"):
                return [("reasoning", {"text": d["thinking"]})]   # only if the CLI ever un-redacts it
        if et == "content_block_stop":
            return [("block_end", {})]
        return []
    if t == "assistant":
        out = []
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") == "thinking":
                out.append(("thinking_done", {"redacted": not block.get("thinking")}))
            elif block.get("type") in ("tool_use", "server_tool_use", "mcp_tool_use"):
                out.append(("tool", {"tool": block.get("name"), "input": _short(block.get("input"))}))
        return out
    if t == "result" and ev.get("is_error"):
        return [("notice", {"text": f"CLI error: {str(ev.get('result') or ev.get('subtype'))[:300]}"})]
    return []


def claude_tokens(u: dict | None) -> dict | None:
    """Anthropic usage -> {input, cached, output, reasoning}. Anthropic's input_tokens
    excludes cache reads and writes, so they are added back to get the full input."""
    if not u:
        return None
    cached = int(u.get("cache_read_input_tokens") or 0)
    return {"input": int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + cached,
            "cached": cached,
            "output": int(u.get("output_tokens") or 0),
            "reasoning": int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)}


def _short(v, n: int = 300):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"
