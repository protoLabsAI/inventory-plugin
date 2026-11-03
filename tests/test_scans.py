"""Scan intake (slice 4): the scans table, photo storage, identification / pricing turns the
plugin starts with ``sdk.run_in_session`` (stubbed — no host), the agent tools, and the API."""

from __future__ import annotations

import json
import sys
import types

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin import scans as sc
from inventory_plugin.store import InventoryError, _scan_row_to_dict

from tests.test_photos import jpeg, png_file


@pytest.fixture
def host(monkeypatch):
    """A fake host whose ``run_in_session`` records every turn the plugin starts."""
    calls: list[dict] = []
    graph = types.ModuleType("graph")
    sdk = types.ModuleType("graph.sdk")

    def run_in_session(session_id, prompt, *, delay_seconds=0.0, job_id=None):
        calls.append({"session": session_id, "prompt": prompt, "job_id": job_id, "delay": delay_seconds})
        return {"ok": True, "job_id": job_id}

    def multimodal_tool_result(text, images):
        return "\x1e[multimodal-tool-v1]" + json.dumps({"text": text, "images": images})

    sdk.run_in_session = run_in_session
    sdk.multimodal_tool_result = multimodal_tool_result
    graph.sdk = sdk
    monkeypatch.setitem(sys.modules, "graph", graph)
    monkeypatch.setitem(sys.modules, "graph.sdk", sdk)
    sdk.calls = calls
    return sdk


def _client(registry):
    inventory_plugin.register(registry)
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    tools = {t.name: t for t in registry.tools}
    return TestClient(app), tools


def _store(registry):
    from inventory_plugin.store import InventoryStore

    return InventoryStore(registry.config["db_path"])


# ── store ────────────────────────────────────────────────────────────────────────
def test_a_box_scan_needs_what_was_paid_and_makes_its_lot_at_once(store):
    with pytest.raises(InventoryError, match="what you paid"):
        store.create_scan("box", paid="", actor="t")
    with pytest.raises(InventoryError, match="what you paid"):
        store.create_scan("box", paid=0, actor="t")
    with pytest.raises(InventoryError, match="money amount"):
        store.create_scan("box", paid="lots", actor="t")
    s = store.create_scan("box", paid="101", source="LGS", name="Box scanned 11-03 14:20", actor="console")
    assert s["state"] == "new" and s["lot_id"] == "SCAN-" + s["id"][1:]
    lot = store.get_lot(s["lot_id"])
    assert lot["name"] == "Box scanned 11-03 14:20" and lot["acquisition_cost"] == 101.0 and lot["source"] == "LGS"
    assert [a["action"] for a in store.audit_log(entity_id=s["id"])] == ["create"]


def test_photos_are_sanitized_capped_and_restart_identification(store):
    s = store.create_scan("unit", actor="t")
    with pytest.raises(InventoryError, match="unsupported image"):
        store.add_scan_photo(s["id"], b"GIF89a" + bytes(20))
    s = store.add_scan_photo(s["id"], jpeg(), actor="t")
    assert s["state"] == "identifying" and len(s["photos"]) == 1
    path, ctype = store.scan_photo_file(s["id"], s["photos"][0]["id"])
    stored = path.read_bytes()
    assert ctype == "image/jpeg" and b"45,31N" not in stored and b"Photoshop" not in stored  # metadata stripped
    store.add_scan_photo(s["id"], png_file())
    store.add_scan_photo(s["id"], jpeg())
    with pytest.raises(InventoryError, match="at most 3"):
        store.add_scan_photo(s["id"], jpeg())
    assert sorted(p.name for p in (store.scans_dir / s["id"]).iterdir()) == sorted(
        f"{p['id']}.{p['ext']}" for p in store.get_scan(s["id"])["photos"]
    )  # no temp files left behind


