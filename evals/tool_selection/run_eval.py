"""Fixed first-tool selection benchmark for the inventory plugin (never executes selected tools).

Install requirements-dev.txt, then run:
    python evals/tool_selection/run_eval.py --label smoke --dry-run
    python evals/tool_selection/run_eval.py --label baseline

Live runs use EVAL_BASE_URL (a /v1-style base), EVAL_API_KEY and EVAL_MODEL. This is a
standalone developer CLI: it is independent of the plugin's runtime settings and never reads
an operator store. Use --config KEY=VALUE for plugin settings. Dry-run picks are synthetic
plumbing checks, not model accuracy. Mirrors careercoach-plugin's evals/tool_selection.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")
PKG = "inventory_plugin"
SYSTEM_PROMPT = (
    "You run a reseller's inventory. Select the first tool needed for the user's request, or "
    "answer directly when no tool is needed. Rules: a target price needs a basis (sold comps "
    "first); never set an item to sold by hand, a sale is recorded with its price; never invent "
    "item facts; you cannot post, end or edit listings on eBay or message buyers."
)

# Tools the plugin's own turns use (scan identify/pricing, listing drafts, sale pastes). They are
# registered so a model can wrongly pick them; no operator prompt expects one.
TURN_ONLY = frozenset(
    {
        "inventory_scan_photos",
        "inventory_scan_propose",
        "inventory_scan_fail",
        "inventory_scan_pricing_done",
        "inventory_scan_sealed_price",
        "inventory_draft_item",
        "inventory_draft_write",
        "inventory_draft_fail",
        "inventory_sale_candidates",
        "inventory_sale_propose",
        "inventory_sale_fail",
    }
)

# Host tools an operator chat on a merchant agent also has. ebay_price_check stands in for the
# ebay plugin's sold-comps tool, the first step of a re-price in the inventory-ops skill.
DECOYS = (
    ("web_search", {"query": "string"}, "Search the public web for information."),
    ("fetch_url", {"url": "string"}, "Fetch the text of a web page."),
    ("memory_recall", {"query": "string"}, "Recall information from previous conversations."),
    (
        "ebay_price_check",
        {"query": "string", "sold": "boolean"},
        "Look up eBay prices for an item: sold=true returns recently SOLD listings (what buyers "
        "paid) with p25/median/p75; sold=false returns active asking prices.",
    ),
)


def score(picks, prompts, aliases):
    """Score an id -> tool-name/None mapping without mutating inputs.

    Every prompt must have exactly one recorded pick. Missing observations are errors, not
    abstentions. Only the literal expectation 'none' accepts no call; aliases extend tool
    expectations, never 'none'. A turn-only tool is never correct for an operator prompt.
    """
    ids = [p["id"] for p in prompts]
    if len(set(ids)) != len(ids) or set(picks) != set(ids):
        raise ValueError("picks must match unique prompt ids exactly")
    rows = []
    groups = {}
    for prompt in prompts:
        pick = picks[prompt["id"]]
        pick = None if pick == "none" else pick
        expect = prompt["expect"]
        correct = (
            pick is None
            if expect == "none"
            else pick is not None
            and pick not in TURN_ONLY
            and pick in [*expect, *aliases.get(prompt["capability"], [])]
        )
        rows.append({**prompt, "pick": pick, "correct": correct, "turn_only_pick": pick in TURN_ONLY})
        group = groups.setdefault(prompt["capability"], {"correct": 0, "total": 0})
        group["correct"] += int(correct)
        group["total"] += 1
    for group in groups.values():
        group["accuracy"] = group["correct"] / group["total"]
    total = len(rows)
    correct = sum(row["correct"] for row in rows)
    return {
        "picks": rows,
        "overall": {"correct": correct, "total": total, "accuracy": correct / total if total else 0.0},
        "per_capability": groups,
    }


def load_prompts(path=PROMPTS_PATH):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    prompts, aliases = data["prompts"], data["aliases"]
    if not isinstance(aliases, dict) or any(
        not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names)
        for names in aliases.values()
    ):
        raise ValueError("aliases must map capabilities to lists of tool names")
    for p in prompts:
        if any(not isinstance(p[k], str) or not p[k].strip() for k in ("id", "prompt", "capability")):
            raise ValueError("prompt ids, text and capabilities must be non-empty strings")
        expect = p["expect"]
        if expect != "none" and (
            not isinstance(expect, list) or not expect or any(not isinstance(n, str) or not n.strip() for n in expect)
        ):
            raise ValueError("expect must be 'none' or a non-empty tool-name list")
    if len({p["id"] for p in prompts}) != len(prompts):
        raise ValueError("prompt ids must be unique")
    return prompts, aliases


def function_tool(name, description, parameters):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


class _Registry:
    """The host surface register() uses, recording only what it registers."""

    def __init__(self, config):
        self.config = config
        self.plugin_id = "inventory"
        self.tools, self.routers, self.skill_dirs = [], [], []

    def register_tool(self, tool):
        self.tools.append(tool)

    def register_router(self, router, prefix=None):
        self.routers.append((router, prefix))

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)

    def emit(self, topic, data=None):
        pass


def _plugin():
    if PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[PKG] = module
        spec.loader.exec_module(module)
    return sys.modules[PKG]


def collect_tools(config):
    """Register the real plugin (on a throwaway database) and take every schema from it."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"db_path": str(Path(tmp) / "eval.db"), "workspace_dir": tmp, **config}
        registry = _Registry(cfg)
        _plugin().register(registry)
        tools = []
        for tool in registry.tools:
            schema = tool.tool_call_schema
            schema = schema if isinstance(schema, dict) else schema.model_json_schema()
            tools.append(function_tool(tool.name, tool.description, schema))
    for name, props, description in DECOYS:
        tools.append(
            function_tool(
                name,
                description,
                {
                    "type": "object",
                    "properties": {k: {"type": t} for k, t in props.items()},
                    "required": [next(iter(props))],
                },
            )
        )
    return sorted(tools, key=lambda t: t["function"]["name"])


