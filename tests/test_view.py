"""The console view: served at the path the manifest declares, on the public prefix, and
four-rules-compliant (the artifact-plugin lesson: test the ACTUAL registered path)."""

from __future__ import annotations

import re
from pathlib import Path

import inventory_plugin
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


def _app(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app)


def test_the_declared_view_path_is_served_publicly_and_not_under_api(registry):
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    (view,) = manifest["views"]
    assert view["path"].startswith("/plugins/inventory/")
    c = _app(registry)
    r = c.get(view["path"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>Inventory</title>" in r.text
    assert c.get("/api" + view["path"]).status_code == 404  # the page is not a data route


def test_the_page_follows_the_four_rules():
    from inventory_plugin.view import PAGE

    assert 'location.pathname.split("/plugins/")[0]' in PAGE  # rule 3: slug-aware base
    assert 'BASE+"/_ds/plugin-kit.css"' in PAGE and 'import(BASE + "/_ds/plugin-kit.js")' in PAGE  # rule 4
    assert "kit.apiFetch(" in PAGE and 'const API = "/api/plugins/inventory"' in PAGE  # rule 2: gated data via the kit
    assert "kit.initPluginView(boot)" in PAGE
    assert not re.search(r":root\s*\{[^}]*--pl-", PAGE)  # no hand-rolled theme map
    assert 'addEventListener("message"' not in PAGE  # the kit owns the handshake
    assert "http://localhost" not in PAGE


def test_every_text_the_page_renders_from_data_is_escaped():
    """A grep-level guard: data fields are only ever interpolated through esc()/fmt()."""
    from inventory_plugin.view import PAGE

    body = PAGE.split("function renderItems", 1)[1]
    for raw in ("it.name +", "it.notes +", "it.price_basis +", "l.name +", "a.changes +"):
        assert raw not in body, f"unescaped interpolation: {raw}"


def test_the_module_script_parses(tmp_path):
    """A single bad quote in a template string kills the whole module before any wiring
    runs — the page renders its chrome and nothing else, with no error banner. Node's
    parser is the only honest check; skipped where node is not installed."""
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    module = re.search(r'<script type="module">(.*?)</script>', PAGE, re.S).group(1)
    head = re.search(r"<script>(.*?)</script>", PAGE, re.S).group(1)
    for label, src, args in (("module", module, ["--input-type=module", "--check"]), ("head", head, ["--check", "-"])):
        r = subprocess.run([node, *args], input=src, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"{label} script does not parse:\n{r.stderr[:800]}"