def test_photo_lookups_refuse_anything_but_their_own_ids(store):
    s = store.add_scan_photo(store.create_scan("unit")["id"], jpeg())
    pid = s["photos"][0]["id"]
    assert store.scan_photo_file(s["id"], "../../etc/passwd") is None
    assert store.scan_photo_file("../x", pid) is None
    assert store.scan_photo_file(store.create_scan("unit")["id"], pid) is None  # another scan's photo


def _box_proposed(store):
    s = store.create_scan("box", paid=101, actor="console")
    store.add_scan_photo(s["id"], jpeg())
    return store.propose_scan(
        s["id"],
        {
            "name": "Warcry: Pyre and Flood",
            "system": "Warcry / Age of Sigmar",
            "condition": "NIB",
            "confidence": 0.92,
            "alternatives": [
                "Warcry: Pyre and Flood",
                "Warcry Starter Set",
                {"name": "Warcry: Heart of Ghur"},
                "x",
                "y",
            ],
            "contents": [
                {"name": "Pyregheists", "quantity": 1, "model_count": 10},
                {"name": "Ydrilan Riverblades", "quantity": "1"},
                {"name": "  "},
                "Idol of the Old Ones",
            ],
        },
        actor="agent",
    )


def test_a_proposal_is_validated_and_only_accepted_while_identifying(store):
    s = _box_proposed(store)
    p = s["proposal"]
    assert s["state"] == "proposed" and p["confidence"] == 0.92
    assert [a["name"] for a in p["alternatives"]] == ["Warcry Starter Set", "Warcry: Heart of Ghur", "x"]  # ≤3, no self
    assert [u["name"] for u in p["contents"]] == ["Pyregheists", "Ydrilan Riverblades", "Idol of the Old Ones"]
    assert p["contents"][1]["model_count"] == 0  # unknown stays 0, never guessed
    with pytest.raises(InventoryError, match="isn't waiting"):
        store.propose_scan(s["id"], {"name": "again", "confidence": 1})
    with pytest.raises(InventoryError, match="needs the product's name"):
        store.propose_scan(s["id"], {"name": " ", "confidence": 1})
    u = store.add_scan_photo(store.create_scan("unit")["id"], jpeg())
    with pytest.raises(InventoryError, match="confidence"):
        store.propose_scan(u["id"], {"name": "A", "confidence": "high"})
    assert store.propose_scan(u["id"], {"name": "A", "confidence": 7, "contents": [{"name": "ignored"}]})[
        "proposal"
    ] == {
        "name": "A",
        "system": "",
        "category": "",
        "condition": "",
        "confidence": 1.0,
        "alternatives": [],
        "contents": [],
        "notes": "",
    }


def test_a_turn_that_never_answers_reads_as_failed_with_retry(store):
    s = store.add_scan_photo(store.create_scan("unit")["id"], jpeg())
    with store._read() as con:
        row = con.execute("SELECT * FROM scans WHERE id=?", (s["id"],)).fetchone()
    from datetime import UTC, datetime, timedelta

    later = datetime.now(UTC) + timedelta(minutes=11)
    d = _scan_row_to_dict(row, 10, now=later)
    assert d["state"] == "failed" and "too long" in d["reason"] and d["stored_state"] == "identifying"
    assert _scan_row_to_dict(row, 10)["state"] == "identifying"
    assert store.retry_scan(s["id"])["state"] == "identifying"
    # a late answer still counts: better than a retry
    assert store.propose_scan(s["id"], {"name": "Late", "confidence": 0.5})["state"] == "proposed"


