"""0.10.1: a label sheet opens in the operator's browser. The gated /labels/sheet route parks the
rendered sheet under a short-lived token, and the public GET /plugins/inventory/sheet/<token> serves
it, so the desktop app can hand the link to the system browser (it ignores a blank window)."""

from __future__ import annotations

import json
import shutil
import subprocess

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.sheetlinks import SHEET_HEADERS, SHEETS, SheetLinks

HUB = "https://hub.example.ts.net/agents/merchantAgent-6604"
API = "/api/plugins/inventory"


@pytest.fixture(autouse=True)
def _fresh_sheets():
    SHEETS.clear()
    yield
    SHEETS.clear()


def _client(registry, monkeypatch):
    monkeypatch.setenv("A2A_PUBLIC_URL", HUB)
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ── the store ─────────────────────────────────────────────────────────────────────
def test_a_sheet_lives_for_its_ttl_and_can_be_read_more_than_once():
    clock = Clock()
    links = SheetLinks(ttl_s=900, clock=clock)
    token = links.put("<p>sheet</p>")
    assert len(token) >= 43  # 32 random bytes, url-safe
    assert links.get(token) == ("<p>sheet</p>", False)
    clock.t += 899
    assert links.get(token) == ("<p>sheet</p>", False)  # print preview reloads are fine
    clock.t += 1
    assert links.get(token) == (None, True)  # just expired
    assert links.get(token) == (None, False)  # and then forgotten


def test_the_oldest_sheet_goes_first_past_the_cap_and_expired_ones_are_purged():
    clock = Clock()
    links = SheetLinks(ttl_s=60, cap=3, clock=clock)
    tokens = [links.put(f"<p>{i}</p>") for i in range(4)]
    assert links.get(tokens[0]) == (None, False)  # evicted
    assert [links.get(t)[0] for t in tokens[1:]] == ["<p>1</p>", "<p>2</p>", "<p>3</p>"]
    clock.t += 61
    links.put("<p>new</p>")
    assert len(links) == 1  # the stale three were purged on the way in


@pytest.mark.parametrize("bad", ["", "short", "x" * 65, "a/b" + "c" * 40, "../../api/plugins/inventory/items"])
def test_a_token_that_was_never_issued_reads_as_unknown(bad):
    assert SheetLinks().get(bad) == (None, False)


# ── the routes ────────────────────────────────────────────────────────────────────
def test_a_bin_sheet_link_serves_the_same_sheet_with_no_store_headers(registry, monkeypatch):
    c = _client(registry, monkeypatch)
    c.post(API + "/bins", json={"count": 2})
    r = c.post(API + "/labels/sheet", json={"all": True}).json()
    assert r["link"].startswith("/plugins/inventory/sheet/")
    for _ in range(2):  # reload, print preview
        got = c.get(r["link"])
        assert got.status_code == 200 and got.text == r["html"]
        for k, v in SHEET_HEADERS.items():
            assert got.headers[k] == v
    # opening the link records nothing more: the sheet was marked printed once, at mint
    audit = c.get(API + "/audit?limit=50").json()["audit"]
    assert sum(1 for a in audit if a["entity"] == "bin" and a["action"] == "labels") == 2


def test_an_item_sheet_link_serves_the_avery_sheet_and_marks_each_item_once(registry, monkeypatch):
    c = _client(registry, monkeypatch)
    c.put(API + "/lots/L", json={"name": "L"})
    for iid in ("A-1", "A-2"):
        c.post(API + "/items", json={"id": iid, "name": "Item " + iid, "lot_id": "L"})
    r = c.post(API + "/labels/sheet", json={"items": ["A-1", "A-2"]}).json()
    got = c.get(r["link"])
    assert got.status_code == 200 and got.text == r["html"]
    assert '<div class="iid">A-1</div>' in got.text and got.text.count("<svg") == 2  # one QR per item
    c.get(r["link"])
    audit = c.get(API + "/audit?limit=50").json()["audit"]
    assert sum(1 for a in audit if a["entity"] == "item" and a["action"] == "labels") == 2


