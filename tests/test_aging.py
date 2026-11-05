"""0.14.0: the aging-stock sweep. Days count from the live listing, else from when the item
became listed/available in the audit trail, else from when it was added; tiers come from
Settings; a snooze is recorded and hides the item until it ends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.automations import AGING_DAYS_DEFAULT, aging_tiers
from inventory_plugin.store import InventoryError

from tests.conftest import FakeRegistry
from tests.test_view import _run_page


def _ago(n: int) -> str:
    # The store counts days in UTC (today_iso); a local date.today() is a day off every evening
    # west of Greenwich, which made these tests fail after ~4 pm Pacific.
    return (datetime.now(UTC).date() - timedelta(days=n)).isoformat()


def _item(store, iid, *, status="available", target=40):
    store.upsert_lot({"id": "L", "name": "Lot"}, actor="t")
    store.upsert_item({"id": iid, "name": f"Item {iid}", "lot_id": "L", "status": status}, actor="t")
    store.set_price(iid, target=target, basis="b", actor="t")


@pytest.mark.parametrize(
    ("cfg", "want"),
    [
        ({}, AGING_DAYS_DEFAULT),
        ({"aging_days_1": 30, "aging_days_2": "60", "aging_days_3": 90}, (30, 60, 90)),
        ({"aging_days_1": 200, "aging_days_2": 20, "aging_days_3": 100}, (20, 100, 200)),  # sorted
        ({"aging_days_1": "x", "aging_days_2": 0, "aging_days_3": 9999}, (1, 45, 730)),  # bad → default, bounded
    ],
)
def test_aging_tiers_are_bounded_sorted_settings(cfg, want):
    assert aging_tiers(cfg) == want


def test_days_count_from_the_live_listing_and_the_tier_is_the_highest_reached(store):
    _item(store, "A")
    store.add_listing("A", channel="eBay", url="https://www.ebay.com/itm/1", listed_on=_ago(95), actor="t")
    _item(store, "B")
    store.add_listing("B", channel="eBay", listed_on=_ago(20), actor="t")  # under the first tier
    out = store.aging((45, 90, 120))
    assert [r["item_id"] for r in out["items"]] == ["A"]
    a = out["items"][0]
    assert (a["days"], a["basis"], a["tier"], a["tier_index"], a["channel"]) == (95, "listed", 90, 1, "eBay")
    assert a["target"] == 40.0 and a["url"] == "https://www.ebay.com/itm/1"


def test_an_unlisted_item_ages_from_when_it_became_available(store):
    _item(store, "A", status="planned")
    store.upsert_item({"id": "A", "status": "available"}, actor="t")
    with store._tx() as con:  # pretend the audit row is old
        con.execute(
            "UPDATE audit SET ts=? WHERE entity_id='A' AND changes LIKE '%\"status\": \"available\"%'",
            (_ago(50) + "T10:00:00Z",),
        )
    (a,) = store.aging((45, 90, 120))["items"]
    assert (a["basis"], a["days"], a["tier"]) == ("not listed", 50, 45)


def test_with_no_listing_date_or_status_change_it_falls_back_to_when_it_was_added(store):
    _item(store, "A")
    with store._tx() as con:
        con.execute("DELETE FROM audit WHERE entity_id='A'")
        con.execute("UPDATE items SET created_at=? WHERE id='A'", (_ago(130) + "T00:00:00Z",))
    (a,) = store.aging((45, 90, 120))["items"]
    assert (a["basis"], a["days"], a["tier_index"]) == ("added", 130, 2)


def test_planned_pending_and_sold_never_age(store):
    for iid, status in (("P", "planned"), ("Q", "pending"), ("K", "kept")):
        _item(store, iid, status=status)
    _item(store, "S")
    store.mark_sold("S", price=10, channel="eBay", actor="t")
    with store._tx() as con:
        con.execute("UPDATE items SET created_at=?", (_ago(400) + "T00:00:00Z",))
        con.execute("DELETE FROM audit")
    assert store.aging((45, 90, 120))["items"] == []


def test_snooze_hides_an_item_until_it_ends_and_is_audited(store):
    _item(store, "A")
    store.add_listing("A", channel="eBay", listed_on=_ago(60), actor="t")
    out = store.snooze_aging("A", days=14, actor="op")
    assert out["snoozed_until"] == (datetime.now(UTC).date() + timedelta(days=14)).isoformat()
    assert store.aging((45, 90, 120))["items"] == []
    later = (datetime.now(UTC).date() + timedelta(days=15)).isoformat()
    assert [r["item_id"] for r in store.aging((45, 90, 120), today=later)["items"]] == ["A"]
    log = store.audit_log(entity_id="A")
    assert any("aging_snoozed_until" in str(a["changes"]) and a["actor"] == "op" for a in log)
    for bad in (0, 91, "x"):
        with pytest.raises(InventoryError):
            store.snooze_aging("A", days=bad)
    with pytest.raises(InventoryError):
        store.snooze_aging("NOPE")


def test_the_routes_use_the_settings_and_are_gated(tmp_path):
    reg = FakeRegistry(
        {"db_path": str(tmp_path / "inv.db"), "aging_days_1": 10, "aging_days_2": 20, "aging_days_3": 30}
    )
    inventory_plugin.register(reg)
    app = FastAPI()
    for router, prefix in reg.routers:
        app.include_router(router, prefix=prefix or "")
    c = TestClient(app)
    api = "/api/plugins/inventory"
    assert c.get(api + "/view-config").json()["aging_days"] == [10, 20, 30]
    c.put(api + "/lots/L", json={"name": "L"})
    c.post(api + "/items", json={"id": "A", "name": "A", "lot_id": "L", "status": "available"})
    c.post(api + "/items/A/listings", json={"channel": "eBay", "listed_on": _ago(25)})
    got = c.get(api + "/aging").json()
    assert got["tiers"] == [10, 20, 30] and got["items"][0]["tier"] == 20
    assert c.post(api + "/items/A/snooze", json={"days": 14}).status_code == 200
    assert c.get(api + "/aging").json()["items"] == []
    assert c.post(api + "/items/A/snooze", json={"days": 500}).status_code == 400


def test_the_page_states_each_rows_basis_and_one_move_per_tier():
    probe = (
        "\nconst xs = [{item_id:'A', name:'Old <b>', days:95, basis:'listed', channel:'eBay', tier_index:1, target:40},"
        "{item_id:'B', name:'Shelf', days:50, basis:'not listed', channel:'', tier_index:0, target:20},"
        "{item_id:'C', name:'Mystery', days:130, basis:'added', channel:'', tier_index:2, target:null}];"
        "\nconsole.log(JSON.stringify({ rows: agingRows(xs), since: xs.map(agingSince), bundle: bundleText(xs.slice(0,2)) }));\n"
    )
    out = _run_page(probe)
    assert out["since"] == [
        "listed 95 days on eBay",
        "available 50 days, not listed",
        "added 130 days ago, no listing date",
    ]
    r = out["rows"]
    assert "Drop another 10%, or bundle it" in r and "Send an offer to watchers, or drop 10%" in r
    assert "Decide: clearance, bundle, or pull it" in r
    assert "Old &lt;b&gt;" in r and "Old <b>" not in r  # the name is escaped, never raw HTML
    assert 'data-act="markdown" data-id="C" disabled' in r  # nothing to mark down without a target
    assert "watchers" not in out["bundle"].split("\n", 1)[1]  # no invented watcher data
    assert "- Old <b> (A), now $40, listed 95 days on eBay" in out["bundle"]
