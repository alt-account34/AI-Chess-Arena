"""Adapter parsing and command lines, without calling any real CLI."""
import json

import pytest

from arena.agents.base import AgentFailure
from arena.agents.claude_code import ClaudeCodeAgent, parse_stream
from arena.agents.codex import CodexAgent, parse_events
from arena.config import AgentConfig, Settings
from arena.procutil import ProcResult, resolve_cli


def jl(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


# --------------------------------------------------------------------------- Claude Code

CLAUDE_OK = jl(
    {"type": "system", "subtype": "init", "session_id": "S1", "model": "claude-opus-5-5",
     "tools": [], "mcp_servers": [], "apiKeySource": "none"},
    {"type": "assistant", "session_id": "S1", "message": {"content": [{"type": "thinking", "thinking": ""}]}},
    {"type": "assistant", "session_id": "S1", "message": {"content": [{"type": "text", "text": "MOVE: e4\nPLAN: x"}]}},
    {"type": "result", "subtype": "success", "is_error": False, "session_id": "S1",
     "result": "MOVE: e4\nPLAN: x", "total_cost_usd": 0.01},
)


def test_claude_parse_ok():
    r = parse_stream(CLAUDE_OK)
    assert r.text == "MOVE: e4\nPLAN: x" and r.session_id == "S1"
    assert r.tool_events == [] and r.warnings == [] and r.meta["cost_usd"] == 0.01


def test_claude_detects_tool_use_and_config_leaks():
    out = jl(
        {"type": "system", "subtype": "init", "session_id": "S1", "tools": ["Read"],
         "mcp_servers": [{"name": "x"}], "apiKeySource": "ANTHROPIC_API_KEY"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "C:/x"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "server_tool_use", "name": "web_search", "input": {}}]}},
        {"type": "result", "subtype": "success", "is_error": False, "session_id": "S1", "result": "MOVE: d4"},
    )
    r = parse_stream(out)
    assert [e["tool"] for e in r.tool_events] == ["Read", "web_search"]
    assert len(r.warnings) == 3  # tools listed, MCP connected, API key billing


def make_claude(tmp_path):
    a = ClaudeCodeAgent(AgentConfig(id="c", cli="claude_code", model="claude-opus-5-5",
                                    reasoning_effort="medium"), Settings(claude_path="claude.exe"))
    a.setup(tmp_path)
    return a


def test_claude_argv(tmp_path):
    a = make_claude(tmp_path)
    first = a.build_argv(None, "NEW")
    assert first[first.index("--session-id") + 1] == "NEW" and "--resume" not in first
    assert first[first.index("--tools") + 1] == ""          # empty arg survives (no shell)
    assert first[first.index("--model") + 1] == "claude-opus-5-5"
    assert first[first.index("--effort") + 1] == "medium"
    for flag in ("-p", "--strict-mcp-config", "--safe-mode", "--disable-slash-commands"):
        assert flag in first
    assert "--mcp-config" not in first
    resumed = a.build_argv("OLD", None)
    assert resumed[resumed.index("--resume") + 1] == "OLD" and "--session-id" not in resumed


def fake_run(stdout="", stderr="", rc=0):
    return lambda argv, stdin, cwd, timeout, on_line=None: ProcResult(stdout, stderr, rc, False, False)


def test_claude_resume_missing(tmp_path):
    a = make_claude(tmp_path)
    a.runner.run = fake_run(jl({"type": "result", "subtype": "error_during_execution", "is_error": True,
                                "session_id": "OLD"}),
                            "No conversation found with session ID: OLD", 1)
    assert a.ask("p", "OLD", 10).resume_failed


def test_claude_error_result_is_failure(tmp_path):
    a = make_claude(tmp_path)
    a.runner.run = fake_run(jl({"type": "result", "subtype": "success", "is_error": True,
                                "result": "Usage limit reached", "session_id": "S"}), "", 1)
    with pytest.raises(AgentFailure, match="Usage limit"):
        a.ask("p", None, 10)


# --------------------------------------------------------------------------- Codex

