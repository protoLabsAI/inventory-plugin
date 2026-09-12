"""The store: money handling, CRUD, status transitions, the sale arithmetic, the roll-ups,
the audit trail."""

from __future__ import annotations

import pytest
from inventory_plugin.store import InventoryError, InventoryStore, normalize_status, to_cents


class TestMoney:
    def test_to_cents_reads_what_a_spreadsheet_holds(self):
        assert to_cents(12.5) == 1250
        assert to_cents("12.50") == 1250
        assert to_cents("$1,234.56") == 123456
        assert to_cents("") is None
        assert to_cents(None) is None
        assert to_cents("n/a") is None
        assert to_cents(True) is None  # a bool is not a price

    def test_no_float_drift(self):
        assert to_cents(0.29) == 29
        assert to_cents(1.005) == 100 or to_cents(1.005) == 101  # rounding, not truncation


class TestStatus:
    @pytest.mark.parametrize(
        "raw,expected,price",
        [
            ("Available", "available", None),
            ("", "available", None),
            ("Pending Sell", "pending", None),
            ("Planned Split", "planned", None),
            ("Sold ($5)", "sold", 500),
            ("Sold ($25)", "sold", 2500),
            ("sold", "sold", None),
            ("Listed on eBay", "listed", None),
            ("keep", "kept", None),
            ("withdrawn", "withdrawn", None),
        ],
    )
    def test_free_text_statuses_normalize(self, raw, expected, price):
        assert normalize_status(raw) == (expected, price)

    def test_garbage_status_is_refused(self):
        with pytest.raises(InventoryError, match="unknown status"):
            normalize_status("banana")


class TestLotsAndItems:
    def test_lot_upsert_creates_then_updates_only_given_fields(self, store):
        lot = store.upsert_lot({"id": "L1", "name": "Box", "acquisition_cost": "355.00"}, actor="t")
        assert lot["acquisition_cost"] == 355.0
        lot = store.upsert_lot({"id": "L1", "notes": "torn shrink"}, actor="t")
        assert lot["name"] == "Box" and lot["acquisition_cost"] == 355.0 and lot["notes"] == "torn shrink"

    def test_item_id_is_minted_when_absent(self, store):
        item = store.upsert_item({"name": "Thing"}, actor="t")
        assert item["id"].startswith("INV-") and item["status"] == "available"

    def test_new_item_needs_a_name(self, store):
        with pytest.raises(InventoryError, match="needs a name"):
            store.upsert_item({"lot_id": "x"}, actor="t")

    def test_update_touches_only_given_fields_and_keeps_money_exact(self, store):
        store.upsert_item({"id": "X", "name": "Thing", "retail": 43.5, "quantity": 2}, actor="t")
        item = store.upsert_item({"id": "X", "notes": "hi"}, actor="t")
        assert item["retail"] == 43.5 and item["quantity"] == 2 and item["notes"] == "hi"

    def test_list_filters(self, seeded):
        assert {i["id"] for i in seeded.list_items(status="sold")} == {"C"}
        assert {i["id"] for i in seeded.list_items(status="available,listed")} == {"A", "B", "D"}
        assert {i["id"] for i in seeded.list_items(category="dice")} == {"B"}
        assert {i["id"] for i in seeded.list_items(query="book")} == {"C"}
        with pytest.raises(InventoryError, match="unknown status"):
            seeded.list_items(status="nope")

    def test_delete_lot_refuses_while_items_remain(self, seeded):
        with pytest.raises(InventoryError, match="still has 4 item"):
            seeded.delete_lot("LOT-1", actor="t")
        assert seeded.delete_lot("LOT-1", actor="t", cascade=True) == 1
        assert seeded.list_items() == []

    def test_get_item_carries_its_evidence(self, seeded):
        item = seeded.get_item("C")
        assert item["status"] == "sold" and len(item["sales"]) == 1
        assert item["sales"][0]["net"] == 22.5  # 25 + 5 − 3.5 − 4


