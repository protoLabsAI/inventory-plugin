"""Bins (where an item physically is) and the pick list (sales not yet packed)."""

from __future__ import annotations

import json
import sqlite3

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.store import InventoryError, InventoryStore


def _old_db(path):
    """A database as 0.5.0 left it: no bins table, no items.bin_id, no sales.packed_on."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE lots (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          acquisition_cost_cents INTEGER NOT NULL DEFAULT 0, acquired_on TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE items (id TEXT PRIMARY KEY, lot_id TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL, condition TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL DEFAULT 1,
          model_count INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', cost_basis_cents INTEGER,
          status TEXT NOT NULL DEFAULT 'available', target_low_cents INTEGER, target_cents INTEGER,
          target_high_cents INTEGER, retail_cents INTEGER, price_basis TEXT NOT NULL DEFAULT '',
          price_updated_on TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          system TEXT NOT NULL DEFAULT '');
        CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, channel TEXT NOT NULL,
          sold_on TEXT NOT NULL, price_cents INTEGER NOT NULL, shipping_charged_cents INTEGER NOT NULL DEFAULT 0,
          fees_cents INTEGER NOT NULL DEFAULT 0, shipping_cost_cents INTEGER NOT NULL DEFAULT 0,
          net_cents INTEGER NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '');
        INSERT INTO items(id, name, status, created_at, updated_at) VALUES ('OLD', 'Old sale', 'sold', 't', 't');
        INSERT INTO items(id, name, status, created_at, updated_at) VALUES ('STOCK', 'On the shelf', 'available', 't', 't');
        INSERT INTO sales(item_id, channel, sold_on, price_cents, net_cents) VALUES ('OLD', 'eBay', '2026-09-13', 12500, 10543);
        INSERT INTO sales(item_id, channel, sold_on, price_cents, net_cents) VALUES ('OLD', 'eBay', '', 100, 100);
        """
    )
    con.commit()
    con.close()


class TestMigration:
    def test_an_old_database_gains_bins_and_its_past_sales_are_already_packed(self, tmp_path):
        path = tmp_path / "old.db"
        _old_db(path)
        store = InventoryStore(path)
        assert store.to_pack() == []  # history never lands on the pick list
        sales = store.list_sales()
        assert {s["packed_on"] for s in sales} >= {"2026-09-13"} and all(s["packed_on"] for s in sales)
        assert store.get_item("STOCK")["bin_id"] == ""
        assert store.list_bins() == {"bins": [], "unbinned_unsold": 1}
        InventoryStore(path)  # re-open: idempotent, nothing re-run
        assert store.to_pack() == []

    def test_a_new_sale_after_migration_waits_on_the_pick_list(self, tmp_path):
        path = tmp_path / "old.db"
        _old_db(path)
        store = InventoryStore(path)
        store.mark_sold("STOCK", price=10, channel="eBay", actor="t")
        assert [s["item_id"] for s in store.to_pack()] == ["STOCK"]


