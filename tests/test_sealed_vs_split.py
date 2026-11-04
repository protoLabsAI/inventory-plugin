"""0.14.0: sealed vs split. A box scan's pricing turn also prices the box sealed (kept on the
scan, never as an item); the view compares that with the pieces at their targets, net of fees
when both fee figures are known, and can turn the scan back into one sealed item."""

from __future__ import annotations

import json

import pytest
from inventory_plugin import scans as sc
from inventory_plugin.store import InventoryError, InventoryStore

from tests.test_photos import jpeg
from tests.test_scans import _client, _store, host  # noqa: F401 — host is a fixture
from tests.test_view import _run_page


def _confirmed_box(store, pieces=(("Unit A", False), ("Unit B", False), ("Unit C", True))):
    s = store.create_scan("box", paid=100, actor="console")
    store.add_scan_photo(s["id"], jpeg())
    store.propose_scan(
        s["id"],
        {"name": "Example Army Box", "system": "Example System", "condition": "NIB", "confidence": 0.9},
    )
    units = [{"name": n, "keep": k} for n, k in pieces]
    out = store.confirm_scan(
        s["id"], {"name": "Example Army Box", "system": "Example System", "units": units}, actor="console"
    )
    return out


def _price(store, scan, targets):
    sell = [store.get_item(i) for i in scan["item_ids"]]
    for it in sell:
        if it["status"] == "planned" and it["name"] in targets:
            store.set_price(it["id"], target=targets[it["name"]], basis="comps", actor="agent")
    store.request_pricing(scan["id"], actor="console")


@pytest.mark.parametrize(
    ("sealed", "pieces", "verdict", "basis"),
    [
        # Helsmiths-style: the sealed box beats the pieces once fees are counted.
        (
            {"target": 170, "sealed_fees": 23, "split_fees": 30},
            [{"target": 60}, {"target": 60}, {"target": 30}],
            "sealed",
            "net of fees",
        ),
        # Combat Patrol-style: the pieces add up to much more than the box.
        (
            {"target": 110, "sealed_fees": 15, "split_fees": 25},
            [{"target": 60}, {"target": 50}, {"target": 45}],
            "split",
            "net of fees",
        ),
        # Close either way: about even.
        ({"target": 100}, [{"target": 50}, {"target": 48}], "even", "gross (fees not known)"),
        # A piece without a price makes the split total low: say so, don't recommend.
        ({"target": 100}, [{"target": 30}, {"target": None}], "incomplete", "gross (fees not known)"),
    ],
)
def test_the_comparison_and_its_verdict(sealed, pieces, verdict, basis):
    c = InventoryStore.sealed_vs_split(sealed, pieces)
    assert c["verdict"] == verdict and c["basis"] == basis
    assert c["difference"] == round(c["sealed"] - c["split"], 2)


def test_quantities_count_and_no_sealed_price_means_no_comparison():
    c = InventoryStore.sealed_vs_split({"target": 100}, [{"target": 20, "quantity": 3}])
    assert c["split_gross"] == 60.0 and c["verdict"] == "sealed"
    assert InventoryStore.sealed_vs_split({"target": None}, [{"target": 10}]) is None
    assert InventoryStore.sealed_vs_split(None, [{"target": 10}]) is None


def test_the_sealed_price_is_kept_on_the_scan_and_validated(store):
    scan = _confirmed_box(store)
    with pytest.raises(InventoryError, match="no pricing in progress"):
        store.record_sealed_price(scan["id"], target=150, basis="b")
    _price(store, scan, {"Unit A": 60, "Unit B": 50})
    with pytest.raises(InventoryError, match="needs its basis"):
        store.record_sealed_price(scan["id"], target=150)
    with pytest.raises(InventoryError, match="negative"):
        store.record_sealed_price(scan["id"], target=150, basis="b", sealed_fees=-1)
    out = store.record_sealed_price(
        scan["id"],
        target=150,
        low=140,
        high=170,
        n=8,
        query="example army box sealed",
        basis="eBay sold comps (8 sold)",
        sealed_fees=20,
        split_fees=15,
    )
    s = out["pricing"]["sealed"]
    assert (s["target"], s["low"], s["high"], s["n"], s["sealed_fees"]) == (150.0, 140.0, 170.0, 8, 20.0)
    c = out["sealed_vs_split"]
    assert (c["split_gross"], c["sealed"], c["split"], c["verdict"]) == (110.0, 130.0, 95.0, "sealed")
    # nothing was created for the box itself
    assert len(out["item_ids"]) == 3
    assert any(a["action"] == "sealed_priced" for a in store.audit_log(entity_id=scan["id"]))
    unit = store.create_scan("unit", actor="console")
    with pytest.raises(InventoryError, match="only a box scan"):
        store.record_sealed_price(unit["id"], target=1, basis="b")


def test_no_sealed_comps_is_recorded_without_a_comparison(store):
    scan = _confirmed_box(store)
    _price(store, scan, {"Unit A": 60})
    out = store.record_sealed_price(scan["id"], target=None, basis="Only 1 sealed sale")
    assert out["pricing"]["sealed"]["basis"] == "Only 1 sealed sale" and out["sealed_vs_split"] is None


