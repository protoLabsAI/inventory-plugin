"""0.9.1: one Stale price setting for the view, the review and the tools; Ask for a re-price
drafts in chat through the console bridge (never sends; an http(s) console gets its exact origin, the
desktop app's tauri:// console gets "*"); Edit never sets status."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

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
    assert c.get("/api/plugins/inventory/view-config").json() == {"stale_price_days": 45, "aging_days": [45, 90, 120]}
    api = "/api/plugins/inventory"
    c.put(api + "/lots/L", json={"name": "L"})
    # Relative to today: a fixed date goes stale (or fresh) with the calendar (#17 review).
    today = date.today()
    # 40 days old is stale under the default 30 and fresh under 45: the setting decides it.
    old, fresh = (today - timedelta(days=100)).isoformat(), (today - timedelta(days=40)).isoformat()
    for iid, when in (("OLD", old), ("NEW", fresh)):
        c.post(api + "/items", json={"id": iid, "name": iid, "lot_id": "L"})
        c.post(api + f"/items/{iid}/price", json={"target": 10, "basis": "b", "observed_on": when})
    got = c.get(api + "/stale").json()
    assert {s["id"] for s in got["stale_prices"]} == {"OLD"} and got["thresholds"]["price_days"] == 45
    tools = {t.name: t for t in reg.tools}
    got = json.loads(tools["inventory_stale"].invoke({}))
    assert {s["id"] for s in got["stale_prices"]} == {"OLD"} and got["thresholds"]["price_days"] == 45
    # same data, no setting: the default 30 catches the 40-day row too
    c30, _ = _client(tmp_path / "d30")
    c30.put(api + "/lots/L", json={"name": "L"})
    for iid, when in (("OLD", old), ("NEW", fresh)):
        c30.post(api + "/items", json={"id": iid, "name": iid, "lot_id": "L"})
        c30.post(api + f"/items/{iid}/price", json={"target": 10, "basis": "b", "observed_on": when})
    assert {s["id"] for s in c30.get(api + "/stale").json()["stale_prices"]} == {"OLD", "NEW"}


def test_the_page_posts_only_a_chat_compose_to_the_computed_target():
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


def _compose_target(parent_is_self: bool, ancestor: str, referrer: str = "") -> str:
    import json
    import shutil
    import subprocess

    from inventory_plugin.view import PAGE

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    start = PAGE.index("  function hostOrigin() {")
    end = PAGE.index("  const COMPOSE_MAX")
    src = PAGE[start:end]
    parent = "window" if parent_is_self else "self"
    ancestors = json.dumps([ancestor] if ancestor else [])
    env = (
        f"const self = {{}}; const window = {{}}; window.parent = {parent};"
        f"const location = {{ ancestorOrigins: {ancestors} }}; const document = {{ referrer: {json.dumps(referrer)} }};"
    )
    out = subprocess.run(
        [node, "-e", env + src + "console.log(JSON.stringify(composeTarget()));"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize(
    ("parent_is_self", "ancestor", "referrer", "want"),
    [
        (False, "https://hub.example.ts.net", "", "https://hub.example.ts.net"),
        (False, "http://127.0.0.1:7881", "", "http://127.0.0.1:7881"),
        # The desktop app's console: a custom scheme a WebView may treat as opaque, so "*".
        (False, "tauri://localhost", "", "*"),
        (False, "", "", "*"),  # embedded but the origin can't be read: still reach the host
        (True, "", "", ""),  # not embedded: no button, no post
    ],
)
def test_compose_targets_the_console_origin_and_falls_back_to_star_off_http(parent_is_self, ancestor, referrer, want):
    assert _compose_target(parent_is_self, ancestor, referrer) == want
