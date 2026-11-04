"""0.12.0 — the double-sell guard: a sale never silently closes a listing on another channel."""

from __future__ import annotations

import pytest
from inventory_plugin.store import InventoryError, same_channel


def _item(store, iid="X", qty=1):
    store.upsert_item({"id": iid, "name": "Thing " + iid, "quantity": qty}, actor="t")


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [("eBay", "ebay", True), ("eBay", "E-Bay", True), ("eBay", "Personal connection", False), ("", "", False)],
)
def test_same_channel_ignores_case_and_punctuation(a, b, same):
    assert same_channel(a, b) is same


def test_a_sale_on_the_listings_channel_closes_it_as_sold(store):
    _item(store)
    lst = store.add_listing("X", channel="eBay", url="https://www.ebay.com/itm/1", actor="t")
    sale = store.mark_sold("X", price=30, channel="ebay", actor="t")
    assert sale["needs_end"] == []
    assert store.get_item("X")["listings"][0]["state"] == "sold"
    assert store.listings_to_end() == []
    assert lst["state"] == "active"


def test_a_sale_elsewhere_flags_the_live_listing_instead_of_closing_it(store):
    _item(store)
    lst = store.add_listing("X", channel="eBay", url="https://www.ebay.com/itm/1", actor="t")
    sale = store.mark_sold("X", price=30, channel="Personal connection", sold_on="2026-11-01", actor="t")
    assert sale["needs_end"] == [lst["id"]]
    assert store.get_item("X")["status"] == "sold"
    [row] = store.listings_to_end()
    assert row["id"] == lst["id"] and row["state"] == "needs_end" and row["ended_on"] == ""
    assert row["item_name"] == "Thing X" and row["sold_channel"] == "Personal connection"
    assert row["sold_on"] == "2026-11-01" and row["url"] == "https://www.ebay.com/itm/1"
    actions = [(a["entity"], a["action"]) for a in store.audit_log(limit=10)]
    assert ("listing", "needs_end") in actions


def test_i_ended_it_closes_the_flag_and_leaves_the_item_sold(store):
    _item(store)
    lst = store.add_listing("X", channel="eBay", actor="t")
    store.mark_sold("X", price=30, channel="Facebook", actor="t")
    ended = store.end_listing(lst["id"], state="ended", actor="t")
    assert ended["state"] == "ended" and ended["ended_on"]
    assert store.listings_to_end() == []
    assert store.get_item("X")["status"] == "sold"


def test_needs_end_cannot_be_set_by_hand(store):
    _item(store)
    lst = store.add_listing("X", channel="eBay", actor="t")
    with pytest.raises(InventoryError, match="set by a sale"):
        store.end_listing(lst["id"], state="needs_end", actor="t")


def test_a_partial_sale_flags_nothing(store):
    _item(store, qty=3)
    store.add_listing("X", channel="eBay", actor="t")
    sale = store.mark_sold("X", price=10, channel="Local", quantity=1, actor="t")
    assert sale["needs_end"] == [] and sale["remaining_quantity"] == 2
    assert store.get_item("X")["listings"][0]["state"] == "active"
    assert store.listings_to_end() == []


def test_two_live_listings_one_sells_the_other_is_flagged(store):
    _item(store)
    ebay = store.add_listing("X", channel="eBay", actor="t")
    fb = store.add_listing("X", channel="Facebook", actor="t")
    sale = store.mark_sold("X", price=30, channel="eBay", actor="t")
    assert sale["needs_end"] == [fb["id"]]
    states = {lst["id"]: lst["state"] for lst in store.get_item("X")["listings"]}
    assert states == {ebay["id"]: "sold", fb["id"]: "needs_end"}


def test_the_api_lists_listings_to_end_and_ends_one(registry):
    import inventory_plugin
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    c, api = TestClient(app), "/api/plugins/inventory"
    c.post(api + "/items", json={"id": "X", "name": "Thing"})
    lid = c.post(api + "/items/X/listings", json={"channel": "eBay", "url": "https://www.ebay.com/itm/9"}).json()[
        "listing"
    ]["id"]
    c.post(api + "/items/X/sold", json={"price": 20, "channel": "Personal connection"})
    rows = c.get(api + "/listings/to-end").json()["listings"]
    assert [r["id"] for r in rows] == [lid]
    assert c.post(api + f"/listings/{lid}/end", json={"state": "ended"}).status_code == 200
    assert c.get(api + "/listings/to-end").json()["listings"] == []
