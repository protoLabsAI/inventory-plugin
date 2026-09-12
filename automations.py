"""Slice 3 — the automations: a plugin-owned weekly review job and the re-price plan the
agent (or that job) works through. Nothing here touches a listing or a price by itself;
the job is an agent TURN that uses the same tools an operator would, with the same rules.
"""

from __future__ import annotations

import logging

from .store import InventoryStore

log = logging.getLogger("protoagent.plugins.inventory")

JOB_ID = "weekly-review"
DEFAULT_CRON = "0 9 * * 1"  # Monday 09:00

WEEKLY_REVIEW_PROMPT = """Weekly inventory review (automated; owned by the inventory plugin). Load the inventory-ops skill and the ebay-pricing skill first, then:

1. Call inventory_stale(listed_days={listed_days}, price_days={price_days}).
2. Stale PRICES — at most {max_items} items this run, oldest evidence first: for each, run ebay_price_check (sold) with a buyer-style query (inventory_reprice_plan suggests one). Read notes / headline_count / results_found before the statistics. With at least 5 exact sold comps, call inventory_set_price with low=p25, target=median, high=p75, basis "eBay sold comps (N sold, incl. shipping)" and the evidence fields (source=ebay_sold, n, p25, median, p75, query). With fewer, keep the targets and call inventory_set_price with the existing numbers and a basis noting "sold comps thin (N)" so the attempt is on record. If eBay answers with a sign-in wall or a challenge page, stop the eBay calls and say so.
3. Stale LISTINGS — for each, report the channel, days live, listed price vs the current target, and recommend one of: drop the price, relist, bundle, withdraw. Do NOT change or end a listing yourself; that is the operator's call.
4. Finish with inventory_summary and report per lot: cost, remaining at target (with the low–high band), realized net, projected net at target. Every number carries its basis; say once that fees are not yet netted out of remaining value.

Keep the whole report short enough to read in a minute."""


def weekly_review_prompt(cfg: dict) -> str:
    return WEEKLY_REVIEW_PROMPT.format(
        listed_days=int(cfg.get("stale_listing_days") or 14),
        price_days=int(cfg.get("stale_price_days") or 30),
        max_items=int(cfg.get("review_max_items") or 15),
    )


def arm(registry, cfg: dict, plugin_id: str, *, enabled: bool) -> dict:
    """Arm (or disarm) the weekly review job through the host scheduler. Idempotent by
    job id, so a config reload re-arms cleanly; a disable cancels. Returns what happened."""
    from graph import sdk  # host-only; absent in the test suite unless stubbed

    if not enabled:
        removed = sdk.cancel_scheduled(JOB_ID, plugin_id=plugin_id)
        return {"armed": False, "cancelled": bool(removed)}
    cron = str(cfg.get("weekly_review_cron") or DEFAULT_CRON).strip()
    tz = str(cfg.get("review_timezone") or "").strip() or None
    res = sdk.schedule_recurring(weekly_review_prompt(cfg), cron, plugin_id=plugin_id, job_id=JOB_ID, timezone=tz)
    if not res.get("ok"):
        log.warning("[inventory] weekly review not scheduled: %s", res.get("message"))
    else:
        log.info("[inventory] weekly review armed: %s (%s) next %s", cron, tz or "UTC", res.get("next_fire"))
    return {
        "armed": bool(res.get("ok")),
        "cron": cron,
        "timezone": tz or "UTC",
        "next_fire": res.get("next_fire"),
        "message": res.get("message"),
    }


_CONDITION_WORDS = ("sealed", "new on sprue", "on sprue", "nib", "new", "unpainted", "painted", "assembled")


def suggest_query(item: dict) -> str:
    """A buyer-style eBay query for an item: the name, plus the condition words that change
    the price, minus the operator's own shorthand (ids, "x10", parenthetical notes)."""
    name = str(item.get("name") or "").strip()
    cond = str(item.get("condition") or "").strip().lower()
    words = [w for w in _CONDITION_WORDS if w in cond and w not in name.lower()]
    # keep the most specific condition phrase only ("new on sprue" already implies "new")
    if "new on sprue" in words and "new" in words:
        words.remove("new")
    if "new on sprue" in words and "on sprue" in words:
        words.remove("on sprue")
    q = " ".join([name, *words]).strip()
    return q


def reprice_plan(store: InventoryStore, *, lot_id: str = "", max_items: int = 15, price_days: int = 30) -> dict:
    """The unsold items whose price evidence is oldest (or missing), capped, each with a
    suggested query and its current band — the worklist a re-price loops over."""
    stale = store.stale(listed_days=10_000, price_days=price_days)["stale_prices"]
    if lot_id:
        stale = [i for i in stale if i["lot_id"] == lot_id]
    stale.sort(key=lambda i: (i["price_updated_on"] or "", i["id"]))
    plan = [
        {
            "item_id": i["id"],
            "name": i["name"],
            "lot_id": i["lot_id"],
            "status": i["status"],
            "query": suggest_query(i),
            "current": {"low": i["target_low"], "target": i["target"], "high": i["target_high"]},
            "price_basis": i["price_basis"],
            "price_updated_on": i["price_updated_on"],
        }
        for i in stale[: max(1, int(max_items))]
    ]
    return {"items": plan, "total_stale": len(stale), "price_days": price_days}
