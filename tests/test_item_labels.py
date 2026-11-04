"""Item labels (0.10.0): the public /i/<item> hop, the Avery 5160 sticker sheet, and the view's
?item= boot and entry points. Bin labels live in tests/test_labels.py."""

from __future__ import annotations

import re
import sys
import types

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

HUB = "https://hub.example.ts.net/agents/merchantAgent-6604"
API = "/api/plugins/inventory"


@pytest.fixture
def host(monkeypatch):
    """A fake protoAgent host: install `graph.sdk` with whatever helpers a test gives it."""

    def install(**fns):
        graph = types.ModuleType("graph")
        sdk = types.ModuleType("graph.sdk")
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


# ── the public hop ────────────────────────────────────────────────────────────────
def test_item_hop_redirects_to_the_console_link_and_keeps_the_ids_case(host, registry):
    calls = []

    def view_url(plugin_id, view_id=None, params=None):
        calls.append((plugin_id, view_id, params))
        return "https://hub.example.ts.net/app/agent/merchantAgent-6604/?plugin=inventory&vp=item%3DNH-SH-Harrows"

    host(view_url=view_url)
    r = _client(registry).get("/plugins/inventory/i/NH-SH-Harrows", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("vp=item%3DNH-SH-Harrows")
    assert r.headers["cache-control"] == "no-store"
    assert calls == [("inventory", None, {"item": "NH-SH-Harrows"})]  # item ids are case-sensitive


def test_item_hop_on_an_older_host_explains_and_links_the_hub_console(host, registry, monkeypatch):
    host(console_url=lambda: "http://127.0.0.1:7881")  # 0.226: no view_url
    monkeypatch.setenv("A2A_PUBLIC_URL", HUB)
    r = _client(registry).get("/plugins/inventory/i/flash-gitz-nib")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "needs a protoAgent update" in r.text and ">flash-gitz-nib<" in r.text and "Stock" in r.text
    assert 'href="https://hub.example.ts.net/app/agent/merchantAgent-6604/"' in r.text


@pytest.mark.parametrize("bad", ["<script>", "a b", "..", "-x", "x" * 65, "a%2F..", "%00"])
def test_item_hop_refuses_anything_that_is_not_an_item_id(host, registry, bad):
    host(view_url=lambda *a, **k: "https://evil.example/")
    r = _client(registry).get("/plugins/inventory/i/" + bad, follow_redirects=False)
    assert r.status_code == 404
    assert "evil" not in r.headers.get("location", "") and "<script>" not in r.text


def test_item_hop_reads_no_data_and_needs_no_bearer(host, registry):
    """Public by design: an item that exists and one that doesn't answer the same."""
    c = _client(registry)
    c.put(API + "/lots/L", json={"name": "L"})  # data routes are open in the test app; the hop never reads them
    c.post(API + "/items", json={"id": "REAL-1", "name": "Secret name", "lot_id": "L"})
    a, b = c.get("/plugins/inventory/i/REAL-1"), c.get("/plugins/inventory/i/NOPE-1")
    assert a.status_code == b.status_code == 200
    assert "Secret name" not in a.text
    assert a.text.replace("REAL-1", "X") == b.text.replace("NOPE-1", "X")


def test_item_hop_failing_view_url_falls_back_instead_of_breaking(host, registry):
    def boom(*a, **k):
        raise ValueError("refused")

    host(view_url=boom)
    r = _client(registry).get("/plugins/inventory/i/A1", follow_redirects=False)
    assert r.status_code == 200 and "needs a protoAgent update" in r.text


# ── the sheet ─────────────────────────────────────────────────────────────────────
def _with_items(registry, monkeypatch, n=3):
    monkeypatch.setenv("A2A_PUBLIC_URL", HUB)
    c = _client(registry)
    c.put(API + "/lots/L", json={"name": "L"})
    c.post(API + "/bins", json={"count": 1})
    for k in range(n):
        body = {"id": f"IT-{k:02d}", "name": f"Unit <{k}> " + "very long name " * 3, "lot_id": "L", "condition": "NoS"}
        assert c.post(API + "/items", json=body).status_code == 200
    c.post(API + "/bins/move", json={"item_ids": ["IT-00"], "bin_id": "B01"})
    return c


def _boxes(html):
    """(left, top) in inches of every label box, blanks included, in document order."""
    return [
        (float(a), float(b))
        for a, b in re.findall(r'class="label(?: blank)?" style="left:([\d.]+)in;top:([\d.]+)in"', html)
    ]


def test_item_sheet_is_avery_5160_with_qr_name_condition_bin_and_id(host, registry, monkeypatch):
    from inventory_plugin.labels import item_label_url, qr_svg

    c = _with_items(registry, monkeypatch, 3)
    r = c.post(API + "/labels/sheet", json={"items": ["IT-00", "IT-01", "IT-02"]})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["kind"] == "items" and got["ids"] == ["IT-00", "IT-01", "IT-02"]
    html = got["html"]
    assert "@page{size:8.5in 11in;margin:0}" in html and "Avery 5160" in html
    assert html.count('class="label"') == 3 and html.count("<svg") == 3
    # every QR encodes exactly that item's hop address
    for iid in ("IT-00", "IT-01", "IT-02"):
        assert qr_svg(item_label_url(HUB, iid)) in html
    assert item_label_url(HUB, "IT-00") == HUB + "/plugins/inventory/i/IT-00"
    assert "Unit &lt;0&gt;" in html and "Unit <0>" not in html  # escaped
    assert "NoS · B01" in html and ">IT-00<" in html
    assert "$" not in html  # no price on a sticker
    assert _boxes(html)[:3] == [(0.1875, 0.5), (2.9375, 0.5), (5.6875, 0.5)]
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")  # nothing to fetch


def test_item_sheet_geometry_fills_exactly_one_letter_page_of_thirty():
    from inventory_plugin.labels import AVERY_5160 as g
    from inventory_plugin.labels import item_sheet_html

    items = [{"id": f"I{k}", "name": "n"} for k in range(31)]
    html = item_sheet_html(items, HUB)
    assert html.count('class="page"') == 2
    boxes = _boxes(html)
    assert len(boxes) == 31
    first_page = boxes[:30]
    assert first_page[29] == (0.1875 + 2 * 2.75, 0.5 + 9 * 1.0)  # bottom-right label
    assert boxes[30] == (0.1875, 0.5)  # page 2 starts top-left again
    # the grid closes exactly on US Letter
    assert g["side"] * 2 + g["w"] * 3 + (g["pitch_x"] - g["w"]) * 2 == pytest.approx(8.5)
    assert g["top"] * 2 + g["h"] * 10 == pytest.approx(11.0)


def test_start_at_skips_used_positions_on_the_first_sheet_only():
    from inventory_plugin.labels import item_sheet_html

    html = item_sheet_html([{"id": f"I{k}", "name": "n"} for k in range(30)], HUB, start_at=29)
    assert html.count('class="label blank"') == 28 and html.count('class="label"') == 30
    assert html.count('class="page"') == 2  # 28 blanks + 30 labels = 58 slots
    assert "Starting at label 29" in html


def test_sheet_marks_items_printed_audits_and_mark_false_records_nothing(host, registry, monkeypatch):
    c = _with_items(registry, monkeypatch, 2)
    assert c.post(API + "/labels/sheet", json={"items": ["IT-00"], "mark": False}).status_code == 200
    assert c.get(API + "/items/IT-00").json()["item"]["labels_printed_on"] == ""
    assert c.post(API + "/labels/sheet", json={"items": ["IT-00", "IT-00", "IT-01"]}).json()["ids"] == [
        "IT-00",
        "IT-01",
    ]
    assert c.get(API + "/items/IT-01").json()["item"]["labels_printed_on"]
    audit = c.get(API + "/audit?limit=50").json()["audit"]
    assert sum(1 for a in audit if a["entity"] == "item" and a["action"] == "labels") == 2


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"items": []}, "non-empty list"),
        ({"items": "IT-00"}, "non-empty list"),
        ({"items": ["IT-00", "NOPE"]}, "no item 'NOPE'"),
        ({"items": ["IT-00"], "start_at": 0}, "1 to 30"),
        ({"items": ["IT-00"], "start_at": 31}, "1 to 30"),
        ({"items": ["IT-00"], "start_at": "x"}, "1 to 30"),
        ({"items": ["IT-00"] * 301}, "at most 300"),
    ],
)
def test_item_sheet_refuses_bad_requests_with_the_reason(host, registry, monkeypatch, body, fragment):
    c = _with_items(registry, monkeypatch, 1)
    r = c.post(API + "/labels/sheet", json=body)
    assert r.status_code == 400 and fragment in r.json()["detail"]


