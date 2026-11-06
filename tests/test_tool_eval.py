"""The tool-selection benchmark: scoring contracts, the committed corpus, and the offline runner."""

from __future__ import annotations

import copy
import json

import pytest

from evals.tool_selection import run_eval as evaluation


@pytest.mark.parametrize(
    ("expect", "pick", "aliases", "correct"),
    [
        (["inventory_list", "inventory_get"], "inventory_get", {}, True),
        (["inventory_list"], "inventory_get", {"find": ["inventory_get"]}, True),
        (["inventory_list"], "inventory_get", {"other": ["inventory_get"]}, False),
        (["inventory_list"], "web_search", {}, False),
        (["inventory_list"], None, {}, False),
        ("none", None, {}, True),
        ("none", "none", {}, True),
        ("none", "inventory_list", {}, False),
        # A turn-only tool is never right for an operator prompt, even if a corpus listed it.
        (["inventory_sale_candidates"], "inventory_sale_candidates", {}, False),
    ],
)
def test_score_acceptance(expect, pick, aliases, correct):
    prompts = [{"id": "one", "prompt": "Example", "expect": expect, "capability": "find"}]
    before = copy.deepcopy((prompts, aliases))
    result = evaluation.score({"one": pick}, prompts, aliases)
    assert result["picks"][0]["correct"] is correct
    assert result["overall"] == {"correct": int(correct), "total": 1, "accuracy": float(correct)}
    assert (prompts, aliases) == before


def test_score_rejects_incomplete_observations():
    prompts = [{"id": "a", "expect": ["x"], "capability": "find"}, {"id": "b", "expect": "none", "capability": "chat"}]
    result = evaluation.score({"a": "x", "b": "y"}, prompts, {})
    assert result["overall"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    with pytest.raises(ValueError, match="exactly"):
        evaluation.score({"a": "x"}, prompts, {})
    with pytest.raises(ValueError, match="exactly"):
        evaluation.score({"a": "x", "b": None, "c": None}, prompts, {})


def test_committed_prompts_match_the_registered_surface():
    tools = {t["function"]["name"] for t in evaluation.collect_tools({})}
    decoys = {name for name, _props, _desc in evaluation.DECOYS}
    plugin_tools = tools - decoys
    prompts, aliases = evaluation.load_prompts()

    assert 40 <= len(prompts) <= 60
    assert len({p["id"] for p in prompts}) == len(prompts)
    # The turn-only set is exactly the plugin's tools for its own turns: all registered.
    assert plugin_tools >= evaluation.TURN_ONLY
    expected = [name for p in prompts if p["expect"] != "none" for name in p["expect"]]
    assert set(expected) <= tools, set(expected) - tools
    assert not set(expected) & evaluation.TURN_ONLY, "an operator prompt must never expect a turn-only tool"
    for names in aliases.values():
        assert set(names) <= tools and not set(names) & evaluation.TURN_ONLY
    # Every operator-facing tool is exercised at least twice, so a renamed or new tool can't
    # silently drop out of the benchmark.
    for name in sorted(plugin_tools - evaluation.TURN_ONLY):
        assert sum(name in p["expect"] for p in prompts if p["expect"] != "none") >= 2, name
    assert any(p["expect"] == "none" and p["capability"] == "trap" for p in prompts)
    assert any(p["expect"] == "none" and p["capability"] == "chat" for p in prompts)


def test_the_corpus_carries_no_operator_data():
    text = evaluation.PROMPTS_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("josh", "merchantagent", "nerdsville", "helsmiths", "ts.net", "@"):
        assert forbidden not in text, forbidden


def test_dry_run_writes_results_without_a_network(tmp_path, monkeypatch, capsys):
    for key in ("EVAL_BASE_URL", "EVAL_API_KEY", "EVAL_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert evaluation.main(["--dry-run", "--label", "plumbing", "--output-dir", str(tmp_path)]) == 0
    result = json.loads((tmp_path / "plumbing.json").read_text())
    prompts, _ = evaluation.load_prompts()
    assert result["dry_run"] is True and result["model"] == "deterministic-fake"
    assert result["overall"]["total"] == len(prompts)
    assert {r["id"] for r in result["picks"]} == {p["id"] for p in prompts}
    assert result["config_keys"] == []
    assert "turn-only picks on operator prompts" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://gateway.example/v1", True),
        ("ftp://gateway.example/v1", False),
        ("https://user:pw@gateway.example/v1", False),
        ("https://gateway.example/v1?key=x", False),
    ],
)
def test_live_endpoint_validation(monkeypatch, url, ok):
    monkeypatch.setenv("EVAL_BASE_URL", url)
    monkeypatch.setenv("EVAL_API_KEY", "k")
    monkeypatch.setenv("EVAL_MODEL", "m")
    if ok:
        assert evaluation.endpoint_from_env() == (url, "k", "m")
    else:
        with pytest.raises(ValueError):
            evaluation.endpoint_from_env()


def test_live_run_needs_all_three_settings(monkeypatch):
    for key in ("EVAL_BASE_URL", "EVAL_API_KEY", "EVAL_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="EVAL_BASE_URL"):
        evaluation.endpoint_from_env()
