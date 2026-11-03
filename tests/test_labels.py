"""Bin labels: the address a QR encodes, the public /b/<bin> hop, and the printable sheet."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

HUB = "https://hub.example.ts.net/agents/merchantAgent-6604"


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


# ── the address on the label ─────────────────────────────────────────────────────
def test_base_prefers_config_then_public_url_then_console(host, monkeypatch):
    from inventory_plugin.labels import label_base

    host(console_url=lambda: "http://127.0.0.1:7881")
    got = label_base({})
    assert got["base"] == "http://127.0.0.1:7881" and got["source"] == "console" and not got["phone_reachable"]
    monkeypatch.setenv("A2A_PUBLIC_URL", HUB + "/")
    got = label_base({})
    assert got == {"base": HUB, "source": "public_url", "phone_reachable": True, "problem": ""}
    got = label_base({"label_base_url": " https://shop.example.com/inv/ "})
    assert got["base"] == "https://shop.example.com/inv" and got["source"] == "config"


@pytest.mark.parametrize(
    "bad",
    ["ftp://x.example", "https://user:pw@x.example", "https://x.example/?a=1", "not a url", "https://x.example/#f"],
)
def test_a_bad_configured_base_is_refused_not_guessed_around(host, monkeypatch, bad):
    from inventory_plugin.labels import label_base

    monkeypatch.setenv("A2A_PUBLIC_URL", HUB)
    got = label_base({"label_base_url": bad})
    assert got["base"] == "" and got["source"] == "config" and "Settings" in got["problem"]


def test_no_address_at_all_says_what_to_set(host):
    from inventory_plugin.labels import label_base

    got = label_base({})
    assert got["base"] == "" and "Bin label address" in got["problem"]


def test_label_url_and_the_qr_encode_exactly_that_address():
    from inventory_plugin.labels import label_url, qr_svg
    from inventory_plugin.vendor.qrcodegen import QrCode

    url = label_url(HUB, "B07")
    assert url == HUB + "/plugins/inventory/b/B07"
    svg = qr_svg(url)
    assert svg.startswith("<svg") and 'fill="#000"' in svg and "http://www.w3.org/2000/svg" in svg
    # the SVG's dark modules are the encoder's matrix for this exact text, quiet zone of 4
    qr = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
    cells = {(int(x) - 4, int(y) - 4) for x, y in re.findall(r"M(\d+),(\d+)h1v1h-1z", svg)}
    n = qr.get_size()
    assert cells == {(x, y) for y in range(n) for x in range(n) if qr.get_module(x, y)}


# ── the public hop ────────────────────────────────────────────────────────────────
def test_hop_redirects_to_the_console_link_when_the_host_can_make_one(host, registry):
    calls = []

    def view_url(plugin_id, view_id=None, params=None):
        calls.append((plugin_id, view_id, params))
        return "https://hub.example.ts.net/app/agent/merchantAgent-6604/?plugin=inventory&vp=bin%3DB07"

    host(view_url=view_url, console_url=lambda: "http://127.0.0.1:7881")
    c = _client(registry)
    r = c.get("/plugins/inventory/b/b07", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("vp=bin%3DB07")
    assert r.headers["cache-control"] == "no-store"
    assert calls == [("inventory", None, {"bin": "B07"})]  # upper-cased like the store keeps it


def test_hop_on_an_older_host_explains_and_links_the_hub_console(host, registry, monkeypatch):
    host(console_url=lambda: "http://127.0.0.1:7881")  # 0.226: no view_url
    monkeypatch.setenv("A2A_PUBLIC_URL", HUB)
    r = _client(registry).get("/plugins/inventory/b/B07")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert "needs a protoAgent update" in r.text and ">B07<" in r.text
    assert 'href="https://hub.example.ts.net/app/agent/merchantAgent-6604/"' in r.text


def test_hop_with_no_host_still_answers_without_a_link(host, registry):
    r = _client(registry).get("/plugins/inventory/b/B07")
    assert r.status_code == 200 and "needs a protoAgent update" in r.text and "<a " not in r.text


def test_a_refusing_view_url_falls_back_instead_of_breaking(host, registry):
    def boom(*a, **k):
        raise ValueError("refused")

    host(view_url=boom)
    r = _client(registry).get("/plugins/inventory/b/B07", follow_redirects=False)
    assert r.status_code == 200 and "needs a protoAgent update" in r.text


@pytest.mark.parametrize("bad", ["<script>", "B 07", "..", "-B07", "x" * 65, "B07%2F..", "%00"])
def test_hop_refuses_anything_that_is_not_a_bin_id(host, registry, bad):
    host(view_url=lambda *a, **k: "https://evil.example/")
    r = _client(registry).get("/plugins/inventory/b/" + bad, follow_redirects=False)
    assert r.status_code == 404
    assert "evil" not in r.headers.get("location", "") and "<script>" not in r.text


def test_hop_reads_no_data_and_needs_no_bearer(host, registry):
    """Public by design: the response is the same for a bin that exists and one that doesn't."""
    c = _client(registry)
    a, b = c.get("/plugins/inventory/b/B01"), c.get("/plugins/inventory/b/B99")
    assert a.status_code == b.status_code == 200
    assert a.text.replace("B01", "Bxx") == b.text.replace("B99", "Bxx")


