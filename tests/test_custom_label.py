"""0.12.0 — the eBay Custom label (SKU) on a listing draft: bin first, capped at eBay's 50."""

from __future__ import annotations

import pytest
from inventory_plugin.store import CUSTOM_LABEL_MAX, InventoryError, custom_label

DESC = "New on sprue.\nAll parts present."


@pytest.mark.parametrize(
    ("bin_id", "item_id", "want"),
    [
        ("b03", "army-box-nib", "B03-army-box-nib"),
        ("", "MEK", "MEK"),
        ("B01", "Odd_Id", "B01-Odd_Id"),
    ],
)
def test_custom_label_is_bin_first_and_keeps_the_ids_case(bin_id, item_id, want):
    assert custom_label(bin_id, item_id) == want


def test_custom_label_is_capped_at_ebays_limit_keeping_the_bin():
    sku = custom_label("B12", "x" * 80)
    assert CUSTOM_LABEL_MAX == 50 and len(sku) == 50 and sku.startswith("B12-")


def _draft(store, iid="A", bin_id=""):
    store.upsert_item({"id": iid, "name": "Kit " + iid}, actor="t")
    store.set_price(iid, target=30, basis="comps", actor="t")
    if bin_id:
        store.create_bins(count=2, actor="t")
        store.move_items([iid], bin_id, actor="t")
    store.request_draft(iid)
    return store.write_draft(iid, "Kit title", DESC)


def test_the_draft_carries_the_sku_and_copying_it_is_remembered(store):
    d = _draft(store, bin_id="B01")
    assert d["sku"] == "B01-A" and d["sku_copied"] == "" and d["sku_stale"] is False
    d = store.draft_copied("A", "sku")
    assert d["sku_copied"] == "B01-A" and d["state"] == "copied" and d["sku_stale"] is False


def test_moving_bins_after_copying_the_sku_asks_for_an_update(store):
    _draft(store, bin_id="B01")
    store.draft_copied("A", "sku")
    store.draft_live("A", "https://www.ebay.com/itm/123456789012")
    store.move_items(["A"], "B02", actor="t")
    d = store.get_draft("A")
    assert d["sku"] == "B02-A" and d["sku_stale"] is True
    assert [x["item_id"] for x in store.list_drafts() if x["sku_stale"]] == ["A"]
    d = store.draft_sku_updated("A", actor="t")
    assert d["sku_copied"] == "B02-A" and d["sku_stale"] is False


def test_no_update_asked_when_the_sku_was_never_copied_or_the_item_sold(store):
    _draft(store, bin_id="B01")
    store.draft_copied("A", "title")
    store.move_items(["A"], "B02", actor="t")
    assert store.get_draft("A")["sku_stale"] is False  # they never pasted the SKU
    store.draft_copied("A", "sku")
    store.move_items(["A"], "B01", actor="t")
    assert store.get_draft("A")["sku_stale"] is True
    store.mark_sold("A", price=30, channel="eBay", actor="t")
    assert store.get_draft("A")["sku_stale"] is False


def test_copied_what_is_still_checked(store):
    _draft(store)
    with pytest.raises(InventoryError, match="title, price, description or sku"):
        store.draft_copied("A", "photos")
