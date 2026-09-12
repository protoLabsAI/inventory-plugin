"""Round-1 review findings, each pinned: atomic writes, the sold guard, non-clobbering
re-import, sale idempotency and partial quantities, strict money, path confinement,
lot validation, delete snapshots, alias preference, the unassigned roll-up."""

from __future__ import annotations

import json

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.csvio import export_csv, import_csv
from inventory_plugin.store import InventoryError, InventoryStore, normalize_status, to_cents
from inventory_plugin.tools import resolve_workspace_path


class TestAtomicity:
    def test_a_failing_sale_leaves_nothing_behind(self, tmp_path):
        class Flaky(InventoryStore):
            @staticmethod
            def _audit(con, entity, entity_id, action, actor, changes=None):
                if entity == "sale":
                    raise RuntimeError("audit write failed")
                InventoryStore._audit(con, entity, entity_id, action, actor, changes)

        store = Flaky(tmp_path / "inv.db")
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        store.add_listing("X", channel="eBay", price=10, actor="t")
        with pytest.raises(RuntimeError):
            store.mark_sold("X", price=10, channel="eBay", actor="t")
        item = InventoryStore(tmp_path / "inv.db").get_item("X")
        assert item["status"] == "listed" and item["sales"] == [] and item["listings"][0]["state"] == "active"

    def test_a_bad_observation_rolls_the_price_back(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        with pytest.raises(InventoryError, match="unknown source"):
            store.set_price("X", target=10, basis="b", actor="t", observation={"source": "vibes"})
        with pytest.raises(InventoryError, match="unknown observation field"):
            store.set_price("X", target=10, basis="b", actor="t", observation={"source": "ebay_sold", "bogus": 1})
        item = store.get_item("X")
        assert item["target"] is None and item["price_basis"] == "" and item["observations"] == []

    def test_set_price_on_an_unknown_item_says_so(self, store):
        with pytest.raises(InventoryError, match="no item 'nope'"):
            store.set_price("nope", target=10, basis="b", actor="t")


class TestSoldGuard:
    @pytest.mark.parametrize("status", ["sold", "Sold", "SOLD", "Sold ($5)", "sold out"])
    def test_status_sold_by_hand_is_refused_in_every_spelling(self, store, status):
        with pytest.raises(InventoryError, match="mark_sold"):
            store.upsert_item({"name": "Thing", "status": status}, actor="t")

    def test_an_already_sold_item_can_be_updated_without_tripping_the_guard(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        store.mark_sold("X", price=5, channel="x", actor="t")
        assert store.upsert_item({"id": "X", "notes": "shipped", "status": "Sold"}, actor="t")["notes"] == "shipped"

    def test_tool_and_api_refuse_it_too(self, registry):
        inventory_plugin.register(registry)
        t = {x.name: x for x in registry.tools}
        assert (
            "inventory_mark_sold"
            in json.loads(t["inventory_upsert_item"].invoke({"name": "x", "status": "Sold"}))["error"]
        )


class TestSales:
    def test_a_retried_sale_does_not_double_the_revenue(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        store.mark_sold("X", price=10, channel="x", actor="t")
        with pytest.raises(InventoryError, match="already sold .*force=True"):
            store.mark_sold("X", price=10, channel="x", actor="t")
        store.mark_sold("X", price=10, channel="x", actor="t", force=True)
        assert len(store.get_item("X")["sales"]) == 2

    def test_kept_and_withdrawn_cannot_be_sold_or_listed(self, store):
        store.upsert_item({"id": "K", "name": "Keeper", "status": "kept"}, actor="t")
        with pytest.raises(InventoryError, match="already kept"):
            store.mark_sold("K", price=1, channel="x", actor="t")
        with pytest.raises(InventoryError, match="is kept"):
            store.add_listing("K", channel="x", actor="t")

    def test_partial_quantity_sale_keeps_the_rest_on_the_shelf(self, store):
        store.upsert_lot({"id": "L", "name": "L"}, actor="t")
        store.upsert_item({"id": "X", "name": "Dice", "quantity": 4, "lot_id": "L"}, actor="t")
        store.set_price("X", target=10, basis="b", actor="t")
        sale = store.mark_sold("X", price=10, channel="x", quantity=1, actor="t")
        assert sale["remaining_quantity"] == 3 and sale["item_status"] == "available"
        item = store.get_item("X")
        assert item["quantity"] == 3 and item["status"] == "available"
        assert store.summary("L")["lots"][0]["remaining"]["target"] == 30.0
        store.mark_sold("X", price=10, channel="x", quantity=3, actor="t")
        assert store.get_item("X")["status"] == "sold"

    def test_end_listing_as_sold_is_refused(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        lst = store.add_listing("X", channel="x", actor="t")
        with pytest.raises(InventoryError, match="mark_sold"):
            store.end_listing(lst["id"], state="sold", actor="t")
        assert store.get_item("X")["status"] == "listed"


class TestMoney:
    @pytest.mark.parametrize(
        "raw,cents",
        [
            ("12,50", 1250),
            ("1,250", 125000),
            ("$1,234.56", 123456),
            ("(5.00)", -500),
            ("-$5", -500),
            ("$-5", -500),
            ("+7", 700),
            ("54%", None),
            ("2026-07", None),
            ("n/a", None),
            ("tbd", None),
            ("", None),
            ("12.345", 1235),
        ],
    )
    def test_anchored_parsing(self, raw, cents):
        assert to_cents(raw) == cents

    def test_garbage_money_is_an_error_not_a_null_or_zero(self, store):
        with pytest.raises(InventoryError, match="acquisition_cost must be a money amount"):
            store.upsert_lot({"id": "L", "acquisition_cost": "n/a"}, actor="t")
        store.upsert_lot({"id": "L", "name": "L", "acquisition_cost": 355}, actor="t")
        assert (
            store.upsert_lot({"id": "L", "acquisition_cost": ""}, actor="t")["acquisition_cost"] == 355.0
        )  # blank = unchanged
        assert (
            store.upsert_lot({"id": "L", "acquisition_cost": None}, actor="t")["acquisition_cost"] == 0.0
        )  # explicit null = no cost
        store.upsert_item({"id": "X", "name": "Thing", "retail": 40}, actor="t")
        with pytest.raises(InventoryError, match="retail must be a money amount"):
            store.upsert_item({"id": "X", "retail": "tbd"}, actor="t")
        assert store.upsert_item({"id": "X", "retail": ""}, actor="t")["retail"] == 40.0
        assert store.upsert_item({"id": "X", "retail": None}, actor="t")["retail"] is None

    def test_status_dates_are_not_prices_and_words_are_whole(self):
        assert normalize_status("Sold 9/12") == ("sold", None)
        assert normalize_status("Sold ($25)") == ("sold", 2500)
        assert normalize_status("sold $7.50") == ("sold", 750)
        for bad in ("delisted", "unavailable", "relisting"):
            with pytest.raises(InventoryError):
                normalize_status(bad)


class TestReimport:
    def test_blank_cells_and_sheet_statuses_never_clobber_agent_work(self, store):
        store.upsert_lot({"id": "L", "name": "L"}, actor="t")
        sheet = "inventory_id,lot_id,item,target_price_usd,price_source,status\nX,L,Thing,,,Available\n"
        import_csv(store, "inventory_id,lot_id,item,target_price_usd,status\nX,L,Thing,12,Available\n", actor="t")
        store.set_price(
            "X", target=40, basis="eBay sold comps (22)", actor="agent", observation={"source": "ebay_sold", "n": 22}
        )
        store.add_listing("X", channel="eBay", price=40, actor="agent")
        out = import_csv(store, sheet, actor="t")
        assert out["updated"] == 1 and out["created"] == 0
        item = store.get_item("X")
        assert item["target"] == 40.0 and item["price_basis"] == "eBay sold comps (22)"
        assert item["status"] == "listed" and item["listings"][0]["state"] == "active"

    def test_a_sold_item_is_not_walked_back_by_an_old_sheet(self, store):
        store.upsert_lot({"id": "L", "name": "L"}, actor="t")
        import_csv(store, "inventory_id,lot_id,item,status\nX,L,Thing,Available\n", actor="t")
        store.mark_sold("X", price=9, channel="x", actor="t")
        import_csv(store, "inventory_id,lot_id,item,status\nX,L,Thing,Available\n", actor="t")
        assert store.get_item("X")["status"] == "sold"

    def test_a_sheet_without_ids_matches_by_lot_and_name(self, store):
        store.upsert_lot({"id": "L", "name": "L"}, actor="t")
        sheet = "lot_id,name,target\nL,Thing,10\nL,Other,5\n"
        first = import_csv(store, sheet, actor="t")
        second = import_csv(store, sheet, actor="t")
        assert first["created"] == 2 and second["created"] == 0 and second["updated"] == 2
        assert len(store.list_items()) == 2
        assert any("no id column" in w for w in second["warnings"])

    def test_an_unknown_lot_gets_a_stub_with_a_warning(self, store):
        out = import_csv(store, "inventory_id,lot_id,item\nX,NEWLOT,Thing\n", actor="t")
        assert out["created"] == 1 and store.get_lot("NEWLOT")["name"] == "NEWLOT"
        assert any("created a stub" in w for w in out["warnings"])

    def test_alias_preference_beats_sheet_order(self, store):
        out = import_csv(store, "id,name,price,target_price_usd\nX,Thing,99,12\n", actor="t")
        assert store.get_item("X")["target"] == 12.0
        assert "price" in out["ignored_columns"] and out["mapped_columns"]["target_price_usd"] == "target"

    def test_malformed_csv_is_an_answer_not_an_exception(self, store):
        import csv as _csv

        big = "id,name\nX," + "a" * (_csv.field_size_limit() + 10) + "\n"
        out = import_csv(store, big, actor="t")
        assert out["ok"] is False and "parse" in out["error"]


class TestLotsAndRollups:
    @pytest.mark.parametrize("bad", ["BB/23", "has space", "a?b", "x#1", "", "-lead", "x" * 65])
    def test_ids_that_cannot_travel_in_a_url_are_refused(self, store, bad):
        with pytest.raises(InventoryError, match="id .* is not usable|needs an id"):
            store.upsert_lot({"id": bad, "name": "L"}, actor="t")
        if bad:
            with pytest.raises(InventoryError, match="is not usable"):
                store.upsert_item({"id": bad, "name": "Thing"}, actor="t")

    def test_names_cannot_be_blanked(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        with pytest.raises(InventoryError, match="name cannot be blank"):
            store.upsert_item({"id": "X", "name": "  "}, actor="t")
        store.upsert_lot({"id": "L", "name": "Lot"}, actor="t")
        with pytest.raises(InventoryError, match="name cannot be blank"):
            store.upsert_lot({"id": "L", "name": ""}, actor="t")

    def test_an_item_needs_an_existing_lot_or_none(self, store):
        with pytest.raises(InventoryError, match="no lot 'GHOST'"):
            store.upsert_item({"name": "Thing", "lot_id": "GHOST"}, actor="t")
        store.upsert_item({"name": "Loose", "lot_id": ""}, actor="t")  # no lot is fine

    def test_unassigned_items_still_count_in_the_totals(self, store):
        store.upsert_item({"id": "X", "name": "Loose"}, actor="t")
        store.set_price("X", target=20, basis="b", actor="t")
        store.upsert_item({"id": "Y", "name": "Loose sold"}, actor="t")
        store.mark_sold("Y", price=50, channel="x", actor="t")
        s = store.summary()
        assert s["lots"] == [] and s["items_without_lot"] == 2
        assert s["unassigned"]["remaining"]["target"] == 20.0 and s["unassigned"]["realized"]["net"] == 50.0
        assert s["totals"]["realized_net"] == 50.0 and s["totals"]["remaining"]["target"] == 20.0

    def test_deletes_snapshot_what_they_removed(self, store):
        store.upsert_lot({"id": "L", "name": "Box", "acquisition_cost": 100}, actor="t")
        store.upsert_item({"id": "X", "name": "Thing", "lot_id": "L"}, actor="t")
        store.mark_sold("X", price=9, channel="x", actor="t")
        store.delete_lot("L", actor="console", cascade=True)
        log = {(e["entity"], e["action"]): e for e in store.audit_log()}
        assert log[("item", "delete")]["changes"]["snapshot"]["item"]["name"] == "Thing"
        assert log[("item", "delete")]["changes"]["snapshot"]["sales"][0]["price_cents"] == 900
        assert log[("lot", "delete")]["changes"]["snapshot"]["acquisition_cost_cents"] == 10000

    def test_quantity_zero_counts_as_nothing_left(self, store):
        store.upsert_lot({"id": "L", "name": "L"}, actor="t")
        store.upsert_item({"id": "X", "name": "Thing", "lot_id": "L", "quantity": 0}, actor="t")
        store.set_price("X", target=10, basis="b", actor="t")
        assert store.summary("L")["lots"][0]["remaining"]["target"] == 0.0


class TestPathsAndExport:
    def test_paths_stay_inside_the_workspace(self, tmp_path):
        cfg = {"workspace_dir": str(tmp_path)}
        assert resolve_workspace_path(cfg, "out/x.csv") == (tmp_path / "out" / "x.csv").resolve()
        for bad in ("../x.csv", "/etc/passwd", str(tmp_path.parent / "x.csv")):
            with pytest.raises(InventoryError, match="outside the workspace"):
                resolve_workspace_path(cfg, bad)

    def test_tool_refuses_to_write_outside_and_reads_windows_sheets(self, registry, tmp_path):
        inventory_plugin.register(registry)
        t = {x.name: x for x in registry.tools}
        assert (
            "outside the workspace" in json.loads(t["inventory_export_csv"].invoke({"path": "../escape.csv"}))["error"]
        )
        (tmp_path / "win.csv").write_bytes("id,name\nX,Caf\xe9 set\n".encode("cp1252"))
        out = json.loads(t["inventory_import_csv"].invoke({"path": "win.csv"}))
        assert out["ok"] and out["created"] == 1
        assert json.loads(t["inventory_get"].invoke({"item_id": "X"}))["item"]["name"] == "Café set"

    def test_sales_export_exists_and_says_what_export_is(self, seeded):
        text = export_csv(seeded, kind="sales")
        assert "net" in text.splitlines()[0] and "22.5" in text
        with pytest.raises(InventoryError, match="unknown export kind"):
            export_csv(seeded, kind="everything")


def test_api_import_reports_a_malformed_sheet_as_400(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    c = TestClient(app)
    import csv as _csv

    big = "id,name\nX," + "a" * (_csv.field_size_limit() + 10) + "\n"
    r = c.post("/api/plugins/inventory/import", json={"csv": big})
    assert r.status_code == 400 and "parse" in r.json()["detail"]