def test_confirming_a_box_writes_keep_and_sell_items_and_records_the_correction(store):
    s = _box_proposed(store)
    piece = store.create_scan("unit", parent_id=s["id"], actor="console")
    assert piece["lot_id"] == s["lot_id"]
    store.add_scan_photo(piece["id"], jpeg())
    store.propose_scan(piece["id"], {"name": "Lumineth Wardens?", "confidence": 0.3})
    with pytest.raises(InventoryError, match="confirmed with its box"):
        store.confirm_scan(piece["id"], {"name": "x"})
    with pytest.raises(InventoryError, match="every piece needs a name"):
        store.confirm_scan(s["id"], {"name": "Warcry: Pyre and Flood", "units": [{"name": ""}]})
    out = store.confirm_scan(
        s["id"],
        {
            "name": "Warcry: Pyre and Flood",
            "system": "Warcry / Age of Sigmar",
            "condition": "NoS",
            "units": [
                {"name": "Pyregheists", "keep": True, "model_count": 10},
                {"name": "Idol of the Old Ones", "keep": False, "category": "Terrain"},
                {"name": "Ydrilan Riverblades", "keep": "false", "scan_id": piece["id"]},
            ],
        },
        actor="console",
    )
    assert out["state"] == "confirmed" and out["correction"] == {
        "proposed": "Warcry: Pyre and Flood",
        "chosen": "Warcry: Pyre and Flood",
        "how": "accepted",
    }
    items = [store.get_item(i) for i in out["item_ids"]]
    assert [(i["name"], i["status"], i["lot_id"]) for i in items] == [
        ("Pyregheists", "kept", s["lot_id"]),
        ("Idol of the Old Ones", "planned", s["lot_id"]),
        ("Ydrilan Riverblades", "planned", s["lot_id"]),
    ]
    assert items[0]["model_count"] == 10 and items[1]["model_count"] == 0
    assert items[1]["condition"] == ""  # the box's condition is not the pieces' condition
    assert items[2]["system"] == "Warcry / Age of Sigmar" and "From scan " + s["id"] in items[2]["notes"]
    assert store.get_lot(s["lot_id"])["name"] == "Warcry: Pyre and Flood"
    child = [u for u in out["units"] if u["id"] == piece["id"]][0]
    assert child["state"] == "confirmed" and child["correction"] == {
        "proposed": "Lumineth Wardens?",
        "chosen": "Ydrilan Riverblades",
    }
    with pytest.raises(InventoryError, match="already confirmed"):
        store.confirm_scan(s["id"], {"name": "x", "units": [{"name": "y"}]})


def test_a_typed_name_is_recorded_as_typed(store):
    u = store.add_scan_photo(store.create_scan("unit")["id"], jpeg())
    store.fail_scan(u["id"], "too dark")
    out = store.confirm_scan(u["id"], {"name": "Big Mek", "keep": False, "condition": "NoS"})
    assert out["correction"] == {"proposed": "", "chosen": "Big Mek", "how": "typed"}
    assert store.get_item(out["item_ids"][0])["status"] == "planned"


def test_pricing_and_binning_close_the_scan(store):
    s = _box_proposed(store)
    out = store.confirm_scan(
        s["id"], {"name": "Pyre", "units": [{"name": "Keep me", "keep": True}, {"name": "Sell A"}, {"name": "Sell B"}]}
    )
    keep, a, b = out["item_ids"]
    scan, items = store.request_pricing(s["id"])
    assert scan["pricing"]["state"] == "queued" and [i["id"] for i in items] == [a, b]
    with pytest.raises(InventoryError, match="not part of"):
        store.pricing_done(s["id"], [{"item_id": "NOPE", "reason": "x"}])
    done = store.pricing_done(s["id"], [{"item_id": b, "reason": "only 1 sold comp"}])
    assert done["pricing"]["state"] == "done" and done["pricing"]["unpriced"] == [
        {"item_id": b, "reason": "only 1 sold comp"}
    ]
    store.create_bins(count=2, actor="t")
    with pytest.raises(InventoryError, match="no bin"):
        store.finish_scan(s["id"], {a: "B09"})
    with pytest.raises(InventoryError, match="not part of"):
        store.finish_scan(s["id"], {"OTHER": "B01"})
    fin = store.finish_scan(s["id"], {a: "B01", keep: "B02", b: ""}, actor="console")
    assert fin["finished_at"]
    assert (store.get_item(a)["status"], store.get_item(a)["bin_id"]) == ("available", "B01")
    assert (store.get_item(keep)["status"], store.get_item(keep)["bin_id"]) == ("kept", "B02")  # kept stays kept
    assert (store.get_item(b)["status"], store.get_item(b)["bin_id"]) == ("planned", "")  # unbinned stays to split
    assert [x["action"] for x in store.audit_log(entity_id=a)][:2] == ["update", "move"]
    assert store.list_scans() == [] and store.list_scans(open_only=False)[0]["id"] == s["id"]


