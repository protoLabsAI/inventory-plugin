"""Scan intake: the agent turns behind a photo — identify it, then (on request) price the
sell pile. The plugin starts each turn itself with ``sdk.run_in_session``; the view never
talks to the agent. Each scan gets its OWN session (``inventory-scan-<id>``): a turn carries
its photos as images, and a shared session would resend every earlier scan's images on
every later turn. The operator's chat is never used.

Both prompts keep the turn cheap and scoped: the identify turn may not use eBay, price tools
or subagents and allows at most two web searches; the pricing turn uses the eBay plugin's
sold comps and nothing else. Neither messages the operator — the Inventory view shows the
result.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.inventory")

#: A second photo taken within this window rides the same identification turn (the job id is
#: stable, and a re-enqueue replaces the pending one-shot).
IDENTIFY_DELAY_S = 4.0

IDENTIFY_BOX = """Inventory scan {scan_id} — a box the operator just bought to break down{lot_hint}. Identify it from its photos. This is an automated turn owned by the inventory plugin; keep it cheap.

1. Call inventory_scan_photos("{scan_id}") to see the photos.
2. Do NOT use eBay, Amazon or any pricing tool, and do NOT start subagents. At most two web searches, and only to confirm what this exact box contains.
3. Call inventory_scan_propose once, with: name (the product as printed on the box), system (the game system, e.g. Warhammer 40K, Blood Bowl, Age of Sigmar, Kill Team), category, condition (a short label ONLY: NIB for a sealed box, else NoS, Assembled, Painted or Used as the photo shows; put any doubt in notes, never in the label), confidence from 0 to 1 that you have the EXACT product, up to 3 alternatives when unsure, and contents: the units it splits into, each with name, quantity, category and model_count. Use 0 for any model count you cannot see or confirm. Never guess contents you cannot see or confirm; a short list is better than an invented one.
4. If you cannot tell what it is, call inventory_scan_fail("{scan_id}", reason) instead.

Do not message the operator; the Inventory view shows the result."""

IDENTIFY_UNIT = """Inventory scan {scan_id} — one piece the operator photographed{lot_hint}. Identify it from its photos. This is an automated turn owned by the inventory plugin; keep it cheap.

1. Call inventory_scan_photos("{scan_id}") to see the photos.
2. Do NOT use eBay, Amazon or any pricing tool, and do NOT start subagents. At most two web searches.
3. Call inventory_scan_propose once, with: name (the unit or kit, as a buyer would search for it), system, category, condition (a short label ONLY: NoS for new on sprue, NIB, Assembled, Painted or Used as the photo shows; put any doubt in notes, never in the label), confidence from 0 to 1, and up to 3 alternatives when unsure. Leave contents empty.{known}
4. If you cannot tell what it is, call inventory_scan_fail("{scan_id}", reason) instead.

Do not message the operator; the Inventory view shows the result."""

PRICE = """Inventory scan {scan_id} — price its sell pile from eBay SOLD comps. This is an automated turn owned by the inventory plugin. Load the ebay-pricing skill first if it is available.

Items:
{items}

For each item: run ebay_price_check (sold) with a precise buyer-style query (name plus condition words like "new on sprue" or "NIB"). Read notes / headline_count / results_found before the statistics. With at least 3 relevant sold comps, call inventory_set_price with low=p25, target=median, high=p75, basis "eBay sold comps (N sold, incl. shipping)" and the evidence (source=ebay_sold, n, p25, median, p75, query). With fewer, set NO price. If eBay shows a sign-in wall or a challenge page, stop the eBay calls.

{sealed}Do not start subagents and do not message the operator. Finish by calling inventory_scan_pricing_done("{scan_id}", unpriced) listing every item you did not price, each with item_id and a one-line reason."""

SEALED = """The box itself: "{box}". Price it SEALED (new in box) the same way, with ebay_price_check (sold) and the same 3-comp rule, but do NOT call inventory_set_price for it: it isn't an item, it's the comparison that tells the operator whether to sell it sealed instead of split. If ebay_net_proceeds is available, use it for the fees: once on the sealed target, and once per piece on the target you set (shipping_charged 0); add the piece fees up. Then call inventory_scan_sealed_price("{scan_id}", low, target, high, n, query, basis, sealed_fees, split_fees) BEFORE inventory_scan_pricing_done. With fewer than 3 sealed comps, call it with target null and say why in basis. Leave a fee argument null when you couldn't compute it; never guess a fee.

"""


def session_for(scan_id: str, purpose: str = "identify") -> str:
    """One session per scan per purpose — never the operator's chat."""
    return f"inventory-scan-{scan_id}" + ("" if purpose == "identify" else f"-{purpose}")


def _sdk():
    try:
        from graph import sdk  # host-only; absent in the test suite unless stubbed

        return sdk
    except Exception:  # noqa: BLE001
        return None


def identify_prompt(scan: dict, *, lot_name: str = "", known: list[str] | None = None) -> str:
    hint = f" (lot: {lot_name})" if lot_name else ""
    if scan["kind"] == "box":
        return IDENTIFY_BOX.format(scan_id=scan["id"], lot_hint=hint)
    names = [n for n in (known or []) if n][:30]
    known_txt = (
        " It came out of this box, whose other pieces are: "
        + "; ".join(names)
        + ". Prefer one of those names when it matches."
        if names
        else ""
    )
    return IDENTIFY_UNIT.format(scan_id=scan["id"], lot_hint=hint, known=known_txt)