def test_an_unknown_or_expired_link_answers_a_short_page_with_no_data(registry, monkeypatch):
    c = _client(registry, monkeypatch)
    c.post(API + "/bins", json={"count": 1, "label": "Secret shelf"})
    r = c.post(API + "/labels/sheet", json={"all": True}).json()
    unknown = c.get("/plugins/inventory/sheet/" + "A" * 43)
    assert unknown.status_code == 404 and "has expired" in unknown.text
    clock = Clock()
    monkeypatch.setattr(SHEETS, "_clock", clock)
    SHEETS.clear()
    token = SHEETS.put(r["html"])
    clock.t += 15 * 60
    gone = c.get("/plugins/inventory/sheet/" + token)
    assert gone.status_code == 410 and "Print labels again" in gone.text
    for page in (unknown, gone):
        assert "Secret shelf" not in page.text and "B01" not in page.text and "<svg" not in page.text
        assert page.headers["Cache-Control"] == "no-store"


def test_only_the_gated_router_mints_and_the_public_one_only_reads():
    from inventory_plugin.api import build_data_router
    from inventory_plugin.view import build_view_router

    data = {(r.path, m) for r in build_data_router(None, {}).routes for m in r.methods}
    assert ("/labels/sheet", "POST") in data  # mounted under /api/plugins/inventory: the host's bearer gate
    public = {(r.path, m) for r in build_view_router({}).routes for m in r.methods}
    assert ("/sheet/{token}", "GET") in public and not any(m != "GET" for _, m in public)


# ── the view ──────────────────────────────────────────────────────────────────────
def _open_sheet(with_tab: bool) -> dict:
    """Run the view's real sheetTab/openSheet under node with a tiny stub of what they touch."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    src = PAGE[PAGE.index("  function sheetTab()") : PAGE.index("  async function labelsDialog(")]
    env = """
const calls = { opened: [], replaced: [], toasts: [] };
const result = { innerHTML: "" }, ok = { removed: false, remove() { this.removed = true; } }, x = { textContent: "Cancel" };
const BASE = "/agents/merchantAgent-6604";
const location = { href: "https://hub.example.ts.net/agents/merchantAgent-6604/plugins/inventory/view" };
const tab = { opener: "parent", location: { replace: (u) => calls.replaced.push(u) } };
const window = { open: (...a) => { calls.opened.push(a); return a[0] === "" ? (WITH_TAB ? tab : null) : null; } };
const document = { querySelector: () => x };
const $ = (sel) => (sel === "#dlg-result" ? result : sel === "#dlg-ok" ? ok : null);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const toast = (m) => calls.toasts.push(m);
const refresh = async () => {};
""".replace("WITH_TAB", "true" if with_tab else "false")
    probe = """
const w = sheetTab();
openSheet(w, { link: "/plugins/inventory/sheet/TOKEN" }, 3).then((closed) => {
  console.log(JSON.stringify({ closed, calls, html: result.innerHTML, okGone: ok.removed, x: x.textContent, opener: tab.opener }));
});
"""
    out = subprocess.run([node, "-e", env + src + probe], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[:800]
    return json.loads(out.stdout.strip().splitlines()[-1])


URL = "https://hub.example.ts.net/agents/merchantAgent-6604/plugins/inventory/sheet/TOKEN"


def test_on_the_web_the_tab_opened_in_the_click_is_sent_to_the_sheet_link():
    got = _open_sheet(with_tab=True)
    assert got["calls"]["opened"] == [["", "_blank"]]  # the blank tab, inside the click
    assert got["calls"]["replaced"] == [URL] and got["opener"] is None
    assert got["closed"] is False and got["okGone"] is True and got["x"] == "Done"
    assert 'href="' + URL + '"' in got["html"] and "in your browser" in got["html"]


def test_in_the_desktop_app_the_link_itself_is_opened_for_the_system_browser():
    got = _open_sheet(with_tab=False)  # the desktop shell ignores the blank window
    assert got["calls"]["opened"] == [["", "_blank"], [URL, "_blank", "noopener"]]
    assert got["calls"]["replaced"] == []
    assert 'href="' + URL + '"' in got["html"] and "open the label sheet" in got["html"]
    assert got["calls"]["toasts"] == ["Opening 3 labels in your browser"]


def test_the_blank_tab_write_is_gone():
    from inventory_plugin.view import PAGE

    assert "document.write" not in PAGE and "w.document" not in PAGE
    assert "new Blob([r.html]" not in PAGE