class TestBins:
    def test_minted_ids_continue_the_sequence_and_duplicates_are_refused(self, store):
        assert [b["id"] for b in store.create_bins(count=3, actor="t")] == ["B01", "B02", "B03"]
        store.create_bins(bin_id="b10", label="Overflow", actor="t")
        assert [b["id"] for b in store.create_bins(count=2, actor="t")] == ["B11", "B12"]
        with pytest.raises(InventoryError, match="already exists"):
            store.create_bins(bin_id="B02", actor="t")
        with pytest.raises(InventoryError, match="not both"):
            store.create_bins(bin_id="B99", count=2, actor="t")
        with pytest.raises(InventoryError):
            store.create_bins(bin_id="B 7", actor="t")
        assert store.update_bin("b10", label="Blood Bowl overflow", actor="t")["label"] == "Blood Bowl overflow"

    def test_moves_are_audited_with_from_and_to_and_all_or_nothing(self, seeded):
        seeded.create_bins(count=2, actor="t")
        out = seeded.move_items(["A", "B"], "b01", actor="t")
        assert out == {"bin_id": "B01", "moved": ["A", "B"], "unchanged": []}
        out = seeded.move_items(["A", "B"], "B02", actor="t")
        assert out["moved"] == ["A", "B"]
        assert seeded.move_items(["A"], "B02", actor="t")["unchanged"] == ["A"]
        moves = [a["changes"] for a in seeded.audit_log(entity_id="A") if a["action"] == "move"]
        assert moves[0] == {"bin": ["B01", "B02"]} and moves[1] == {"bin": ["", "B01"]}
        with pytest.raises(InventoryError, match="no bin"):
            seeded.move_items(["A"], "B77", actor="t")
        with pytest.raises(InventoryError, match="no item"):
            seeded.move_items(["A", "NOPE"], "B01", actor="t")
        assert seeded.get_item("A")["bin_id"] == "B02"  # the refused move changed nothing
        assert seeded.move_items(["A"], "", actor="t")["moved"] == ["A"]
        assert seeded.get_item("A")["bin_id"] == ""

    def test_a_bin_with_items_cannot_be_deleted(self, seeded):
        seeded.create_bins(count=1, actor="t")
        seeded.move_items(["A"], "B01", actor="t")
        with pytest.raises(InventoryError, match="still holds 1"):
            seeded.delete_bin("B01", actor="t")
        seeded.move_items(["A"], "", actor="t")
        assert seeded.delete_bin("b01", actor="t") == 1
        assert seeded.delete_bin("B01", actor="t") == 0

    def test_list_counts_items_and_unsold_value_and_the_unbinned(self, seeded):
        seeded.create_bins(count=2, actor="t")
        seeded.move_items(["A", "B", "C"], "B01", actor="t")  # C is sold: counted, worth nothing
        out = seeded.list_bins()
        b1, b2 = out["bins"]
        assert (b1["id"], b1["items"], b1["value_at_target"]) == ("B01", 3, 60.0)  # 30 + 15 x 2
        assert (b2["items"], b2["value_at_target"]) == (0, 0.0)
        assert out["unbinned_unsold"] == 1  # D
        assert sorted(i["id"] for i in seeded.list_items(bin_id="B01")) == ["A", "B", "C"]
        assert [i["id"] for i in seeded.list_items(bin_id="-")] == ["D"]
        assert sorted(i["id"] for i in seeded.list_items(query="b01")) == ["A", "B", "C"]


def _pack_seeded_history(store):
    """The seeded fixture records a sale of C on a fresh database: on the pick list, as any new
    sale is. Clear it so each test starts from an empty list."""
    for sale in store.to_pack():
        store.mark_packed(sale["id"], actor="t")


class TestPickList:
    def test_packing_clears_a_sold_out_item_from_its_bin_but_not_a_partial_sale(self, seeded):
        assert [p["item_id"] for p in seeded.to_pack()] == ["C"]  # a new sale waits to be packed
        _pack_seeded_history(seeded)
        seeded.create_bins(count=1, actor="t")
        seeded.move_items(["A", "B"], "B01", actor="t")
        a = seeded.mark_sold("A", price=30, channel="eBay", actor="t")
        b = seeded.mark_sold("B", price=15, channel="local", quantity=1, actor="t")  # 1 of 2
        picks = seeded.to_pack()
        assert [(p["item_id"], p["bin_id"]) for p in picks] == [("A", "B01"), ("B", "B01")]
        assert picks[0]["item_name"] == "Team A"
        seeded.mark_packed(a["id"], actor="t")
        assert seeded.get_item("A")["bin_id"] == ""  # it left the bin
        seeded.mark_packed(b["id"], actor="t")
        assert seeded.get_item("B")["bin_id"] == "B01"  # one unit still on the shelf
        assert seeded.to_pack() == []
        with pytest.raises(InventoryError, match="already packed"):
            seeded.mark_packed(a["id"], actor="t")
        with pytest.raises(InventoryError, match="no sale"):
            seeded.mark_packed(9999, actor="t")

    def test_unbinned_sales_still_show_so_the_list_is_never_wrong_by_omission(self, seeded):
        _pack_seeded_history(seeded)
        seeded.mark_sold("A", price=30, channel="eBay", actor="t")
        assert [(p["item_id"], p["bin_id"]) for p in seeded.to_pack()] == [("A", "")]

    def test_a_sheet_import_of_history_is_already_packed(self, store):
        from inventory_plugin.csvio import import_csv

        out = import_csv(store, "id,item,status,sold_price_usd\nX1,Old team,sold,40\n", actor="t")
        assert out["ok"] and out["sales_recorded"] == 1
        assert store.to_pack() == []


