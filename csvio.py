"""CSV in and out — the bridge from the spreadsheets an operator already has.

Import is header-tolerant: the columns a human names ``item`` / ``target_price_usd`` /
``inventory_id`` map onto the store's fields through an alias table (alias PREFERENCE
decides, not sheet order — ``target_price_usd`` beats a stray ``price``), free-text
statuses ("Pending Sell", "Sold ($5)", "Planned Split") normalize, and a "sold" row with a
price becomes a sale record so realized revenue is not lost.

Re-import is the seed workflow (import the sheet, let the agent work, re-import to pick
up new rows), so it must never clobber what the agent did: a blank cell leaves the stored
value alone, a sheet status never walks an item back from listed/pending/sold, and a sheet
without an id column matches rows by lot + name instead of minting duplicates.
"""

from __future__ import annotations

import csv
import io

from .store import InventoryError, InventoryStore, normalize_status, to_cents

ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("inventory_id", "id", "sku", "item_id"),
    "lot_id": ("lot_id", "lot"),
    "category": ("category", "group", "type"),
    "system": ("system", "game_system", "game"),
    "name": ("item", "name", "title", "item_name"),
    "condition": ("condition", "cond"),
    "public": ("public", "on_site", "show_on_site"),
    "blurb": ("blurb", "public_blurb", "site_blurb"),
    "quantity": ("unit_quantity", "quantity", "qty"),
    "model_count": ("model_count", "models"),
    "notes": ("notes", "note", "description"),
    "cost_basis": ("cost_basis_usd", "cost_basis", "unit_cost_usd", "cost"),
    "status": ("status",),
    "target_low": ("target_low_usd", "target_low", "low"),
    "target": ("target_price_usd", "target_usd", "target", "price", "asking"),
    "target_high": ("target_high_usd", "target_high", "high"),
    "retail": ("gw_estimate_usd", "retail_usd", "retail", "msrp"),
    "price_basis": ("price_source", "price_basis", "basis"),
    "price_updated_on": ("price_last_updated", "price_updated_on", "price_date"),
    "sold_price": ("sold_price_usd", "sold_price", "sold_for"),
    "sold_channel": ("sold_channel", "channel"),
    "sold_on": ("sold_on", "sold_date", "date_sold"),
}
LOT_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("lot_id", "id"),
    "name": ("lot_name", "name"),
    "description": ("description",),
    "acquisition_cost": ("acquisition_cost_usd", "acquisition_cost", "cost", "cost_usd"),
    "acquired_on": ("inventory_date", "acquired_on", "date", "acquired"),
    "source": ("source", "bought_from"),
    "notes": ("source_note", "notes", "note"),
}
#: Columns the operator's sheets carry that we deliberately do not store.
_KNOWN_IGNORED = {
    "discount_estimate",
    "inventory_date",
    "declared_total_models",
    "declared_items_to_sell",
    "estimated_resale_low_usd",
    "estimated_resale_high_usd",
    "estimated_profit_low_usd",
    "estimated_profit_high_usd",
}
#: Headers that mark an ITEMS sheet; a sheet with none of these and a lot-ish column is lots.
_ITEMISH = {
    "item",
    "inventory_id",
    "sku",
    "item_id",
    "target_price_usd",
    "target_usd",
    "target",
    "status",
    "condition",
    "unit_quantity",
    "qty",
}
_LOTISH = {
    "lot_name",
    "acquisition_cost_usd",
    "acquisition_cost",
    "cost_usd",
    "acquired_on",
    "acquired",
    "bought_from",
    "source_note",
}
#: A status from a sheet never walks an item BACK from these.
_STICKY = {"listed", "pending", "sold"}

ITEM_EXPORT_COLUMNS = (
    "id",
    "lot_id",
    "system",
    "category",
    "name",
    "condition",
    "quantity",
    "model_count",
    "status",
    "public",
    "target_low",
    "target",
    "target_high",
    "retail",
    "cost_basis",
    "price_basis",
    "price_updated_on",
    "blurb",
    "notes",
    "updated_at",
)
LOT_EXPORT_COLUMNS = ("id", "name", "description", "acquisition_cost", "acquired_on", "source", "notes", "updated_at")
SALE_EXPORT_COLUMNS = (
    "id",
    "item_id",
    "channel",
    "sold_on",
    "quantity",
    "price",
    "shipping_charged",
    "fees",
    "shipping_cost",
    "net",
    "notes",
)


