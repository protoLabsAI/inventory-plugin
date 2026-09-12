"""The gated data API, mounted exactly as register() mounts it (prefix /api/plugins/inventory)."""

from __future__ import annotations

import inventory_plugin
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app), registry


def test_no_public_route_in_this_slice(registry):
    """Slice 1 ships the DATA API only; every route sits under the bearer-gated /api prefix.
    (The console view lands in slice 2 with its own public page router.)"""
    _, reg = _client(registry)
    assert all(prefix.startswith("/api/plugins/inventory") for _, prefix in reg.routers)


def test_crud_flow_over_http(registry):
    c, reg = _client(registry)
    assert c.put("/api/plugins/inventory/lots/L1", json={"name": "Box", "acquisition_cost": 100}).status_code == 200
    r = c.post("/api/plugins/inventory/items", json={"name": "Team", "lot_id": "L1"})
    assert r.status_code == 200
    iid = r.json()["item"]["id"]
    assert (
        c.put(f"/api/plugins/inventory/items/{iid}", json={"notes": "n", "retail": 43.5}).json()["item"]["retail"]
        == 43.5
    )
    r = c.post(
        f"/api/plugins/inventory/items/{iid}/price",
        json={
            "low": 20,
            "target": 30,
            "high": 40,
            "basis": "b",
            "observation": {"source": "ebay_sold", "n": 9, "median": 30},
        },
    )
    assert r.status_code == 200 and r.json()["item"]["target"] == 30.0
    assert c.get(f"/api/plugins/inventory/items/{iid}").json()["item"]["observations"][0]["n"] == 9
    r = c.post(
        f"/api/plugins/inventory/items/{iid}/listings",
        json={"channel": "eBay", "url": "https://www.ebay.com/itm/1", "price": 30},
    )
    lid = r.json()["listing"]["id"]
    assert c.get("/api/plugins/inventory/items", params={"status": "listed"}).json()["count"] == 1
    assert c.post(f"/api/plugins/inventory/listings/{lid}/end", json={"state": "ended"}).status_code == 200
    assert c.get("/api/plugins/inventory/items", params={"status": "available"}).json()["count"] == 1
    r = c.post(f"/api/plugins/inventory/items/{iid}/sold", json={"price": 30, "channel": "eBay", "fees": 4})
    assert r.status_code == 200 and r.json()["sale"]["net"] == 26.0
    s = c.get("/api/plugins/inventory/summary").json()
    assert s["lots"][0]["realized"]["net"] == 26.0
    assert c.get("/api/plugins/inventory/sales").json()["sales"][0]["net"] == 26.0
    audit = c.get("/api/plugins/inventory/audit").json()["audit"]
    assert audit and all(e["actor"] == "console" for e in audit)
    assert c.get("/api/plugins/inventory/stale").status_code == 200
    assert c.delete(f"/api/plugins/inventory/items/{iid}").status_code == 200
    assert c.get(f"/api/plugins/inventory/items/{iid}").status_code == 404
    assert c.delete("/api/plugins/inventory/lots/L1").status_code == 200
    assert {"lot.changed", "item.changed", "sale.recorded"} <= {e[0] for e in reg.events}


def test_errors_are_400_with_the_reason(registry):
    c, _ = _client(registry)
    r = c.post("/api/plugins/inventory/items", json={"notes": "no name"})
    assert r.status_code == 400 and "needs a name" in r.json()["detail"]
    r = c.post("/api/plugins/inventory/items", json={"name": "x", "lot_id": "NOPE"})
    assert r.status_code == 400 and "no lot 'NOPE'" in r.json()["detail"]
    c.post("/api/plugins/inventory/items", json={"id": "X", "name": "x"})
    for status in ("sold", "Sold", "SOLD ($5)"):
        r = c.put("/api/plugins/inventory/items/X", json={"status": status})
        assert r.status_code == 400 and "sold" in r.json()["detail"], status
    # an edit form that echoes an already-sold item's status back is a no-op, not a refusal
    c.post("/api/plugins/inventory/items", json={"id": "S", "name": "sold thing"})
    c.post("/api/plugins/inventory/items/S/sold", json={"price": 5, "channel": "x"})
    r = c.put("/api/plugins/inventory/items/S", json={"name": "sold thing (fixed)", "status": "sold"})
    assert (
        r.status_code == 200
        and r.json()["item"]["name"] == "sold thing (fixed)"
        and r.json()["item"]["status"] == "sold"
    )
    r = c.put("/api/plugins/inventory/items/X", json={"target": 12})
    assert r.status_code == 400 and "basis" in r.json()["detail"]
    assert c.put("/api/plugins/inventory/items/TYPO", json={"name": "y"}).status_code == 404
    r = c.post("/api/plugins/inventory/items/X/price", json={"target": 12, "basis": "b", "observation": ["nope"]})
    assert r.status_code == 400
    r = c.post(
        "/api/plugins/inventory/items/X/price",
        json={"target": 12, "basis": "b", "observation": {"source": "ebay_sold", "n": "nine"}},
    )
    assert r.status_code == 400 and "whole number" in r.json()["detail"]
    assert (
        c.get("/api/plugins/inventory/items/X").json()["item"]["target"] is None
    )  # nothing written by the failed price
    r = c.post("/api/plugins/inventory/items/X/sold", json={"price": 5, "channel": "x", "quantity": "one"})
    assert r.status_code == 400 and "whole number" in r.json()["detail"]
    assert c.get("/api/plugins/inventory/items", params={"status": "nope"}).status_code == 400
    assert c.get("/api/plugins/inventory/summary", params={"lot_id": "nope"}).status_code == 400
    assert c.post("/api/plugins/inventory/import", json={"csv": ""}).status_code == 400


def test_import_and_export_over_http(registry):
    c, _ = _client(registry)
    r = c.post(
        "/api/plugins/inventory/import",
        json={"csv": "inventory_id,item,target_price_usd,status\nA,Thing,12.5,Available\n"},
    )
    assert r.status_code == 200 and r.json()["created"] == 1
    r = c.get("/api/plugins/inventory/export", params={"kind": "items"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and "Thing" in r.text