def test_un_sell_returns_the_item_to_stock_and_keeps_the_sale_on_the_books(seeded):
    """The drawer's Return to stock: status back to available through the normal write; the
    sale row and the realized money stay (a refund is recorded separately)."""
    seeded.mark_sold("A", price=30, channel="eBay", actor="t")
    before = seeded.summary()["totals"]
    item = seeded.upsert_item({"id": "A", "status": "available"}, actor="console")
    assert item["status"] == "available"
    assert [s["item_id"] for s in seeded.get_item("A")["sales"]] == ["A"]
    after = seeded.summary()["totals"]
    assert after["realized_net"] == before["realized_net"]
    assert after["remaining"]["target"] == before["remaining"]["target"] + 30
    assert any(a["changes"].get("status") == "available" for a in seeded.audit_log(entity_id="A"))
    with pytest.raises(InventoryError, match="mark_sold"):
        seeded.upsert_item({"id": "A", "status": "sold"}, actor="console")  # and back is never by hand


def test_bins_over_http(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    c = TestClient(app)
    base = "/api/plugins/inventory"
    assert c.get(base + "/bins").json() == {"bins": [], "unbinned_unsold": 0}
    assert [b["id"] for b in c.post(base + "/bins", json={"count": 2}).json()["bins"]] == ["B01", "B02"]
    assert c.put(base + "/bins/B02", json={"label": "Terrain"}).json()["bin"]["label"] == "Terrain"
    iid = c.post(base + "/items", json={"name": "Team"}).json()["item"]["id"]
    assert c.post(base + "/bins/move", json={"item_ids": "x", "bin_id": "B01"}).status_code == 400
    r = c.post(base + "/bins/move", json={"item_ids": [iid], "bin_id": "B01"})
    assert r.status_code == 200 and r.json()["moved"] == [iid]
    assert c.post(base + "/bins/move", json={"item_ids": [iid], "bin_id": "B09"}).status_code == 400
    assert [i["id"] for i in c.get(base + "/items", params={"bin_id": "B01"}).json()["items"]] == [iid]
    assert c.delete(base + "/bins/B01").status_code == 400  # not empty
    assert c.delete(base + "/bins/B07").status_code == 404
    sale = c.post(base + f"/items/{iid}/sold", json={"price": 10, "channel": "local"}).json()["sale"]
    assert [s["bin_id"] for s in c.get(base + "/to-pack").json()["sales"]] == ["B01"]
    assert c.post(base + f"/sales/{sale['id']}/packed").status_code == 200
    assert c.post(base + f"/sales/{sale['id']}/packed").status_code == 400
    assert c.get(base + "/to-pack").json()["sales"] == []
    assert c.delete(base + "/bins/B01").json() == {"ok": True}  # packing emptied it
    topics = [t for t, _ in registry.events]
    assert "bin.changed" in topics and "sale.packed" in topics


def test_bin_tools(registry):
    inventory_plugin.register(registry)
    t = {x.name: x for x in registry.tools}
    made = json.loads(t["inventory_bins"].invoke({"action": "create", "count": 2}))
    assert [b["id"] for b in made["bins"]] == ["B01", "B02"]
    iid = json.loads(t["inventory_upsert_item"].invoke({"name": "Team"}))["item"]["id"]
    moved = json.loads(t["inventory_move_to_bin"].invoke({"item_ids": [iid], "bin_id": "B02"}))
    assert moved["ok"] and moved["moved"] == [iid]
    bad = json.loads(t["inventory_move_to_bin"].invoke({"item_ids": [iid], "bin_id": "B44"}))
    assert not bad["ok"] and "no bin" in bad["error"]
    listed = json.loads(t["inventory_bins"].invoke({"action": "list"}))
    assert [(b["id"], b["items"]) for b in listed["bins"]] == [("B01", 0), ("B02", 1)]
    assert json.loads(t["inventory_list"].invoke({"bin_id": "B02"}))["count"] == 1
    sale = json.loads(t["inventory_mark_sold"].invoke({"item_id": iid, "price": 5, "channel": "local"}))["sale"]
    picks = json.loads(t["inventory_packing"].invoke({"action": "list"}))
    assert picks["count"] == 1 and picks["sales"][0]["bin_id"] == "B02"
    assert json.loads(t["inventory_packing"].invoke({"action": "packed", "sale_id": sale["id"]}))["ok"]
    assert json.loads(t["inventory_bins"].invoke({"action": "delete", "bin_id": "B02"}))["ok"]
    assert not json.loads(t["inventory_bins"].invoke({"action": "nope"}))["ok"]