# ── the sheet ─────────────────────────────────────────────────────────────────────
def _with_bins(registry, monkeypatch, n=5):
    monkeypatch.setenv("A2A_PUBLIC_URL", HUB)
    c = _client(registry)
    assert c.post("/api/plugins/inventory/bins", json={"count": n, "label": "Shelf <2>"}).status_code == 200
    return c


def test_base_route_reports_where_labels_point(host, registry, monkeypatch):
    c = _with_bins(registry, monkeypatch, 1)
    got = c.get("/api/plugins/inventory/labels/base").json()
    assert got["base"] == HUB and got["phone_reachable"] is True and got["path"] == "/plugins/inventory/b/"


def test_sheet_for_unprinted_bins_marks_them_and_then_has_nothing_left(host, registry, monkeypatch):
    c = _with_bins(registry, monkeypatch, 3)
    r = c.post("/api/plugins/inventory/labels/sheet", json={"unprinted": True})
    assert r.status_code == 200 and r.json()["ids"] == ["B01", "B02", "B03"]
    html = r.json()["html"]
    assert html.count('class="label"') == 3 and html.count("<svg") == 3
    assert "Shelf &lt;2&gt;" in html and "Shelf <2>" not in html  # label text escaped
    assert "@media print" in html and "size:letter" in html
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")  # nothing to fetch
    bins = c.get("/api/plugins/inventory/bins").json()["bins"]
    assert all(b["labels_printed_on"] for b in bins)
    again = c.post("/api/plugins/inventory/labels/sheet", json={"unprinted": True})
    assert again.status_code == 400 and "nothing to print" in again.json()["detail"]
    audit = c.get("/api/plugins/inventory/audit?limit=50").json()["audit"]
    assert sum(1 for a in audit if a["entity"] == "bin" and a["action"] == "labels") == 3


def test_sheet_range_is_in_bin_number_order_and_mark_false_records_nothing(host, registry, monkeypatch):
    c = _with_bins(registry, monkeypatch, 12)
    r = c.post("/api/plugins/inventory/labels/sheet", json={"from": "B11", "to": "B09", "mark": False})
    assert r.json()["ids"] == ["B09", "B10", "B11"]
    assert not any(b["labels_printed_on"] for b in c.get("/api/plugins/inventory/bins").json()["bins"])
    assert c.post("/api/plugins/inventory/labels/sheet", json={"from": "B01", "to": "B77"}).status_code == 400
    assert c.post("/api/plugins/inventory/labels/sheet", json={"ids": ["B02", "NOPE"]}).status_code == 400
    assert c.post("/api/plugins/inventory/labels/sheet", json={}).status_code == 400


def test_sheet_without_an_address_is_refused_with_the_fix(host, registry):
    c = _client(registry)
    c.post("/api/plugins/inventory/bins", json={"count": 1})
    r = c.post("/api/plugins/inventory/labels/sheet", json={"all": True})
    assert r.status_code == 409 and "Bin label address" in r.json()["detail"]


def test_label_routes_are_gated_like_every_other_data_route():
    from inventory_plugin.api import build_data_router

    paths = {r.path for r in build_data_router(None, {}).routes}
    assert {"/labels/base", "/labels/sheet"} <= paths  # mounted under /api/plugins/inventory (bearer)


def test_migration_adds_labels_printed_on_to_an_older_db(tmp_path):
    import sqlite3

    from inventory_plugin.store import SCHEMA, InventoryStore

    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)  # the pre-0.7.0 bins table has no labels_printed_on
    con.execute(
        "INSERT INTO bins(id, label, note, created_on, created_at, updated_at) VALUES ('B01','','','d','t','t')"
    )
    con.commit()
    con.close()
    s = InventoryStore(p)
    (b,) = s.list_bins()["bins"]
    assert b["labels_printed_on"] == ""
    assert s.mark_labels_printed(["b01", "B01"], actor="t") == ["B01"]
    with pytest.raises(Exception, match="no bin"):
        s.mark_labels_printed(["B02"], actor="t")


def test_the_manifest_exempts_exactly_the_label_hop_from_the_host_auth_gate():
    # The host only auto-exempts `views:` paths. The hop is opened by the iPhone Camera app with
    # no bearer, so it must be declared, and nothing broader than its own subtree may be.
    import yaml

    manifest = yaml.safe_load((Path(__file__).resolve().parents[1] / "protoagent.plugin.yaml").read_text())
    assert manifest["public_paths"] == ["/plugins/inventory/b/"]
