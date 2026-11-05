"""0.15.0: check a bin. Scan every label in it; see what's found, what wasn't scanned, and what
doesn't belong (in another bin, in none, or sold). Finishing moves strays in, flags the rest
missing (a flag, not a status), stamps the bin's checked_on, all in one audited transaction."""

from __future__ import annotations

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.store import InventoryError

from tests.conftest import FakeRegistry
from tests.test_view import _run_page


def _setup(store):
    store.upsert_lot({"id": "L", "name": "Lot"}, actor="t")
    store.create_bins(count=2, actor="t")  # B01, B02
    for iid in ("IN1", "IN2", "IN3", "AWAY", "LOOSE", "GONE"):
        store.upsert_item({"id": iid, "name": f"Item {iid}", "lot_id": "L", "status": "available"}, actor="t")
    store.move_items(["IN1", "IN2", "IN3"], "B01", actor="t")
    store.move_items(["AWAY"], "B02", actor="t")
    store.move_items(["GONE"], "B01", actor="t")
    store.mark_sold("GONE", price=10, channel="eBay", actor="t")
    sale = store.to_pack()[0]["id"]
    store.mark_packed(sale, actor="t")  # sold, packed, out of its bin


def test_a_check_moves_strays_in_flags_the_unscanned_and_stamps_the_bin(store):
    _setup(store)
    out = store.check_bin(
        "b01",
        ["IN1", "IN2", "AWAY", "LOOSE", "NOT-AN-ITEM"],
        move_here=["AWAY", "LOOSE"],
        mark_missing=["IN3"],
        actor="t",
    )
    assert out["bin_id"] == "B01" and sorted(out["moved"]) == ["AWAY", "LOOSE"] and out["missing"] == ["IN3"]
    assert store.get_item("AWAY")["bin_id"] == "B01" and store.get_item("LOOSE")["bin_id"] == "B01"
    assert (
        store.get_item("IN3")["missing_since"] and store.get_item("IN3")["bin_id"] == "B01"
    )  # a flag, still recorded here
    assert store.get_item("IN3")["status"] == "available"
    b = next(b for b in store.list_bins()["bins"] if b["id"] == "B01")
    assert b["checked_on"]
    acts = [(a["entity"], a["action"]) for a in store.audit_log()]
    assert ("bin", "check") in acts and ("item", "missing") in acts
    move = next(a for a in store.audit_log(entity_id="AWAY") if a["action"] == "move" and a["changes"].get("check"))
    assert move["changes"]["bin"] == ["B02", "B01"]


def test_a_missing_item_is_found_again_by_a_check_a_move_or_found_it(store):
    _setup(store)
    store.check_bin("B01", ["IN1"], mark_missing=["IN2", "IN3"], actor="t")
    assert store.check_bin("B01", ["IN2"], actor="t")["found_again"] == ["IN2"]
    assert store.get_item("IN2")["missing_since"] == ""
    store.move_items(["IN3"], "B02", actor="t")
    assert store.get_item("IN3")["missing_since"] == ""  # moved means someone has it in hand
    store.check_bin("B01", [], mark_missing=["IN1"], actor="t")
    assert store.clear_missing("IN1", actor="t")["missing_since"] == ""
    assert any(a["action"] == "found" for a in store.audit_log(entity_id="IN1"))


def test_a_check_refuses_what_cant_be_true(store):
    _setup(store)
    with pytest.raises(InventoryError, match="sold and packed"):
        store.check_bin("B01", ["GONE"], move_here=["GONE"], actor="t")
    with pytest.raises(InventoryError, match="not recorded in B01"):
        store.check_bin("B01", [], mark_missing=["AWAY"], actor="t")
    with pytest.raises(InventoryError, match="scanned in the check"):
        store.check_bin("B01", ["IN1"], mark_missing=["IN1"], actor="t")
    with pytest.raises(InventoryError, match="no item"):
        store.check_bin("B01", [], move_here=["NOPE"], actor="t")
    with pytest.raises(InventoryError, match="no bin"):
        store.check_bin("B99", [], actor="t")
    assert not next(b for b in store.list_bins()["bins"] if b["id"] == "B01")["checked_on"]  # nothing half-saved