def fake_pick(prompt, tools):
    """Deterministic and independent of expectations; deliberately imperfect."""
    choices = [None, *(t["function"]["name"] for t in tools)]
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    return choices[int.from_bytes(digest[:8], "big") % len(choices)]


def model_pick(client, base_url, api_key, model, prompt, tools):
    response = client.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0,
            "tool_choice": "auto",
            "tools": tools,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        },
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    if calls:
        return calls[0]["function"]["name"]
    if message.get("function_call"):  # older OpenAI-compatible servers
        return message["function_call"]["name"]
    return None


def endpoint_from_env():
    base_url, api_key, model = (os.environ.get(k, "") for k in ("EVAL_BASE_URL", "EVAL_API_KEY", "EVAL_MODEL"))
    if not all((base_url, api_key, model)):
        raise ValueError("live runs require EVAL_BASE_URL, EVAL_API_KEY and EVAL_MODEL")
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("EVAL_BASE_URL must be an HTTP(S) URL with a hostname")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("EVAL_BASE_URL must not contain credentials, a query or a fragment")
    return base_url, api_key, model


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="dry-run")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=PROMPTS_PATH.parent / "results")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.label):
        parser.error("label must be a filename-safe identifier")
    config = {}
    for item in args.config:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            parser.error("config must be KEY=VALUE (repeat --config for multiple keys)")
        config[key] = yaml.safe_load(value)
    prompts, aliases = load_prompts()
    tools = collect_tools(config)
    if args.dry_run:
        picks = {p["id"]: fake_pick(p["prompt"], tools) for p in prompts}
        model = "deterministic-fake"
    else:
        try:
            base_url, api_key, model = endpoint_from_env()
        except ValueError as exc:
            parser.error(str(exc))
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            picks = {p["id"]: model_pick(client, base_url, api_key, model, p["prompt"], tools) for p in prompts}
    result = score(picks, prompts, aliases)
    result.update(
        {
            "label": args.label,
            "dry_run": args.dry_run,
            "model": model,
            "tools": tools,
            "turn_only_tools": sorted(TURN_ONLY),
            "aliases": aliases,
            "prompt_set_sha256": hashlib.sha256(PROMPTS_PATH.read_bytes()).hexdigest(),
            "system_prompt": SYSTEM_PROMPT,
        }
    )
    result["config_keys"] = sorted(config)  # config can hold secrets: keys only, never values
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"{args.label}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{'Capability':<20} {'Correct':>9} {'Accuracy':>10}")
    for capability, stats in [("overall", result["overall"]), *sorted(result["per_capability"].items())]:
        print(f"{capability:<20} {stats['correct']:>3}/{stats['total']:<5} {stats['accuracy']:>9.1%}")
    # Every prompt is an operator message (chat and trap rows included), and a turn-only pick is
    # wrong on any of them; count them all, and the tool-expecting rows separately.
    turn_only = sum(r["turn_only_pick"] for r in result["picks"])
    on_tool_rows = sum(r["turn_only_pick"] for r in result["picks"] if r["expect"] != "none")
    print(f"turn-only picks: {turn_only} (on prompts that expect a tool: {on_tool_rows})")
    print(f"{'Synthetic dry-run' if args.dry_run else 'Model evaluation'} results: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
