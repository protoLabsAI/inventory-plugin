"""0.15.0: how an item ships. A weight in whole ounces (never guessed), a box in tenths of an
inch, and packaging presets the operator makes (seeded empty) whose own weight is added for the
packed weight. Every change is audited."""

from __future__ import annotations

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.store import InventoryError, InventoryStore, side_tenths, weight_ounces

from tests.conftest import FakeRegistry


@pytest.mark.parametrize(
    ("lb", "oz", "total", "want"),
    [
        (2, 4, None, 36),
        ("1", "", None, 16),
        ("", "7.2", None, 8),  # partial ounces round UP, the way carriers bill
        (None, None, 12.0, 12),
        (0, 0.4, None, 1),
        (None, None, None, None),  # blank = not weighed
        ("", "", None, None),
    ],
)
def test_weight_is_whole_ounces_rounded_up(lb, oz, total, want):
    assert weight_ounces(lb, oz, total_oz=total) == want


@pytest.mark.parametrize(("lb", "oz"), [(0, 0), (-1, 0), ("x", 2), (151, 0)])
def test_a_weightless_negative_garbage_or_freight_weight_is_refused(lb, oz):
    with pytest.raises(InventoryError):
        weight_ounces(lb, oz)


def test_box_sides_keep_one_decimal_and_refuse_nonsense():
    assert side_tenths("length", "10") == 100
    assert side_tenths("length", 8.25) == 83  # half up
    assert side_tenths("length", "") is None
    for bad in ("0", "-2", "abc", "109", "nan"):
        with pytest.raises(InventoryError):
            side_tenths("length", bad)


def _item(store, iid="A"):
    store.upsert_lot({"id": "L", "name": "Lot"}, actor="t")
    store.upsert_item({"id": iid, "name": f"Item {iid}", "lot_id": "L", "status": "available"}, actor="t")


def test_an_item_starts_unweighed_and_unmeasured(store):
    _item(store)
    it = store.get_item("A")
    assert it["weight_oz"] is None and it["box"] is None and it["packaging_id"] == ""


def test_shipping_is_recorded_kept_and_cleared_audited(store):
    _item(store)
    it = store.set_shipping("A", {"weight_lb": 1, "weight_oz": 6, "length": 10, "width": 8, "height": 3.5}, actor="t")
    assert it["weight_oz"] == 22 and it["box"] == {"length": 10.0, "width": 8.0, "height": 3.5}
    it = store.set_shipping("A", {"weight_oz": 20, "weight_lb": None}, actor="t")  # box untouched
    assert it["weight_oz"] == 20 and it["box"]["height"] == 3.5
    it = store.set_shipping("A", {"length": None, "width": None, "height": None}, actor="t")
    assert it["box"] is None and it["weight_oz"] == 20
    it = store.set_shipping("A", {"weight_total_oz": None}, actor="t")
    assert it["weight_oz"] is None
    acts = [a for a in store.audit_log(entity_id="A") if a["action"] == "shipping"]
    assert len(acts) == 4 and acts[-1]["changes"]["weight_oz"] == 22
    store.set_shipping("A", {"weight_total_oz": None}, actor="t")  # no change, no audit row
    assert len([a for a in store.audit_log(entity_id="A") if a["action"] == "shipping"]) == 4


def test_two_of_three_sides_is_refused(store):
    _item(store)
    with pytest.raises(InventoryError, match="all three"):
        store.set_shipping("A", {"length": 10, "width": 8}, actor="t")


