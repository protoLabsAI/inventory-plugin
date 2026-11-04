"""0.11.0, label first: a scan ends by printing labels, and Put away links a piece to its bin by
reading the piece's label and then the bin's, in the view, with the vendored jsQR."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.store import InventoryError

ROOT = Path(__file__).resolve().parents[1]
API = "/api/plugins/inventory"


def _audit(store, item_id):
    with store._read() as con:
        return [
            (r["action"], json.loads(r["changes"] or "{}"))
            for r in con.execute("SELECT action, changes FROM audit WHERE entity_id=? ORDER BY id", (item_id,))
        ]


def _lot_with(store, *items):
    store.upsert_lot({"id": "L", "name": "Box"}, actor="t")
    for iid, status in items:
        store.upsert_item({"id": iid, "name": "Piece " + iid, "lot_id": "L", "status": status}, actor="t")
    store.create_bins(count=2, actor="t")


# ── the store ─────────────────────────────────────────────────────────────────────
def test_put_away_bins_a_planned_piece_and_puts_it_up_for_sale_audited(store):
    _lot_with(store, ("P", "planned"), ("A", "available"), ("K", "kept"))
    out = store.move_items(["P", "A", "K"], "b01", actor="op", put_away=True)
    assert out == {"bin_id": "B01", "moved": ["P", "A", "K"], "unchanged": [], "now_available": ["P"]}
    assert {i: store.get_item(i)["status"] for i in "PAK"} == {"P": "available", "A": "available", "K": "kept"}
    assert ("move", {"bin": ["", "B01"], "put_away": True}) in _audit(store, "P")
    assert ("update", {"status": "available", "reason": "put away"}) in _audit(store, "P")
    # already in the bin: no second move row, and nothing left to promote
    again = store.move_items(["P"], "B01", actor="op", put_away=True)
    assert again["moved"] == [] and again["now_available"] == []


def test_a_plain_move_never_changes_status(store):
    _lot_with(store, ("P", "planned"))
    assert store.move_items(["P"], "B01", actor="op")["now_available"] == []
    assert store.get_item("P")["status"] == "planned"


def test_put_away_refuses_sold_items_and_needs_a_bin_all_or_nothing(store):
    _lot_with(store, ("P", "planned"), ("S", "available"))
    store.mark_sold("S", price=5, channel="x", actor="t")
    with pytest.raises(InventoryError, match="sold item"):
        store.move_items(["P", "S"], "B01", actor="op", put_away=True)
    assert store.get_item("P")["bin_id"] == "" and store.get_item("P")["status"] == "planned"
    with pytest.raises(InventoryError, match="needs a bin"):
        store.move_items(["P"], "", actor="op", put_away=True)


def test_finishing_a_scan_without_placements_leaves_pieces_unbinned_and_planned(store):
    # The scan flow no longer picks bins: finish({}) closes the scan and Put away bins later.
    from tests.test_scans import _box_proposed

    s = _box_proposed(store)
    s = store.confirm_scan(
        s["id"],
        {
            "name": s["proposal"]["name"],
            "units": [{"name": "Pyregheists", "keep": True}, {"name": "Idol of the Old Ones"}],
        },
        actor="op",
    )
    done = store.finish_scan(s["id"], {}, actor="op")
    items = [store.get_item(i) for i in done["item_ids"]]
    assert {(i["status"], i["bin_id"]) for i in items} == {("kept", ""), ("planned", "")}


# ── the route ─────────────────────────────────────────────────────────────────────
def test_the_move_route_takes_put_away(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    c = TestClient(app)
    c.put(API + "/lots/L", json={"name": "Box"})
    c.post(API + "/items", json={"id": "P", "name": "Piece", "lot_id": "L", "status": "planned"})
    c.post(API + "/bins", json={"count": 1})
    r = c.post(API + "/bins/move", json={"item_ids": ["P"], "bin_id": "B01", "put_away": True})
    assert r.status_code == 200 and r.json()["now_available"] == ["P"]
    assert c.get(API + "/items/P").json()["item"]["status"] == "available"


# ── the page ──────────────────────────────────────────────────────────────────────
def test_the_decoder_is_vendored_with_its_licence_and_inlined_only_in_the_served_page():
    from inventory_plugin.view import PAGE, served_page

    js = (ROOT / "vendor" / "jsqr" / "jsQR.js").read_text()
    assert js.startswith("/*! jsQR 1.4.0") and "Apache License 2.0" in js.splitlines()[0]
    assert "Apache License" in (ROOT / "vendor" / "jsqr" / "LICENSE").read_text()
    assert "</script" not in js.lower()
    assert PAGE.count("<!--vendor:jsqr-->") == 1 and "webpackUniversalModuleDefinition" not in PAGE
    page = served_page()
    assert page.count("webpackUniversalModuleDefinition") == 1 and "<!--vendor:jsqr-->" not in page
    assert page.index("webpackUniversalModuleDefinition") < page.index('<script type="module">')


def test_the_scan_flow_ends_by_labelling_not_binning():
    from inventory_plugin.view import PAGE

    assert "data-binsel" not in PAGE and "Put each piece in a bin" not in PAGE
    assert 'btnP("sc-tolabels", "Label them")' in PAGE and '"sc-done-labels"' in PAGE and '"Skip labels"' in PAGE
    # native capture stays the path that always works; 0.13.0 adds a live viewfinder on top of it
    assert 'capture="environment"' in PAGE and "data-cap=\"' + what" in PAGE


def _qr_rgba(text: str, scale: int = 6, border: int = 4):
    from inventory_plugin.vendor.qrcodegen import QrCode

    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n = qr.get_size()
    side = (n + 2 * border) * scale
    px = bytearray()
    for y in range(side):
        for x in range(side):
            mx, my = x // scale - border, y // scale - border
            dark = 0 <= mx < n and 0 <= my < n and qr.get_module(mx, my)
            v = 0 if dark else 255
            px += bytes((v, v, v, 255))
    return side, bytes(px)


def _node_decode(cases: list[str]) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    parse = PAGE[PAGE.index("  function parseLabel(text) {") : PAGE.index("  function parseTyped(text) {")]
    images = []
    for t in cases:
        side, px = _qr_rgba(t)
        images.append({"side": side, "hex": px.hex()})
    script = (
        "const jsQR = require(process.argv[1]);\n"
        + parse
        + "\nconst imgs = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "console.log(JSON.stringify(imgs.map((im) => { const d = new Uint8ClampedArray(Buffer.from(im.hex, 'hex'));"
        + " const r = jsQR(d, im.side, im.side); return r ? { text: r.data, label: parseLabel(r.data) } : null; })));"
    )
    out = subprocess.run(
        [node, "-e", script, str(ROOT / "vendor" / "jsqr" / "jsQR.js")],
        input=json.dumps(images),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(out.stdout)


def test_a_printed_label_round_trips_through_jsqr_and_the_views_parser():
    hub = "https://hub.example.ts.net/agents/merchantAgent-6604"
    from inventory_plugin.labels import item_label_url, label_url

    cases = [
        item_label_url(hub, "helsmiths-hashut-army-box-nib"),
        item_label_url(hub, "NH-SH-Harrows"),
        label_url(hub, "B03"),
        "https://example.com/plugins/other/i/X1",  # someone else's QR
        "javascript:alert(1)",
    ]
    got = _node_decode(cases)
    assert [g["text"] for g in got] == cases  # jsQR reads every one exactly
    assert [g["label"] for g in got] == [
        {"kind": "item", "id": "helsmiths-hashut-army-box-nib"},
        {"kind": "item", "id": "NH-SH-Harrows"},  # item ids keep their case
        {"kind": "bin", "id": "B03"},
        None,
        None,
    ]


def test_the_parser_accepts_only_our_two_label_paths():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    parse = PAGE[PAGE.index("  function parseLabel(text) {") : PAGE.index("  function parseTyped(text) {")]
    probes = [
        "http://127.0.0.1:7881/plugins/inventory/b/b07",
        "https://hub.example.ts.net/agents/x/plugins/inventory/i/Odd%20Id",
        "https://hub.example.ts.net/plugins/inventory/i/..%2F..%2Fapi",
        "https://hub.example.ts.net/plugins/inventory/view?bin=B01",
        "ftp://hub.example.ts.net/plugins/inventory/b/B01",
        "not a url",
    ]
    out = subprocess.run(
        [node, "-e", parse + "\nconsole.log(JSON.stringify(" + json.dumps(probes) + ".map(parseLabel)));"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert json.loads(out.stdout) == [{"kind": "bin", "id": "B07"}, None, None, None, None, None]


def test_put_away_is_reachable_from_bins_the_drawer_and_next():
    from inventory_plugin.view import PAGE

    assert 'data-act="putaway">Put away</button>' in PAGE
    assert 'data-act="pa-add"' in PAGE and "waiting to be put away" in PAGE
    assert re.search(r"put_away: true", PAGE)
