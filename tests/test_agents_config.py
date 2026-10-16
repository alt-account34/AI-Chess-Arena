from arena.config import ROOT, expand_family, load_agents


def test_family_expansion():
    out = expand_family({"id_prefix": "sonnet-5", "name": "Sonnet 5", "group": "Claude", "cli": "claude_code",
                         "model": "claude-sonnet-5", "efforts": ["low", "max"]})
    assert [a["id"] for a in out] == ["sonnet-5-low", "sonnet-5-max"]
    assert out[1]["display_name"] == "Sonnet 5 (max)" and out[1]["reasoning_effort"] == "max"
    assert out[0]["family"] == "Sonnet 5" and out[0]["group"] == "Claude" and out[0]["model"] == "claude-sonnet-5"
    single = expand_family({"id_prefix": "x", "name": "X", "cli": "codex"})
    assert single[0]["id"] == "x" and single[0]["reasoning_effort"] is None and single[0]["group"] == "codex"


def test_shipped_agents_keep_old_ids_and_have_the_new_models():
    agents = load_agents(ROOT / "agents.yaml")
    # ids of games already in the record must not change
    for aid in ("opus-5.5-low", "opus-5.5-medium", "opus-5.5-max", "astra-low", "astra-medium", "astra-xhigh"):
        assert aid in agents
    assert agents["astra-medium"].display_name == "Astra (medium)" and agents["astra-medium"].model == "gpt-6-astra"
    assert agents["opus-5.5-high"].display_name == "Opus 5.5 (high)"
    models = {a.model for a in agents.values()}
    for m in ("claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-fable-5-1", "gpt-5.6-luna",
              "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol"):
        assert m in models
    assert agents["human"].cli == "human"
    efforts = {a.reasoning_effort for a in agents.values() if a.cli in ("claude_code", "codex")}
    assert efforts == {"low", "medium", "high", "xhigh", "max"}
