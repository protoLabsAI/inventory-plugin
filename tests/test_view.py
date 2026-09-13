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
    """Tight enough that a page with a hand-rolled theme map, a message sniffer, a hardcoded
    host or an un-kitted fetch fails — the earlier presence-only version let all four through."""
    from inventory_plugin.view import PAGE, VIEW_PATH, build_view_router

    assert 'location.pathname.split("/plugins/")[0]' in PAGE  # rule 3: slug-aware base
    assert 'BASE+"/_ds/plugin-kit.css"' in PAGE and 'import(BASE + "/_ds/plugin-kit.js")' in PAGE  # rule 4
    assert 'const API = "/api/plugins/inventory"' in PAGE and "kit.initPluginView(boot)" in PAGE
    # rule 2: EVERY data call is kit.apiFetch(API + …); the only bare fetch( is the no-kit shim
    assert not re.search(r"kit\.apiFetch\((?!API \+)", PAGE)
    assert len(re.findall(r"(?<![\w.])fetch\(", PAGE)) == 1 and "fetch(BASE + p, i)" in PAGE
    style = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)
    assert not re.search(r"--pl-[\w-]+\s*:", style)  # tokens are consumed, never defined
    assert not re.search(r"\bonmessage\b|addEventListener\(\s*[\"']message", PAGE)  # the kit owns the handshake
    assert not re.search(r"(fetch|import)\(\s*[\"']https?://|(src|href)=[\"']https?://", PAGE)  # nothing hardcoded
    # the public router serves exactly the page, at exactly the manifest path
    router = build_view_router({})
    assert [(r.path, set(r.methods)) for r in router.routes] == [(VIEW_PATH, {"GET"})]
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert manifest["views"][0]["path"] == "/plugins/inventory" + VIEW_PATH


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


def test_the_pure_helpers_under_node():
    """Run the real module under node with a stub DOM: money formatting (signs, nulls), the
    escaper, and the field builder against a hostile value. Skipped where node is absent."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    module = re.search(r'<script type="module">(.*?)</script>', PAGE, re.S).group(1)
    harness = (
        "globalThis.BASE = ''; globalThis.location = { pathname: '/plugins/inventory/view' };\n"
        "const el = () => new Proxy({}, { get: (t, k) => (k === 'addEventListener' || k === 'removeEventListener' || k === 'appendChild' || k === 'remove' || k === 'focus' ? () => {} : k === 'querySelectorAll' ? () => [] : k === 'querySelector' || k === 'closest' ? () => el() : k === 'classList' ? { toggle() {}, add() {}, remove() {} } : k === 'dataset' ? {} : k === 'hidden' ? true : ''), set: () => true });\n"
        "globalThis.document = { querySelector: () => el(), querySelectorAll: () => [], getElementById: () => el(), createElement: () => el(), addEventListener() {}, removeEventListener() {}, body: el() };\n"
        "globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => 0; globalThis.fetch = async () => ({ ok: false, status: 0, statusText: 'stub', text: async () => '' });\n"
    )
    probe = (
        "\nconsole.log(JSON.stringify({ neg: fmt(-12.5), pos: fmt(1234.5), nul: fmt(null), zero: fmt(0), sneg: signed(-3), spos: signed(3), snul: signed(null),"
        " esc: esc('<a href=\"x\">&\\'</a>'), field: field('name', 'Name', '\"><img src=x onerror=alert(1)>'), opt: field('lot_id', 'Lot', '', { type: 'select', options: [['\"><b>', 'x</option><script>']] }),"
        " md: mdList([{ name: 'Reikland  Reavers\\nHuman Team', category: 'Blood Bowl 2016 Split', target: 57.69 }, { name: 'Loose dice', category: '', target: null }, { name: 'Bundle', category: 'Misc', target: 1234.5 }]) }));\n"
    )
    r = subprocess.run(
        [node, "--input-type=module"], input=harness + module + probe, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr[:1200]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["neg"] == "-$12.50" and out["pos"] == "$1,234.50" and out["nul"] == "—" and out["zero"] == "$0.00"
    assert out["sneg"] == "-$3.00" and out["spos"] == "+$3.00" and out["snul"] == "—"
    assert out["esc"] == "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;"
    assert "<img" not in out["field"] and "&quot;&gt;&lt;img" in out["field"]
    assert "<b>" not in out["opt"] and "<script>" not in out["opt"]
    assert (
        out["md"]
        == "- Reikland Reavers Human Team — Blood Bowl 2016 Split — $57.69\n- Loose dice — —\n- Bundle — Misc — $1,234.50"
    )


def test_the_page_has_multiselect_and_copy():
    from inventory_plugin.view import PAGE

    for needle in (
        'id="sel-all"',
        'data-sel="',
        'id="copy-md"',
        "navigator.clipboard.writeText",
        'execCommand("copy")',
        "copySelected()",
    ):
        assert needle in PAGE, needle
