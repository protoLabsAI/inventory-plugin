"""The optional game-system field: an additive migration on an existing database, a filter,
the ordering, CSV in/out, and the API listing of systems in use."""

from __future__ import annotations

import sqlite3

import inventory_plugin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.csvio import export_csv, import_csv, parse_csv
from inventory_plugin.store import SCHEMA, InventoryStore


def test_an_existing_database_gains_the_column_on_open(tmp_path):
    """A 0.3.x database has no `system` column; opening it with the new store adds it without
    touching the rows — the additive-migration path, exercised for real."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA.replace("  system TEXT NOT NULL DEFAULT '',\n", ""))
    con.execute(
        "INSERT INTO items(id, name, created_at, updated_at) VALUES ('X', 'Old thing', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    con.commit()
    assert "system" not in {r[1] for r in con.execute("PRAGMA table_info(items)")}
    con.close()
    store = InventoryStore(path)
    item = store.get_item("X")
    assert item["name"] == "Old thing" and item["system"] == ""
    assert store.upsert_item({"id": "X", "system": "Blood Bowl"}, actor="t")["system"] == "Blood Bowl"


def test_filter_search_and_ordering_by_system(store):
    store.upsert_lot({"id": "L", "name": "L"}, actor="t")
    store.upsert_item(
        {"id": "B1", "name": "Human Team", "lot_id": "L", "system": "Blood Bowl", "category": "Teams"}, actor="t"
    )
    store.upsert_item(
        {"id": "W1", "name": "Warboss", "lot_id": "L", "system": "Warhammer 40K", "category": "Orks"}, actor="t"
    )
    store.upsert_item({"id": "N1", "name": "Loose dice", "lot_id": "L"}, actor="t")
    assert [i["id"] for i in store.list_items()] == ["N1", "B1", "W1"]  # blank system first, then alphabetical
    assert [i["id"] for i in store.list_items(system="blood bowl")] == ["B1"]  # case-insensitive
    assert [i["id"] for i in store.list_items(query="40k")] == ["W1"]  # search covers the system
    assert store.systems() == ["Blood Bowl", "Warhammer 40K"]


def test_csv_alias_and_export(store):
    out = import_csv(store, "inventory_id,item,game_system,target_price_usd\nX,Thing,Kill Team,12\n", actor="t")
    assert out["created"] == 1 and out["mapped_columns"]["game_system"] == "system"
    assert store.get_item("X")["system"] == "Kill Team"
    headers, rows = parse_csv(export_csv(store, kind="items"))
    assert "system" in headers and rows[0]["system"] == "Kill Team"


def test_api_lists_systems_and_filters(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    c = TestClient(app)
    c.post("/api/plugins/inventory/items", json={"id": "A", "name": "a", "system": "Blood Bowl"})
    c.post("/api/plugins/inventory/items", json={"id": "B", "name": "b", "system": "Warhammer 40K"})
    c.post("/api/plugins/inventory/items", json={"id": "C", "name": "c"})
    assert c.get("/api/plugins/inventory/systems").json() == {"systems": ["Blood Bowl", "Warhammer 40K"]}
    c.put("/api/plugins/inventory/items/A", json={"condition": "NoS"})
    c.put("/api/plugins/inventory/items/B", json={"condition": "NIB"})
    assert c.get("/api/plugins/inventory/conditions").json() == {"conditions": ["NIB", "NoS"]}
    assert [
        i["id"] for i in c.get("/api/plugins/inventory/items", params={"system": "Blood Bowl"}).json()["items"]
    ] == ["A"]
    assert (
        c.put("/api/plugins/inventory/items/C", json={"system": "Necromunda"}).json()["item"]["system"] == "Necromunda"
    )


def test_tool_sets_and_filters_system(registry):
    import json

    inventory_plugin.register(registry)
    t = {x.name: x for x in registry.tools}
    t["inventory_upsert_item"].invoke({"id": "A", "name": "a", "system": "Blood Bowl"})
    t["inventory_upsert_item"].invoke({"id": "B", "name": "b"})
    out = json.loads(t["inventory_list"].invoke({"system": "Blood Bowl"}))
    assert [i["id"] for i in out["items"]] == ["A"] and out["items"][0]["system"] == "Blood Bowl"