def _norm(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")


def _map_headers(headers: list[str], aliases: dict[str, tuple[str, ...]]) -> tuple[dict[str, str], list[str]]:
    """header → field by alias PREFERENCE (the first alias present wins), plus the headers
    nothing claimed."""
    mapping: dict[str, str] = {}
    by_norm = {}
    for h in headers:
        by_norm.setdefault(_norm(h), h)
    for field, names in aliases.items():
        for name in names:
            h = by_norm.get(name)
            if h is not None and h not in mapping:
                mapping[h] = field
                break
    unknown = [h for h in headers if h not in mapping and _norm(h) not in _KNOWN_IGNORED]
    return mapping, unknown


def detect_kind(headers: list[str]) -> str:
    n = {_norm(h) for h in headers}
    if n & _LOTISH and not n & _ITEMISH:
        return "lots"
    if not n & _ITEMISH and n >= {"id", "name"} and n & {"cost", "date"}:
        return "lots"
    return "items"


def parse_csv(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(r) for r in reader]
    return list(reader.fieldnames or []), rows


def import_csv(store: InventoryStore, text: str, *, kind: str = "auto", actor: str = "", default_lot: str = "") -> dict:
    """Load a CSV into the store. Returns counts + warnings; a bad row is reported with its
    line number, never fatal; a malformed sheet is an ``ok: False`` answer, not an exception."""
    try:
        headers, rows = parse_csv(text)
    except csv.Error as exc:
        return {"ok": False, "error": f"could not parse the CSV: {exc}"}
    if not headers:
        return {"ok": False, "error": "empty CSV (no header row)"}
    if kind == "auto":
        kind = detect_kind(headers)
    aliases = LOT_ALIASES if kind == "lots" else ITEM_ALIASES
    mapping, unknown = _map_headers(headers, aliases)
    created = updated = sales = 0
    warnings: list[str] = []
    stub_lots: set[str] = set()
    if kind == "items" and "id" not in mapping.values():
        warnings.append(
            "no id column — rows are matched to existing items by lot + name (add an id column to be exact)"
        )
    for i, raw in enumerate(rows, start=2):  # line numbers as a spreadsheet shows them
        data = {mapping[h]: (raw.get(h) or "").strip() for h in mapping}
        if not any(data.values()):
            continue
        try:
            if kind == "lots":
                existed = store.get_lot(data.get("id", "")) is not None
                store.upsert_lot(_drop_blanks(data, existed), actor=actor)
            else:
                sold_price = data.pop("sold_price", "")
                sold_channel = data.pop("sold_channel", "") or "import"
                sold_on = data.pop("sold_on", "")
                status_text = data.pop("status", "")
                if not data.get("lot_id") and default_lot:
                    data["lot_id"] = default_lot
                existing = store.get_item(data["id"]) if data.get("id") else None
                if existing is None and not data.get("id") and data.get("name"):
                    existing = store.find_item(lot_id=data.get("lot_id", ""), name=data["name"])
                    if existing:
                        data["id"] = existing["id"]
                existed = existing is not None
                data = _drop_blanks(data, existed)
                if data.get("lot_id") and store.get_lot(data["lot_id"]) is None:
                    store.upsert_lot({"id": data["lot_id"], "name": data["lot_id"]}, actor=actor)
                    if data["lot_id"] not in stub_lots:
                        stub_lots.add(data["lot_id"])
                        warnings.append(f"lot {data['lot_id']!r} did not exist — created a stub; set its cost and date")
                status, price_in_status, note = _sheet_status(status_text, existing, warnings, i)
                if status:
                    data["status"] = status
                if note and not existed:
                    data["notes"] = ((data.get("notes") or "").rstrip() + "\n" + note).strip()
                item = store.upsert_item(data, actor=actor, allow_sold=True)
                price_c = to_cents(sold_price) if sold_price else price_in_status
                if status == "sold" and price_c is not None and not store.get_item(item["id"])["sales"]:
                    store.mark_sold(
                        item["id"],
                        price=price_c / 100,
                        channel=sold_channel,
                        sold_on=sold_on,
                        notes=f"imported from CSV (status column read {status_text!r})",
                        actor=actor,
                        force=True,  # the row already says sold; record what it sold for
                    )
                    sales += 1
            if existed:
                updated += 1
            else:
                created += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the sheet
            warnings.append(f"line {i}: {exc}")
    return {
        "ok": True,
        "kind": kind,
        "rows": len(rows),
        "created": created,
        "updated": updated,
        "sales_recorded": sales,
        "mapped_columns": dict(mapping),
        "ignored_columns": unknown,
        "warnings": warnings,
    }


def _drop_blanks(data: dict, existed: bool) -> dict:
    """On an UPDATE a blank cell means "nothing to say", not "erase what the agent set"."""
    if not existed:
        return data
    return {k: v for k, v in data.items() if v != "" or k == "id"}


def _sheet_status(
    status_text: str, existing: dict | None, warnings: list[str], line: int
) -> tuple[str, int | None, str]:
    """The status to write from a sheet cell, honouring the store's own state: blank → keep;
    unknown words → available + a note in the item; never walk an item back from
    listed/pending/sold. Returns ``(status or "", sold price or None, note or "")``."""
    if not status_text:
        return ("" if existing else "available"), None, ""
    try:
        status, price = normalize_status(status_text)
    except InventoryError:
        warnings.append(f"line {line}: status {status_text!r} is not one of the known statuses; kept as available")
        if existing:
            return "", None, ""
        return "available", None, f"status as imported: {status_text}"
    if existing and existing["status"] in _STICKY and status not in _STICKY:
        return "", None, ""  # the store knows better than the sheet here
    return status, price, ""


def export_csv(store: InventoryStore, *, kind: str = "items", lot_id: str = "", status: str = "") -> str:
    """Items, lots or sales as CSV. NOTE: an items export carries statuses but not the
    sales, listings or observations behind them — export ``sales`` too for a full picture;
    this is a spreadsheet view, not a backup of the database file."""
    buf = io.StringIO()
    if kind == "lots":
        w = csv.DictWriter(buf, fieldnames=LOT_EXPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for lot in store.list_lots():
            if lot_id and lot["id"] != lot_id:
                continue
            w.writerow(lot)
    elif kind == "sales":
        w = csv.DictWriter(buf, fieldnames=SALE_EXPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for sale in store.list_sales(lot_id=lot_id, limit=100000):
            w.writerow(sale)
    elif kind == "items":
        w = csv.DictWriter(buf, fieldnames=ITEM_EXPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for item in store.list_items(lot_id=lot_id, status=status, limit=100000):
            w.writerow(item)
    else:
        raise InventoryError(f"unknown export kind {kind!r}; one of items, lots, sales")
    return buf.getvalue()
