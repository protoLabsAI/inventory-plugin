"""Agent tools over the inventory store. Every tool returns JSON; every mutation names
its actor ("agent") in the audit trail and emits an event so other plugins can react."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .photos import MAX_BYTES
from .store import STATUSES, InventoryError, InventoryStore, normalize_status

log = logging.getLogger("protoagent.plugins.inventory")

ACTOR = "agent"


def _err(exc: Exception) -> str:
    return json.dumps({"ok": False, "error": str(exc)})


def resolve_workspace_path(cfg: dict, path: str) -> Path:
    """A path for import/export: relative → under ``workspace_dir``; when a workspace is
    configured, NOTHING outside it (the manifest promises ``filesystem: scoped``)."""
    workspace = str(cfg.get("workspace_dir") or "").strip()
    p = Path(path).expanduser()
    base = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    p = (p if p.is_absolute() else base / p).resolve()
    if workspace and base not in p.parents and p != base:
        raise InventoryError(f"{path!r} is outside the workspace ({base}); files stay inside it")
    return p


def as_bool(value, default: bool) -> bool:
    """A YAML/console flag: real bools pass through; the strings "false"/"no"/"0"/"off" mean
    False; blank means unset."""
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        return text not in {"false", "no", "0", "off"}
    return bool(value)


def build_tools(store: InventoryStore, cfg: dict, *, emit=lambda topic, data: None):
    from langchain_core.tools import tool

    @tool
    def inventory_summary(lot_id: str = "") -> str:
        """Profit-and-loss roll-up of the inventory: per lot, what it cost, what is left to sell at low/target/high, what has been realized (gross and net), and the projected net if the rest sells at target. Start here for "how are we doing" questions.

        lot_id: one lot, or blank for every lot plus overall totals.
        """
        try:
            return json.dumps({"ok": True, **store.summary(lot_id)})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_list(
        lot_id: str = "", status: str = "", category: str = "", system: str = "", query: str = "", limit: int = 200
    ) -> str:
        """List inventory items with their targets and status, ordered by game system, then lot, then category. Filter by lot, status (comma-separated: planned,available,listed,pending,sold,kept,withdrawn), category, game system, or a text search over name/notes/id/category/system.

        Prices are in dollars. `price_basis` says what a target rests on — repeat it when you quote one.
        """
        try:
            items = store.list_items(
                lot_id=lot_id, status=status, category=category, system=system, query=query, limit=limit
            )
            return json.dumps({"ok": True, "count": len(items), "items": items})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_get(item_id: str) -> str:
        """One item in full: fields, its listings, its sales, and its last 20 price observations (the evidence behind its targets)."""
        item = store.get_item(item_id)
        if item is None:
            return json.dumps({"ok": False, "error": f"no item {item_id!r}"})
        return json.dumps({"ok": True, "item": item})

    @tool
    def inventory_upsert_lot(
        id: str,
        name: str = "",
        acquisition_cost: float | None = None,
        acquired_on: str = "",
        description: str = "",
        source: str = "",
        notes: str = "",
    ) -> str:
        """Create or update a lot — a purchase that items came from (a box, a collection, an auction win). `acquisition_cost` in dollars is what the whole lot cost; P&L is computed against it. Only the fields you pass change."""
        data = {"id": id}
        for k, v in (
            ("name", name),
            ("acquired_on", acquired_on),
            ("description", description),
            ("source", source),
            ("notes", notes),
        ):
            if v:
                data[k] = v
        if acquisition_cost is not None:
            data["acquisition_cost"] = acquisition_cost
        try:
            lot = store.upsert_lot(data, actor=ACTOR)
            emit("lot.changed", {"id": lot["id"]})
            return json.dumps({"ok": True, "lot": lot})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_upsert_item(
        id: str = "",
        name: str = "",
        lot_id: str = "",
        category: str = "",
        system: str = "",
        condition: str = "",
        quantity: int | None = None,
        model_count: int | None = None,
        status: str = "",
        cost_basis: float | None = None,
        retail: float | None = None,
        notes: str = "",
        public: bool | None = None,
        blurb: str = "",
    ) -> str:
        """Create an item (leave `id` blank to mint one) or update fields on an existing one — only the fields you pass change. `system` is the game system (Warhammer 40K, Blood Bowl, …), optional but worth setting: lists and copied Markdown group by it. `public` marks the item for the public site catalog and `blurb` is its short public description (one or two plain sentences a buyer reads; never cost, lot or private notes) — marking it public does not publish anything: the operator reviews and publishes from the Inventory view. Targets/prices are NOT set here: use inventory_set_price so the evidence is recorded with them. Status is one of planned, available, listed, pending, sold, kept, withdrawn (planned = exists once a sealed box is split); to record a sale use inventory_mark_sold instead of setting status=sold."""
        data: dict = {}
        if id:
            data["id"] = id
        for k, v in (
            ("name", name),
            ("lot_id", lot_id),
            ("category", category),
            ("system", system),
            ("condition", condition),
            ("status", status),
            ("notes", notes),
            ("blurb", blurb),
        ):
            if v:
                data[k] = v
        for k, v in (
            ("public", public),
            ("quantity", quantity),
            ("model_count", model_count),
            ("cost_basis", cost_basis),
            ("retail", retail),
        ):
            if v is not None:
                data[k] = v
        try:
            if data.get("status") and normalize_status(data["status"])[0] == "sold":
                return json.dumps(
                    {"ok": False, "error": "record a sale with inventory_mark_sold, which sets status=sold itself"}
                )
            item = store.upsert_item(data, actor=ACTOR)
            emit("item.changed", {"id": item["id"], "action": "upsert"})
            return json.dumps({"ok": True, "item": item})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_delete_item(item_id: str) -> str:
        """Permanently remove an item and its listings, sales and observations. Prefer status=withdrawn or kept for things that merely left the sale pile — delete is for mistakes and duplicates."""
        n = store.delete_item(item_id, actor=ACTOR)
        if n:
            emit("item.changed", {"id": item_id, "action": "delete"})
        return json.dumps({"ok": bool(n), **({} if n else {"error": f"no item {item_id!r}"})})

    @tool
    def inventory_set_price(
        item_id: str,
        basis: str,
        target: float | None = None,
        low: float | None = None,
        high: float | None = None,
        observed_on: str = "",
        source: str = "",
        n: int = 0,
        p25: float | None = None,
        median: float | None = None,
        p75: float | None = None,
        query: str = "",
    ) -> str:
        """Set an item's price band WITH the evidence it rests on. `basis` is mandatory and human-readable (e.g. "eBay sold comps (22 sold, incl. shipping)" or "retail anchor; no sold comps"). Pass the comps too — source (ebay_sold, ebay_active, amazon, retail, manual), n, p25/median/p75, query — and they are stored as a price observation so the "why" survives a later re-price. Dollars throughout."""
        observation = None
        if source:
            observation = {
                "source": source,
                "n": n,
                "p25": p25,
                "median": median,
                "p75": p75,
                "query": query,
                "basis": basis,
            }
        try:
            item = store.set_price(
                item_id,
                low=low,
                target=target,
                high=high,
                basis=basis,
                observed_on=observed_on,
                actor=ACTOR,
                observation=observation,
            )
            emit("item.changed", {"id": item_id, "action": "price"})
            return json.dumps({"ok": True, "item": item})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_mark_sold(
        item_id: str,
        price: float,
        channel: str,
        sold_on: str = "",
        fees: float = 0,
        shipping_charged: float = 0,
        shipping_cost: float = 0,
        quantity: int = 1,
        notes: str = "",
        force: bool = False,
    ) -> str:
        """Record a sale: writes the sale (net = price + shipping charged − fees − shipping cost), decrements the item's quantity or, on the last unit, sets it to sold and closes its live listings. `channel` is where it sold (eBay, Facebook, local, r/miniswap …). Dollars; `sold_on` YYYY-MM-DD, default today. An item that is already sold is refused — a retried call must not double the revenue — unless force=True."""
        try:
            sale = store.mark_sold(
                item_id,
                price=price,
                channel=channel,
                sold_on=sold_on,
                fees=fees,
                shipping_charged=shipping_charged,
                shipping_cost=shipping_cost,
                quantity=quantity,
                notes=notes,
                actor=ACTOR,
                force=force,
            )
            emit("sale.recorded", {"item_id": item_id, "sale_id": sale["id"], "net": sale["net"], "channel": channel})
            return json.dumps({"ok": True, "sale": sale})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_listing(
        action: str,
        item_id: str = "",
        listing_id: int = 0,
        channel: str = "",
        url: str = "",
        price: float | None = None,
        state: str = "ended",
        notes: str = "",
    ) -> str:
        """Track where an item is listed. action="add" (item_id, channel, url, price) records a live listing and moves an available item to listed; action="end" (listing_id, state=ended|sold) closes it and returns the item to available if nothing else is live. Dollars."""
        try:
            if action == "add":
                listing = store.add_listing(item_id, channel=channel, url=url, price=price, notes=notes, actor=ACTOR)
                emit("item.changed", {"id": item_id, "action": "listed"})
                return json.dumps({"ok": True, "listing": listing})
            if action == "end":
                listing = store.end_listing(int(listing_id), state=state, actor=ACTOR)
                emit("item.changed", {"id": listing["item_id"], "action": "listing_ended"})
                return json.dumps({"ok": True, "listing": listing})
            return json.dumps({"ok": False, "error": "action must be 'add' or 'end'"})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_import_csv(path: str = "", csv_text: str = "", kind: str = "auto", default_lot: str = "") -> str:
        """Load a spreadsheet into the inventory. Pass a file path (relative paths resolve against the agent workspace) or the CSV text itself. Headers are matched by common names (inventory_id/id, item/name, lot_id, target_low_usd, target_price_usd, target_high_usd, status, sold_price_usd, price_source …); kind is auto-detected (items vs lots) unless given. Rows are upserted by id; a row whose status reads sold with a price also records the sale. Returns counts, the column mapping used, ignored columns and per-line warnings."""
        try:
            text = csv_text
            if not text:
                if not path:
                    return json.dumps({"ok": False, "error": "pass a path or csv_text"})
                p = resolve_workspace_path(cfg, path)
                raw = p.read_bytes()
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252")  # Excel on Windows
            from .csvio import import_csv

            out = import_csv(store, text, kind=kind, actor=ACTOR, default_lot=default_lot)
            if out.get("ok"):
                emit(
                    "imported", {"kind": out.get("kind"), "created": out.get("created"), "updated": out.get("updated")}
                )
            return json.dumps(out)
        except (OSError, UnicodeDecodeError, csv.Error, InventoryError, ValueError) as exc:
            return _err(exc)

    @tool
    def inventory_export_csv(path: str = "", kind: str = "items", lot_id: str = "", status: str = "") -> str:
        """Write items, lots or sales (kind=items|lots|sales; optionally one lot / a status filter) as CSV to `path` inside the agent workspace, or return the CSV text when no path is given. A spreadsheet view, not a backup: an items export carries statuses, not the sales behind them — export sales too."""
        from .csvio import export_csv

        try:
            text = export_csv(store, kind=kind, lot_id=lot_id, status=status)
            if not path:
                return json.dumps({"ok": True, "csv": text})
            p = resolve_workspace_path(cfg, path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return json.dumps({"ok": True, "path": str(p), "rows": max(0, text.count("\n") - 1)})
        except (OSError, InventoryError) as exc:
            return _err(exc)

    @tool
    def inventory_reprice_plan(lot_id: str = "", max_items: int = 15, price_days: int = 30) -> str:
        """The re-price worklist: unsold items whose price evidence is older than `price_days` or missing, oldest first, capped at `max_items`, each with a suggested buyer-style eBay query and its current band. Loop it: ebay_price_check (sold) → inventory_set_price with the basis and the comps."""
        from .automations import reprice_plan

        return json.dumps(
            {"ok": True, **reprice_plan(store, lot_id=lot_id, max_items=max_items, price_days=price_days)}
        )

    @tool
    def inventory_add_photo(item_id: str, path: str, alt: str = "") -> str:
        """Attach a photo file (JPEG, PNG, WebP, or HEIC on a Mac) from the agent workspace to an item. Location, camera and other metadata are stripped on the way in. The first photo is the item's cover on the public site. `alt` describes the photo for people using screen readers, e.g. "Griff Oberwald miniature, front view, unpainted"."""
        try:
            if not str(cfg.get("workspace_dir") or "").strip():
                return _err(
                    InventoryError(
                        "no agent workspace is configured, so photos can't be read from disk — "
                        "set inventory.workspace_dir, or add photos in the Inventory view"
                    )
                )
            p = resolve_workspace_path(cfg, path)
            if not p.is_file():
                return _err(InventoryError(f"no file at {path!r}"))
            if p.stat().st_size > MAX_BYTES:
                return _err(InventoryError(f"photos are capped at {MAX_BYTES // (1024 * 1024)} MB"))
            photo = store.add_photo(item_id, p.read_bytes(), alt=alt, actor=ACTOR)
            emit("item.changed", {"id": item_id, "action": "photo_added"})
            return json.dumps({"ok": True, "photo": photo})
        except (OSError, InventoryError) as exc:
            return _err(exc)

    @tool
    def inventory_publish_preview() -> str:
        """Preview the public site catalog as it would be published right now: how many items, which were added, removed or changed since the last publish, and which public items are left out and why (no price, not for sale). READ-ONLY — there is no publish tool: only the operator publishes, from the Inventory view. Use it to tell the operator what is ready and what still needs a price, photos or a blurb."""
        from .publish import preview

        try:
            out = preview(store, cfg)
        except (OSError, InventoryError) as exc:
            return _err(exc)
        out.pop("hash", None)  # the publish token stays with the operator's page
        out["items_preview"] = [
            {"id": e["id"], "name": e["name"], "price": e["price_cents"] / 100, "photos": len(e["photos"])}
            for e in out["items_preview"]
        ]
        return json.dumps(out)

    @tool
    def inventory_stale(listed_days: int = 14, price_days: int = 30) -> str:
        """What needs attention: listings live longer than `listed_days`, and unsold items whose price evidence is older than `price_days` or missing. The weekly-review starting point."""
        return json.dumps({"ok": True, **store.stale(listed_days=listed_days, price_days=price_days)})

    return [
        inventory_summary,
        inventory_list,
        inventory_get,
        inventory_upsert_lot,
        inventory_upsert_item,
        inventory_delete_item,
        inventory_set_price,
        inventory_mark_sold,
        inventory_listing,
        inventory_import_csv,
        inventory_export_csv,
        inventory_reprice_plan,
        inventory_add_photo,
        inventory_publish_preview,
        inventory_stale,
    ]


__all__ = ["build_tools", "STATUSES"]