def test_discarding_an_unconfirmed_box_removes_its_empty_lot_pieces_and_photos(store):
    s = store.add_scan_photo(store.create_scan("box", paid=50)["id"], jpeg())
    piece = store.add_scan_photo(store.create_scan("unit", parent_id=s["id"])["id"], jpeg())
    assert (store.scans_dir / piece["id"]).exists()
    assert store.discard_scan(s["id"], actor="console") == 1
    assert (
        store.get_lot(s["lot_id"]) is None
        and not (store.scans_dir / s["id"]).exists()
        and not (store.scans_dir / piece["id"]).exists()
    )
    with pytest.raises(InventoryError):
        store.get_scan(piece["id"])
    done = _box_proposed(store)
    store.confirm_scan(done["id"], {"name": "x", "units": [{"name": "y"}]})
    with pytest.raises(InventoryError, match="already made items"):
        store.discard_scan(done["id"])


# ── the turns the plugin starts ─────────────────────────────────────────────────
def test_a_photo_starts_one_cheap_identification_turn_in_its_own_session(registry, host):
    c, _ = _client(registry)
    s = c.post("/api/plugins/inventory/scans", json={"kind": "box", "paid": "101"}).json()["scan"]
    r = c.post(f"/api/plugins/inventory/scans/{s['id']}/photos", content=jpeg(), headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200 and r.json()["scan"]["state"] == "identifying"
    (call,) = host.calls
    assert call["session"] == f"inventory-scan-{s['id']}" and call["job_id"] == f"scan-{s['id']}" and call["delay"] > 0
    p = call["prompt"]
    assert f'inventory_scan_photos("{s["id"]}")' in p and "inventory_scan_propose" in p and "inventory_scan_fail" in p
    assert "Do NOT use eBay" in p and "do NOT start subagents" in p and "At most two web searches" in p
    assert "model_count" in p and "Never guess" in p


def test_a_piece_from_a_box_is_told_the_boxs_known_contents(registry, host):
    c, _ = _client(registry)
    store = _store(registry)
    box = _box_proposed(store)
    s = c.post("/api/plugins/inventory/scans", json={"kind": "unit", "parent_id": box["id"]}).json()["scan"]
    c.post(f"/api/plugins/inventory/scans/{s['id']}/photos", content=png_file(), headers={"Content-Type": "image/png"})
    p = host.calls[-1]["prompt"]
    assert "Pyregheists; Ydrilan Riverblades; Idol of the Old Ones" in p and "Leave contents empty" in p


def test_no_host_seam_fails_the_scan_with_a_reason_instead_of_spinning(registry, monkeypatch):
    monkeypatch.delitem(sys.modules, "graph", raising=False)
    monkeypatch.delitem(sys.modules, "graph.sdk", raising=False)
    monkeypatch.setitem(sys.modules, "graph", None)  # `from graph import sdk` raises
    c, _ = _client(registry)
    s = c.post("/api/plugins/inventory/scans", json={"kind": "unit"}).json()["scan"]
    out = c.post(f"/api/plugins/inventory/scans/{s['id']}/photos", content=jpeg()).json()["scan"]
    assert out["state"] == "failed" and "background turn" in out["reason"]


def test_a_refused_enqueue_is_reported_on_the_scan(store):
    s = store.add_scan_photo(store.create_scan("unit")["id"], jpeg())
    bad = types.SimpleNamespace(run_in_session=lambda *a, **k: {"ok": False, "message": "scheduler unavailable"})
    out = sc.start_identify(store, s, timeout_minutes=10, sdk=bad)
    assert out["state"] == "failed" and out["reason"] == "scheduler unavailable"


def test_the_tools_round_trip_a_scan(registry, host):
    c, tools = _client(registry)
    s = c.post("/api/plugins/inventory/scans", json={"kind": "box", "paid": 60}).json()["scan"]
    c.post(f"/api/plugins/inventory/scans/{s['id']}/photos", content=jpeg())
    seen = tools["inventory_scan_photos"].invoke({"scan_id": s["id"]})
    env = json.loads(seen.split("]", 1)[1])
    assert (
        len(env["images"]) == 1
        and env["images"][0]["mime"] == "image/jpeg"
        and json.loads(env["text"])["kind"] == "box"
    )
    out = json.loads(
        tools["inventory_scan_propose"].invoke(
            {
                "scan_id": s["id"],
                "name": "Ork Flash Gitz",
                "confidence": 0.95,
                "system": "Warhammer 40K",
                "contents": [{"name": "Flash Gitz (10)", "quantity": 1}],
            }
        )
    )
    assert out["ok"] and out["state"] == "proposed" and out["proposal"]["contents"][0]["model_count"] == 0
    assert (
        json.loads(tools["inventory_scan_fail"].invoke({"scan_id": s["id"], "reason": "x"}))["ok"] is False
    )  # not identifying now
    scan = c.post(
        f"/api/plugins/inventory/scans/{s['id']}/confirm",
        json={"name": "Ork Flash Gitz", "units": [{"name": "Flash Gitz (10)"}]},
    ).json()["scan"]
    priced = c.post(f"/api/plugins/inventory/scans/{s['id']}/price").json()["scan"]
    assert priced["pricing"]["state"] == "queued"
    call = host.calls[-1]
    assert call["session"] == f"inventory-scan-{s['id']}-price" and call["job_id"] == f"scan-price-{s['id']}"
    assert (
        "ebay_price_check" in call["prompt"]
        and scan["item_ids"][0] in call["prompt"]
        and "inventory_scan_pricing_done" in call["prompt"]
    )
    done = json.loads(tools["inventory_scan_pricing_done"].invoke({"scan_id": s["id"], "unpriced": []}))
    assert done["ok"] and done["pricing"]["state"] == "done"
    assert ("inventory.scan.changed" in [t for t, _ in registry.events]) or any(
        t == "scan.changed" for t, _ in registry.events
    )


def test_the_scan_api_refuses_bad_input_and_unknown_scans(registry, host):
    c, _ = _client(registry)
    assert c.post("/api/plugins/inventory/scans", json={"kind": "crate"}).status_code == 400
    assert c.get("/api/plugins/inventory/scans/S0000000000").status_code == 404
    assert c.get("/api/plugins/inventory/scans/../../x").status_code == 404
    s = c.post("/api/plugins/inventory/scans", json={"kind": "unit"}).json()["scan"]
    big = c.post(
        f"/api/plugins/inventory/scans/{s['id']}/photos",
        content=b"x",
        headers={"Content-Length": str(30 * 1024 * 1024)},
    )
    assert big.status_code == 413
    lst = c.get("/api/plugins/inventory/scans").json()
    assert lst["threshold"] == 0.7 and lst["timeout_minutes"] == 10 and [x["id"] for x in lst["scans"]] == [s["id"]]
    assert c.delete(f"/api/plugins/inventory/scans/{s['id']}").json() == {"ok": True}
    assert c.delete(f"/api/plugins/inventory/scans/{s['id']}").status_code == 404


def test_scan_routes_are_gated_like_every_other_data_route():
    from inventory_plugin.api import build_data_router

    paths = {r.path for r in build_data_router(None, {}).routes}
    assert {"/scans", "/scans/{scan_id}", "/scans/{scan_id}/photos", "/scans/{scan_id}/confirm"} <= paths


def test_an_older_db_gains_the_scans_table_on_open(tmp_path):
    import sqlite3

    from inventory_plugin.store import InventoryStore

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE lots (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    con.commit()
    con.close()
    store = InventoryStore(db)
    assert store.list_scans() == []
