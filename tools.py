"""Agent tools over the inventory store. Every tool returns JSON; every mutation names
its actor ("agent") in the audit trail and emits an event so other plugins can react."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .automations import stale_price_days
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
        lot_id: str = "",
        status: str = "",
        category: str = "",
        system: str = "",
        query: str = "",
        bin_id: str = "",
        limit: int = 200,
    ) -> str:
        """List inventory items with their targets, status and bin, ordered by game system, then lot, then category. Filter by lot, status (comma-separated: planned,available,listed,pending,sold,kept,withdrawn), category, game system, bin (a bin id such as B03, or "-" for items in no bin), or a text search over name/notes/id/category/system/bin.

        Prices are in dollars. `price_basis` says what a target rests on — repeat it when you quote one.
        """
        try:
            items = store.list_items(
                lot_id=lot_id, status=status, category=category, system=system, query=query, bin_id=bin_id, limit=limit
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
    ) -> str:
        """Create an item (leave `id` blank to mint one) or update fields on an existing one — only the fields you pass change. `system` is the game system (Warhammer 40K, Blood Bowl, …), optional but worth setting: lists and copied Markdown group by it. Targets/prices are NOT set here: use inventory_set_price so the evidence is recorded with them. Status is one of planned, available, listed, pending, sold, kept, withdrawn (planned = exists once a sealed box is split); to record a sale use inventory_mark_sold instead of setting status=sold."""
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
        ):
            if v:
                data[k] = v
        for k, v in (
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
        """Record a sale: writes the sale (net = price + shipping charged − fees − shipping cost), decrements the item's quantity or, on the last unit, sets it to sold: a live listing on the SAME channel closes as sold, one on any other channel is flagged needs_end (still up on that marketplace; the result lists them under "needs_end" — tell the operator to end it there). `channel` is where it sold (eBay, Facebook, local, r/miniswap …). Dollars; `sold_on` YYYY-MM-DD, default today. An item that is already sold is refused — a retried call must not double the revenue — unless force=True."""
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
        """Track where an item is listed. action="add" (item_id, channel, url, price) records a live listing and moves an available item to listed; action="end" (listing_id, state=ended) closes it and returns the item to available if nothing else is live; action="to_end" lists listings still up on a marketplace for items that sold elsewhere (end them there, then action="end"). Dollars."""
        try:
            if action == "add":
                listing = store.add_listing(item_id, channel=channel, url=url, price=price, notes=notes, actor=ACTOR)
                emit("item.changed", {"id": item_id, "action": "listed"})
                return json.dumps({"ok": True, "listing": listing})
            if action == "end":
                listing = store.end_listing(int(listing_id), state=state, actor=ACTOR)
                emit("item.changed", {"id": listing["item_id"], "action": "listing_ended"})
                return json.dumps({"ok": True, "listing": listing})
            if action == "to_end":
                return json.dumps({"ok": True, "listings": store.listings_to_end()})
            return json.dumps({"ok": False, "error": "action must be 'add', 'end' or 'to_end'"})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_bins(action: str = "list", bin_id: str = "", count: int = 1, label: str = "", note: str = "") -> str:
        """The physical bins stock lives in (B01, B02 …). action="list" returns every bin with its item count and unsold value at target, plus how many unsold items are in no bin; action="create" makes `count` new bins continuing the B01 sequence (or the one named `bin_id`), with an optional `label` such as "Blood Bowl overflow"; action="update" changes a bin's label/note; action="delete" removes an EMPTY bin. To see what is in a bin, use inventory_list(bin_id=…)."""
        try:
            if action == "list":
                return json.dumps({"ok": True, **store.list_bins()})
            if action == "create":
                bins = store.create_bins(count=count, bin_id=bin_id, label=label, note=note, actor=ACTOR)
                emit("bin.changed", {"ids": [b["id"] for b in bins], "action": "create"})
                return json.dumps({"ok": True, "bins": bins})
            if action == "update":
                b = store.update_bin(bin_id, label=label or None, note=note or None, actor=ACTOR)
                emit("bin.changed", {"ids": [b["id"]], "action": "update"})
                return json.dumps({"ok": True, "bin": b})
            if action == "delete":
                n = store.delete_bin(bin_id, actor=ACTOR)
                if n:
                    emit("bin.changed", {"ids": [bin_id.upper()], "action": "delete"})
                return json.dumps({"ok": bool(n), **({} if n else {"error": f"no bin {bin_id!r}"})})
            return json.dumps({"ok": False, "error": "action must be list, create, update or delete"})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_move_to_bin(item_ids: list[str], bin_id: str) -> str:
        """Record where items physically are: put every item in `item_ids` into bin `bin_id` (it must exist — inventory_bins action="create" first). Pass bin_id="" to take them out of any bin. All or nothing: an unknown item or bin refuses the whole move. Every move is in the audit trail with where it came from. Only move what the operator says they moved — never guess a location."""
        try:
            out = store.move_items(item_ids, bin_id, actor=ACTOR)
            for i in out["moved"]:
                emit("item.changed", {"id": i, "action": "move", "bin_id": out["bin_id"]})
            return json.dumps({"ok": True, **out})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_packing(action: str = "list", sale_id: int = 0) -> str:
        """The pick list. action="list" returns every sale not yet packed, with the item and the bin to pull it from ('' = no bin recorded), grouped by bin. action="packed" (sale_id) ticks one off once the operator says it is packed; an item with no stock left is taken out of its bin. Sales recorded before bins existed were already shipped and never appear."""
        try:
            if action == "list":
                sales = store.to_pack()
                return json.dumps({"ok": True, "count": len(sales), "sales": sales})
            if action == "packed":
                sale = store.mark_packed(int(sale_id), actor=ACTOR)
                emit("sale.packed", {"sale_id": sale["id"], "item_id": sale["item_id"]})
                return json.dumps({"ok": True, "sale": sale})
            return json.dumps({"ok": False, "error": "action must be 'list' or 'packed'"})
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
    def inventory_reprice_plan(lot_id: str = "", max_items: int = 15, price_days: int = 0) -> str:
        """The re-price worklist: unsold items whose price evidence is older than `price_days` (0 = the operator's Stale price setting) or missing, oldest first, capped at `max_items`, each with a suggested buyer-style eBay query and its current band. Loop it: ebay_price_check (sold) → inventory_set_price with the basis and the comps."""
        from .automations import reprice_plan

        return json.dumps(
            {
                "ok": True,
                **reprice_plan(
                    store, lot_id=lot_id, max_items=max_items, price_days=price_days or stale_price_days(cfg)
                ),
            }
        )

    @tool
    def inventory_stale(listed_days: int = 14, price_days: int = 0) -> str:
        """What needs attention: listings live longer than `listed_days`, and unsold items whose price evidence is older than `price_days` (0 = the operator's Stale price setting) or missing. The weekly-review starting point."""
        return json.dumps(
            {"ok": True, **store.stale(listed_days=listed_days, price_days=price_days or stale_price_days(cfg))}
        )

    # ── scans: the turns the plugin starts after a photo (scans.py) ─────────────
    from .scans import scan_timeout

    tmo = scan_timeout(cfg)

    @tool
    def inventory_scan_photos(scan_id: str) -> str:
        """See the photos of an inventory scan (the operator photographed a box or a piece in the Inventory view). Returns the images plus what is already known about the scan. Use it in a scan-identification turn, then call inventory_scan_propose or inventory_scan_fail."""
        try:
            scan = store.get_scan(scan_id, timeout_minutes=tmo)
        except InventoryError as exc:
            return _err(exc)
        images = []
        for p in scan["photos"]:
            found = store.scan_photo_file(scan["id"], p["id"])
            if found:
                images.append({"path": str(found[0]), "mime": found[1]})
        info = {
            "ok": True,
            "scan_id": scan["id"],
            "kind": scan["kind"],
            "state": scan["state"],
            "lot_id": scan["lot_id"],
            "photos": len(images),
        }
        if not images:
            return json.dumps({**info, "ok": False, "error": "this scan has no photos yet"})
        try:
            from graph.sdk import multimodal_tool_result  # host-only
        except Exception:  # noqa: BLE001 — a host without the envelope can't show images
            return json.dumps({**info, "ok": False, "error": "this protoAgent can't pass images to the model"})
        return multimodal_tool_result(json.dumps(info), images=images)

    @tool
    def inventory_scan_propose(
        scan_id: str,
        name: str,
        confidence: float,
        system: str = "",
        category: str = "",
        condition: str = "",
        alternatives: list[str] | None = None,
        contents: list[dict] | None = None,
        notes: str = "",
    ) -> str:
        """Record what an inventory scan shows. name = the product as printed (a box) or as a buyer would search for it (a piece). confidence = 0..1 that it is the EXACT product. alternatives = up to 3 other names when unsure. contents (a box only) = the units it splits into, each {"name", "quantity", "category", "model_count"} — model_count 0 when you cannot see or confirm it; never invent contents. The operator confirms or corrects this in the Inventory view."""
        try:
            scan = store.propose_scan(
                scan_id,
                {
                    "name": name,
                    "confidence": confidence,
                    "system": system,
                    "category": category,
                    "condition": condition,
                    "alternatives": alternatives or [],
                    "contents": contents or [],
                    "notes": notes,
                },
                actor=ACTOR,
                timeout_minutes=tmo,
            )
        except InventoryError as exc:
            return _err(exc)
        emit("scan.changed", {"id": scan["id"], "state": scan["state"], "action": "propose"})
        return json.dumps({"ok": True, "scan_id": scan["id"], "state": scan["state"], "proposal": scan["proposal"]})

    @tool
    def inventory_scan_fail(scan_id: str, reason: str) -> str:
        """Give up identifying an inventory scan, with a one-line reason the operator will read (e.g. "the photo is too dark to read the box"). They can then type the name or retake the photo."""
        try:
            scan = store.fail_scan(scan_id, reason, actor=ACTOR, timeout_minutes=tmo)
        except InventoryError as exc:
            return _err(exc)
        emit("scan.changed", {"id": scan["id"], "state": scan["state"], "action": "fail"})
        return json.dumps({"ok": True, "scan_id": scan["id"], "state": scan["state"]})

    @tool
    def inventory_scan_pricing_done(scan_id: str, unpriced: list[dict] | None = None) -> str:
        """Close a scan's pricing turn. unpriced = every item you did NOT set a price for, each {"item_id", "reason"} (e.g. "only 1 sold comp"). Items you priced need no entry: inventory_set_price already recorded them."""
        try:
            scan = store.pricing_done(scan_id, unpriced or [], actor=ACTOR, timeout_minutes=tmo)
        except InventoryError as exc:
            return _err(exc)
        emit("scan.changed", {"id": scan["id"], "state": scan["state"], "action": "priced"})
        return json.dumps({"ok": True, "scan_id": scan["id"], "pricing": scan["pricing"]})

    # ── listing drafts: the turn the plugin starts on "Draft listing" (drafts.py) ───
    @tool
    def inventory_draft_item(item_id: str) -> str:
        """See an inventory item for writing its eBay listing draft: the record (name, system, category, condition, quantity, model count, notes) plus its photos from the scan. Use it in a listing-draft turn, then call inventory_draft_write or inventory_draft_fail."""
        try:
            item = store.get_item(item_id)
            files = store.draft_photo_files(item_id)
        except InventoryError as exc:
            return _err(exc)
        if item is None:
            return _err(InventoryError(f"no item {item_id!r}"))
        keep = ("id", "name", "system", "category", "condition", "quantity", "model_count", "notes", "status")
        info = {"ok": True, "item": {k: item.get(k) for k in keep}, "photos": min(len(files), 3)}
        if not files:
            return json.dumps({**info, "note": "no photos: write from the record alone, and say so where it matters"})
        try:
            from graph.sdk import multimodal_tool_result  # host-only
        except Exception:  # noqa: BLE001 — a host without the envelope: the record alone
            return json.dumps({**info, "photos": 0, "note": "this protoAgent can't pass images; write from the record"})
        images = [{"path": str(path), "mime": mime} for path, mime in files[:3]]
        return multimodal_tool_result(json.dumps(info), images=images)

    @tool
    def inventory_draft_write(item_id: str, title: str, description: str, notes: str = "") -> str:
        """Save the eBay listing draft for an inventory item. title = at most 80 characters, buyer-search style. description = plain text, condition on the first line, only what the record and photos show. notes = anything the operator should check (optional). The price is the item's target and is never part of the draft. Nothing is posted anywhere: the operator copies it into eBay."""
        try:
            d = store.write_draft(item_id, title, description, notes=notes, actor=ACTOR)
        except InventoryError as exc:
            return _err(exc)
        emit("draft.changed", {"item_id": d["item_id"], "state": d["state"], "action": "write"})
        return json.dumps({"ok": True, "item_id": d["item_id"], "state": d["state"], "title": d["title"]})

    @tool
    def inventory_draft_fail(item_id: str, reason: str) -> str:
        """Give up writing an item's listing draft, with a one-line reason the operator will read (e.g. "no photos and the name doesn't say what is in the box")."""
        try:
            d = store.fail_draft(item_id, reason, actor=ACTOR)
        except InventoryError as exc:
            return _err(exc)
        emit("draft.changed", {"item_id": d["item_id"], "state": d["state"], "action": "fail"})
        return json.dumps({"ok": True, "item_id": d["item_id"], "state": d["state"]})

    # ── pasted sales: the turn the plugin starts on "Record a sale" (sales.py) ─────
    @tool
    def inventory_sale_candidates(query: str, limit: int = 8) -> str:
        """Find inventory items matching a description of what sold (product name, part number, a few words from an order). Returns the best matches first, each with id, name, condition, system, bin, status, quantity, target price, lot and any live listing. Items already SOLD in the last 180 days are included with status "sold" plus that sale's sold_on, sale_id, channel and price: a paste that repeats one of those is already recorded. Use it in a pasted-sale turn before inventory_sale_propose."""
        try:
            return json.dumps({"ok": True, "items": store.sale_candidates(query, limit=limit)})
        except InventoryError as exc:
            return _err(exc)

    @tool
    def inventory_sale_propose(
        paste_id: str,
        lines: list[dict],
        confidence: float,
        questions: list[str] | None = None,
        notes: str = "",
        total: float | None = None,
    ) -> str:
        """Propose the sale(s) in a pasted order for the operator to check and record. lines = one per item: {"item_id", "quantity", "price", "shipping_charged", "fees", "fees_estimated", "shipping_cost", "channel", "sold_on" (YYYY-MM-DD), "buyer_ref", "order_ref", "notes"} — dollars. total = the bundle price when several items sold for one price (the line prices must add up to it). questions = what the operator should check. Nothing is recorded until the operator confirms it in the Inventory view. A refusal says what to fix: fix it and call again."""
        try:
            p = store.propose_paste(
                paste_id, lines, confidence=confidence, questions=questions or [], notes=notes, total=total, actor=ACTOR
            )
        except InventoryError as exc:
            return _err(exc)
        emit("paste.changed", {"id": p["id"], "state": p["state"], "action": "propose"})
        return json.dumps({"ok": True, "paste_id": p["id"], "state": p["state"], "lines": len(p["proposal"]["lines"])})

    @tool
    def inventory_sale_fail(paste_id: str, reason: str, already_recorded: str = "") -> str:
        """Give up reading a pasted order, with a one-line reason the operator will read (e.g. "nothing in inventory matches 'Necron Warriors'"). already_recorded = the item id when the paste repeats a sale that is already recorded (a "sold" candidate with the same item and a similar price/date); the reason then reads "Already recorded: <item> sold <date> on <channel> for $X"."""
        try:
            p = store.fail_paste(paste_id, reason, actor=ACTOR, already_recorded=already_recorded)
        except InventoryError as exc:
            return _err(exc)
        emit("paste.changed", {"id": p["id"], "state": p["state"], "action": "fail"})
        return json.dumps({"ok": True, "paste_id": p["id"], "state": p["state"]})

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
        inventory_bins,
        inventory_move_to_bin,
        inventory_packing,
        inventory_import_csv,
        inventory_export_csv,
        inventory_reprice_plan,
        inventory_stale,
        inventory_scan_photos,
        inventory_scan_propose,
        inventory_scan_fail,
        inventory_scan_pricing_done,
        inventory_draft_item,
        inventory_draft_write,
        inventory_draft_fail,
        inventory_sale_candidates,
        inventory_sale_propose,
        inventory_sale_fail,
    ]


__all__ = ["build_tools", "STATUSES"]