def test_item_sheet_without_an_address_is_refused_with_the_fix(host, registry):
    c = _client(registry)
    c.put(API + "/lots/L", json={"name": "L"})
    c.post(API + "/items", json={"id": "A1", "name": "A", "lot_id": "L"})
    r = c.post(API + "/labels/sheet", json={"items": ["A1"]})
    assert r.status_code == 409 and "Bin label address" in r.json()["detail"]


def test_migration_adds_labels_printed_on_to_older_items(tmp_path):
    import sqlite3

    from inventory_plugin.store import SCHEMA, InventoryStore

    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO items(id, name, created_at, updated_at) VALUES ('A1', 'A', 't', 't')")
    con.commit()
    con.close()
    s = InventoryStore(p)
    assert s.get_item("A1")["labels_printed_on"] == ""
    assert s.mark_item_labels_printed(["A1", "A1"], actor="t") == ["A1"]
    with pytest.raises(Exception, match="no item"):
        s.mark_item_labels_printed(["B2"], actor="t")


# ── the view ──────────────────────────────────────────────────────────────────────
def test_the_view_boots_into_a_scanned_item_and_offers_labels_where_they_are_useful():
    from inventory_plugin.view import PAGE

    assert 'URLSearchParams(location.search || "").get("item")' in PAGE
    boot = PAGE.split("function applyStartBin()", 1)[1].split("let inflight", 1)[0]
    assert 'state.tab = "stock"' in boot and "openItem(START_ITEM)" in boot and "no item" in boot
    assert 'data-act="labelsel">Print labels' in PAGE  # Stock selection bar
    assert 'data-act="itemlabel"' in PAGE and "Never printed" in PAGE  # the item drawer
    assert '"sc-done-labels"' in PAGE and "finishScan(true)" in PAGE  # end of a scan
    # the sheet's tab opens inside the click itself, before any await (else it's a blocked popup)
    body = PAGE.split("async function itemLabelsDialog", 1)[1].split("async function newBinsDialog", 1)[0]
    submit = body.split("onSubmit: async (v) => {", 1)[1]
    pre = submit.split("const w = sheetTab();", 1)[0]
    assert "await" not in pre and "openSheet(w, r, r.ids.length)" in submit
    assert ".print(" not in PAGE
