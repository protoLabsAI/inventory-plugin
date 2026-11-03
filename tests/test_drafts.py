"""Listing drafts (slice 5): the drafts table and its states, the photos a draft uses, the agent
turn the plugin starts with ``sdk.run_in_session`` (stubbed), the tools, and the API."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from inventory_plugin import drafts as dr
from inventory_plugin.store import InventoryError, ebay_item_url

from tests.test_photos import jpeg
from tests.test_scans import _client, _store, host  # noqa: F401 — `host` is a fixture

DESC = "New on sprue.\nOne kit of ten models, as pictured."


def _priced(store, item_id="A", status="available", target=20):
    store.upsert_lot({"id": "L", "name": "Lot"}, actor="t")
    store.upsert_item({"id": item_id, "name": "Item " + item_id, "lot_id": "L", "status": status}, actor="t")
    if target is not None:
        store.set_price(item_id, target=target, basis="eBay sold comps (5 sold)", actor="t")


# ── store ────────────────────────────────────────────────────────────────────────
def test_a_draft_needs_a_price_and_unlisted_stock(store):
    _priced(store, target=None)
    with pytest.raises(InventoryError, match="set a price first"):
        store.request_draft("A")
    store.set_price("A", target=20, basis="b", actor="t")
    store.upsert_item({"id": "K", "name": "Kept", "lot_id": "L", "status": "kept"}, actor="t")
    with pytest.raises(InventoryError, match="only unlisted stock"):
        store.request_draft("K")
    with pytest.raises(InventoryError, match="no item"):
        store.request_draft("NOPE")
    d = store.request_draft("A", actor="console")
    assert d["state"] == d["listing_state"] == "drafting" and d["title"] == ""


def test_the_lifecycle_draft_copied_live_records_a_listing_and_audits_each_step(store):
    _priced(store)
    store.request_draft("A", actor="console")
    d = store.write_draft("A", "Warhammer Item A New on Sprue", DESC, notes="check the count", actor="agent")
    assert (d["state"], d["title"], d["description"], d["notes"]) == (
        "draft",
        "Warhammer Item A New on Sprue",
        DESC,
        "check the count",
    )
    assert store.draft_copied("A", "title", actor="console")["state"] == "copied"
    again = store.draft_copied("A", "price", actor="console")  # later copies don't re-stamp
    assert again["copied_at"] == store.get_draft("A")["copied_at"] and again["copied_stale"] is False
    live = store.draft_live("A", "https://www.ebay.com/itm/Item-A-New/123456789012?hash=item1c", actor="console")
    assert live["state"] == live["listing_state"] == "live"
    assert live["listing"]["url"] == "https://www.ebay.com/itm/123456789012"
    assert live["listing"]["price"] == 20.0 and live["listing"]["channel"] == "eBay"
    assert store.get_item("A")["status"] == "listed"
    actions = [(a["entity"], a["action"]) for a in store.audit_log(limit=50) if a["entity"] in ("draft", "listing")]
    assert actions[::-1] == [
        ("draft", "request"),
        ("draft", "write"),
        ("draft", "copy"),
        ("listing", "create"),
        ("draft", "live"),
    ]
    with pytest.raises(InventoryError, match="only unlisted stock"):  # it is listed now
        store.request_draft("A")
    with pytest.raises(InventoryError, match="already live"):
        store.write_draft("A", "t", "d")


def test_a_sale_closes_the_live_listing_and_reads_sold(store):
    _priced(store)
    store.request_draft("A")
    store.write_draft("A", "T", DESC)
    store.draft_live("A", "https://ebay.com/itm/123456789012")
    store.mark_sold("A", price=25, channel="eBay", actor="t")
    d = store.get_draft("A")
    assert d["listing_state"] == "sold" and d["listing"]["state"] == "sold"


def test_an_ended_listing_reads_ended(store):
    _priced(store)
    store.request_draft("A")
    store.write_draft("A", "T", DESC)
    lid = store.draft_live("A", "https://ebay.com/itm/123456789012")["listing"]["id"]
    store.end_listing(lid, actor="t")
    assert store.get_draft("A")["listing_state"] == "ended"


@pytest.mark.parametrize(
    ("title", "desc", "match"),
    [
        ("", DESC, "needs a title"),
        ("x" * 81, DESC, "81 characters; eBay allows 80"),
        ("ok", "  \n ", "needs a description"),
        ("ok", "<b>bold</b> claim", "plain text"),
        ("ok", "x" * 4001, "under 4000"),
    ],
)
def test_the_agents_copy_is_refused_with_the_fix_not_trimmed(store, title, desc, match):
    _priced(store)
    store.request_draft("A")
    with pytest.raises(InventoryError, match=match):
        store.write_draft("A", title, desc)
    assert store.get_draft("A")["state"] == "drafting"


def test_an_80_character_title_and_a_tidied_description_are_kept(store):
    _priced(store)
    d = store.write_draft("A", "  " + "t" * 80 + " ", "Line  one\r\n\n\n\nLine   two  ")
    assert len(d["title"]) == 80 and d["description"] == "Line one\n\nLine two"


def test_a_fail_needs_a_draft_in_progress_and_a_stuck_one_reads_failed(store):
    _priced(store)
    with pytest.raises(InventoryError, match="no draft"):
        store.fail_draft("A", "x")
    store.request_draft("A")
    assert store.fail_draft("A", "no photos and a vague name")["reason"] == "no photos and a vague name"
    with pytest.raises(InventoryError, match="isn't being written"):
        store.fail_draft("A", "again")
    store.request_draft("A")
    with store._tx() as con:
        con.execute("UPDATE drafts SET requested_at='2026-01-01T00:00:00Z' WHERE item_id='A'")
    d = store.get_draft("A")
    assert d["state"] == "failed" and "didn't finish" in d["reason"]


def test_copied_a_day_ago_and_not_live_is_flagged(store):
    _priced(store)
    store.write_draft("A", "T", DESC)
    store.draft_copied("A", "description")
    with store._tx() as con:
        con.execute("UPDATE drafts SET copied_at='2026-01-01T00:00:00Z' WHERE item_id='A'")
    assert store.get_draft("A")["copied_stale"] is True
    assert store.list_drafts()[0]["copied_stale"] is True


def test_copy_needs_a_written_draft_and_a_known_field(store):
    _priced(store)
    with pytest.raises(InventoryError, match="copied what"):
        store.draft_copied("A", "photos")
    with pytest.raises(InventoryError, match="no draft to copy"):
        store.draft_copied("A", "title")


@pytest.mark.parametrize(
    ("url", "stored"),
    [
        ("https://www.ebay.com/itm/123456789012", "https://www.ebay.com/itm/123456789012"),
        ("https://www.ebay.com/itm/Some-Title-Slug/123456789012?_trksid=p1", "https://www.ebay.com/itm/123456789012"),
        ("http://m.ebay.com/itm/123456789012#x", "https://www.ebay.com/itm/123456789012"),
        ("https://ebay.us/m/AbC123", "https://ebay.us/m/AbC123"),
    ],
)
def test_ebay_urls_are_normalised(url, stored):
    assert ebay_item_url(url) == stored


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ebay.com/itm/123456789012",
        "https://evil.com/itm/123456789012",
        "https://www.ebay.com.evil.com/itm/123456789012",
        "https://www.ebay.com/sch/i.html?_nkw=x",
        "javascript:alert(1)",
        "https://www.ebay.com/itm/12",
    ],
)
def test_anything_but_an_ebay_listing_is_refused(url):
    with pytest.raises(InventoryError, match="eBay listing"):
        ebay_item_url(url)


def test_a_drafts_photos_are_the_pieces_own_then_the_box(store):
    box = store.create_scan("box", paid=10, actor="console")
    store.add_scan_photo(box["id"], jpeg())
    store.propose_scan(
        box["id"], {"name": "Box", "confidence": 0.9, "contents": [{"name": "Piece"}, {"name": "Other"}]}
    )
    piece = store.create_scan("unit", parent_id=box["id"], actor="console")
    store.add_scan_photo(piece["id"], jpeg())
    store.propose_scan(piece["id"], {"name": "Piece", "confidence": 0.9})
    out = store.confirm_scan(
        box["id"], {"name": "Box", "units": [{"name": "Piece", "scan_id": piece["id"]}, {"name": "Other"}]}
    )
    p_id, o_id = out["item_ids"]
    photos = store.get_draft(p_id)["photos"]
    assert [ph["scan_id"] for ph in photos] == [piece["id"], box["id"]]
    assert [ph["scan_id"] for ph in store.get_draft(o_id)["photos"]] == [box["id"]]
    assert len(store.draft_photo_files(p_id)) == 2


def test_deleting_an_item_removes_its_draft_with_a_snapshot(store):
    _priced(store)
    store.write_draft("A", "T", DESC)
    store.delete_item("A", actor="t")
    assert store.list_drafts() == []
    snap = json.loads(json.dumps([a for a in store.audit_log(limit=5) if a["action"] == "delete"][0]["changes"]))
    assert "draft" in (snap if isinstance(snap, dict) else json.loads(snap))["snapshot"]


# ── the turn ─────────────────────────────────────────────────────────────────────
def test_the_draft_turn_is_cheap_scoped_and_in_its_own_session(store, host):  # noqa: F811
    _priced(store)
    store.request_draft("A")
    d = dr.start_draft(store, "A", sdk=host)
    (call,) = host.calls
    assert call["session"] == "inventory-draft-A" and call["job_id"] == "draft-A" and d["state"] == "drafting"
    p = call["prompt"]
    for must in (
        'inventory_draft_item("A")',
        'inventory_draft_write("A"',
        "Do NOT use eBay",
        "do NOT start subagents",
        "at most 80 characters",
        "never invent contents",
        "Do not message the operator",
    ):
        assert must in p


def test_a_host_that_cant_start_a_turn_marks_the_draft_failed(store):
    import types

    _priced(store)
    store.request_draft("A")
    bad = types.SimpleNamespace(run_in_session=lambda *a, **k: {"ok": False, "message": "scheduler unavailable"})
    d = dr.start_draft(store, "A", sdk=bad)
    assert d["state"] == "failed" and d["reason"] == "scheduler unavailable"


# ── tools + API ──────────────────────────────────────────────────────────────────
def test_the_tools_round_trip_with_photos(registry, host):  # noqa: F811
    c, tools = _client(registry)
    store = _store(registry)
    u = store.create_scan("unit", actor="console")
    store.add_scan_photo(u["id"], jpeg())
    store.propose_scan(u["id"], {"name": "Big Mek", "confidence": 0.9})
    (item_id,) = store.confirm_scan(u["id"], {"name": "Big Mek", "condition": "NoS"})["item_ids"]
    store.set_price(item_id, target=40, basis="b", actor="t")
    store.request_draft(item_id)
    seen = tools["inventory_draft_item"].invoke({"item_id": item_id})
    env = json.loads(seen.split("]", 1)[1])
    assert len(env["images"]) == 1 and json.loads(env["text"])["item"]["condition"] == "NoS"
    out = json.loads(
        tools["inventory_draft_write"].invoke({"item_id": item_id, "title": "x" * 90, "description": DESC})
    )
    assert out["ok"] is False and "eBay allows 80" in out["error"]
    out = json.loads(
        tools["inventory_draft_write"].invoke({"item_id": item_id, "title": "Big Mek NoS", "description": DESC})
    )
    assert out == {"ok": True, "item_id": item_id, "state": "draft", "title": "Big Mek NoS"}
    assert store.audit_log(limit=1)[0]["actor"] == "agent"


def test_the_api_drafts_one_or_a_capped_sequential_batch(registry, host):  # noqa: F811
    c, _ = _client(registry)
    store = _store(registry)
    store.upsert_lot({"id": "L", "name": "Lot"}, actor="t")
    for i in range(12):
        store.upsert_item({"id": f"I{i}", "name": f"Item {i}", "lot_id": "L", "status": "available"}, actor="t")
        store.set_price(f"I{i}", target=10, basis="b", actor="t")
    store.upsert_item({"id": "NOPRICE", "name": "No price", "lot_id": "L", "status": "available"}, actor="t")
    assert c.post("/api/plugins/inventory/items/I0/draft").json()["draft"]["state"] == "drafting"
    r = c.post("/api/plugins/inventory/drafts/bulk", json={"item_ids": [f"I{i}" for i in range(11)]})
    assert r.status_code == 400 and "at most 10" in r.json()["detail"]
    r = c.post("/api/plugins/inventory/drafts/bulk", json={"item_ids": ["I1", "I2", "NOPRICE", "I1"]}).json()
    assert [s["item_id"] for s in r["started"]] == ["I1", "I2"] and r["refused"][0]["item_id"] == "NOPRICE"
    delays = [x["delay"] for x in host.calls]
    assert delays == [0.0, 0.0, dr.BULK_SPACING_S]  # the single one, then the batch one after another
    assert {d["item_id"] for d in c.get("/api/plugins/inventory/drafts").json()["drafts"]} == {"I0", "I1", "I2"}


def test_the_api_copy_live_and_photos_zip(registry, host):  # noqa: F811
    c, _ = _client(registry)
    store = _store(registry)
    u = store.create_scan("unit", actor="console")
    store.add_scan_photo(u["id"], jpeg())
    store.propose_scan(u["id"], {"name": "Big Mek", "confidence": 0.9})
    (item_id,) = store.confirm_scan(u["id"], {"name": "Big Mek"})["item_ids"]
    store.set_price(item_id, target=40, basis="b", actor="t")
    store.write_draft(item_id, "Big Mek", DESC)
    base = f"/api/plugins/inventory/items/{item_id}"
    assert c.post(base + "/draft/copied", json={"what": "title"}).json()["draft"]["state"] == "copied"
    z = c.get(base + "/photos.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist() == [f"01-{item_id}.jpg"]
    bad = c.post(base + "/draft/live", json={"url": "https://example.com/itm/123456789012"})
    assert bad.status_code == 400 and "eBay listing" in bad.json()["detail"]
    ok = c.post(base + "/draft/live", json={"url": "https://www.ebay.com/itm/123456789012"}).json()["draft"]
    assert ok["state"] == "live" and store.get_item(item_id)["status"] == "listed"
    assert c.get("/api/plugins/inventory/items/NOPE/draft").status_code == 404
    assert c.get("/api/plugins/inventory/items/NOPE/photos.zip").status_code == 404


def test_draft_routes_are_gated_like_every_other_data_route(registry):
    import inventory_plugin

    inventory_plugin.register(registry)
    data_paths = {r.path for router, prefix in registry.routers if prefix.startswith("/api/") for r in router.routes}
    for p in (
        "/drafts",
        "/drafts/bulk",
        "/items/{item_id}/draft",
        "/items/{item_id}/draft/copied",
        "/items/{item_id}/draft/live",
        "/items/{item_id}/photos.zip",
    ):
        assert p in data_paths


def test_an_id_with_like_wildcards_still_finds_its_photos(store):
    # Review on #16: a LIKE pre-filter that stripped "_" and "%" lost these ids' photos.
    for item_id in ("Wartrakk_2", "Odd%Id"):
        u = store.create_scan("unit", actor="console")
        store.add_scan_photo(u["id"], jpeg())
        store.propose_scan(u["id"], {"name": item_id, "confidence": 0.9})
        out = store.confirm_scan(u["id"], {"name": item_id})
        (made,) = out["item_ids"]
        with store._tx() as con:  # give the item the awkward id the store would otherwise never mint
            con.execute("UPDATE items SET id=? WHERE id=?", (item_id, made))
            con.execute("UPDATE scans SET item_ids=? WHERE id=?", (json.dumps([item_id]), u["id"]))
        assert [p["scan_id"] for p in store.get_draft(item_id)["photos"]] == [u["id"]]