def test_sell_sealed_swaps_the_pieces_for_one_sealed_item(store):
    scan = _confirmed_box(store, pieces=(("Unit A", False), ("Unit B", False)))
    _price(store, scan, {"Unit A": 40, "Unit B": 40})
    store.record_sealed_price(
        scan["id"], target=150, low=140, high=160, n=6, query="q", basis="eBay sold comps (6 sold)"
    )
    old = list(scan["item_ids"])
    out = store.sell_sealed(scan["id"], actor="console")
    (iid,) = out["item_ids"]
    it = store.get_item(iid)
    assert (it["name"], it["condition"], it["status"], it["target"], it["system"]) == (
        "Example Army Box",
        "NIB",
        "available",
        150.0,
        "Example System",
    )
    assert it["price_basis"] == "eBay sold comps (6 sold)"
    assert [store.get_item(i) for i in old] == [None, None]
    (obs,) = it["observations"]
    assert (obs["source"], obs["n"], obs["query"]) == ("ebay_sold", 6, "q")
    acts = [a["action"] for a in store.audit_log(entity_id=scan["id"])]
    assert "sell_sealed" in acts
    # once chosen, the scan stops comparing (it would compare the sealed item with itself)
    assert "sealed_vs_split" not in out
    assert out["pricing"]["sealed"]["chosen"] == "sealed" and out["pricing"]["sealed"]["item_id"] == iid
    assert "sealed_vs_split" not in store.get_scan(scan["id"])
    assert store.split_check(scan["lot_id"]) is None
    with pytest.raises(InventoryError, match="already being sold sealed"):
        store.sell_sealed(scan["id"])
    assert {a["action"] for a in store.audit_log(limit=500) if a["entity_id"] in old} >= {"delete"}


def test_sell_sealed_is_refused_once_the_box_is_open(store):
    scan = _confirmed_box(store)  # Unit C is kept: the box is already broken down
    _price(store, scan, {"Unit A": 40, "Unit B": 40})
    store.record_sealed_price(scan["id"], target=150, basis="b")
    with pytest.raises(InventoryError, match="already broken down"):
        store.sell_sealed(scan["id"])
    other = _confirmed_box(store, pieces=(("X", False),))
    with pytest.raises(InventoryError, match="no sealed price"):
        store.sell_sealed(other["id"])


def test_the_lot_shows_the_check_only_while_every_piece_is_planned(store):
    scan = _confirmed_box(store, pieces=(("Unit A", False), ("Unit B", False)))
    _price(store, scan, {"Unit A": 40, "Unit B": 40})
    store.record_sealed_price(scan["id"], target=150, basis="b")
    c = store.split_check(scan["lot_id"])
    assert c["scan_id"] == scan["id"] and c["verdict"] == "sealed"
    store.upsert_item({"id": scan["item_ids"][0], "status": "available"}, actor="t")
    assert store.split_check(scan["lot_id"]) is None


def test_a_box_pricing_prompt_asks_for_the_sealed_price_and_a_piece_does_not():
    items = [{"id": "I1", "name": "Unit A"}]
    box = sc.price_prompt("S1", items, box='Example "Army" Box')
    assert "inventory_scan_sealed_price" in box and "Example 'Army' Box" in box
    assert "never guess a fee" in box and "do NOT call inventory_set_price for it" in box
    piece = sc.price_prompt("S1", items)
    assert "inventory_scan_sealed_price" not in piece


def test_the_pricing_turn_for_a_box_names_it_and_the_tool_records_it(registry, host):  # noqa: F811
    c, tools = _client(registry)
    st = _store(registry)
    scan = _confirmed_box(st, pieces=(("Unit A", False), ("Unit B", False)))
    r = c.post(f"/api/plugins/inventory/scans/{scan['id']}/price")
    assert r.status_code == 200
    assert "Example Army Box" in host.calls[-1]["prompt"]
    got = json.loads(
        tools["inventory_scan_sealed_price"].invoke(
            {"scan_id": scan["id"], "target": 150, "basis": "eBay sold comps (6 sold)", "n": 6}
        )
    )
    assert got["ok"] is True
    assert c.post(f"/api/plugins/inventory/scans/{scan['id']}/sell-sealed").status_code == 200
    check = c.get(f"/api/plugins/inventory/lots/{scan['lot_id']}/split-check").json()
    assert check == {"check": None}  # one sealed item now, no pieces left to compare


def test_the_page_renders_the_comparison_and_offers_sealed_only_when_it_wins():
    probe = (
        "\nconst win = {split:95, sealed:130, difference:35, verdict:'sealed', basis:'net of fees', recommendation:'Sealed wins by $35.00: one listing, one parcel.', sealed_basis:'eBay sold comps (8 sold)', observed_on:'2026-11-04', sealed_gross:150};"
        "\nconst lose = {...win, split:160, sealed:95, difference:-65, verdict:'split', recommendation:'Split wins by $65.00.'};"
        "\nconsole.log(JSON.stringify({ win: svsHtml(win, 'S1'), lose: svsHtml(lose, 'S1'), none: svsHtml(null) }));\n"
    )
    out = _run_page(probe)
    assert 'data-act="sell-sealed" data-id="S1"' in out["win"] and "Net of estimated eBay fees" in out["win"]
    assert "sell-sealed" not in out["lose"] and "Split wins by $65.00." in out["lose"]
    assert out["none"] == ""
