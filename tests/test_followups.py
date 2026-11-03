"""0.9.1: one Stale price setting for the view, the review and the tools; Ask for a re-price
drafts in chat through the console bridge (never sends, never "*"); Edit never sets status."""

from __future__ import annotations

import json
import re

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin.automations import stale_price_days, weekly_review_prompt

from tests.conftest import FakeRegistry
from tests.test_view import _run_page


@pytest.mark.parametrize(
    ("raw", "days"),
    [(None, 30), ("", 30), (45, 45), ("45", 45), (0, 1), (-3, 1), (9999, 365), ("soon", 30), (12.7, 12)],
)
def test_stale_price_days_is_one_bounded_setting(raw, days):
    assert stale_price_days({"stale_price_days": raw}) == days


def test_the_weekly_review_uses_the_same_number():
    assert "price_days=45)" in weekly_review_prompt({"stale_price_days": 45})
    assert "price_days=30)" in weekly_review_prompt({})


def _client(tmp_path, **cfg):
    reg = FakeRegistry({"db_path": str(tmp_path / "inv.db"), "workspace_dir": str(tmp_path), **cfg})
    inventory_plugin.register(reg)
    app = FastAPI()
    for router, prefix in reg.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app), reg


def test_the_view_reads_the_setting_and_stale_defaults_to_it(tmp_path):
    c, reg = _client(tmp_path, stale_price_days=45)
    assert c.get("/api/plugins/inventory/view-config").json() == {"stale_price_days": 45}
    api = "/api/plugins/inventory"
    c.put(api + "/lots/L", json={"name": "L"})
    for iid, when in (("OLD", "2026-01-01"), ("NEW", "2099-01-01")):
        c.post(api + "/items", json={"id": iid, "name": iid, "lot_id": "L"})
        c.post(api + f"/items/{iid}/price", json={"target": 10, "basis": "b", "observed_on": when})
    got = c.get(api + "/stale").json()
    assert {s["id"] for s in got["stale_prices"]} == {"OLD"} and got["thresholds"]["price_days"] == 45
    tools = {t.name: t for t in reg.tools}
    got = json.loads(tools["inventory_stale"].invoke({}))
    assert {s["id"] for s in got["stale_prices"]} == {"OLD"} and got["thresholds"]["price_days"] == 45


def test_the_page_posts_only_a_chat_compose_and_never_to_star():
    from inventory_plugin.view import PAGE

    calls = re.findall(r"postMessage\(([^;]*)\);", PAGE)
    assert len(calls) == 1
    assert '"protoagent:chat:compose"' in calls[0] and calls[0].rstrip().endswith(", origin")
    assert not re.search(r"postMessage\([^;]*[\"']\*[\"']", PAGE)


def test_the_reprice_request_names_the_items_and_stays_under_4kb():
    probe = (
        "\nconst many = Array.from({length: 200}, (_, k) => ({ id: 'ITEM-' + k, name: 'A fairly long item name number ' + k, target: 20 + k }));"
        "\nconst few = [{ id: 'A1', name: 'Nobz', target: 30 }];"
        "\nconsole.log(JSON.stringify({ many: repriceText(many), few: repriceText(few), top: hostOrigin() }));\n"
    )
    out = _run_page(probe)
    assert len(out["many"]) <= 4000 and "…and " in out["many"] and "inventory_reprice_plan" in out["many"]
    assert "- Nobz (A1), now $30" in out["few"] and "inventory_set_price" in out["few"] and "1 item " in out["few"]
    assert out["top"] == ""  # not embedded: no host origin, so no button and no post
