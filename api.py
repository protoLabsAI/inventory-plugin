"""The gated data API under ``/api/plugins/inventory`` — what the console view (and any
operator script with the bearer) drives. Same store, same audit trail, actor "console"."""

from __future__ import annotations

import contextlib
import logging

from fastapi import Request  # module level: FastAPI resolves the (postponed) annotation from these globals

from .store import InventoryError, InventoryStore, normalize_status

#: Target fields never travel through the generic item write — they need a basis (POST /price).
_PRICE_ONLY = ("target", "target_low", "target_high", "price_basis", "price_updated_on")

log = logging.getLogger("protoagent.plugins.inventory")

ACTOR = "console"


def _bin_order(bin_id: str) -> tuple:
    """B2 before B10: numbered bins in number order, anything else after, alphabetically."""
    import re

    m = re.match(r"^B(\d+)$", bin_id)
    return (0, int(m.group(1)), "") if m else (1, 0, bin_id)


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

    @r.get("/systems")
    async def _systems() -> dict:
        return {"systems": store.systems()}

    @r.get("/conditions")
    async def _conditions() -> dict:
        return {"conditions": store.conditions()}

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
        system: str = "",
        q: str = "",
        bin_id: str = "",
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ) -> dict:
        try:
            items = store.list_items(
                lot_id=lot_id,
                status=status,
                category=category,
                system=system,
                query=q,
                bin_id=bin_id,
                limit=limit,
                offset=offset,
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
        current = store.get_item(item_id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r} (POST /items creates one)")
        if current["status"] == "sold" and body.get("status"):
            with contextlib.suppress(InventoryError):
                if normalize_status(body["status"])[0] == "sold":
                    body = {
                        k: v for k, v in body.items() if k != "status"
                    }  # unchanged "sold" is a no-op, not a refusal
        body = _guard_item_body(body)
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

    # ── bins + the pick list ──────────────────────────────────────────────────
    @r.get("/bins")
    async def _bins() -> dict:
        return store.list_bins()

    @r.post("/bins")
    async def _create_bins(body: dict | None = None) -> dict:
        body = body or {}
        try:
            bins = store.create_bins(
                count=body.get("count") or 1,
                bin_id=str(body.get("id") or ""),
                label=str(body.get("label") or ""),
                note=str(body.get("note") or ""),
                actor=ACTOR,
            )
        except InventoryError as exc:
            _raise(exc)
        emit("bin.changed", {"ids": [b["id"] for b in bins], "action": "create"})
        return {"bins": bins}

    @r.put("/bins/{bin_id}")
    async def _put_bin(bin_id: str, body: dict) -> dict:
        try:
            b = store.update_bin(bin_id, label=body.get("label"), note=body.get("note"), actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        emit("bin.changed", {"ids": [b["id"]], "action": "update"})
        return {"bin": b}

    @r.delete("/bins/{bin_id}")
    async def _delete_bin(bin_id: str) -> dict:
        try:
            n = store.delete_bin(bin_id, actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        if not n:
            raise HTTPException(status_code=404, detail=f"no bin {bin_id!r}")
        emit("bin.changed", {"ids": [bin_id.upper()], "action": "delete"})
        return {"ok": True}

    @r.post("/bins/move")
    async def _move(body: dict) -> dict:
        ids = body.get("item_ids")
        if not isinstance(ids, list):
            raise HTTPException(status_code=400, detail="item_ids must be a list of item ids")
        try:
            out = store.move_items(ids, str(body.get("bin_id") or ""), actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        for i in out["moved"]:
            emit("item.changed", {"id": i, "action": "move", "bin_id": out["bin_id"]})
        return out

    # ── bin labels: the address a QR opens, and the printable sheet ──────────────
    @r.get("/labels/base")
    async def _label_base() -> dict:
        from .labels import LABEL_PATH, label_base

        return {**label_base(cfg), "path": LABEL_PATH}

    @r.post("/labels/sheet")
    async def _label_sheet(body: dict | None = None) -> dict:
        """Build a printable page of labels: ``ids``, or ``unprinted``, or ``from``/``to``, or
        ``all``. Records the bins as printed (``mark``, default true) in the same call."""
        from .labels import label_base, sheet_html

        body = body or {}
        info = label_base(cfg)
        if not info["base"]:
            raise HTTPException(status_code=409, detail=info["problem"])
        bins = sorted(store.list_bins()["bins"], key=lambda b: _bin_order(b["id"]))
        by_id = {b["id"]: b for b in bins}
        if isinstance(body.get("ids"), list):
            want = [str(i or "").strip().upper() for i in body["ids"]]
            unknown = [i for i in want if i not in by_id]
            if unknown:
                raise HTTPException(status_code=400, detail=f"no bin {unknown[0]!r}")
            picked = [by_id[i] for i in dict.fromkeys(want)]
        elif body.get("unprinted"):
            picked = [b for b in bins if not b.get("labels_printed_on")]
        elif body.get("from") or body.get("to"):
            ids = [b["id"] for b in bins]
            lo = str(body.get("from") or (ids[0] if ids else "")).strip().upper()
            hi = str(body.get("to") or (ids[-1] if ids else "")).strip().upper()
            if lo not in by_id or hi not in by_id:
                raise HTTPException(status_code=400, detail="pick a range between two bins that exist")
            i, j = sorted((ids.index(lo), ids.index(hi)))
            picked = bins[i : j + 1]
        elif body.get("all"):
            picked = bins
        else:
            raise HTTPException(status_code=400, detail="say which bins: ids, unprinted, from/to, or all")
        if not picked:
            raise HTTPException(status_code=400, detail="no bins match, so there is nothing to print")
        ids = [b["id"] for b in picked]
        if body.get("mark", True):
            try:
                store.mark_labels_printed(ids, actor=ACTOR)
            except InventoryError as exc:
                _raise(exc)
            emit("bin.changed", {"ids": ids, "action": "labels"})
        return {
            "ids": ids,
            "base": info["base"],
            "phone_reachable": info["phone_reachable"],
            "html": sheet_html(picked, info["base"]),
        }

    @r.get("/to-pack")
    async def _to_pack() -> dict:
        return {"sales": store.to_pack()}

    @r.post("/sales/{sale_id}/packed")
    async def _packed(sale_id: int) -> dict:
        try:
            sale = store.mark_packed(sale_id, actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        emit("sale.packed", {"sale_id": sale["id"], "item_id": sale["item_id"]})
        return {"sale": sale}

    # ── scans: photo intake (spec §3) ─────────────────────────────────────────
    # The page uploads the File itself as the body (kit.apiFetch passes a Blob through), its
    # type in Content-Type. Identification and pricing are agent turns this plugin starts
    # (scans.py); the page polls GET /scans/{id} while one is pending.
    from .scans import confidence_threshold, scan_timeout, start_identify, start_pricing

    tmo = scan_timeout(cfg)

    def _scan_changed(scan: dict, action: str) -> None:
        emit("scan.changed", {"id": scan["id"], "state": scan["state"], "action": action})

    @r.get("/scans")
    async def _scans(open: bool = True, limit: int = Query(50, ge=1, le=500)) -> dict:  # noqa: A002 — the query name
        return {
            "scans": store.list_scans(open_only=open, limit=limit, timeout_minutes=tmo),
            "threshold": confidence_threshold(cfg),
            "timeout_minutes": tmo,
        }

    @r.post("/scans")
    async def _new_scan(body: dict | None = None) -> dict:
        body = body or {}
        try:
            scan = store.create_scan(
                str(body.get("kind") or ""),
                paid=body.get("paid"),
                source=str(body.get("source") or ""),
                name=str(body.get("name") or ""),
                lot_id=str(body.get("lot_id") or ""),
                parent_id=str(body.get("parent_id") or ""),
                actor=ACTOR,
                timeout_minutes=tmo,
            )
        except InventoryError as exc:
            _raise(exc)
        _scan_changed(scan, "create")
        return {"scan": scan}

    def _get_scan(scan_id: str) -> dict:
        try:
            return store.get_scan(scan_id, timeout_minutes=tmo)
        except InventoryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @r.get("/scans/{scan_id}")
    async def _scan(scan_id: str) -> dict:
        return {"scan": _get_scan(scan_id)}

    @r.post("/scans/{scan_id}/photos")
    async def _scan_photo(scan_id: str, request: Request) -> dict:
        from fastapi.concurrency import run_in_threadpool

        from .photos import MAX_BYTES

        _get_scan(scan_id)
        too_big = f"photos are capped at {MAX_BYTES // (1024 * 1024)} MB"
        declared = request.headers.get("content-length") or ""
        if declared.isdigit() and int(declared) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=too_big)
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > MAX_BYTES:
                raise HTTPException(status_code=413, detail=too_big)
        try:  # sniff + sanitize (+ a HEIC conversion) off the event loop
            scan = await run_in_threadpool(store.add_scan_photo, scan_id, bytes(buf), actor=ACTOR, timeout_minutes=tmo)
        except InventoryError as exc:
            _raise(exc)
        scan = start_identify(store, scan, timeout_minutes=tmo)
        _scan_changed(scan, "photo")
        return {"scan": scan}

    @r.get("/scans/{scan_id}/photos/{photo_id}")
    async def _scan_photo_bytes(scan_id: str, photo_id: str):
        from fastapi.responses import FileResponse

        found = store.scan_photo_file(scan_id, photo_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no photo {photo_id!r} on scan {scan_id!r}")
        path, ctype = found
        return FileResponse(path, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})

    @r.post("/scans/{scan_id}/retry")
    async def _scan_retry(scan_id: str) -> dict:
        try:
            scan = store.retry_scan(scan_id, actor=ACTOR, timeout_minutes=tmo)
        except InventoryError as exc:
            _raise(exc)
        scan = start_identify(store, scan, timeout_minutes=tmo)
        _scan_changed(scan, "retry")
        return {"scan": scan}

    @r.post("/scans/{scan_id}/confirm")
    async def _scan_confirm(scan_id: str, body: dict) -> dict:
        try:
            scan = store.confirm_scan(scan_id, body, actor=ACTOR, timeout_minutes=tmo)
        except InventoryError as exc:
            _raise(exc)
        for i in scan["item_ids"]:
            emit("item.changed", {"id": i, "action": "create"})
        _scan_changed(scan, "confirm")
        return {"scan": scan}

    @r.post("/scans/{scan_id}/price")
    async def _scan_price(scan_id: str) -> dict:
        try:
            scan, items = store.request_pricing(scan_id, actor=ACTOR, timeout_minutes=tmo)
        except InventoryError as exc:
            _raise(exc)
        start_pricing(store, scan, items, timeout_minutes=tmo)
        scan = store.get_scan(scan_id, timeout_minutes=tmo)
        _scan_changed(scan, "price")
        return {"scan": scan}

    @r.post("/scans/{scan_id}/finish")
    async def _scan_finish(scan_id: str, body: dict | None = None) -> dict:
        try:
            scan = store.finish_scan(scan_id, (body or {}).get("placements") or {}, actor=ACTOR, timeout_minutes=tmo)
        except InventoryError as exc:
            _raise(exc)
        for i in scan["item_ids"]:
            emit("item.changed", {"id": i, "action": "binned"})
        _scan_changed(scan, "finish")
        return {"scan": scan}

    @r.delete("/scans/{scan_id}")
    async def _scan_discard(scan_id: str) -> dict:
        try:
            n = store.discard_scan(scan_id, actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        if not n:
            raise HTTPException(status_code=404, detail=f"no scan {scan_id!r}")
        emit("scan.changed", {"id": scan_id.upper(), "state": "deleted", "action": "delete"})
        return {"ok": True}

    # ── listing drafts (drafts.py starts the agent turn; nothing is ever posted) ────
    from .drafts import BULK_SPACING_S, MAX_BULK, start_draft

    def _draft_changed(d: dict, action: str) -> None:
        emit("draft.changed", {"item_id": d["item_id"], "state": d["state"], "action": action})

    @r.get("/drafts")
    async def _drafts() -> dict:
        return {"drafts": store.list_drafts(), "max_bulk": MAX_BULK}

    @r.get("/items/{item_id}/draft")
    async def _draft(item_id: str) -> dict:
        try:
            return {"draft": store.get_draft(item_id)}
        except InventoryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @r.post("/items/{item_id}/draft")
    async def _draft_request(item_id: str) -> dict:
        try:
            store.request_draft(item_id, actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        d = start_draft(store, item_id)
        _draft_changed(d, "request")
        return {"draft": d}

    @r.post("/drafts/bulk")
    async def _draft_bulk(body: dict) -> dict:
        """Draft several items, one turn after another. Capped: a click never starts more than
        ``MAX_BULK`` agent turns."""
        ids = [str(i) for i in (body or {}).get("item_ids") or [] if str(i).strip()]
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise HTTPException(status_code=400, detail="pick at least one item")
        if len(ids) > MAX_BULK:
            raise HTTPException(status_code=400, detail=f"draft at most {MAX_BULK} at a time")
        started, refused = [], []
        for item_id in ids:
            try:
                store.request_draft(item_id, actor=ACTOR)
            except InventoryError as exc:
                refused.append({"item_id": item_id, "reason": str(exc)})
                continue
            d = start_draft(store, item_id, delay_seconds=BULK_SPACING_S * len(started))
            _draft_changed(d, "request")
            (started if d["state"] == "drafting" else refused).append(
                {"item_id": item_id} if d["state"] == "drafting" else {"item_id": item_id, "reason": d["reason"]}
            )
        return {"started": started, "refused": refused}

    @r.post("/items/{item_id}/draft/copied")
    async def _draft_copied(item_id: str, body: dict) -> dict:
        try:
            d = store.draft_copied(item_id, str((body or {}).get("what") or ""), actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        _draft_changed(d, "copy")
        return {"draft": d}

    @r.post("/items/{item_id}/draft/live")
    async def _draft_live(item_id: str, body: dict) -> dict:
        try:
            d = store.draft_live(item_id, str((body or {}).get("url") or ""), actor=ACTOR)
        except InventoryError as exc:
            _raise(exc)
        _draft_changed(d, "live")
        emit("item.changed", {"id": item_id, "action": "listed"})
        return {"draft": d}

    @r.get("/items/{item_id}/photos.zip")
    async def _draft_photos_zip(item_id: str):
        """The item's photos in listing order, for uploading to eBay (cover first)."""
        import io
        import zipfile

        from fastapi.responses import Response

        if store.get_item(item_id) is None:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r}")
        files = store.draft_photo_files(item_id)
        if not files:
            raise HTTPException(status_code=404, detail="this item has no photos yet")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for n, (path, _mime) in enumerate(files, start=1):
                zf.write(path, arcname=f"{n:02d}-{item_id}{path.suffix}")
        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{item_id}-photos.zip"',
                "Cache-Control": "private, no-store",
            },
        )

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