def test_packaging_starts_empty_and_a_preset_fills_the_box(store):
    assert store.list_packaging() == []  # never invented
    _item(store)
    pk = store.upsert_packaging(
        {"name": "6×9 bubble mailer", "length": 9, "width": 6, "height": 1, "tare_oz": 1}, actor="t"
    )
    assert (
        pk["id"].startswith("PK-") and pk["box"] == {"length": 9.0, "width": 6.0, "height": 1.0} and pk["tare_oz"] == 1
    )
    it = store.set_shipping("A", {"packaging_id": pk["id"], "weight_oz": 7}, actor="t")
    assert it["packaging_id"] == pk["id"] and it["box"]["length"] == 9.0
    assert store.packed_weight_oz(it, pk) == 8  # item 7 oz + mailer 1 oz
    # sides given in the same call win over the preset's
    it = store.set_shipping("A", {"packaging_id": pk["id"], "length": 12, "width": 9, "height": 2}, actor="t")
    assert it["box"]["length"] == 12.0
    assert store.list_packaging()[0]["items"] == 1


def test_a_preset_in_use_cant_be_deleted_and_unknown_ones_are_refused(store):
    _item(store)
    pk = store.upsert_packaging({"name": "Box", "length": 10, "width": 8, "height": 4}, actor="t")
    store.set_shipping("A", {"packaging_id": pk["id"]}, actor="t")
    with pytest.raises(InventoryError, match="ship in Box"):
        store.delete_packaging(pk["id"], actor="t")
    store.set_shipping("A", {"packaging_id": ""}, actor="t")
    assert store.delete_packaging(pk["id"], actor="t") == 1
    with pytest.raises(InventoryError, match="no packaging"):
        store.set_shipping("A", {"packaging_id": "PK-NOPE"}, actor="t")
    with pytest.raises(InventoryError, match="name the packaging"):
        store.upsert_packaging({"name": " "}, actor="t")
    with pytest.raises(InventoryError, match="all three"):
        store.upsert_packaging({"name": "Half", "length": 3}, actor="t")