CODEX_OK = jl(
    {"type": "thread.started", "thread_id": "T1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "hmm"}},
    {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "MOVE: c5\nPLAN: y"}},
    {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
)


def test_codex_parse_ok():
    r = parse_events(CODEX_OK)
    assert r.text == "MOVE: c5\nPLAN: y" and r.session_id == "T1" and r.tool_events == []
    assert r.meta["usage"]["output_tokens"] == 5


def test_codex_detects_tool_items():
    out = jl(
        {"type": "thread.started", "thread_id": "T1"},
        {"type": "item.completed", "item": {"id": "a", "type": "command_execution", "command": "dir",
                                            "exit_code": 0}},
        {"type": "item.completed", "item": {"id": "b", "type": "mcp_tool_call", "server": "x", "tool": "y"}},
        {"type": "item.completed", "item": {"id": "c", "type": "web_search", "query": "stockfish"}},
        {"type": "item.completed", "item": {"id": "d", "type": "error", "message": "metadata missing"}},
        {"type": "item.completed", "item": {"id": "e", "type": "agent_message", "text": "MOVE: e5"}},
        {"type": "turn.completed"},
    )
    r = parse_events(out)
    assert [e["tool"] for e in r.tool_events] == ["command_execution", "mcp_tool_call", "web_search"]
    assert r.warnings == ["codex: metadata missing"] and r.text == "MOVE: e5"


def make_codex(tmp_path):
    a = CodexAgent(AgentConfig(id="x", cli="codex", model="gpt-6-astra", reasoning_effort="medium"),
                   Settings(codex_path="codex.exe"),
                   features=frozenset({"shell_tool", "computer_use", "browser_use", "apps", "plugins",
                                       "fast_mode"}))
    a.setup(tmp_path)
    return a


def test_codex_argv(tmp_path):
    a = make_codex(tmp_path)
    first = a.build_argv(None)
    assert first[1] == "exec" and first[-1] == "-"
    assert first[first.index("-s") + 1] == "read-only" and first[first.index("-C") + 1] == str(tmp_path)
    assert first[first.index("-m") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="medium"' in first and 'approval_policy="never"' in first
    assert "--ignore-user-config" in first and "--json" in first
    disabled = {first[i + 1] for i, x in enumerate(first) if x == "--disable"}
    # only features this Codex knows are passed; unrelated ones are left alone
    assert disabled == {"shell_tool", "computer_use", "browser_use", "apps", "plugins"}
    resumed = a.build_argv("T1")
    assert resumed[1:4] == ["exec", "resume", "T1"] and resumed[-1] == "-"
    assert "-s" not in resumed and 'sandbox_mode="read-only"' in resumed


def test_codex_resume_missing(tmp_path):
    a = make_codex(tmp_path)
    a.runner.run = fake_run("", "Error: thread/resume: thread/resume failed: no rollout found for thread id T1", 1)
    assert a.ask("p", "T1", 10).resume_failed


def test_codex_turn_failed_is_failure(tmp_path):
    a = make_codex(tmp_path)
    a.runner.run = fake_run(jl({"type": "thread.started", "thread_id": "T"},
                               {"type": "turn.failed", "error": {"message": "requires a newer version of Codex"}}),
                            "", 1)
    with pytest.raises(AgentFailure, match="newer version"):
        a.ask("p", None, 10)


# --------------------------------------------------------------------------- shim resolution

def test_resolve_npm_shims(tmp_path):
    (tmp_path / "node_modules" / "pkg" / "bin").mkdir(parents=True)
    exe = tmp_path / "node_modules" / "pkg" / "bin" / "tool.exe"
    exe.write_bytes(b"")
    (tmp_path / "tool.cmd").write_text('@ECHO off\r\n"%dp0%\\node_modules\\pkg\\bin\\tool.exe"   %*\r\n')
    assert resolve_cli("tool", str(tmp_path / "tool.cmd")) == [str(exe)]
    js = tmp_path / "node_modules" / "pkg" / "bin" / "tool.js"
    js.write_text("")
    (tmp_path / "jtool.cmd").write_text('"%_prog%"  "%dp0%\\node_modules\\pkg\\bin\\tool.js" %*\r\n')
    argv = resolve_cli("jtool", str(tmp_path / "jtool.cmd"))
    assert argv[-1] == str(js) and argv[0].lower().endswith(("node.exe", "node"))


# --------------------------------------------------------------------------- Codex rollout log

def test_codex_rollout_tool_calls_are_violations(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    monkeypatch.setenv("CODEX_HOME", str(home))
    day = home / "sessions" / "2026" / "10" / "15"
    day.mkdir(parents=True)
    log = day / "rollout-2026-10-15T11-00-35-T9.jsonl"
    rec = lambda p: json.dumps({"type": "response_item", "payload": p}) + "\n"
    log.write_text(rec({"type": "message", "role": "user"}) +
                   rec({"type": "custom_tool_call", "name": "exec", "input": "text(2+2)"}) +
                   rec({"type": "custom_tool_call_output", "output": "code-mode host is disabled"}),
                   encoding="utf-8")
    a = make_codex(tmp_path)
    ok = jl({"type": "thread.started", "thread_id": "T9"},
            {"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "MOVE: e4"}},
            {"type": "turn.completed"})
    a.runner.run = fake_run(ok)
    r = a.ask("p", None, 10)
    assert [(e["tool"], e["source"]) for e in r.tool_events] == [("exec", "rollout")]
    # the next turn only reports calls made after the first one
    a.runner.run = fake_run(ok.replace('"thread.started", "thread_id": "T9"', '"turn.started"'))
    assert a.ask("p", "T9", 10).tool_events == []
    with log.open("a", encoding="utf-8") as f:
        f.write(rec({"type": "function_call", "name": "wait", "arguments": "{}"}))
    assert [e["tool"] for e in a.ask("p", "T9", 10).tool_events] == ["wait"]


def test_codex_missing_rollout_is_a_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty"))
    a = make_codex(tmp_path)
    a.runner.run = fake_run(jl({"type": "thread.started", "thread_id": "T1"},
                               {"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "x"}}))
    r = a.ask("p", None, 10)
    assert r.tool_events == [] and any("rollout log not found" in w for w in r.warnings)


def test_claude_rejects_unknown_effort():
    with pytest.raises(ValueError, match="not one of"):
        ClaudeCodeAgent(AgentConfig(id="c", cli="claude_code", reasoning_effort="extreme"),
                        Settings(claude_path="claude.exe"))


def test_all_configured_agents_construct():
    from arena.agents.registry import create_agent
    from arena.config import load_agents
    for cfg in load_agents().values():
        if cfg.cli == "claude_code":
            create_agent(cfg, Settings(claude_path="claude.exe"))