def test_the_routes(tmp_path):
    reg = FakeRegistry({"db_path": str(tmp_path / "inv.db")})
    inventory_plugin.register(reg)
    app = FastAPI()
    for router, prefix in reg.routers:
        app.include_router(router, prefix=prefix or "")
    c = TestClient(app)
    api = "/api/plugins/inventory"
    c.put(api + "/lots/L", json={"name": "L"})
    c.post(api + "/bins", json={"count": 1})
    for iid in ("A", "B"):
        c.post(api + "/items", json={"id": iid, "name": iid, "lot_id": "L", "status": "available"})
    c.post(api + "/bins/move", json={"item_ids": ["A", "B"], "bin_id": "B01"})
    r = c.post(api + "/bins/B01/check", json={"found": ["A"], "mark_missing": ["B"]})
    assert r.status_code == 200 and r.json()["missing"] == ["B"]
    assert c.get(api + "/items/B").json()["item"]["missing_since"]
    assert c.post(api + "/items/B/found").json()["item"]["missing_since"] == ""
    assert c.post(api + "/bins/B01/check", json={"found": "A"}).status_code == 400
    assert c.post(api + "/items/NOPE/found").status_code == 404
    assert c.get(api + "/bins").json()["bins"][0]["checked_on"]


def test_the_page_classifies_each_scanned_label():
    probe = (
        "\nconst items = [{id:'IN', bin_id:'B01', status:'available'}, {id:'Away', bin_id:'B02', status:'listed'},"
        "{id:'LOOSE', bin_id:'', status:'available'}, {id:'GONE', bin_id:'', status:'sold'}, {id:'TOPACK', bin_id:'B01', status:'sold'}];"
        "\nconst L = (kind, id) => ({ kind, id });"
        "\nconsole.log(JSON.stringify([L('item','IN'), L('item','away'), L('item','LOOSE'), L('item','GONE'), L('item','TOPACK'),"
        " L('item','NOPE'), L('bin','B01'), L('bin','B02'), null].map((l) => bcClassify(l, 'B01', items))));\n"
    )
    got = _run_page(probe)
    assert [g["kind"] for g in got] == [
        "found",
        "elsewhere",
        "loose",
        "sold",
        "found",
        "unknown",
        "thisbin",
        "otherbin",
        "foreign",
    ]
    assert got[1] == {"kind": "elsewhere", "id": "Away", "from": "B02"}  # case-insensitive id match keeps the real id
    assert got[4]["kind"] == "found"  # sold but not packed yet: it's still physically here, to pick


def test_the_check_reuses_the_live_camera_and_has_the_photo_fallback():
    from inventory_plugin.view import PAGE

    paint = PAGE[PAGE.index("  function bcPaint() {") : PAGE.index("  async function bcScan(file) {")]
    assert 'data-act="pa-cam"' in paint and 'capBtn("bc"' in paint and 'id="pa-cam-slot"' in paint
    assert "STRAY_TEXT[c.kind](c)" in paint and "Not scanned yet" in paint and "Unknown label" in paint
    start = PAGE[PAGE.index("  async function camStart() {") : PAGE.index("  function camTick(now) {")]
    assert (
        'dk === "bincheck"' in start and 'CAM.ctx = dk === "bincheck" || dk === "scanlabel" ? dk : "putaway"' in start
    )
    assert "state.drawer.kind !== CAM.ctx" in start  # the drawer that started it owns it
    hit = PAGE[PAGE.index("  async function camHit(text) {") : PAGE.index("  async function camTorch() {")]
    assert 'if (CAM.ctx === "bincheck")' in hit and "location" not in hit
    assert 'btn("bincheck", "Check this bin")' in PAGE and 'data-see="missing"' in PAGE and 'data-act="found"' in PAGE
    assert "In another bin: " in PAGE and "Sold: should not be here" in PAGE and "Not in a bin → move here?" in PAGE
