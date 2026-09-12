"""CSV in and out — the bridge from the spreadsheets an operator already has.

Import is header-tolerant: the columns a human names ``item`` / ``target_price_usd`` /
``inventory_id`` map onto the store's fields through an alias table, free-text statuses
("Pending Sell", "Sold ($5)") normalize, and a "sold" row with a price becomes a sale
record so realized revenue is not lost. Unknown columns are reported, never silently
dropped into the void.
"""

from __future__ import annotations

import csv
import io

from .store import InventoryError, InventoryStore, normalize_status, to_cents

ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("inventory_id", "id", "sku", "item_id"),
    "lot_id": ("lot_id", "lot"),
    "category": ("category", "group", "type"),
    "name": ("item", "name", "title", "item_name"),
    "condition": ("condition", "cond"),
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

ITEM_EXPORT_COLUMNS = (
    "id",
    "lot_id",
    "category",
    "name",
    "condition",
    "quantity",
    "model_count",
    "status",
    "target_low",
    "target",
    "target_high",
    "retail",
    "cost_basis",
    "price_basis",
    "price_updated_on",
    "notes",
    "updated_at",
)
LOT_EXPORT_COLUMNS = ("id", "name", "description", "acquisition_cost", "acquired_on", "source", "notes", "updated_at")


def _norm(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")


def _map_headers(headers: list[str], aliases: dict[str, tuple[str, ...]]) -> tuple[dict[str, str], list[str]]:
    """header → field, plus the headers nothing claimed."""
    mapping: dict[str, str] = {}
    claimed: set[str] = set()
    for field, names in aliases.items():
        for h in headers:
            if _norm(h) in names and field not in claimed:
                mapping[h] = field
                claimed.add(field)
                break
    unknown = [h for h in headers if h not in mapping and _norm(h) not in _KNOWN_IGNORED]
    return mapping, unknown


def detect_kind(headers: list[str]) -> str:
    n = {_norm(h) for h in headers}
    if n & {"lot_name", "acquisition_cost_usd", "acquisition_cost"} and not n & {
        "item",
        "inventory_id",
        "target_price_usd",
    }:
        return "lots"
    return "items"


def parse_csv(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(r) for r in reader]
    return list(reader.fieldnames or []), rows


def import_csv(store: InventoryStore, text: str, *, kind: str = "auto", actor: str = "", default_lot: str = "") -> dict:
    """Load a CSV into the store. Returns counts + warnings; raises nothing for a bad row —
    it is reported so the operator sees exactly which line needs fixing."""
    headers, rows = parse_csv(text)
    if not headers:
        return {"ok": False, "error": "empty CSV (no header row)"}
    if kind == "auto":
        kind = detect_kind(headers)
    aliases = LOT_ALIASES if kind == "lots" else ITEM_ALIASES
    mapping, unknown = _map_headers(headers, aliases)
    created = updated = sales = 0
    warnings: list[str] = []
    for i, raw in enumerate(rows, start=2):  # line numbers as a spreadsheet shows them
        data = {mapping[h]: (raw.get(h) or "").strip() for h in mapping}
        if not any(data.values()):
            continue
        try:
            if kind == "lots":
                existed = store.get_lot(data.get("id", "")) is not None
                store.upsert_lot(data, actor=actor)
            else:
                existed = bool(data.get("id")) and store.get_item(data["id"]) is not None
                sold_price = data.pop("sold_price", "")
                sold_channel = data.pop("sold_channel", "") or "import"
                sold_on = data.pop("sold_on", "")
                status_text = data.get("status", "")
                try:
                    status, price_in_status = normalize_status(status_text)
                except InventoryError:
                    # A sheet's own vocabulary ("Planned Split") is not a reason to lose the row:
                    # keep it as available, keep the words, and say so.
                    status, price_in_status = "available", None
                    data["notes"] = (data.get("notes") or "").rstrip()
                    data["notes"] = (
                        data["notes"] + "\n" if data["notes"] else ""
                    ) + f"status as imported: {status_text}"
                    warnings.append(
                        f"line {i}: status {status_text!r} is not one of the known statuses; kept as available"
                    )
                data["status"] = status
                if not data.get("lot_id") and default_lot:
                    data["lot_id"] = default_lot
                item = store.upsert_item(data, actor=actor)
                price_c = to_cents(sold_price) if sold_price else price_in_status
                if status == "sold" and price_c is not None and not store.get_item(item["id"])["sales"]:
                    store.mark_sold(
                        item["id"],
                        price=price_c / 100,
                        channel=sold_channel,
                        sold_on=sold_on,
                        notes=f"imported from CSV (status column read {status_text!r})",
                        actor=actor,
                    )
                    sales += 1
            if existed:
                updated += 1
            else:
                created += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the sheet
            warnings.append(f"line {i}: {exc}")
    out = {
        "ok": True,
        "kind": kind,
        "rows": len(rows),
        "created": created,
        "updated": updated,
        "sales_recorded": sales,
        "mapped_columns": {h: f for h, f in mapping.items()},
        "ignored_columns": unknown,
        "warnings": warnings,
    }
    return out


def export_csv(store: InventoryStore, *, kind: str = "items", lot_id: str = "", status: str = "") -> str:
    buf = io.StringIO()
    if kind == "lots":
        w = csv.DictWriter(buf, fieldnames=LOT_EXPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for lot in store.list_lots():
            if lot_id and lot["id"] != lot_id:
                continue
            w.writerow(lot)
    else:
        w = csv.DictWriter(buf, fieldnames=ITEM_EXPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for item in store.list_items(lot_id=lot_id, status=status, limit=100000):
            w.writerow(item)
    return buf.getvalue()