def test_the_migration_adds_the_columns_to_an_older_db(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    InventoryStore(db)  # current schema …
    con = sqlite3.connect(db)
    for col in ("weight_oz", "box_l_tenths", "box_w_tenths", "box_h_tenths", "packaging_id", "missing_since"):
        con.execute(f"ALTER TABLE items DROP COLUMN {col}")  # … rolled back to 0.14.0
    con.execute("ALTER TABLE bins DROP COLUMN checked_on")
    con.execute("DROP TABLE packaging")
    con.commit()
    con.close()
    s = InventoryStore(db)
    s.upsert_lot({"id": "L", "name": "L"}, actor="t")
    s.upsert_item({"id": "A", "name": "A", "lot_id": "L"}, actor="t")
    assert s.set_shipping("A", {"weight_oz": 5}, actor="t")["weight_oz"] == 5
    assert s.list_packaging() == []


def test_the_routes(tmp_path):
    reg = FakeRegistry({"db_path": str(tmp_path / "inv.db")})
    inventory_plugin.register(reg)
    app = FastAPI()
    for router, prefix in reg.routers:
        app.include_router(router, prefix=prefix or "")
    c = TestClient(app)
    api = "/api/plugins/inventory"
    c.put(api + "/lots/L", json={"name": "L"})
    c.post(api + "/items", json={"id": "A", "name": "A", "lot_id": "L", "status": "available"})
    pk = c.post(
        api + "/packaging", json={"name": "Small box", "length": 8, "width": 6, "height": 4, "tare_oz": 3}
    ).json()
    pid = pk["packaging"]["id"]
    assert c.get(api + "/packaging").json()["packaging"][0]["name"] == "Small box"
    assert (
        c.put(api + f"/packaging/{pid}", json={"name": "Small box", "tare_oz": 4}).json()["packaging"]["tare_oz"] == 4
    )
    r = c.put(api + "/items/A/shipping", json={"weight_lb": 1, "weight_oz": 2, "packaging_id": pid})
    assert r.status_code == 200 and r.json()["item"]["weight_oz"] == 18
    assert c.put(api + "/items/A/shipping", json={"weight_lb": -1}).status_code == 400
    assert c.put(api + "/items/NOPE/shipping", json={"weight_oz": 1}).status_code == 404
    assert c.delete(api + f"/packaging/{pid}").status_code == 400  # in use
    assert ("inventory.packaging.changed", {"id": pid, "action": "create"}) in reg.events or any(
        t.endswith("packaging.changed") for t, _ in reg.events
    )


def test_the_page_formats_weight_and_box_the_way_ebay_asks_and_adds_the_packaging():
    from tests.test_view import _run_page

    probe = (
        "\nstate.packaging = [{id:'PK-1', name:'Mailer', box:{length:9,width:6,height:1}, tare_oz:2}];"
        "\nconst a = {id:'A', name:'A', status:'available', weight_oz:34, box:{length:10,width:8,height:3.5}, packaging_id:'PK-1'};"
        "\nconst b = {id:'B', name:'B', status:'available', weight_oz:null, box:null, packaging_id:''};"
        "\nconst c = {id:'C', name:'C', status:'sold', weight_oz:null, box:null, packaging_id:''};"
        "\nconsole.log(JSON.stringify({ w: [weightText(16), weightText(5), weightText(34), weightText(null)],"
        " packed: packedOz(a), box: boxText(a.box), none: [noWeight(a), noWeight(b), noWeight(c)],"
        " rows: shipRows(a), unweighed: shipRows(b), sum: shipSummary(a), sumNone: shipSummary(b) }));\n"
    )
    out = _run_page(probe)
    assert out["w"] == ["1 lb", "5 oz", "2 lb 2 oz", ""]
    assert out["packed"] == 36 and out["box"] == "10 × 8 × 3.5 in"
    assert out["none"] == [False, True, False]  # only for-sale stock is nagged
    assert "2 lb 4 oz" in out["rows"] and "Includes Mailer (2 oz)" in out["rows"]
    for k in ('data-k="wlb"', 'data-k="woz"', 'data-k="bl"', 'data-k="bw"', 'data-k="bh"'):
        assert k in out["rows"]
    assert "Not weighed yet." in out["unweighed"] and 'data-act="shipping" data-id="B"' in out["unweighed"]
    assert "2 lb 4 oz packed · 10 × 8 × 3.5 in · Mailer" in out["sum"] and "Not weighed" in out["sumNone"]


def test_shipping_is_reachable_from_the_drawer_put_away_more_and_next():
    from inventory_plugin.view import PAGE

    assert '["Packaging", () => packagingDialog()]' in PAGE
    assert 'kv("Shipping", shipSummary(it)' in PAGE
    tray = PAGE[PAGE.index("  function paPaint() {") : PAGE.index("  const paSay")]
    assert 'data-act="shipping"' in tray  # Weigh from the put-away list, never a blocking step
    assert "no packed weight" in PAGE and 'data-see="noweight"' in PAGE and 'f.preset === "noweight"' in PAGE
    copy = PAGE[PAGE.index("  async function copyDraft(btn) {") : PAGE.index("  async function downloadPhotos(id) {")]
    assert "SHIP[k]" in copy and copy.index("SHIP[k]") < copy.index(
        "/draft/copied"
    )  # package numbers don't flip the draft


@pytest.mark.parametrize(("tare", "want"), [(0, 0), ("0", 0), ("", 0), (None, 0), (0.0, 0), (3.2, 4)])
def test_packaging_can_weigh_nothing_but_not_less(store, tare, want):
    # Review on #25: a 0 empty weight went through the item weight rule and was refused, and the
    # Edit dialog prefills 0 for a preset saved blank, so editing one could never be saved.
    pk = store.upsert_packaging({"name": "Poly bag", "tare_oz": tare}, actor="t")
    assert pk["tare_oz"] == want
    again = store.upsert_packaging({"id": pk["id"], "name": "Poly bag", "tare_oz": str(pk["tare_oz"])}, actor="t")
    assert again["tare_oz"] == want


def test_packaging_refuses_a_negative_or_garbage_empty_weight(store):
    for bad in (-1, "-0.5", "abc"):
        with pytest.raises(InventoryError, match="empty weight"):
            store.upsert_packaging({"name": "Box", "tare_oz": bad}, actor="t")
