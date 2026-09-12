"""Slice 3: the weekly review job is armed/disarmed through the host scheduler seam, the
prompt drives the same tools, and the re-price plan orders and caps the worklist."""

from __future__ import annotations

import json
import sys
import types

import inventory_plugin
import pytest
import yaml
from inventory_plugin.automations import DEFAULT_CRON, JOB_ID, reprice_plan, suggest_query, weekly_review_prompt


@pytest.fixture
def fake_sdk(monkeypatch):
    """A stand-in for the host's `graph.sdk` — records schedule/cancel calls."""
    calls = []
    sdk = types.SimpleNamespace(
        schedule_recurring=lambda prompt, cron, *, plugin_id, job_id, session="", timezone=None: (
            calls.append(("schedule", prompt, cron, plugin_id, job_id, timezone))
            or {
                "ok": True,
                "job_id": f"plugin:{plugin_id}:{job_id}",
                "next_fire": "2026-09-14T09:00:00",
                "message": "ok",
            }
        ),
        cancel_scheduled=lambda job_id, *, plugin_id: calls.append(("cancel", job_id, plugin_id)) or True,
    )
    graph = types.ModuleType("graph")
    graph.sdk = sdk
    monkeypatch.setitem(sys.modules, "graph", graph)
    monkeypatch.setitem(sys.modules, "graph.sdk", sdk)
    return calls


class TestArming:
    def test_enabled_arms_the_job_with_the_configured_cadence(self, registry, fake_sdk):
        registry.config.update(
            {
                "weekly_review": True,
                "weekly_review_cron": "30 8 * * 2",
                "review_timezone": "America/Los_Angeles",
                "review_max_items": 7,
            }
        )
        inventory_plugin.register(registry)
        sched = [c for c in fake_sdk if c[0] == "schedule"]
        assert len(sched) == 1
        _, prompt, cron, plugin_id, job_id, tz = sched[0]
        assert (cron, plugin_id, job_id, tz) == ("30 8 * * 2", "inventory", JOB_ID, "America/Los_Angeles")
        for tool in (
            "inventory_stale",
            "inventory_reprice_plan",
            "ebay_price_check",
            "inventory_set_price",
            "inventory_summary",
        ):
            assert tool in prompt
        assert "at most 7 items" in prompt
        assert "Do NOT change or end a listing" in prompt

    def test_disabled_cancels_the_job(self, registry, fake_sdk):
        registry.config.update({"weekly_review": "false"})
        inventory_plugin.register(registry)
        assert [c for c in fake_sdk if c[0] == "schedule"] == []
        assert ("cancel", JOB_ID, "inventory") in fake_sdk

    def test_default_is_off_and_default_cron_is_monday_morning(self, registry, fake_sdk):
        inventory_plugin.register(registry)
        assert [c for c in fake_sdk if c[0] == "schedule"] == []
        assert DEFAULT_CRON == "0 9 * * 1"

    def test_no_scheduler_seam_does_not_break_registration(self, registry, monkeypatch):
        monkeypatch.setitem(sys.modules, "graph", None)  # `from graph import sdk` raises ImportError
        registry.config.update({"weekly_review": True})
        inventory_plugin.register(registry)  # must not raise
        assert registry.tools

    def test_the_manifest_ships_the_review_off_with_settings(self):
        from pathlib import Path

        manifest = yaml.safe_load((Path(inventory_plugin.__file__).parent / "protoagent.plugin.yaml").read_text())
        assert manifest["config"]["weekly_review"] is False
        assert manifest["config"]["weekly_review_cron"] == DEFAULT_CRON
        assert {s["key"] for s in manifest["settings"]} >= {
            "weekly_review",
            "weekly_review_cron",
            "review_timezone",
            "stale_price_days",
        }


class TestReplan:
    def test_suggest_query_keeps_price_changing_condition_words_only(self):
        assert (
            suggest_query({"name": "Brionne Barons Bretonnian Team", "condition": "new on sprue"})
            == "Brionne Barons Bretonnian Team new on sprue"
        )
        assert (
            suggest_query({"name": "Third Season box", "condition": "Sealed (shrink torn)"})
            == "Third Season box sealed"
        )
        assert suggest_query({"name": "Sealed Second Season box", "condition": "sealed"}) == "Sealed Second Season box"
        assert suggest_query({"name": "Dice", "condition": ""}) == "Dice"

    def test_plan_orders_oldest_evidence_first_and_caps(self, store):
        for i, (iid, when) in enumerate((("A", "2026-09-01"), ("B", ""), ("C", "2025-01-01"), ("D", "2026-09-10"))):
            store.upsert_item({"id": iid, "name": f"Item {iid}", "lot_id": "L"}, actor="t")
            if when:
                store.set_price(iid, target=10 + i, basis="b", observed_on=when, actor="t")
        store.upsert_item({"id": "S", "name": "Sold", "lot_id": "L"}, actor="t")
        store.mark_sold("S", price=5, channel="x", actor="t")
        plan = reprice_plan(store, price_days=30, max_items=2)
        assert [p["item_id"] for p in plan["items"]] == ["B", "C"]  # missing evidence first, then oldest
        assert plan["total_stale"] == 2  # A (11 days) and D are fresh; S is sold; B has no evidence, C is ancient
        assert plan["items"][1]["current"]["target"] == 12.0
        assert plan["items"][0]["query"] == "Item B"

    def test_plan_tool_and_lot_filter(self, registry):
        inventory_plugin.register(registry)
        t = {x.name: x for x in registry.tools}
        t["inventory_upsert_item"].invoke({"id": "X", "name": "Thing", "lot_id": "L1"})
        t["inventory_upsert_item"].invoke({"id": "Y", "name": "Other", "lot_id": "L2"})
        out = json.loads(t["inventory_reprice_plan"].invoke({"lot_id": "L1"}))
        assert out["ok"] and [p["item_id"] for p in out["items"]] == ["X"]

    def test_prompt_uses_the_configured_thresholds(self):
        p = weekly_review_prompt({"stale_listing_days": 7, "stale_price_days": 21, "review_max_items": 3})
        assert "listed_days=7" in p and "price_days=21" in p and "at most 3 items" in p
