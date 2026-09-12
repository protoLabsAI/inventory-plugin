"""The inventory store — SQLite, owned by this plugin, the single source of truth.

Money is stored as INTEGER cents and exposed as dollars (floats) at the edges, so a
target of $12.50 never becomes 12.499999. Every mutation writes an audit row: an
inventory is only a source of truth if you can see who changed what, and when.

SQLite rules (the host's own metrics store is the reference): one connection PER CALL
(never shared across threads), ``busy_timeout`` set BEFORE ``journal_mode=WAL`` (the WAL
transition itself takes locks), a process-wide lock around writes (busy_timeout is a
retry loop, not a queue), and additive ``ALTER TABLE`` migrations at connect.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("protoagent.plugins.inventory")

#: ``planned`` = a piece that will exist once a sealed box is split (the operator's sheets
#: call it "Planned Split"); it is not sellable yet but its target counts toward the lot.
STATUSES = ("planned", "available", "listed", "pending", "sold", "kept", "withdrawn")
#: Items in these states still have value on the shelf; sold/kept/withdrawn do not count
#: toward "what is left to sell".
UNSOLD = ("planned", "available", "listed", "pending")
LISTING_STATES = ("active", "ended", "sold")
OBSERVATION_SOURCES = ("ebay_sold", "ebay_active", "amazon", "retail", "manual", "other")

_WRITE_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  acquisition_cost_cents INTEGER NOT NULL DEFAULT 0,
  acquired_on TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  lot_id TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  condition TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL DEFAULT 1,
  model_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  cost_basis_cents INTEGER,
  status TEXT NOT NULL DEFAULT 'available',
  target_low_cents INTEGER,
  target_cents INTEGER,
  target_high_cents INTEGER,
  retail_cents INTEGER,
  price_basis TEXT NOT NULL DEFAULT '',
  price_updated_on TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_items_lot ON items(lot_id);
CREATE INDEX IF NOT EXISTS ix_items_status ON items(status);
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  price_cents INTEGER,
  listed_on TEXT NOT NULL DEFAULT '',
  ended_on TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active',
  notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_listings_item ON listings(item_id);
CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  sold_on TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  shipping_charged_cents INTEGER NOT NULL DEFAULT 0,
  fees_cents INTEGER NOT NULL DEFAULT 0,
  shipping_cost_cents INTEGER NOT NULL DEFAULT 0,
  net_cents INTEGER NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sales_item ON sales(item_id);
CREATE TABLE IF NOT EXISTS price_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  observed_on TEXT NOT NULL,
  source TEXT NOT NULL,
  query TEXT NOT NULL DEFAULT '',
  n INTEGER NOT NULL DEFAULT 0,
  p25_cents INTEGER,
  median_cents INTEGER,
  p75_cents INTEGER,
  basis TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_obs_item ON price_observations(item_id);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  changes TEXT NOT NULL DEFAULT ''
);
"""

#: Additive migrations: (table, column, DDL). Applied at connect when the column is missing.
_MIGRATIONS: list[tuple[str, str, str]] = []


class InventoryError(ValueError):
    """A caller mistake, phrased for the operator (unknown item, bad status, …)."""


