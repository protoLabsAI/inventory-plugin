"""The gated data API under ``/api/plugins/inventory`` — what the console view (and any
operator script with the bearer) drives. Same store, same audit trail, actor "console"."""

from __future__ import annotations

import logging

from .store import InventoryError, InventoryStore, normalize_status

#: Target fields never travel through the generic item write — they need a basis (POST /price).
_PRICE_ONLY = ("target", "target_low", "target_high", "price_basis", "price_updated_on")

log = logging.getLogger("protoagent.plugins.inventory")

ACTOR = "console"


def build_data_router(store: InventoryStore, cfg: dict, *, emit=lambda topic, data: None):
    from fastapi import APIRouter, HTTPException, Query
    from fastapi.responses import PlainTextResponse

    r = APIRouter()

    def _raise(exc: InventoryError):
        raise HTTPException(status_code=400, detail=str(exc))

    @r.get("/summary")
    async def _summary(lot_id: str = "") -> dict:
        try:
            return store.summary(lot_id)
        except InventoryError as exc:
            _raise(exc)

    @r.get("/lots")
    async def _lots() -> dict:
        return {"lots": store.list_lots()}

    @r.put("/lots/{lot_id}")
    async def _put_lot(lot_id: str, body: dict) -> dict:
        try:
            lot = store.upsert_lot({**body, "id": lot_id}, actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        emit("lot.changed", {"id": lot_id})
        return {"lot": lot}

    @r.delete("/lots/{lot_id}")
    async def _delete_lot(lot_id: str, cascade: bool = False) -> dict:
        try:
            n = store.delete_lot(lot_id, actor=ACTOR, cascade=cascade)
        except InventoryError as exc:
            _raise(exc)
        if not n:
            raise HTTPException(status_code=404, detail=f"no lot {lot_id!r}")
        emit("lot.changed", {"id": lot_id, "action": "delete"})
        return {"ok": True}

    @r.get("/items")
    async def _items(
        lot_id: str = "",
        status: str = "",
        category: str = "",
        q: str = "",
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ) -> dict:
        try:
            items = store.list_items(
                lot_id=lot_id, status=status, category=category, query=q, limit=limit, offset=offset
            )
        except InventoryError as exc:
            _raise(exc)
        return {"items": items, "count": len(items)}

    def _guard_item_body(body: dict) -> dict:
        if any(k in body for k in _PRICE_ONLY):
            raise HTTPException(status_code=400, detail="targets need a basis — set them via POST /items/{id}/price")
        status = body.get("status")
        if status:
            try:
                is_sold = normalize_status(status)[0] == "sold"
            except InventoryError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if is_sold:
                raise HTTPException(status_code=400, detail="record a sale via POST /items/{id}/sold")
        return body

    @r.post("/items")
    async def _create_item(body: dict) -> dict:
        body = _guard_item_body(body)
        try:
            item = store.upsert_item(
                {k: v for k, v in body.items() if k != "id"} | ({"id": body["id"]} if body.get("id") else {}),
                actor=ACTOR,
            )
        except InventoryError as exc:
            _raise(exc)
        emit("item.changed", {"id": item["id"], "action": "create"})
        return {"item": item}

    @r.get("/items/{item_id}")
    async def _get_item(item_id: str) -> dict:
        item = store.get_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r}")
        return {"item": item}

    @r.put("/items/{item_id}")
    async def _put_item(item_id: str, body: dict) -> dict:
        body = _guard_item_body(body)
        if store.get_item(item_id) is None:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r} (POST /items creates one)")
        try:
            item = store.upsert_item({**body, "id": item_id}, actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        emit("item.changed", {"id": item_id, "action": "update"})
        return {"item": item}

    @r.delete("/items/{item_id}")
    async def _delete_item(item_id: str) -> dict:
        if not store.delete_item(item_id, actor=ACTOR):
            raise HTTPException(status_code=404, detail=f"no item {item_id!r}")
        emit("item.changed", {"id": item_id, "action": "delete"})
        return {"ok": True}

    @r.post("/items/{item_id}/price")
    async def _price(item_id: str, body: dict) -> dict:
        obs = body.get("observation") or None
        if obs is not None and not isinstance(obs, dict):
            raise HTTPException(status_code=400, detail="observation must be an object")
        try:
            item = store.set_price(
                item_id,
                low=body.get("low"),
                target=body.get("target"),
                high=body.get("high"),
                basis=str(body.get("basis") or ""),
                observed_on=str(body.get("observed_on") or ""),
                actor=ACTOR,
                observation=obs,
            )
        except InventoryError as exc:
            _raise(exc)
        emit("item.changed", {"id": item_id, "action": "price"})
        return {"item": item}

    @r.post("/items/{item_id}/sold")
    async def _sold(item_id: str, body: dict) -> dict:
        try:
            sale = store.mark_sold(
                item_id,
                price=body.get("price"),
                channel=str(body.get("channel") or ""),
                sold_on=str(body.get("sold_on") or ""),
                fees=body.get("fees") or 0,
                shipping_charged=body.get("shipping_charged") or 0,
                shipping_cost=body.get("shipping_cost") or 0,
                quantity=body.get("quantity") or 1,
                notes=str(body.get("notes") or ""),
                actor=ACTOR,
                force=bool(body.get("force")),
            )
        except InventoryError as exc:
            _raise(exc)
        emit(
            "sale.recorded", {"item_id": item_id, "sale_id": sale["id"], "net": sale["net"], "channel": sale["channel"]}
        )
        return {"sale": sale}

    @r.post("/items/{item_id}/listings")
    async def _add_listing(item_id: str, body: dict) -> dict:
        try:
            listing = store.add_listing(
                item_id,
                channel=str(body.get("channel") or ""),
                url=str(body.get("url") or ""),
                price=body.get("price"),
                listed_on=str(body.get("listed_on") or ""),
                notes=str(body.get("notes") or ""),
                actor=ACTOR,
            )
        except InventoryError as exc:
            _raise(exc)
        emit("item.changed", {"id": item_id, "action": "listed"})
        return {"listing": listing}

    @r.post("/listings/{listing_id}/end")
    async def _end_listing(listing_id: int, body: dict | None = None) -> dict:
        body = body or {}
        try:
            listing = store.end_listing(
                listing_id,
                state=str(body.get("state") or "ended"),
                ended_on=str(body.get("ended_on") or ""),
                actor=ACTOR,
            )
        except InventoryError as exc:
            _raise(exc)
        emit("item.changed", {"id": listing["item_id"], "action": "listing_ended"})
        return {"listing": listing}

    @r.get("/sales")
    async def _sales(lot_id: str = "", limit: int = Query(500, ge=1, le=5000)) -> dict:
        return {"sales": store.list_sales(lot_id=lot_id, limit=limit)}

    @r.get("/stale")
    async def _stale(listed_days: int = 14, price_days: int = 30) -> dict:
        return store.stale(listed_days=listed_days, price_days=price_days)

    @r.get("/audit")
    async def _audit(limit: int = Query(100, ge=1, le=2000), entity_id: str = "") -> dict:
        return {"audit": store.audit_log(limit=limit, entity_id=entity_id)}

    @r.post("/import")
    async def _import(body: dict) -> dict:
        from .csvio import import_csv

        text = str(body.get("csv") or "")
        if not text.strip():
            raise HTTPException(status_code=400, detail="body.csv is empty")
        try:
            out = import_csv(
                store,
                text,
                kind=str(body.get("kind") or "auto"),
                actor=ACTOR,
                default_lot=str(body.get("default_lot") or ""),
            )
        except (InventoryError, ValueError) as exc:
            _raise(exc)
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=str(out.get("error") or "import failed"))
        emit("imported", {"kind": out.get("kind"), "created": out.get("created"), "updated": out.get("updated")})
        return out

    @r.get("/export", response_class=PlainTextResponse)
    async def _export(kind: str = "items", lot_id: str = "", status: str = "") -> str:
        from .csvio import export_csv

        try:
            return export_csv(store, kind=kind, lot_id=lot_id, status=status)
        except InventoryError as exc:
            _raise(exc)

    return r
