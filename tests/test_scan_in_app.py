"""0.16.0: scanning lands in the app.

On an iPhone a label read by the Camera app always opens in Safari (iOS hands no link to a
home-screen web app, and the two keep separate storage, so Safari isn't signed in). So the view
scans its own labels, and the public hop answers with a landing page instead of a bare 302: a
signed-in browser goes straight on, anyone else is told how to open the label in the app."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import types

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.labels import CONSOLE_TOKEN_KEYS, LANDING_HEADERS, landing_page
from inventory_plugin.view import PAGE

TARGET = "https://hub.example.ts.net/app/agent/merchantAgent-6604/?plugin=inventory&vp=bin%3DB07"


@pytest.fixture
def host(monkeypatch):
    """A fake protoAgent host: `graph.sdk` with whatever helpers a test gives it."""

    def install(**fns):
        graph, sdk = types.ModuleType("graph"), types.ModuleType("graph.sdk")
        for k, v in fns.items():
            setattr(sdk, k, v)
        graph.sdk = sdk
        monkeypatch.setitem(sys.modules, "graph", graph)
        monkeypatch.setitem(sys.modules, "graph.sdk", sdk)
        return sdk

    monkeypatch.delenv("A2A_PUBLIC_URL", raising=False)
    monkeypatch.delitem(sys.modules, "graph", raising=False)
    monkeypatch.delitem(sys.modules, "graph.sdk", raising=False)
    return install


def _client(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app)


# ── the landing page ────────────────────────────────────────────────────────────────
def test_the_keys_are_the_consoles_own():
    # protoAgent apps/web/src/lib/credentials.ts: DEVICE_TOKEN_KEY, OPERATOR_TOKEN_KEY
    assert CONSOLE_TOKEN_KEYS == ("protoagent.deviceToken", "protoagent.authToken")


def test_the_page_names_the_label_and_how_to_open_it_in_the_app():
    page = landing_page("bin", "B07", TARGET)
    assert ">B07<" in page and "Bin label" in page
    assert "Scan label" in page and "Home Screen" in page and "Safari" in page
    assert 'href="' + TARGET.replace("&", "&amp;") + '"' in page  # the plain fallback link (no JS needed)
    assert "Sign in here instead" in page


def test_the_page_sends_nothing_and_never_puts_a_token_in_a_url():
    page = landing_page("item", "NH-SH-Harrows", TARGET)
    script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    for bad in (
        "fetch(",
        "XMLHttpRequest",
        "sendBeacon",
        "postMessage",
        "document.cookie",
        "+localStorage",
        "?token",
        "&token",
    ):
        assert bad not in script
    assert script.count("localStorage.getItem(") == 1 and "location.replace(" in script


def test_a_hostile_target_cannot_break_out_of_the_script():
    evil = 'https://x.example/?a=</script><script>alert(1)</script>&b=" '
    page = landing_page("bin", "B01", evil)
    script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    assert "</script" not in script and "<script" not in script and " " not in script
    assert page.count("<script>") == 1


def _run(page: str, token: str | None) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    store = json.dumps({"protoagent.deviceToken": token} if token else {})
    stub = (
        "const calls=[];const store=" + store + ";"
        "globalThis.localStorage={getItem:(k)=>Object.prototype.hasOwnProperty.call(store,k)?store[k]:null};"
        "globalThis.document={documentElement:{className:''}};"
        "globalThis.location={replace:(u)=>calls.push(u)};"
    )
    out = subprocess.run(
        [node, "-e", stub + script + ";console.log(JSON.stringify({calls, cls: document.documentElement.className}))"],
        capture_output=True,
        text=True,
        check=True,
    )
    res = json.loads(out.stdout)
    return [res["calls"], res["cls"]]


def test_a_signed_in_browser_goes_straight_on():
    calls, cls = _run(landing_page("bin", "B07", TARGET), token="tok-123")
    assert calls == [TARGET] and cls == "go"  # hidden while it leaves: no flash of the guidance


def test_a_browser_that_isnt_signed_in_stays_on_the_guidance():
    calls, cls = _run(landing_page("bin", "B07", TARGET), token=None)
    assert calls == [] and cls == ""


def test_the_hops_serve_the_landing_page_with_private_headers(host, registry):
    host(view_url=lambda plugin_id, view_id=None, params=None: TARGET)
    c = _client(registry)
    for path in ("/plugins/inventory/b/B07", "/plugins/inventory/i/NH-SH-Harrows"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        for k, v in LANDING_HEADERS.items():
            assert r.headers[k.lower()] == v
        assert "location.replace(" in r.text


def test_the_landing_reads_no_data(host, registry):
    """Same page for an id that exists and one that doesn't (the hop reads nothing)."""
    host(view_url=lambda plugin_id, view_id=None, params=None: "https://hub.example.ts.net/app/?vp=" + str(params))
    c = _client(registry)
    assert c.post("/api/plugins/inventory/bins", json={"count": 1}).status_code == 200  # B01 exists
    a, b = c.get("/plugins/inventory/b/B01"), c.get("/plugins/inventory/b/B98")
    assert a.text.replace("B01", "Bxx") == b.text.replace("B98", "Bxx")


# ── the in-app scanner ────────────────────────────────────────────────────────────────
def test_scan_label_sits_beside_scan_a_box_in_the_header_and_on_the_phone_bar():
    head = PAGE[PAGE.index('<div class="pl-panel-header__actions anchor">') :][:600]
    assert 'data-act="scanlabel">Scan label</button>' in head and 'data-act="scan">Scan a box</button>' in head
    bar = PAGE[PAGE.index('<div class="scanbar">') :][:400]
    assert 'data-act="scanlabel">Scan label' in bar and 'data-act="scan">Scan a box' in bar
    assert ">Scan</button>" not in PAGE  # the old bare label is gone everywhere


def test_the_scanner_opens_items_and_bins_and_never_navigates():
    fn = PAGE[PAGE.index("function slApply(lab)") :][:1400]
    assert "openItem(it.id)" in fn and "openBin(lab.id)" in fn
    assert "location" not in fn and "window.open" not in fn
    assert "parseLabel(text)" in PAGE[PAGE.index("async function slScan") :][:600]
    hit = PAGE[PAGE.index("async function camHit") :][:1400]
    assert "const lab = parseLabel(text);" in hit  # parsed once, before any context sees it
    assert 'CAM.ctx === "scanlabel"' in hit and "slApply(lab)" in hit
    assert 'data-cap === "sl" ? slScan(f)' in PAGE.replace("t.dataset.cap", "data-cap")