# ── money + time ─────────────────────────────────────────────────────────────────
_MONEY_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def to_cents(value) -> int | None:
    """``12.5`` / ``"12.50"`` / ``"$1,234.56"`` → cents; ``None``/blank/unparseable → ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(float(value) * 100)
    m = _MONEY_RE.search(str(value))
    if not m:
        return None
    return round(float(m.group(0).replace(",", "")) * 100)


def dollars(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def normalize_status(raw) -> tuple[str, int | None]:
    """Map free-text status ("Pending Sell", "Sold ($5)") to the vocabulary, plus a sold
    price when the text carries one."""
    text = (str(raw or "")).strip().lower()
    if not text:
        return "available", None
    if text.startswith("sold"):
        return "sold", to_cents(text[4:])
    for key in ("pending", "listed", "kept", "withdrawn", "available", "planned"):
        if key in text:
            return key, None
    if text in {"keep", "keeping"}:
        return "kept", None
    if text in STATUSES:
        return text, None
    raise InventoryError(f"unknown status {raw!r}; one of {', '.join(STATUSES)}")


def new_item_id() -> str:
    return "INV-" + uuid.uuid4().hex[:6].upper()


_ITEM_MONEY = {"cost_basis", "target_low", "target", "target_high", "retail"}
_ITEM_TEXT = {"lot_id", "category", "name", "condition", "notes", "price_basis", "price_updated_on"}
_ITEM_INT = {"quantity", "model_count"}
_LOT_TEXT = {"name", "description", "acquired_on", "source", "notes"}


def _item_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    out = {
        "id": d["id"],
        "lot_id": d["lot_id"],
        "category": d["category"],
        "name": d["name"],
        "condition": d["condition"],
        "quantity": d["quantity"],
        "model_count": d["model_count"],
        "notes": d["notes"],
        "cost_basis": dollars(d["cost_basis_cents"]),
        "status": d["status"],
        "target_low": dollars(d["target_low_cents"]),
        "target": dollars(d["target_cents"]),
        "target_high": dollars(d["target_high_cents"]),
        "retail": dollars(d["retail_cents"]),
        "price_basis": d["price_basis"],
        "price_updated_on": d["price_updated_on"],
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
    }
    return out


def _lot_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["acquisition_cost"] = dollars(d.pop("acquisition_cost_cents"))
    return d


def _sale_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("price", "shipping_charged", "fees", "shipping_cost", "net"):
        d[k] = dollars(d.pop(f"{k}_cents"))
    return d


def _listing_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["price"] = dollars(d.pop("price_cents"))
    return d


def _obs_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("p25", "median", "p75"):
        d[k] = dollars(d.pop(f"{k}_cents"))
    return d


class InventoryStore:
    """All reads and writes go through here. One connection per call; writes serialized."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)
            for table, column, ddl in _MIGRATIONS:
                cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    con.execute(ddl)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")  # BEFORE the WAL switch — that transition takes locks
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    # ── audit ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _audit(con, entity: str, entity_id: str, action: str, actor: str, changes: dict | None = None) -> None:
        con.execute(
            "INSERT INTO audit(ts, entity, entity_id, action, actor, changes) VALUES (?,?,?,?,?,?)",
            (now_iso(), entity, str(entity_id), action, actor or "", json.dumps(changes or {}, default=str)),
        )

    def audit_log(self, limit: int = 100, entity_id: str = "") -> list[dict]:
        with self._connect() as con:
            if entity_id:
                rows = con.execute(
                    "SELECT * FROM audit WHERE entity_id=? ORDER BY id DESC LIMIT ?", (entity_id, limit)
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            with contextlib.suppress(ValueError):
                d["changes"] = json.loads(d["changes"] or "{}")
            out.append(d)
        return out

    # ── lots ──────────────────────────────────────────────────────────────────
    def upsert_lot(self, data: dict, *, actor: str = "") -> dict:
        lot_id = str(data.get("id") or "").strip()
        if not lot_id:
            raise InventoryError("a lot needs an id (e.g. BLOODBOWL-2026-09)")
        fields = {k: str(data[k]) for k in _LOT_TEXT if k in data and data[k] is not None}
        if "acquisition_cost" in data:
            fields["acquisition_cost_cents"] = to_cents(data["acquisition_cost"]) or 0
        with _WRITE_LOCK, self._connect() as con:
            existing = con.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
            ts = now_iso()
            if existing is None:
                if not fields.get("name"):
                    fields["name"] = lot_id
                cols = ["id", "created_at", "updated_at", *fields]
                con.execute(
                    f"INSERT INTO lots({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [lot_id, ts, ts, *fields.values()],
                )
                self._audit(con, "lot", lot_id, "create", actor, fields)
            elif fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                con.execute(f"UPDATE lots SET {sets}, updated_at=? WHERE id=?", [*fields.values(), ts, lot_id])
                self._audit(con, "lot", lot_id, "update", actor, fields)
            row = con.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
        return _lot_row_to_dict(row)

    def get_lot(self, lot_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
        return _lot_row_to_dict(row) if row else None

    def list_lots(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM lots ORDER BY acquired_on DESC, id").fetchall()
        return [_lot_row_to_dict(r) for r in rows]

    def delete_lot(self, lot_id: str, *, actor: str = "", cascade: bool = False) -> int:
        """Remove a lot. Refuses while items reference it unless ``cascade`` (which removes them too)."""
        with _WRITE_LOCK, self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM items WHERE lot_id=?", (lot_id,)).fetchone()[0]
            if n and not cascade:
                raise InventoryError(f"lot {lot_id!r} still has {n} item(s); move or delete them first, or cascade")
            if cascade:
                ids = [r[0] for r in con.execute("SELECT id FROM items WHERE lot_id=?", (lot_id,))]
                for iid in ids:
                    self._delete_item_rows(con, iid, actor)
            cur = con.execute("DELETE FROM lots WHERE id=?", (lot_id,))
            if cur.rowcount:
                self._audit(con, "lot", lot_id, "delete", actor, {"cascade": cascade, "items": n})
            return cur.rowcount

    # ── items ─────────────────────────────────────────────────────────────────
    def upsert_item(self, data: dict, *, actor: str = "") -> dict:
        """Create or update an item. ``id`` optional on create (one is minted)."""
        item_id = str(data.get("id") or "").strip()
        fields: dict = {}
        for k in _ITEM_TEXT:
            if k in data and data[k] is not None:
                fields[k] = str(data[k])
        for k in _ITEM_INT:
            if k in data and data[k] not in (None, ""):
                try:
                    fields[k] = int(float(data[k]))
                except (TypeError, ValueError) as exc:
                    raise InventoryError(f"{k} must be a whole number, got {data[k]!r}") from exc
        for k in _ITEM_MONEY:
            if k in data:
                fields[f"{k}_cents"] = to_cents(data[k])
        if "status" in data and data["status"] is not None:
            status, _ = normalize_status(data["status"])
            fields["status"] = status
        with _WRITE_LOCK, self._connect() as con:
            existing = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone() if item_id else None
            ts = now_iso()
            if existing is None:
                if not fields.get("name"):
                    raise InventoryError("a new item needs a name")
                item_id = item_id or new_item_id()
                cols = ["id", "created_at", "updated_at", *fields]
                con.execute(
                    f"INSERT INTO items({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [item_id, ts, ts, *fields.values()],
                )
                self._audit(con, "item", item_id, "create", actor, fields)
            elif fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                con.execute(f"UPDATE items SET {sets}, updated_at=? WHERE id=?", [*fields.values(), ts, item_id])
                self._audit(con, "item", item_id, "update", actor, fields)
            row = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return _item_row_to_dict(row)

    def get_item(self, item_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return None
            item = _item_row_to_dict(row)
            item["listings"] = [
                _listing_row_to_dict(r)
                for r in con.execute("SELECT * FROM listings WHERE item_id=? ORDER BY id", (item_id,))
            ]
            item["sales"] = [
                _sale_row_to_dict(r) for r in con.execute("SELECT * FROM sales WHERE item_id=? ORDER BY id", (item_id,))
            ]
            item["observations"] = [
                _obs_row_to_dict(r)
                for r in con.execute(
                    "SELECT * FROM price_observations WHERE item_id=? ORDER BY observed_on DESC, id DESC LIMIT 20",
                    (item_id,),
                )
            ]
        return item

    def list_items(
        self,
        *,
        lot_id: str = "",
        status: str = "",
        category: str = "",
        query: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        where, args = [], []
        if lot_id:
            where.append("lot_id=?")
            args.append(lot_id)
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            for s in statuses:
                if s not in STATUSES:
                    raise InventoryError(f"unknown status {s!r}; one of {', '.join(STATUSES)}")
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        if category:
            where.append("lower(category)=lower(?)")
            args.append(category)
        if query:
            like = f"%{query.lower()}%"
            where.append("(lower(name) LIKE ? OR lower(notes) LIKE ? OR lower(id) LIKE ? OR lower(category) LIKE ?)")
            args.extend([like, like, like, like])
        sql = "SELECT * FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY lot_id, category, id LIMIT ? OFFSET ?"
        args.extend([max(1, int(limit)), max(0, int(offset))])
        with self._connect() as con:
            rows = con.execute(sql, args).fetchall()
        return [_item_row_to_dict(r) for r in rows]

    @staticmethod
    def _delete_item_rows(con, item_id: str, actor: str) -> int:
        cur = con.execute("DELETE FROM items WHERE id=?", (item_id,))
        if cur.rowcount:
            con.execute("DELETE FROM listings WHERE item_id=?", (item_id,))
            con.execute("DELETE FROM sales WHERE item_id=?", (item_id,))
            con.execute("DELETE FROM price_observations WHERE item_id=?", (item_id,))
            InventoryStore._audit(con, "item", item_id, "delete", actor)
        return cur.rowcount

    def delete_item(self, item_id: str, *, actor: str = "") -> int:
        with _WRITE_LOCK, self._connect() as con:
            return self._delete_item_rows(con, item_id, actor)

    # ── pricing ───────────────────────────────────────────────────────────────
    def record_observation(
        self,
        item_id: str,
        *,
        source: str,
        n: int = 0,
        p25=None,
        median=None,
        p75=None,
        query: str = "",
        basis: str = "",
        observed_on: str = "",
        notes: str = "",
    ) -> dict:
        if source not in OBSERVATION_SOURCES:
            raise InventoryError(f"unknown source {source!r}; one of {', '.join(OBSERVATION_SOURCES)}")
        with _WRITE_LOCK, self._connect() as con:
            if con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
                raise InventoryError(f"no item {item_id!r}")
            cur = con.execute(
                "INSERT INTO price_observations(item_id, observed_on, source, query, n, p25_cents, median_cents, "
                "p75_cents, basis, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    observed_on or today_iso(),
                    source,
                    query,
                    int(n or 0),
                    to_cents(p25),
                    to_cents(median),
                    to_cents(p75),
                    basis,
                    notes,
                ),
            )
            row = con.execute("SELECT * FROM price_observations WHERE id=?", (cur.lastrowid,)).fetchone()
        return _obs_row_to_dict(row)

    def set_price(
        self,
        item_id: str,
        *,
        low=None,
        target=None,
        high=None,
        basis: str,
        observed_on: str = "",
        actor: str = "",
        observation: dict | None = None,
    ) -> dict:
        """Set the item's target band with the evidence it rests on. ``observation`` (source,
        n, p25/median/p75, query) is recorded alongside so the "why" survives."""
        if not basis:
            raise InventoryError("a price needs a basis (e.g. 'eBay sold comps (22 sold, incl. shipping)')")
        item = self.upsert_item(
            {
                "id": item_id,
                "target_low": low,
                "target": target,
                "target_high": high,
                "price_basis": basis,
                "price_updated_on": observed_on or today_iso(),
            },
            actor=actor,
        )
        if observation:
            self.record_observation(item_id, observed_on=observed_on, **observation)
        return item

    # ── listings + sales ──────────────────────────────────────────────────────
    def add_listing(
        self, item_id: str, *, channel: str, url: str = "", price=None, listed_on: str = "", notes: str = "", actor=""
    ) -> dict:
        if not channel:
            raise InventoryError("a listing needs a channel (eBay, Facebook, local, r/miniswap …)")
        with _WRITE_LOCK, self._connect() as con:
            row = con.execute("SELECT status FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise InventoryError(f"no item {item_id!r}")
            cur = con.execute(
                "INSERT INTO listings(item_id, channel, url, price_cents, listed_on, notes) VALUES (?,?,?,?,?,?)",
                (item_id, channel, url, to_cents(price), listed_on or today_iso(), notes),
            )
            if row["status"] == "available":
                con.execute("UPDATE items SET status='listed', updated_at=? WHERE id=?", (now_iso(), item_id))
            self._audit(con, "listing", cur.lastrowid, "create", actor, {"item_id": item_id, "channel": channel})
            out = con.execute("SELECT * FROM listings WHERE id=?", (cur.lastrowid,)).fetchone()
        return _listing_row_to_dict(out)

    def end_listing(self, listing_id: int, *, state: str = "ended", ended_on: str = "", actor: str = "") -> dict:
        if state not in LISTING_STATES:
            raise InventoryError(f"unknown listing state {state!r}; one of {', '.join(LISTING_STATES)}")
        with _WRITE_LOCK, self._connect() as con:
            row = con.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
            if row is None:
                raise InventoryError(f"no listing {listing_id}")
            con.execute(
                "UPDATE listings SET state=?, ended_on=? WHERE id=?", (state, ended_on or today_iso(), listing_id)
            )
            # No live listing left and not sold → back to available.
            live = con.execute(
                "SELECT COUNT(*) FROM listings WHERE item_id=? AND state='active'", (row["item_id"],)
            ).fetchone()[0]
            status = con.execute("SELECT status FROM items WHERE id=?", (row["item_id"],)).fetchone()["status"]
            if not live and status == "listed":
                con.execute("UPDATE items SET status='available', updated_at=? WHERE id=?", (now_iso(), row["item_id"]))
            self._audit(con, "listing", listing_id, "end", actor, {"state": state})
            out = con.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        return _listing_row_to_dict(out)

    def mark_sold(
        self,
        item_id: str,
        *,
        price,
        channel: str,
        sold_on: str = "",
        fees=0,
        shipping_charged=0,
        shipping_cost=0,
        quantity: int = 1,
        notes: str = "",
        actor: str = "",
    ) -> dict:
        """Record a sale: the sale row, the item → sold, every live listing → sold.
        ``net = price + shipping charged − fees − shipping cost``."""
        price_c = to_cents(price)
        if price_c is None:
            raise InventoryError("a sale needs a price")
        if not channel:
            raise InventoryError("a sale needs a channel")
        fees_c, ship_in_c, ship_out_c = (
            to_cents(fees) or 0,
            to_cents(shipping_charged) or 0,
            to_cents(shipping_cost) or 0,
        )
        net_c = price_c + ship_in_c - fees_c - ship_out_c
        with _WRITE_LOCK, self._connect() as con:
            if con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
                raise InventoryError(f"no item {item_id!r}")
            cur = con.execute(
                "INSERT INTO sales(item_id, channel, sold_on, price_cents, shipping_charged_cents, fees_cents, "
                "shipping_cost_cents, net_cents, quantity, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    channel,
                    sold_on or today_iso(),
                    price_c,
                    ship_in_c,
                    fees_c,
                    ship_out_c,
                    net_c,
                    int(quantity or 1),
                    notes,
                ),
            )
            ts = now_iso()
            con.execute("UPDATE items SET status='sold', updated_at=? WHERE id=?", (ts, item_id))
            con.execute(
                "UPDATE listings SET state='sold', ended_on=? WHERE item_id=? AND state='active'",
                (sold_on or today_iso(), item_id),
            )
            self._audit(
                con,
                "sale",
                cur.lastrowid,
                "create",
                actor,
                {"item_id": item_id, "channel": channel, "price": dollars(price_c), "net": dollars(net_c)},
            )
            out = con.execute("SELECT * FROM sales WHERE id=?", (cur.lastrowid,)).fetchone()
        return _sale_row_to_dict(out)

    def list_sales(self, *, lot_id: str = "", limit: int = 500) -> list[dict]:
        with self._connect() as con:
            if lot_id:
                rows = con.execute(
                    "SELECT s.* FROM sales s JOIN items i ON i.id=s.item_id WHERE i.lot_id=? "
                    "ORDER BY s.sold_on DESC, s.id DESC LIMIT ?",
                    (lot_id, limit),
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM sales ORDER BY sold_on DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [_sale_row_to_dict(r) for r in rows]

    # ── roll-ups ──────────────────────────────────────────────────────────────
    def summary(self, lot_id: str = "") -> dict:
        """Per-lot P&L: what it cost, what is left to sell (at low/target/high), what has
        been realized (gross and net), and where the lot lands if the rest sells at target."""
        with self._connect() as con:
            lots = [_lot_row_to_dict(r) for r in con.execute("SELECT * FROM lots ORDER BY id")]
            if lot_id:
                lots = [lot for lot in lots if lot["id"] == lot_id]
                if not lots:
                    raise InventoryError(f"no lot {lot_id!r}")
            items = [dict(r) for r in con.execute("SELECT * FROM items")]
            sales = [dict(r) for r in con.execute("SELECT * FROM sales")]
        by_lot: dict[str, list[dict]] = {}
        for it in items:
            by_lot.setdefault(it["lot_id"], []).append(it)
        sales_by_item: dict[str, list[dict]] = {}
        for s in sales:
            sales_by_item.setdefault(s["item_id"], []).append(s)
        out_lots = []
        for lot in lots:
            lot_items = by_lot.get(lot["id"], [])
            out_lots.append(self._lot_rollup(lot, lot_items, sales_by_item))
        orphan_items = by_lot.get("", []) + [
            it for k, v in by_lot.items() if k and k not in {lot["id"] for lot in lots} for it in v
        ]
        totals = {
            "lots": len(out_lots),
            "items": sum(lr["counts"]["total"] for lr in out_lots),
            "acquisition_cost": round(sum(lr["acquisition_cost"] for lr in out_lots), 2),
            "realized_gross": round(sum(lr["realized"]["gross"] for lr in out_lots), 2),
            "realized_net": round(sum(lr["realized"]["net"] for lr in out_lots), 2),
            "remaining": {k: round(sum(lr["remaining"][k] for lr in out_lots), 2) for k in ("low", "target", "high")},
            "unpriced_items": sum(lr["remaining"]["unpriced_items"] for lr in out_lots),
        }
        totals["projected_net_at_target"] = round(
            totals["realized_net"] + totals["remaining"]["target"] - totals["acquisition_cost"], 2
        )
        return {"lots": out_lots, "totals": totals, "items_without_lot": len(orphan_items)}

    @staticmethod
    def _lot_rollup(lot: dict, items: list[dict], sales_by_item: dict) -> dict:
        counts = {s: 0 for s in STATUSES}
        for it in items:
            counts[it["status"]] = counts.get(it["status"], 0) + 1
        counts["total"] = len(items)
        unsold = [it for it in items if it["status"] in UNSOLD]
        rem = {"low": 0, "target": 0, "high": 0}
        unpriced = 0
        for it in unsold:
            q = max(1, int(it["quantity"] or 1))
            if it["target_cents"] is None and it["target_low_cents"] is None and it["target_high_cents"] is None:
                unpriced += 1
                continue
            low = it["target_low_cents"] if it["target_low_cents"] is not None else it["target_cents"] or 0
            tgt = it["target_cents"] if it["target_cents"] is not None else low
            high = it["target_high_cents"] if it["target_high_cents"] is not None else tgt
            rem["low"] += (low or 0) * q
            rem["target"] += (tgt or 0) * q
            rem["high"] += (high or 0) * q
        gross = net = 0
        for it in items:
            for s in sales_by_item.get(it["id"], []):
                gross += s["price_cents"] + s["shipping_charged_cents"]
                net += s["net_cents"]
        cost = lot["acquisition_cost"] or 0.0
        remaining = {k: round(v / 100, 2) for k, v in rem.items()}
        remaining["unpriced_items"] = unpriced
        realized = {"gross": round(gross / 100, 2), "net": round(net / 100, 2)}
        return {
            "id": lot["id"],
            "name": lot["name"],
            "acquisition_cost": cost,
            "acquired_on": lot["acquired_on"],
            "counts": counts,
            "remaining": remaining,
            "realized": realized,
            "projected_net_at_target": round(realized["net"] + remaining["target"] - cost, 2),
            "profit_so_far": round(realized["net"] - cost, 2),
        }

    def stale(self, *, listed_days: int = 14, price_days: int = 30) -> dict:
        """What needs attention: listings live longer than ``listed_days``, and unsold items
        whose price evidence is older than ``price_days`` (or missing)."""
        cutoff_listed = _days_ago(listed_days)
        cutoff_price = _days_ago(price_days)
        with self._connect() as con:
            old_listings = [
                dict(r)
                for r in con.execute(
                    "SELECT l.id, l.item_id, i.name, l.channel, l.url, l.price_cents, l.listed_on FROM listings l "
                    "JOIN items i ON i.id=l.item_id WHERE l.state='active' AND l.listed_on!='' AND l.listed_on<=? "
                    "ORDER BY l.listed_on",
                    (cutoff_listed,),
                )
            ]
            stale_prices = [
                _item_row_to_dict(r)
                for r in con.execute(
                    "SELECT * FROM items WHERE status IN ('planned','available','listed','pending') AND "
                    "(price_updated_on='' OR price_updated_on<=?) ORDER BY price_updated_on, id",
                    (cutoff_price,),
                )
            ]
        for row in old_listings:
            row["price"] = dollars(row.pop("price_cents"))
        return {
            "stale_listings": old_listings,
            "stale_prices": stale_prices,
            "thresholds": {"listed_days": listed_days, "price_days": price_days},
        }


def _days_ago(days: int) -> str:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(days=int(days))).strftime("%Y-%m-%d")