def price_prompt(scan_id: str, items: list[dict], *, box: str = "") -> str:
    lines = "\n".join(
        f"- {i['id']}: {i['name']}"
        + (
            f" ({', '.join(x for x in (i.get('condition'), i.get('system')) if x)})"
            if i.get("condition") or i.get("system")
            else ""
        )
        for i in items
    )
    sealed = SEALED.format(box=box.replace('"', "'"), scan_id=scan_id) if box else ""
    return PRICE.format(scan_id=scan_id, items=lines, sealed=sealed)


def enqueue(session_id: str, prompt: str, job_id: str, *, delay_seconds: float = 0.0, sdk=None) -> dict:
    """Start one plugin-owned agent turn. ``{"ok": False, "message": …}`` when the host can't."""
    sdk = sdk if sdk is not None else _sdk()
    run = getattr(sdk, "run_in_session", None) if sdk else None
    if run is None:
        return {"ok": False, "message": "This protoAgent can't start a background turn; update it, or type the name."}
    try:
        res = run(session_id, prompt, delay_seconds=delay_seconds, job_id=job_id)
    except Exception as exc:  # noqa: BLE001 — report it on the scan, never crash the upload
        log.warning("[inventory] could not enqueue %s: %s", job_id, exc)
        return {"ok": False, "message": f"Couldn't start the agent: {exc}"}
    return res if isinstance(res, dict) else {"ok": bool(res)}


def start_identify(store, scan: dict, *, timeout_minutes: int, sdk=None) -> dict:
    """Enqueue identification for ``scan`` (already ``identifying``). On failure the scan is
    marked failed with the reason, so the view shows Retry instead of an endless spinner."""
    lot_name, known = "", []
    if scan.get("lot_id"):
        lot = store.get_lot(scan["lot_id"])
        lot_name = (lot or {}).get("name", "")
    if scan.get("parent_id"):
        parent = store.get_scan(scan["parent_id"], timeout_minutes=timeout_minutes)
        known = [u.get("name", "") for u in ((parent.get("proposal") or {}).get("contents") or [])]
    res = enqueue(
        session_for(scan["id"]),
        identify_prompt(scan, lot_name=lot_name, known=known),
        f"scan-{scan['id']}",
        delay_seconds=IDENTIFY_DELAY_S,
        sdk=sdk,
    )
    if not res.get("ok"):
        return store.fail_scan(
            scan["id"],
            res.get("message") or "Couldn't start identification.",
            actor="console",
            timeout_minutes=timeout_minutes,
        )
    return scan


#: The plugin whose tools give sold comps (``ebay_price_check``). Pricing is built on it; without it a
#: pricing turn has no comps tool and either prices nothing or burns turns on web searches.
COMPS_PLUGIN = "ebay"
NO_COMPS_REASON = (
    "No sold-comps source is installed (the ebay plugin isn't enabled), so nothing was looked up. "
    "Set a price by hand with Set price, stating what it rests on."
)


def comps_available(sdk=None) -> bool:
    """Whether the host has the comps plugin enabled. An unknown host (no SDK, or a config without
    ``plugins_enabled``) is given the benefit of the doubt, as before this check existed."""
    sdk = sdk if sdk is not None else _sdk()
    try:
        enabled = getattr(sdk.config(), "plugins_enabled", None) if sdk is not None else None
    except Exception:  # noqa: BLE001
        enabled = None
    return True if enabled is None else COMPS_PLUGIN in list(enabled)


def start_pricing(store, scan: dict, items: list[dict], *, timeout_minutes: int, sdk=None) -> dict:
    box = ""
    if scan.get("kind") == "box" and not scan.get("parent_id") and scan.get("lot_id"):
        box = ((store.get_lot(scan["lot_id"]) or {}).get("name") or "").strip()
    if not comps_available(sdk):
        # No comps tool: don't spend an agent turn. Close the pricing at once with every piece
        # unpriced and the reason, so the view offers Set price instead of waiting.
        return store.pricing_done(
            scan["id"],
            [{"item_id": i["id"], "reason": NO_COMPS_REASON} for i in items],
            actor="console",
            timeout_minutes=timeout_minutes,
        )
    res = enqueue(
        session_for(scan["id"], "price"),
        price_prompt(scan["id"], items, box=box),
        f"scan-price-{scan['id']}",
        sdk=sdk,
    )
    if not res.get("ok"):
        return store.pricing_failed(
            scan["id"],
            res.get("message") or "Couldn't start pricing.",
            actor="console",
            timeout_minutes=timeout_minutes,
        )
    return scan


def scan_timeout(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("scan_timeout_minutes") or 10))
    except (TypeError, ValueError):
        return 10


def confidence_threshold(cfg: dict) -> float:
    try:
        v = float(
            cfg.get("scan_confidence_threshold") if cfg.get("scan_confidence_threshold") not in (None, "") else 0.7
        )
    except (TypeError, ValueError):
        return 0.7
    return min(1.0, max(0.0, v))
