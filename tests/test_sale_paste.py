"""0.12.0 — paste a sale: an agent turn reads a pasted order and proposes sale lines; the
operator checks and records them (one audited mark_sold per line)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from inventory_plugin import sales
from inventory_plugin.store import InventoryError, InventoryStore, weekday_conflict


def _stock(store):
    store.upsert_lot({"id": "DB", "name": "Dungeon box", "acquisition_cost": 100}, actor="t")
    store.upsert_item({"id": "DICE-H", "name": "Human Team Dice", "lot_id": "DB", "system": "Blood Bowl"}, actor="t")
    store.upsert_item(
        {"id": "DICE-D1", "name": "Dungeon Bowl Dice (red)", "lot_id": "DB", "system": "Blood Bowl"}, actor="t"
    )
    store.upsert_item(
        {"id": "DICE-D2", "name": "Dungeon Bowl Dice (blue)", "lot_id": "DB", "system": "Blood Bowl"}, actor="t"
    )
    store.upsert_item(
        {"id": "MEK", "name": "Big Mek with Shokk Attack Gun", "notes": "GW 99120103031", "system": "Warhammer 40K"},
        actor="t",
    )
    store.upsert_item({"id": "GONE", "name": "Ork Lootas"}, actor="t")
    store.mark_sold("GONE", price=40, channel="eBay", actor="t")


def _today():
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _line(item_id, price, **kw):
    return {
        "item_id": item_id,
        "price": price,
        "channel": kw.pop("channel", "eBay"),
        "sold_on": kw.pop("sold_on", _today()),
        **kw,
    }


class FakeSdk:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def run_in_session(self, session_id, prompt, delay_seconds=0.0, job_id=None):
        self.calls.append((session_id, prompt, job_id))
        return {"ok": self.ok, "message": "" if self.ok else "scheduler unavailable"}


# ── candidates ────────────────────────────────────────────────────────────────
def test_candidates_find_by_words_part_number_and_skip_sold(store):
    _stock(store)
    names = [c["item_id"] for c in store.sale_candidates("both dungeon bowl dice")]
    assert names[:2] == ["DICE-D1", "DICE-D2"] or set(names[:2]) == {"DICE-D1", "DICE-D2"}
    assert store.sale_candidates("Orks Big Mek Shokk Attack Gun 99120103031")[0]["item_id"] == "MEK"
    assert "GONE" not in [c["item_id"] for c in store.sale_candidates("ork lootas")]
    assert store.sale_candidates("zzzz qqqq") == []


def test_candidates_put_the_opened_item_first(store):
    _stock(store)
    assert store.sale_candidates("dice", include="MEK")[0]["item_id"] == "MEK"


# ── the weekday guard ────────────────────────────────────────────────────────
def test_weekday_conflict_catches_a_wrong_year():
    # Mon Dec 23 is a Monday in 2024; 2026-12-23 is a Wednesday.
    assert "Wednesday" in weekday_conflict("Delivered on Mon, Dec 23 Return window closed", "2026-12-23")
    assert weekday_conflict("Delivered on Mon, Dec 23", "2024-12-23") == ""
    assert weekday_conflict("Delivered on Mon, Dec 23, 2024", "2026-12-23") == ""  # a stated year: not our shape
    assert weekday_conflict("sold for 50", "2026-12-23") == ""


# ── propose ──────────────────────────────────────────────────────────────────
def test_propose_validates_and_stores_lines(store):
    _stock(store)
    p = store.create_paste("three dice sets went together, $70 plus $9 shipping, to a friend")
    assert p["state"] == "parsing" and p["id"].startswith("P")
    lines = [
        _line("DICE-H", 24, channel="Personal connection", shipping_charged=3),
        _line("DICE-D1", 23, channel="Personal connection", shipping_charged=3),
        _line("DICE-D2", 23, channel="Personal connection", shipping_charged=3),
    ]
    p = store.propose_paste(p["id"], lines, confidence=0.8, questions=["Split by hand"], total=70)
    assert p["state"] == "proposed"
    assert [ln["item_name"] for ln in p["proposal"]["lines"]] == [
        "Human Team Dice",
        "Dungeon Bowl Dice (red)",
        "Dungeon Bowl Dice (blue)",
    ]
    assert p["proposal"]["lines"][0]["net"] == 27.0 and p["proposal"]["total"] == 70.0
    assert p["proposal"]["questions"] == ["Split by hand"]


def test_propose_refuses_a_split_that_doesnt_add_up(store):
    _stock(store)
    p = store.create_paste("both dungeon dice for $80")
    with pytest.raises(InventoryError, match="add up to 70.0 but the paste's total is 80.0"):
        store.propose_paste(p["id"], [_line("DICE-D1", 35), _line("DICE-D2", 35)], confidence=0.9, total=80)


@pytest.mark.parametrize(
    ("line", "match"),
    [
        ({"item_id": "NOPE", "price": 5, "channel": "eBay"}, "no item 'NOPE'"),
        ({"item_id": "GONE", "price": 5, "channel": "eBay"}, "already sold"),
        ({"item_id": "MEK", "price": 5}, "where did it sell"),
        ({"item_id": "MEK", "channel": "eBay"}, "needs a price"),
        ({"item_id": "MEK", "price": 5, "channel": "eBay", "quantity": 2}, "holds 1"),
        ({"item_id": "MEK", "price": 5, "channel": "eBay", "sold_on": "12/23"}, "YYYY-MM-DD"),
        ({"item_id": "MEK", "price": 5, "channel": "eBay", "sold_on": "2999-01-01"}, "in the future"),
        ({"item_id": "MEK", "price": 5, "channel": "eBay", "fees": -1}, "can't be negative"),
    ],
)
def test_propose_refuses_bad_lines_with_a_fix(store, line, match):
    _stock(store)
    p = store.create_paste("something sold")
    with pytest.raises(InventoryError, match=match):
        store.propose_paste(p["id"], [line], confidence=0.5)
    assert store.get_paste(p["id"])["state"] == "parsing"


def test_propose_refuses_a_year_that_puts_the_weekday_wrong(store):
    _stock(store)
    p = store.create_paste("Big Mek Shokk Attack Gun $50.00 Delivered on Mon, Dec 23")
    # 2025-12-23 is a Tuesday: the paste's "Mon Dec 23" is another year (2024).
    with pytest.raises(InventoryError, match="is a Tuesday"):
        store.propose_paste(p["id"], [_line("MEK", 50, sold_on="2025-12-23")], confidence=0.9)
    # a year in the future is refused outright
    with pytest.raises(InventoryError, match="in the future"):
        store.propose_paste(p["id"], [_line("MEK", 50, sold_on="2026-12-23")], confidence=0.9)
    assert store.propose_paste(p["id"], [_line("MEK", 50, sold_on="2024-12-23")], confidence=0.9)["state"] == "proposed"


def test_propose_refuses_duplicate_items_and_only_once(store):
    _stock(store)
    p = store.create_paste("mek")
    with pytest.raises(InventoryError, match="two lines"):
        store.propose_paste(p["id"], [_line("MEK", 30), _line("MEK", 33)], confidence=0.9)
    store.propose_paste(p["id"], [_line("MEK", 50)], confidence=0.9)
    with pytest.raises(InventoryError, match="isn't waiting"):
        store.propose_paste(p["id"], [_line("MEK", 50)], confidence=0.9)


# ── record ───────────────────────────────────────────────────────────────────
def test_record_writes_one_sale_per_line_and_lands_them_on_to_pack(store):
    _stock(store)
    p = store.create_paste("dice bundle")
    store.propose_paste(p["id"], [_line("DICE-D1", 40, fees=0), _line("DICE-D2", 40)], confidence=0.9, total=80)
    out = store.record_paste(p["id"], actor="console")
    assert out["state"] == "recorded" and len(out["sales"]) == 2 and len(out["sale_ids"]) == 2
    assert {s["item_id"] for s in store.to_pack()} >= {"DICE-D1", "DICE-D2"}
    assert store.get_item("DICE-D1")["status"] == "sold"
    assert "From a pasted order" in out["sales"][0]["notes"]
    with pytest.raises(InventoryError, match="isn't ready"):
        store.record_paste(p["id"])


def test_record_uses_the_operators_edited_lines(store):
    _stock(store)
    p = store.create_paste("mek sold 50 + 5 shipping")
    store.propose_paste(p["id"], [_line("MEK", 50, shipping_charged=5, fees=7.5, fees_estimated=True)], confidence=0.9)
    edited = [dict(store.get_paste(p["id"])["proposal"]["lines"][0], fees=8.88)]
    out = store.record_paste(p["id"], edited, actor="console")
    sale = out["sales"][0]
    assert sale["fees"] == 8.88 and sale["net"] == round(50 + 5 - 8.88, 2)
    assert "fees estimated" in sale["notes"]


def test_a_failed_line_keeps_the_recorded_ones_and_a_retry_skips_them(store):
    _stock(store)
    p = store.create_paste("two things")
    store.propose_paste(p["id"], [_line("DICE-H", 20), _line("MEK", 50)], confidence=0.9)
    store.mark_sold("MEK", price=50, channel="eBay", actor="t")  # sold meanwhile, by hand
    with pytest.raises(InventoryError, match="already sold"):
        store.record_paste(p["id"])  # validation catches it before anything is written
    assert store.get_item("DICE-H")["status"] == "available"
    lines = store.get_paste(p["id"])["proposal"]["lines"][:1]
    out = store.record_paste(p["id"], lines)
    assert out["state"] == "recorded" and store.get_item("DICE-H")["status"] == "sold"


def test_discard_retry_and_fail(store):
    _stock(store)
    p = store.create_paste("??")
    f = store.fail_paste(p["id"], "nothing matches")
    assert f["state"] == "failed" and f["reason"] == "nothing matches"
    assert store.retry_paste(p["id"])["state"] == "parsing"
    assert store.discard_paste(p["id"])["state"] == "discarded"
    assert store.list_pastes() == []
    with pytest.raises(InventoryError, match="paste the order"):
        store.create_paste("   ")
    with pytest.raises(InventoryError, match="no pasted sale"):
        store.get_paste("x")


def test_a_parse_that_never_answers_reads_as_failed(store):
    _stock(store)
    p = store.create_paste("mek")
    old = (datetime.now(UTC) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store._tx() as con:
        con.execute("UPDATE sale_pastes SET started_at=? WHERE id=?", (old, p["id"]))
    got = store.get_paste(p["id"])
    assert got["state"] == "failed" and "didn't finish" in got["reason"]
    # a late answer still counts: better than a retry
    assert store.propose_paste(p["id"], [_line("MEK", 50)], confidence=0.9)["state"] == "proposed"


# ── the turn ─────────────────────────────────────────────────────────────────
def test_the_parse_turn_runs_in_its_own_session_with_the_paste_and_the_rules(store):
    _stock(store)
    p = store.create_paste("sold human dice for 20", item_hint="DICE-H")
    sdk = FakeSdk()
    out = sales.start_parse(store, p, sdk=sdk)
    assert out["state"] == "parsing"
    [(session, prompt, job)] = sdk.calls
    assert session == f"inventory-sale-{p['id']}" and job == f"sale-{p['id']}"
    assert "sold human dice for 20" in prompt and "DICE-H" in prompt and "Human Team Dice" in prompt
    for rule in (
        "Do NOT browse the web",
        "inventory_sale_candidates",
        "inventory_sale_propose",
        "never just stamp the current year",
        "Never copy a buyer's name",
    ):
        assert rule in prompt


def test_a_turn_that_cant_start_fails_the_paste_readably(store):
    _stock(store)
    p = store.create_paste("x")
    out = sales.start_parse(store, p, sdk=FakeSdk(ok=False))
    assert out["state"] == "failed" and "scheduler unavailable" in out["reason"]


def test_an_older_db_gets_the_paste_table_and_listing_state(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE items (id TEXT PRIMARY KEY, lot_id TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', "
        "name TEXT NOT NULL, condition TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL DEFAULT 1, model_count INTEGER "
        "NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', cost_basis_cents INTEGER, status TEXT NOT NULL DEFAULT "
        "'available', target_low_cents INTEGER, target_cents INTEGER, target_high_cents INTEGER, retail_cents INTEGER, "
        "price_basis TEXT NOT NULL DEFAULT '', price_updated_on TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL);"
    )
    con.close()
    store = InventoryStore(db)
    store.upsert_item({"id": "A", "name": "Thing"}, actor="t")
    p = store.create_paste("thing sold")
    assert store.get_paste(p["id"])["state"] == "parsing"


# ── tools + API ──────────────────────────────────────────────────────────────
def test_tools_and_api_round_trip(registry):
    import inventory_plugin
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    inventory_plugin.register(registry)
    tools = {x.name: x for x in registry.tools}
    app = FastAPI()
    for router, prefix in registry.routers:
        app.include_router(router, prefix=prefix)
    c, api = TestClient(app), "/api/plugins/inventory"
    c.post(api + "/items", json={"id": "MEK", "name": "Big Mek with Shokk Attack Gun"})
    # No host here, so the turn can't start: the paste says so instead of spinning.
    p = c.post(
        api + "/sale-pastes", json={"text": "Big Mek Shokk Attack Gun $50.00 +$5.00 shipping", "item_id": "MEK"}
    ).json()["paste"]
    assert p["state"] == "failed" and "can't start" in p["reason"]
    cands = json.loads(tools["inventory_sale_candidates"].invoke({"query": "big mek shokk"}))
    assert cands["ok"] and cands["items"][0]["item_id"] == "MEK"
    bad = json.loads(
        tools["inventory_sale_propose"].invoke(
            {"paste_id": p["id"], "lines": [{"item_id": "X", "price": 1, "channel": "eBay"}], "confidence": 0.5}
        )
    )
    assert bad["ok"] is False and "no item 'X'" in bad["error"]
    ok = json.loads(
        tools["inventory_sale_propose"].invoke(
            {
                "paste_id": p["id"],
                "lines": [{"item_id": "MEK", "price": 50, "shipping_charged": 5, "channel": "eBay"}],
                "confidence": 0.9,
            }
        )
    )
    assert ok["ok"] and ok["lines"] == 1
    assert [x["id"] for x in c.get(api + "/sale-pastes").json()["pastes"]] == [p["id"]]
    rec = c.post(api + f"/sale-pastes/{p['id']}/record", json={}).json()["paste"]
    assert rec["state"] == "recorded" and len(rec["sales"]) == 1
    assert c.get(api + "/to-pack").json()["sales"][0]["item_id"] == "MEK"
    assert c.get(api + "/sale-pastes").json()["pastes"] == []
    assert c.get(api + "/sale-pastes/PNOPE").status_code == 404
    gone = json.loads(tools["inventory_sale_fail"].invoke({"paste_id": p["id"], "reason": "x"}))
    assert gone["ok"] is False
