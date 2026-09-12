"""The agent tools: JSON in, JSON out, actor 'agent', events emitted, refusals phrased."""

from __future__ import annotations

import json

import inventory_plugin


def _tools(registry):
    inventory_plugin.register(registry)
    return {t.name: t for t in registry.tools}


def test_register_contributes_tools_router_skills(registry):
    inventory_plugin.register(registry)
    names = {t.name for t in registry.tools}
    assert {
        "inventory_summary",
        "inventory_list",
        "inventory_set_price",
        "inventory_mark_sold",
        "inventory_import_csv",
    } <= names
    assert len(names) == 12
    assert registry.routers and registry.routers[0][1] == "/api/plugins/inventory"
    assert "skills" in registry.skill_dirs
    for t in registry.tools:
        assert (t.description or "").strip(), f"{t.name} has no description"


def test_lot_item_price_sale_flow(registry):
    t = _tools(registry)
    lot = json.loads(t["inventory_upsert_lot"].invoke({"id": "L1", "name": "Box", "acquisition_cost": 100}))
    assert lot["ok"] and lot["lot"]["acquisition_cost"] == 100.0
    item = json.loads(t["inventory_upsert_item"].invoke({"name": "Team", "lot_id": "L1", "category": "Teams"}))
    iid = item["item"]["id"]
    priced = json.loads(
        t["inventory_set_price"].invoke(
            {
                "item_id": iid,
                "basis": "eBay sold comps (22 sold, incl. shipping)",
                "low": 26,
                "target": 40,
                "high": 51,
                "source": "ebay_sold",
                "n": 22,
                "p25": 26,
                "median": 40,
                "p75": 51,
                "query": "team",
            }
        )
    )
    assert priced["item"]["target"] == 40.0
    got = json.loads(t["inventory_get"].invoke({"item_id": iid}))
    assert got["item"]["observations"][0]["n"] == 22
    listing = json.loads(
        t["inventory_listing"].invoke({"action": "add", "item_id": iid, "channel": "eBay", "price": 40})
    )
    assert listing["ok"] and json.loads(t["inventory_get"].invoke({"item_id": iid}))["item"]["status"] == "listed"
    sale = json.loads(
        t["inventory_mark_sold"].invoke(
            {"item_id": iid, "price": 40, "channel": "eBay", "fees": 5.2, "shipping_charged": 6, "shipping_cost": 5}
        )
    )
    assert sale["sale"]["net"] == 35.8
    summary = json.loads(t["inventory_summary"].invoke({}))
    assert summary["lots"][0]["realized"]["net"] == 35.8
    assert summary["lots"][0]["profit_so_far"] == round(35.8 - 100, 2)
    topics = [e[0] for e in registry.events]
    assert {"lot.changed", "item.changed", "sale.recorded"} <= set(topics)


def test_status_sold_shortcut_is_refused(registry):
    t = _tools(registry)
    out = json.loads(t["inventory_upsert_item"].invoke({"name": "x", "status": "sold"}))
    assert out["ok"] is False and "inventory_mark_sold" in out["error"]


def test_errors_are_json_not_exceptions(registry):
    t = _tools(registry)
    assert json.loads(t["inventory_get"].invoke({"item_id": "nope"}))["ok"] is False
    assert "unknown status" in json.loads(t["inventory_list"].invoke({"status": "nope"}))["error"]
    assert "needs a basis" in json.loads(t["inventory_set_price"].invoke({"item_id": "nope", "basis": ""}))["error"]
    assert json.loads(t["inventory_delete_item"].invoke({"item_id": "nope"}))["ok"] is False
    assert json.loads(t["inventory_listing"].invoke({"action": "zap"}))["ok"] is False


def test_csv_round_trip_through_the_workspace(registry, tmp_path):
    t = _tools(registry)
    (tmp_path / "sheet.csv").write_text(
        "inventory_id,lot_id,item,target_price_usd,status,sold_price_usd\nX1,L,Thing,12.5,Available,\nX2,L,Gone,5,Sold ($5),\n"
    )
    out = json.loads(t["inventory_import_csv"].invoke({"path": "sheet.csv"}))  # relative → workspace_dir
    assert out["ok"] and out["created"] == 2 and out["sales_recorded"] == 1
    exported = json.loads(t["inventory_export_csv"].invoke({"path": "out/items.csv"}))
    assert exported["ok"] and exported["rows"] == 2 and (tmp_path / "out" / "items.csv").exists()
    inline = json.loads(t["inventory_export_csv"].invoke({"kind": "items", "status": "sold"}))
    assert "X2" in inline["csv"] and "X1" not in inline["csv"]
    missing = json.loads(t["inventory_import_csv"].invoke({"path": "nope.csv"}))
    assert missing["ok"] is False


def test_stale_tool(registry):
    t = _tools(registry)
    assert json.loads(t["inventory_stale"].invoke({}))["ok"] is True