class TestPricing:
    def test_price_needs_a_basis(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        with pytest.raises(InventoryError, match="needs a basis"):
            store.set_price("X", target=10, basis="", actor="t")

    def test_set_price_records_the_observation_behind_it(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        item = store.set_price(
            "X",
            low=20,
            target=30,
            high=40,
            basis="eBay sold comps (22 sold, incl. shipping)",
            actor="agent",
            observation={"source": "ebay_sold", "n": 22, "p25": 20, "median": 30, "p75": 40, "query": "thing"},
        )
        assert (item["target_low"], item["target"], item["target_high"]) == (20.0, 30.0, 40.0)
        assert item["price_updated_on"]
        obs = store.get_item("X")["observations"]
        assert len(obs) == 1 and obs[0]["source"] == "ebay_sold" and obs[0]["n"] == 22 and obs[0]["median"] == 30.0

    def test_unknown_observation_source_is_refused(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        with pytest.raises(InventoryError, match="unknown source"):
            store.record_observation("X", source="vibes")


class TestListingsAndSales:
    def test_listing_moves_available_to_listed_and_back(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        listing = store.add_listing("X", channel="eBay", url="https://www.ebay.com/itm/1", price=30, actor="t")
        assert store.get_item("X")["status"] == "listed"
        store.end_listing(listing["id"], state="ended", actor="t")
        assert store.get_item("X")["status"] == "available"

    def test_sale_arithmetic_and_side_effects(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        store.add_listing("X", channel="eBay", price=30, actor="t")
        sale = store.mark_sold(
            "X", price=30, channel="eBay", fees=4.05, shipping_charged=6, shipping_cost=5.5, actor="t"
        )
        assert sale["net"] == 26.45  # 30 + 6 − 4.05 − 5.5
        item = store.get_item("X")
        assert item["status"] == "sold"
        assert item["listings"][0]["state"] == "sold"

    def test_sale_needs_price_and_channel(self, store):
        store.upsert_item({"id": "X", "name": "Thing"}, actor="t")
        with pytest.raises(InventoryError, match="needs a price"):
            store.mark_sold("X", price=None, channel="eBay")
        with pytest.raises(InventoryError, match="needs a channel"):
            store.mark_sold("X", price=5, channel="")

    def test_unknown_item_is_named(self, store):
        with pytest.raises(InventoryError, match="no item 'nope'"):
            store.mark_sold("nope", price=5, channel="x")


class TestRollups:
    def test_summary_reconciles_cost_remaining_and_realized(self, seeded):
        s = seeded.summary()
        lot = s["lots"][0]
        assert lot["acquisition_cost"] == 100.0
        assert lot["counts"]["sold"] == 1 and lot["counts"]["available"] == 3 and lot["counts"]["total"] == 4
        assert "planned" in lot["counts"]
        # remaining: A 20/30/40 + B (qty 2) 20/30/40 ; D unpriced
        assert lot["remaining"] == {"low": 40.0, "target": 60.0, "high": 80.0, "unpriced_items": 1}
        assert lot["realized"] == {"gross": 30.0, "net": 22.5}
        assert lot["projected_net_at_target"] == round(22.5 + 60 - 100, 2)
        assert s["totals"]["projected_net_at_target"] == lot["projected_net_at_target"]

    def test_summary_for_an_unknown_lot_is_an_error(self, seeded):
        with pytest.raises(InventoryError, match="no lot"):
            seeded.summary("nope")

    def test_stale_finds_old_listings_and_old_prices(self, store):
        store.upsert_item({"id": "OLD", "name": "Old"}, actor="t")
        store.set_price("OLD", target=10, basis="b", observed_on="2020-01-01", actor="t")
        store.add_listing("OLD", channel="eBay", listed_on="2020-01-02", actor="t")
        store.upsert_item({"id": "NEW", "name": "New"}, actor="t")
        store.set_price("NEW", target=10, basis="b", actor="t")
        out = store.stale(listed_days=14, price_days=30)
        assert [x["item_id"] for x in out["stale_listings"]] == ["OLD"]
        assert [x["id"] for x in out["stale_prices"]] == ["OLD"]


class TestAudit:
    def test_every_mutation_is_logged_with_its_actor(self, store):
        store.upsert_lot({"id": "L", "name": "Lot"}, actor="console")
        store.upsert_item({"id": "X", "name": "Thing", "lot_id": "L"}, actor="agent")
        store.set_price("X", target=10, basis="b", actor="agent")
        store.mark_sold("X", price=10, channel="eBay", actor="console")
        log = store.audit_log()
        actions = [(e["entity"], e["action"], e["actor"]) for e in log]
        assert ("lot", "create", "console") in actions
        assert ("item", "create", "agent") in actions
        assert ("sale", "create", "console") in actions
        assert all(isinstance(e["changes"], dict) for e in log)
        assert store.audit_log(entity_id="X")


class TestStoreFile:
    def test_reopening_the_file_keeps_the_data(self, tmp_path):
        p = tmp_path / "inv.db"
        InventoryStore(p).upsert_item({"id": "X", "name": "Thing"}, actor="t")
        assert InventoryStore(p).get_item("X")["name"] == "Thing"

    def test_parent_dirs_are_created(self, tmp_path):
        InventoryStore(tmp_path / "a" / "b" / "inv.db")
        assert (tmp_path / "a" / "b" / "inv.db").exists()
