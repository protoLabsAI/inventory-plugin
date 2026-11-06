"""The reference docs must describe the code that exists.

Docs drift because nothing fails when they do. These tests fail. They check COVERAGE, not prose:
every route, tool and setting the code exposes appears in its reference page, and no page names
one that no longer exists. What each one means is a human's job. (The same guard as
projectBoard-plugin's tests/test_docs_reference.py.)
"""

from __future__ import annotations

import ast
import pathlib
import re

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DOCS = _ROOT / "docs"
_ROW = re.compile(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]+)` \|", re.M)


def _decorated_routes(path: pathlib.Path, namespace: dict) -> set[tuple[str, str]]:
    """(VERB, path) for every ``@r.<verb>(...)`` route in a module, read from the AST so a new
    decorator can't slip past a text search. A path built from a constant (``LABEL_PREFIX +
    "/{bin_id}"``) is evaluated against ``namespace``."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            verb = getattr(dec.func, "attr", "")
            owner = getattr(getattr(dec.func, "value", None), "id", "")
            if verb in ("get", "post", "put", "patch", "delete") and owner == "r":
                route = eval(compile(ast.Expression(dec.args[0]), str(path), "eval"), {}, namespace)  # noqa: S307
                found.add((verb.upper(), route))
    return found


def _operator_routes() -> set[tuple[str, str]]:
    return _decorated_routes(_ROOT / "api.py", {})


def _public_routes() -> set[tuple[str, str]]:
    from inventory_plugin.labels import ITEM_PREFIX, LABEL_PREFIX
    from inventory_plugin.sheetlinks import SHEET_PREFIX
    from inventory_plugin.view import VIEW_PATH

    ns = {
        "VIEW_PATH": VIEW_PATH,
        "LABEL_PREFIX": LABEL_PREFIX,
        "ITEM_PREFIX": ITEM_PREFIX,
        "SHEET_PREFIX": SHEET_PREFIX,
    }
    return _decorated_routes(_ROOT / "view.py", ns)


def _tools() -> set[str]:
    names = set()
    for node in ast.walk(ast.parse((_ROOT / "tools.py").read_text())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            ast.unparse(d).split("(")[0] == "tool" for d in node.decorator_list
        ):
            names.add(node.name)
    return names


def _manifest_keys() -> set[str]:
    manifest = yaml.safe_load((_ROOT / "protoagent.plugin.yaml").read_text())
    keys = set(manifest.get("config") or {})
    keys |= {f["key"] for f in manifest.get("settings") or [] if isinstance(f, dict) and "key" in f}
    return keys


def _doc_rows(page: str) -> set[tuple[str, str]]:
    return set(_ROW.findall((_DOCS / page).read_text()))


def test_the_code_exposes_what_these_tests_think_it_does():
    # A guard that reads nothing passes everything. Pin the extractors to real numbers.
    assert len(_operator_routes()) >= 60
    assert ("GET", "/b/{bin_id}") in _public_routes()
    assert "inventory_mark_sold" in _tools() and len(_tools()) >= 25
    assert {"db_path", "stale_price_days", "aging_days_1"} <= _manifest_keys()


def test_every_route_is_in_the_api_reference_and_none_that_was_removed():
    documented = _doc_rows("api.md")
    live = _operator_routes() | _public_routes()
    missing = sorted(f"{v} {p}" for v, p in live if (v, p) not in documented)
    stale = sorted(f"{v} {p}" for v, p in documented if (v, p) not in live)
    assert not missing, f"docs/api.md is missing {missing}: add the row, with what the route is for"
    assert not stale, f"docs/api.md documents routes that no longer exist: {stale}"


def test_every_agent_tool_is_in_the_tools_reference_and_none_that_was_removed():
    doc = (_DOCS / "tools.md").read_text()
    documented = set(re.findall(r"^\| `(inventory_[a-z_]+)` \|", doc, re.M))
    missing = sorted(_tools() - documented)
    stale = sorted(documented - _tools())
    assert not missing, f"docs/tools.md is missing {missing}: every agent tool needs a row"
    assert not stale, f"docs/tools.md documents tools that no longer exist: {stale}"


def test_every_setting_is_in_the_configuration_reference_and_none_that_was_removed():
    doc = (_DOCS / "configuration.md").read_text()
    documented = set(re.findall(r"^\| `([a-z_0-9]+)` \| `", doc, re.M))
    missing = sorted(_manifest_keys() - documented)
    stale = sorted(documented - _manifest_keys())
    assert not missing, f"docs/configuration.md is missing {missing}: add key, default and effect"
    assert not stale, f"docs/configuration.md documents keys the manifest no longer has: {stale}"


def test_the_configuration_defaults_match_the_manifest():
    manifest = yaml.safe_load((_ROOT / "protoagent.plugin.yaml").read_text())["config"]
    doc = (_DOCS / "configuration.md").read_text()
    for key, default in manifest.items():
        shown = re.search(rf"^\| `{key}` \| `([^`]*)` \|", doc, re.M).group(1)
        expected = '""' if default == "" else str(default).lower() if isinstance(default, bool) else str(default)
        assert shown.strip('"') == expected.strip('"'), f"{key}: docs say {shown}, manifest says {default!r}"


def test_the_layout_table_names_only_files_that_exist():
    body = (_DOCS / "development.md").read_text().split("## Layout", 1)[1].split("\n## ", 1)[0]
    names = re.findall(r"^\| `([A-Za-z0-9_./-]+)` \|", body, re.M)
    assert names, "docs/development.md lost its Layout table"
    gone = [n for n in names if not (_ROOT / n).exists()]
    assert not gone, f"docs/development.md's Layout table names files that don't exist: {gone}"


def test_every_relative_link_in_the_docs_resolves():
    pages = [_ROOT / "README.md", *sorted(_DOCS.rglob("*.md")), _ROOT / "skills" / "inventory-ops" / "SKILL.md"]
    broken = []
    for page in pages:
        for target in re.findall(r"\]\(([^)\s]+)\)", page.read_text()):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            path = (page.parent / target.split("#", 1)[0]).resolve()
            if not path.exists():
                broken.append(f"{page.relative_to(_ROOT)} → {target}")
    assert not broken, f"broken relative links: {broken}"


def test_the_install_steps_name_real_cli_subcommands():
    # A setup step that can't run is worse than a missing one. Real verbs of `python -m server
    # plugin` (protoAgent docs/guides/cli.md: install, list, update, uninstall, sync).
    text = (_ROOT / "README.md").read_text() + (_DOCS / "install.md").read_text()
    claimed = set(re.findall(r"python -m server plugin ([a-z][a-z-]*)", text))
    assert claimed and claimed <= {"install", "list", "update", "uninstall", "sync"}, claimed
