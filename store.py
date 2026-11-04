"""The inventory store — SQLite, owned by this plugin, the single source of truth.

Money is stored as INTEGER cents and exposed as dollars (floats) at the edges, so a
target of $12.50 never becomes 12.499999. Every mutation writes an audit row — and a
delete carries a snapshot of what it removed: an inventory is only a source of truth if
you can see who changed what, and what it was before.

SQLite rules (the host's metrics store is the reference): one connection PER CALL, closed
in ``finally``; ``busy_timeout`` set BEFORE ``journal_mode=WAL`` (the WAL transition itself
takes locks); every write runs inside ONE ``BEGIN IMMEDIATE`` transaction under a
process-wide lock (busy_timeout is a retry loop, not a queue), committed on success and
rolled back on any exception — so a sale is the sale row AND the status flip AND the
closed listings AND the audit row, or none of them; additive ``ALTER TABLE`` migrations at
connect.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

log = logging.getLogger("protoagent.plugins.inventory")

#: ``planned`` = a piece that will exist once a sealed box is split (the operator's sheets
#: call it "Planned Split"); it is not sellable yet but its target counts toward the lot.
STATUSES = ("planned", "available", "listed", "pending", "sold", "kept", "withdrawn")
#: Items in these states still have value on the shelf; sold/kept/withdrawn do not count
#: toward "what is left to sell".
UNSOLD = ("planned", "available", "listed", "pending")
#: States an item cannot be sold or listed from.
CLOSED = ("sold", "kept", "withdrawn")
#: ``needs_end`` (0.12.0): the item sold somewhere else while this listing was live, so the
#: marketplace still shows it for sale. It stays on the books until the operator ends it there
#: ("I ended it" → ``ended``). The double-sell guard: a sale never silently closes a listing on
#: another channel, because nothing here can take that listing down for you.
LISTING_STATES = ("active", "ended", "sold", "needs_end")
OBSERVATION_SOURCES = ("ebay_sold", "ebay_active", "amazon", "retail", "manual", "other")
OBSERVATION_FIELDS = ("source", "n", "p25", "median", "p75", "query", "basis", "notes")

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
  system TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS bins (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_on TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'new',
  parent_id TEXT NOT NULL DEFAULT '',
  lot_id TEXT NOT NULL DEFAULT '',
  item_ids TEXT NOT NULL DEFAULT '[]',
  photos TEXT NOT NULL DEFAULT '[]',
  proposal TEXT NOT NULL DEFAULT '',
  correction TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  pricing TEXT NOT NULL DEFAULT '',
  identify_started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scans_parent ON scans(parent_id);
CREATE TABLE IF NOT EXISTS drafts (
  item_id TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'drafting',
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  listing_id INTEGER,
  requested_at TEXT NOT NULL DEFAULT '',
  drafted_at TEXT NOT NULL DEFAULT '',
  copied_at TEXT NOT NULL DEFAULT '',
  live_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sale_pastes (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'parsing',
  text TEXT NOT NULL DEFAULT '',
  item_hint TEXT NOT NULL DEFAULT '',
  proposal TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  sale_ids TEXT NOT NULL DEFAULT '[]',
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
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

#: Additive migrations: (table, column, statements). Applied at open when the column is
#: missing — all statements in ONE transaction, so a column is never added without its
#: backfill.
_MIGRATIONS: list[tuple[str, str, tuple[str, ...]]] = [
    # 0.4.0 — the game system an item belongs to (Warhammer 40K, Blood Bowl, …), optional.
    ("items", "system", ("ALTER TABLE items ADD COLUMN system TEXT NOT NULL DEFAULT ''",)),
    # 0.6.0 — where an item physically is: a bin id, or '' for not in a bin.
    ("items", "bin_id", ("ALTER TABLE items ADD COLUMN bin_id TEXT NOT NULL DEFAULT ''",)),
    # 0.6.0 — when a sale was picked and packed. Every sale that predates bins is history,
    # already shipped: backfill it as packed so the pick list starts empty.
    (
        "sales",
        "packed_on",
        (
            "ALTER TABLE sales ADD COLUMN packed_on TEXT NOT NULL DEFAULT ''",
            "UPDATE sales SET packed_on = CASE WHEN sold_on != '' THEN sold_on ELSE date('now') END",
        ),
    ),
    # 0.7.0 — when a bin's label was last put on a printable sheet ('' = never).
    ("bins", "labels_printed_on", ("ALTER TABLE bins ADD COLUMN labels_printed_on TEXT NOT NULL DEFAULT ''",)),
    # 0.10.0 — when an item's own sticker was last put on a printable sheet ('' = never).
    ("items", "labels_printed_on", ("ALTER TABLE items ADD COLUMN labels_printed_on TEXT NOT NULL DEFAULT ''",)),
    # 0.12.0 — the Custom label (SKU) the operator last copied into eBay for this draft ('' = never),
    # so Next can ask them to update it there when the item moves to another bin.
    ("drafts", "sku_copied", ("ALTER TABLE drafts ADD COLUMN sku_copied TEXT NOT NULL DEFAULT ''",)),
    # 0.14.0 — an aging item the operator put off deciding about, until this date ('' = not snoozed).
    ("items", "aging_snoozed_until", ("ALTER TABLE items ADD COLUMN aging_snoozed_until TEXT NOT NULL DEFAULT ''",)),
]
_POST_MIGRATION = "CREATE INDEX IF NOT EXISTS ix_items_bin ON items(bin_id);"

#: A bin id is printed on a label: B01, B02 … (any id-safe text is accepted; minted ids
#: follow this pattern).
_BIN_SEQ_RE = re.compile(r"^B(\d+)$")


class InventoryError(ValueError):
    """A caller mistake, phrased for the operator (unknown item, bad status, …)."""


# ── money + time ─────────────────────────────────────────────────────────────────
_MONEY_RE = re.compile(r"^([-+])?\s*[$£€¥]?\s*([-+])?\s*(\d[\d,]*(?:\.\d+)?|\d+,\d{1,2})$")
_EURO_DECIMAL_RE = re.compile(r"^\d+,\d{1,2}$")


# ── scans ─────────────────────────────────────────────────────────────────────
SCAN_KINDS = ("box", "unit")
SCAN_STATES = ("new", "identifying", "proposed", "confirmed", "failed")
#: The host hands a tool result at most 3 images (graph.multimodal.MAX_IMAGES_PER_RESULT), so a
#: scan holds no more than the agent can see in one look.
MAX_SCAN_PHOTOS = 3
MAX_SCAN_CONTENTS = 40
#: An identification still running after this long shows as failed (Retry); pricing gets 3x.
SCAN_TIMEOUT_MINUTES = 10
_SCAN_ID_RE = re.compile(r"^S[0-9A-F]{10}$")
_PHOTO_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PHOTO_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


# ── pasted sales (0.12.0) ─────────────────────────────────────────────────────
#: parsing → proposed → recorded; or failed (Try again) / discarded. The text is the operator's
#: own order paste and stays in this database.
PASTE_STATES = ("parsing", "proposed", "recorded", "failed", "discarded")
MAX_PASTE_CHARS = 8000
MAX_PASTE_LINES = 20
#: A parse still running after this long shows as failed (Try again).
# A pasted sale is checked against sales recorded this far back, so a repeat paste is caught.
SOLD_LOOKBACK_DAYS = 180
PASTE_TIMEOUT_MINUTES = 10
_PASTE_ID_RE = re.compile(r"^P[0-9A-F]{10}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
#: "Mon, Dec 23" / "Delivered Mon Dec 23" / "Monday, December 23" — a weekday + month + day with NO
#: year: the shape that once got stamped with the wrong year (a 2024 delivery recorded as 2026-12-23).
_DAY_MONTH_RE = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b(?!,?\s*\d{4})",
    re.IGNORECASE,
)


#: eBay's Custom label field is ``Item.SKU`` in the Trading API, "Maximum length: 50 characters"
#: (developer.ebay.com/api-docs/user-guides/static/trading-user-guide/skus.html; KB 1037 maps the
#: seller tools' "Custom label" to Item.SKU with the same limit).
CUSTOM_LABEL_MAX = 50


def custom_label(bin_id, item_id) -> str:
    """The eBay Custom label (SKU) for an item: its bin FIRST, then its id (``B03-helsmiths…``),
    so eBay's pick list and order search sort by where it lives. No bin → the id alone. The bin is
    upper-cased (bins are); the id keeps its case. Capped at eBay's 50 characters, keeping the bin."""
    iid = str(item_id or "").strip()
    b = str(bin_id or "").strip().upper()
    return ((b + "-" + iid) if b else iid)[:CUSTOM_LABEL_MAX]


def new_paste_id() -> str:
    return "P" + uuid.uuid4().hex[:10].upper()


def paste_key(value) -> str:
    key = str(value or "").strip().upper()
    if not _PASTE_ID_RE.match(key):
        raise InventoryError(f"no pasted sale {value!r}")
    return key


def weekday_conflict(text: str, iso: str) -> str:
    """If the paste names a weekday + month + day with no year ("Mon Dec 23") and ``iso`` is that
    month and day but falls on a DIFFERENT weekday, say so — the year was guessed wrong. ``""``
    when there is nothing to check or it agrees."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return ""
    for m in _DAY_MONTH_RE.finditer(text or ""):
        wd, mon, day = m.group(1).lower()[:3], _MONTHS.index(m.group(2).lower()[:3]) + 1, int(m.group(3))
        if (d.month, d.day) == (mon, day) and _WEEKDAYS[d.weekday()] != wd:
            return (
                f"the paste says {m.group(0).strip()!r} but {iso} is a {d.strftime('%A')}; that date is in another "
                "year. Use the year in which it falls on that weekday, or ask the operator"
            )
    return ""


def new_scan_id() -> str:
    return "S" + uuid.uuid4().hex[:10].upper()


def scan_key(value) -> str:
    """A scan id as stored (S + 10 hex, upper case), or InventoryError."""
    v = str(value or "").strip().upper()
    if not _SCAN_ID_RE.match(v):
        raise InventoryError(f"no scan {value!r}")
    return v


# ── listing drafts ───────────────────────────────────────────────────────────────
DRAFT_TIMEOUT_MINUTES = 10
#: eBay's title limit.
TITLE_MAX = 80
DESCRIPTION_MAX = 4000
#: An eBay item page (any title slug, any query), or the eBay app's share short link.
_EBAY_ITEM_RE = re.compile(r"^https?://(?:www\.|m\.)?ebay\.com/itm/(?:[^/?#\s]+/)?(\d{9,15})(?:[/?#]\S*)?$", re.I)
_EBAY_SHORT_RE = re.compile(r"^https?://ebay\.us/(?:m/)?[A-Za-z0-9]{4,16}$", re.I)


def ebay_item_url(value) -> str:
    """An eBay listing URL as stored: ``https://www.ebay.com/itm/<number>``, or an ``ebay.us``
    share link kept as given. Anything else is refused: this field is what marks an item listed."""
    v = str(value or "").strip()
    m = _EBAY_ITEM_RE.match(v)
    if m:
        return f"https://www.ebay.com/itm/{m.group(1)}"
    if _EBAY_SHORT_RE.match(v):
        return v
    raise InventoryError("paste the eBay listing's address, like https://www.ebay.com/itm/123456789012")


def _age_over(iso: str, *, minutes: int) -> bool:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return datetime.now(UTC) - t > timedelta(minutes=minutes)


def _clean(value, limit: int) -> str:
    """One line of operator- or agent-supplied text: whitespace collapsed, capped."""
    return " ".join(str(value or "").split())[:limit]


_CONDITION_CUT = re.compile(r"\s*(?:\(|\u2014|\u2013| - |;|,|:)")


def condition_label(value) -> tuple[str, str]:
    """An agent-proposed condition as a short label plus whatever explanation rode along.

    The model sometimes answers "NIB (assumed, the photo is a stock shot)". The label is what
    lands on the item, and so in the Copy list, so keep it to the label ("NIB") and hand the
    rest back for the notes."""
    text = " ".join(str(value or "").split())
    m = _CONDITION_CUT.search(text)
    label, rest = (text[: m.start()], text[m.start() :]) if m else (text, "")
    rest = rest.strip(" ()\u2014\u2013-;,:")
    if len(label) > 24:
        label, rest = "", text
    return label, rest


def as_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "keep"}
    return bool(value)


def _loads(text, default):
    if not text:
        return default
    try:
        v = json.loads(text)
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def _older_than(ts: str, minutes: float, now: datetime) -> bool:
    if not ts:
        return False
    try:
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return True
    return now - t > timedelta(minutes=minutes)


def _scan_row_to_dict(r: sqlite3.Row, timeout_minutes: int = SCAN_TIMEOUT_MINUTES, now: datetime | None = None) -> dict:
    """A scan as the API and tools show it. A turn that never answered is reported as failed
    (with Retry) once it is past the timeout; the stored state is kept as ``stored_state``."""
    d = dict(r)
    now = now or datetime.now(UTC)
    state, reason = d["state"], d["reason"]
    if state == "identifying" and _older_than(d["identify_started_at"], timeout_minutes, now):
        state, reason = "failed", "Identification took too long. Retry, or type the name."
    pricing = _loads(d["pricing"], {})
    if pricing.get("state") == "queued" and _older_than(pricing.get("requested_at", ""), timeout_minutes * 3, now):
        pricing = {**pricing, "state": "failed", "reason": "Pricing took too long. Retry, or price them by hand."}
    return {
        "id": d["id"],
        "kind": d["kind"],
        "state": state,
        "stored_state": d["state"],
        "parent_id": d["parent_id"],
        "lot_id": d["lot_id"],
        "item_ids": _loads(d["item_ids"], []),
        "photos": [{"id": p.get("id"), "ext": p.get("ext"), "bytes": p.get("bytes")} for p in _loads(d["photos"], [])],
        "proposal": _loads(d["proposal"], {}) or None,
        "correction": _loads(d["correction"], {}) or None,
        "reason": reason,
        "pricing": pricing or None,
        "identify_started_at": d["identify_started_at"],
        "finished_at": d["finished_at"],
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
    }


def same_channel(a, b) -> bool:
    """Two channel names are the same marketplace: case, spaces and punctuation don't count
    ("eBay" = "ebay" = "E-Bay")."""
    norm = lambda v: re.sub(r"[^a-z0-9]", "", str(v or "").lower())  # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


def to_cents(value) -> int | None:
    """``12.5`` / ``"12.50"`` / ``"$1,234.56"`` / ``"12,50"`` / ``"(5.00)"`` → cents.

    ``None``, blank, and anything that is not a money amount → ``None`` — the CALLER decides
    whether that means "clear" or "reject"; this never guesses a number out of "54%",
    "2026-07" or "n/a" (an unanchored match used to read those as $54, $2,026 and nothing).
    Rounding is half-up on the decimal text, so 1.005 is 101 cents, not 100.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    text = str(value).strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    m = _MONEY_RE.match(text)
    if not m:
        return None
    if "-" in ((m.group(1) or "") + (m.group(2) or "")):
        negative = True
    num = m.group(3)
    num = num.replace(",", ".") if _EURO_DECIMAL_RE.match(num) else num.replace(",", "")
    cents = int((Decimal(num) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -cents if negative else cents


_UNSET = object()


def money_field(name: str, value):
    """Interpret a money value the way a form or a sheet hands it over: ``None`` → clear
    (NULL); a blank string → unset (leave as is); a non-money string → an error, never a
    silent NULL or $0."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return _UNSET
    cents = to_cents(value)
    if cents is None:
        raise InventoryError(f"{name} must be a money amount (12.50, $1,234.56), got {value!r}")
    return cents


def dollars(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


_PRICE_IN_STATUS_RE = re.compile(r"\(\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*\)|\$\s*(\d[\d,]*(?:\.\d+)?)")


def normalize_status(raw) -> tuple[str, int | None]:
    """Map free-text status ("Pending Sell", "Sold ($5)", "Planned Split") to the vocabulary,
    plus a sold price when the text carries one — only a ``$``-prefixed or parenthesised
    amount counts ("Sold 9/12" is a date, not a $9 sale). Whole words only: "delisted"
    is not "listed" and "unavailable" is not "available"."""
    text = str(raw or "").strip().lower()
    if not text:
        return "available", None
    tokens = re.findall(r"[a-z]+", text)
    if tokens and tokens[0] == "sold":
        m = _PRICE_IN_STATUS_RE.search(text)
        return "sold", (to_cents(m.group(1) or m.group(2)) if m else None)
    for key in ("pending", "listed", "kept", "withdrawn", "planned", "available"):
        if key in tokens:
            return key, None
    if tokens and tokens[0] in {"keep", "keeping"}:
        return "kept", None
    raise InventoryError(f"unknown status {raw!r}; one of {', '.join(STATUSES)}")


def new_item_id() -> str:
    return "INV-" + uuid.uuid4().hex[:6].upper()


#: Ids travel in URLs (`/items/{id}`), file names and event payloads: letters, digits and
#: a few separators, no slashes or whitespace, at most 64 characters.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$")


def check_id(kind: str, value: str) -> str:
    value = str(value or "").strip()
    if not _ID_RE.match(value):
        raise InventoryError(
            f"{kind} id {value!r} is not usable — letters, digits and . _ : + - only (no spaces or slashes), up to 64 characters"
        )
    return value


_ITEM_MONEY = ("cost_basis", "target_low", "target", "target_high", "retail")
_ITEM_TEXT = ("lot_id", "category", "system", "name", "condition", "notes", "price_basis", "price_updated_on")
_ITEM_INT = ("quantity", "model_count")
_LOT_TEXT = ("name", "description", "acquired_on", "source", "notes")


def _item_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    return {
        "id": d["id"],
        "lot_id": d["lot_id"],
        "category": d["category"],
        "system": d["system"],
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
        "bin_id": d.get("bin_id") or "",
        "labels_printed_on": d.get("labels_printed_on") or "",
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
    }


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


def _int_field(name: str, value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise InventoryError(f"{name} must be a whole number, got {value!r}") from exc


class InventoryStore:
    """All reads and writes go through here. One connection per call, closed; writes are one
    serialized transaction each."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._read() as con:
            con.executescript(SCHEMA)
        for table, column, statements in _MIGRATIONS:
            with self._tx() as con:
                cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
                if column not in cols:  # re-checked under the write lock: another process may have won
                    for sql in statements:
                        con.execute(sql)
        with self._read() as con:
            con.executescript(_POST_MIGRATION)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)  # autocommit; we BEGIN explicitly
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")  # BEFORE the WAL switch — that transition takes locks
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @contextlib.contextmanager
    def _read(self):
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextlib.contextmanager
    def _tx(self):
        """One write transaction: BEGIN IMMEDIATE under the process lock, COMMIT on success,
        ROLLBACK on any exception, always closed."""
        with _WRITE_LOCK:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise
            finally:
                con.close()

    # ── audit ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _audit(con, entity: str, entity_id, action: str, actor: str, changes: dict | None = None) -> None:
        con.execute(
            "INSERT INTO audit(ts, entity, entity_id, action, actor, changes) VALUES (?,?,?,?,?,?)",
            (now_iso(), entity, str(entity_id), action, actor or "", json.dumps(changes or {}, default=str)),
        )

    def audit_log(self, limit: int = 100, entity_id: str = "") -> list[dict]:
        with self._read() as con:
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
        lot_id = check_id("lot", lot_id)
        fields = {k: str(data[k]) for k in _LOT_TEXT if k in data and data[k] is not None}
        if "name" in fields and not fields["name"].strip():
            raise InventoryError("a lot name cannot be blank")
        if "acquisition_cost" in data:
            cents = money_field("acquisition_cost", data["acquisition_cost"])
            if cents is None:
                cents = 0  # an explicit null means "no cost", never a silent default
            if cents is not _UNSET:
                fields["acquisition_cost_cents"] = cents
        with self._tx() as con:
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
        with self._read() as con:
            row = con.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
        return _lot_row_to_dict(row) if row else None

    def list_lots(self) -> list[dict]:
        with self._read() as con:
            rows = con.execute("SELECT * FROM lots ORDER BY acquired_on DESC, id").fetchall()
        return [_lot_row_to_dict(r) for r in rows]

    def delete_lot(self, lot_id: str, *, actor: str = "", cascade: bool = False) -> int:
        """Remove a lot. Refuses while items reference it unless ``cascade`` (which removes them
        too — every removed row is snapshotted into the audit trail)."""
        with self._tx() as con:
            n = con.execute("SELECT COUNT(*) FROM items WHERE lot_id=?", (lot_id,)).fetchone()[0]
            if n and not cascade:
                raise InventoryError(f"lot {lot_id!r} still has {n} item(s); move or delete them first, or cascade")
            if cascade:
                ids = [r[0] for r in con.execute("SELECT id FROM items WHERE lot_id=?", (lot_id,))]
                for iid in ids:
                    self._delete_item_rows(con, iid, actor)
            row = con.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
            cur = con.execute("DELETE FROM lots WHERE id=?", (lot_id,))
            if cur.rowcount:
                self._audit(
                    con, "lot", lot_id, "delete", actor, {"cascade": cascade, "items": n, "snapshot": dict(row)}
                )
            return cur.rowcount

    # ── items ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _item_fields(data: dict) -> dict:
        """Validate + translate the external item dict into column values. Blank money and
        integer strings are UNSET (left alone), never zero; garbage is an error."""
        fields: dict = {}
        for k in _ITEM_TEXT:
            if k in data and data[k] is not None:
                fields[k] = str(data[k])
        for k in _ITEM_INT:
            if k in data and data[k] is not None and not (isinstance(data[k], str) and not data[k].strip()):
                fields[k] = _int_field(k, data[k])
        for k in _ITEM_MONEY:
            if k in data:
                cents = money_field(k, data[k])
                if cents is not _UNSET:
                    fields[f"{k}_cents"] = cents
        return fields

    def _upsert_item_rows(self, con, data: dict, *, actor: str, allow_sold: bool = False) -> sqlite3.Row:
        item_id = str(data.get("id") or "").strip()
        if item_id:
            item_id = check_id("item", item_id)
        fields = self._item_fields(data)
        existing = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone() if item_id else None
        if existing is not None and "name" in fields and not fields["name"].strip():
            raise InventoryError("an item name cannot be blank")
        status_raw = data.get("status")
        if status_raw is not None and str(status_raw).strip() != "":
            status, _ = normalize_status(status_raw)
            if status == "sold" and not allow_sold and (existing is None or existing["status"] != "sold"):
                raise InventoryError("record a sale with mark_sold — it sets status=sold itself, with the net")
            fields["status"] = status
        lot_id = fields.get("lot_id")
        if lot_id and con.execute("SELECT 1 FROM lots WHERE id=?", (lot_id,)).fetchone() is None:
            raise InventoryError(f"no lot {lot_id!r}; create it first (or import the lots sheet before the items)")
        ts = now_iso()
        if existing is None:
            if not (fields.get("name") or "").strip():
                raise InventoryError(
                    "a new item needs a name" + (f" (no item {item_id!r} exists to update)" if item_id else "")
                )
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
        return con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def upsert_item(self, data: dict, *, actor: str = "", allow_sold: bool = False) -> dict:
        """Create or update an item. ``id`` optional on create (one is minted). Setting
        ``status=sold`` directly is refused unless ``allow_sold`` (a sheet import that
        records the sale itself) — a sale is recorded with :meth:`mark_sold`."""
        with self._tx() as con:
            row = self._upsert_item_rows(con, data, actor=actor, allow_sold=allow_sold)
        return _item_row_to_dict(row)

    def get_item(self, item_id: str) -> dict | None:
        with self._read() as con:
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

    def systems(self) -> list[str]:
        """The distinct game systems in use (for filters and the item form's suggestions)."""
        with self._read() as con:
            rows = con.execute("SELECT DISTINCT system FROM items WHERE system!='' ORDER BY system").fetchall()
        return [r[0] for r in rows]

    def conditions(self) -> list[str]:
        """The distinct condition labels in use (NoS, NIB, sealed, painted …) for suggestions."""
        with self._read() as con:
            rows = con.execute("SELECT DISTINCT condition FROM items WHERE condition!='' ORDER BY condition").fetchall()
        return [r[0] for r in rows]

    def find_item(self, *, lot_id: str, name: str) -> dict | None:
        """The item with this lot + name (case-insensitive) — how a sheet without an id column
        is matched on re-import instead of minting duplicates."""
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM items WHERE lot_id=? AND lower(name)=lower(?) ORDER BY created_at LIMIT 1",
                (lot_id or "", name),
            ).fetchone()
        return _item_row_to_dict(row) if row else None

    def list_items(
        self,
        *,
        lot_id: str = "",
        status: str = "",
        category: str = "",
        system: str = "",
        query: str = "",
        bin_id: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """``bin_id``: one bin, or ``"-"`` for items not in any bin; blank = any."""
        where, args = [], []
        if bin_id == "-":
            where.append("bin_id=''")
        elif bin_id:
            where.append("bin_id=?")
            args.append(bin_id.strip().upper())
        if system:
            where.append("lower(system)=lower(?)")
            args.append(system)
        if lot_id:
            where.append("lot_id=?")
            args.append(lot_id)
        if status:
            statuses = [s.strip().lower() for s in status.split(",") if s.strip()]
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
            where.append(
                "(lower(name) LIKE ? OR lower(notes) LIKE ? OR lower(id) LIKE ? OR lower(category) LIKE ? "
                "OR lower(system) LIKE ? OR lower(bin_id) LIKE ?)"
            )
            args.extend([like, like, like, like, like, like])
        sql = "SELECT * FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        # Game system first, so the grid and a copied list read system → lot → category.
        sql += " ORDER BY system, lot_id, category, id LIMIT ? OFFSET ?"
        args.extend([max(1, int(limit)), max(0, int(offset))])
        with self._read() as con:
            rows = con.execute(sql, args).fetchall()
        return [_item_row_to_dict(r) for r in rows]

    @staticmethod
    def _delete_item_rows(con, item_id: str, actor: str) -> int:
        row = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return 0
        snapshot = {
            "item": dict(row),
            "listings": [dict(r) for r in con.execute("SELECT * FROM listings WHERE item_id=?", (item_id,))],
            "sales": [dict(r) for r in con.execute("SELECT * FROM sales WHERE item_id=?", (item_id,))],
            "observations": [
                dict(r) for r in con.execute("SELECT * FROM price_observations WHERE item_id=?", (item_id,))
            ],
        }
        con.execute("DELETE FROM items WHERE id=?", (item_id,))
        con.execute("DELETE FROM listings WHERE item_id=?", (item_id,))
        con.execute("DELETE FROM sales WHERE item_id=?", (item_id,))
        con.execute("DELETE FROM price_observations WHERE item_id=?", (item_id,))
        draft = con.execute("SELECT * FROM drafts WHERE item_id=?", (item_id,)).fetchone()
        if draft is not None:
            snapshot["draft"] = dict(draft)
            con.execute("DELETE FROM drafts WHERE item_id=?", (item_id,))
        InventoryStore._audit(con, "item", item_id, "delete", actor, {"snapshot": snapshot})
        return 1

    def delete_item(self, item_id: str, *, actor: str = "") -> int:
        with self._tx() as con:
            return self._delete_item_rows(con, item_id, actor)

    # ── pricing ───────────────────────────────────────────────────────────────
    @staticmethod
    def _insert_observation(con, item_id: str, *, observed_on: str = "", **obs) -> sqlite3.Row:
        unknown = set(obs) - set(OBSERVATION_FIELDS)
        if unknown:
            raise InventoryError(f"unknown observation field(s): {', '.join(sorted(unknown))}")
        source = str(obs.get("source") or "")
        if source not in OBSERVATION_SOURCES:
            raise InventoryError(f"unknown source {source!r}; one of {', '.join(OBSERVATION_SOURCES)}")
        n = obs.get("n") or 0
        try:
            n = int(n)
        except (TypeError, ValueError) as exc:
            raise InventoryError(f"n must be a whole number, got {n!r}") from exc
        cur = con.execute(
            "INSERT INTO price_observations(item_id, observed_on, source, query, n, p25_cents, median_cents, "
            "p75_cents, basis, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                observed_on or today_iso(),
                source,
                str(obs.get("query") or ""),
                n,
                to_cents(obs.get("p25")),
                to_cents(obs.get("median")),
                to_cents(obs.get("p75")),
                str(obs.get("basis") or ""),
                str(obs.get("notes") or ""),
            ),
        )
        return con.execute("SELECT * FROM price_observations WHERE id=?", (cur.lastrowid,)).fetchone()

    def record_observation(self, item_id: str, *, observed_on: str = "", **obs) -> dict:
        with self._tx() as con:
            if con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
                raise InventoryError(f"no item {item_id!r}")
            row = self._insert_observation(con, item_id, observed_on=observed_on, **obs)
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
        """Set the item's target band with the evidence it rests on, in ONE transaction: the
        targets and the observation land together or not at all."""
        if not basis:
            raise InventoryError("a price needs a basis (e.g. 'eBay sold comps (22 sold, incl. shipping)')")
        if observation is not None and not isinstance(observation, dict):
            raise InventoryError("observation must be an object with source, n, p25, median, p75, query")
        with self._tx() as con:
            if con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
                raise InventoryError(f"no item {item_id!r}")
            row = self._upsert_item_rows(
                con,
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
                self._insert_observation(con, item_id, observed_on=observed_on, **observation)
        return _item_row_to_dict(row)

    # ── listings + sales ──────────────────────────────────────────────────────
    def add_listing(
        self, item_id: str, *, channel: str, url: str = "", price=None, listed_on: str = "", notes: str = "", actor=""
    ) -> dict:
        if not channel:
            raise InventoryError("a listing needs a channel (eBay, Facebook, local, r/miniswap …)")
        with self._tx() as con:
            row = con.execute("SELECT status FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise InventoryError(f"no item {item_id!r}")
            if row["status"] in CLOSED:
                raise InventoryError(f"item {item_id!r} is {row['status']}; set it back to available before listing it")
            cur = con.execute(
                "INSERT INTO listings(item_id, channel, url, price_cents, listed_on, notes) VALUES (?,?,?,?,?,?)",
                (item_id, channel, url, to_cents(price), listed_on or today_iso(), notes),
            )
            if row["status"] in ("planned", "available"):
                con.execute("UPDATE items SET status='listed', updated_at=? WHERE id=?", (now_iso(), item_id))
            self._audit(con, "listing", cur.lastrowid, "create", actor, {"item_id": item_id, "channel": channel})
            out = con.execute("SELECT * FROM listings WHERE id=?", (cur.lastrowid,)).fetchone()
        return _listing_row_to_dict(out)

    def end_listing(self, listing_id: int, *, state: str = "ended", ended_on: str = "", actor: str = "") -> dict:
        if state == "sold":
            raise InventoryError("record the sale with mark_sold — it closes the listing as sold itself")
        if state == "needs_end":
            raise InventoryError("needs_end is set by a sale on another channel, not by hand")
        if state not in LISTING_STATES:
            raise InventoryError(f"unknown listing state {state!r}; one of ended")
        with self._tx() as con:
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
        force: bool = False,
        packed: bool = False,
    ) -> dict:
        """Record a sale in one transaction: the sale row, the stock change, the closed
        listings, the audit row. ``net = price + shipping charged − fees − shipping cost``.

        Selling fewer units than the item holds decrements its quantity and leaves it on
        sale; selling the last unit flips it to ``sold`` and closes every live listing. An
        item that is already sold (or kept/withdrawn) is refused unless ``force`` — a retried
        tool call must not double the realized revenue. ``packed`` records the sale as already
        picked (a sheet import of history); otherwise it waits on the pick list."""
        price_c = money_field("price", price)
        if price_c is None or price_c is _UNSET:
            raise InventoryError("a sale needs a price")
        if not channel:
            raise InventoryError("a sale needs a channel")
        qty = _int_field("quantity", quantity if quantity not in (None, "") else 1)
        if qty < 1:
            raise InventoryError("quantity must be at least 1")
        fees_c, ship_in_c, ship_out_c = (
            to_cents(fees) or 0,
            to_cents(shipping_charged) or 0,
            to_cents(shipping_cost) or 0,
        )
        net_c = price_c + ship_in_c - fees_c - ship_out_c
        with self._tx() as con:
            row = con.execute("SELECT status, quantity FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise InventoryError(f"no item {item_id!r}")
            if row["status"] in CLOSED and not force:
                prior = con.execute(
                    "SELECT id, sold_on, price_cents FROM sales WHERE item_id=? ORDER BY id DESC LIMIT 1", (item_id,)
                ).fetchone()
                was = (
                    f" (sale #{prior['id']} on {prior['sold_on']} for {dollars(prior['price_cents'])})" if prior else ""
                )
                raise InventoryError(
                    f"item {item_id!r} is already {row['status']}{was}; pass force=True to record another sale"
                )
            remaining = max(0, int(row["quantity"] or 1) - qty)
            needs_end: list[int] = []
            cur = con.execute(
                "INSERT INTO sales(item_id, channel, sold_on, price_cents, shipping_charged_cents, fees_cents, "
                "shipping_cost_cents, net_cents, quantity, notes, packed_on) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    channel,
                    sold_on or today_iso(),
                    price_c,
                    ship_in_c,
                    fees_c,
                    ship_out_c,
                    net_c,
                    qty,
                    notes,
                    (sold_on or today_iso()) if packed else "",
                ),
            )
            ts = now_iso()
            if remaining > 0:
                con.execute("UPDATE items SET quantity=?, updated_at=? WHERE id=?", (remaining, ts, item_id))
            else:
                con.execute("UPDATE items SET status='sold', updated_at=? WHERE id=?", (ts, item_id))
                # A live listing on the channel it sold on closes as sold. One on ANY OTHER channel is
                # still up on that marketplace: flag it so Next asks the operator to end it there,
                # rather than marking it closed when nothing has taken it down (the double-sell guard).
                for lst in con.execute(
                    "SELECT id, channel FROM listings WHERE item_id=? AND state='active'", (item_id,)
                ).fetchall():
                    if same_channel(lst["channel"], channel):
                        con.execute(
                            "UPDATE listings SET state='sold', ended_on=? WHERE id=?",
                            (sold_on or today_iso(), lst["id"]),
                        )
                    else:
                        con.execute("UPDATE listings SET state='needs_end' WHERE id=?", (lst["id"],))
                        needs_end.append(lst["id"])
            self._audit(
                con,
                "sale",
                cur.lastrowid,
                "create",
                actor,
                {
                    "item_id": item_id,
                    "channel": channel,
                    "price": dollars(price_c),
                    "net": dollars(net_c),
                    "quantity": qty,
                    "remaining": remaining,
                    **({"needs_end": needs_end} if needs_end else {}),
                },
            )
            for lid in needs_end:
                self._audit(con, "listing", lid, "needs_end", actor, {"sold_on": channel})
            out = con.execute("SELECT * FROM sales WHERE id=?", (cur.lastrowid,)).fetchone()
        sale = _sale_row_to_dict(out)
        sale["remaining_quantity"] = remaining
        sale["needs_end"] = needs_end
        sale["item_status"] = "sold" if remaining == 0 else row["status"]
        return sale

    def listings_to_end(self) -> list[dict]:
        """Listings still up on a marketplace for an item that sold elsewhere (``needs_end``),
        oldest first, each with the item's name and the sale that caused it."""
        with self._read() as con:
            rows = con.execute(
                "SELECT l.*, i.name AS item_name, i.bin_id AS bin_id FROM listings l "
                "JOIN items i ON i.id = l.item_id WHERE l.state='needs_end' ORDER BY l.id"
            ).fetchall()
            out = []
            for r in rows:
                d = _listing_row_to_dict(r)
                d["item_name"] = r["item_name"]
                sale = con.execute(
                    "SELECT channel, sold_on FROM sales WHERE item_id=? ORDER BY id DESC LIMIT 1", (r["item_id"],)
                ).fetchone()
                d["sold_channel"] = sale["channel"] if sale else ""
                d["sold_on"] = sale["sold_on"] if sale else ""
                out.append(d)
        return out

    def list_sales(self, *, lot_id: str = "", limit: int = 500) -> list[dict]:
        with self._read() as con:
            if lot_id:
                rows = con.execute(
                    "SELECT s.* FROM sales s JOIN items i ON i.id=s.item_id WHERE i.lot_id=? "
                    "ORDER BY s.sold_on DESC, s.id DESC LIMIT ?",
                    (lot_id, limit),
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM sales ORDER BY sold_on DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [_sale_row_to_dict(r) for r in rows]

    # ── bins: where an item physically is ─────────────────────────────────────
    @staticmethod
    def _bin_id(value) -> str:
        return check_id("bin", str(value or "").strip().upper())

    @staticmethod
    def _next_bin_ids(con, n: int) -> list[str]:
        used = {r[0] for r in con.execute("SELECT id FROM bins")}
        top = max((int(m.group(1)) for b in used if (m := _BIN_SEQ_RE.match(b))), default=0)
        return [f"B{k:02d}" for k in range(top + 1, top + 1 + n)]

    def create_bins(
        self, *, count: int = 1, bin_id: str = "", label: str = "", note: str = "", actor: str = ""
    ) -> list[dict]:
        """Make bins. ``bin_id`` names one; otherwise ``count`` new ids continue the B01, B02 …
        sequence. ``label`` / ``note`` apply to each new bin. An existing id is refused."""
        count = _int_field("count", count if count not in (None, "") else 1)
        if bin_id and count != 1:
            raise InventoryError("name one bin, or ask for a count of new ones — not both")
        if not 1 <= count <= 200:
            raise InventoryError("make between 1 and 200 bins at a time")
        with self._tx() as con:
            ids = [self._bin_id(bin_id)] if bin_id else self._next_bin_ids(con, count)
            ts, today = now_iso(), today_iso()
            for b in ids:
                if con.execute("SELECT 1 FROM bins WHERE id=?", (b,)).fetchone():
                    raise InventoryError(f"bin {b!r} already exists")
                con.execute(
                    "INSERT INTO bins(id, label, note, created_on, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (b, str(label or ""), str(note or ""), today, ts, ts),
                )
                self._audit(con, "bin", b, "create", actor, {"label": label or "", "note": note or ""})
            rows = con.execute(f"SELECT * FROM bins WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        order = {b: i for i, b in enumerate(ids)}
        return sorted((dict(r) for r in rows), key=lambda r: order[r["id"]])

    def update_bin(self, bin_id: str, *, label=None, note=None, actor: str = "") -> dict:
        bin_id = self._bin_id(bin_id)
        fields = {k: str(v) for k, v in (("label", label), ("note", note)) if v is not None}
        with self._tx() as con:
            if con.execute("SELECT 1 FROM bins WHERE id=?", (bin_id,)).fetchone() is None:
                raise InventoryError(f"no bin {bin_id!r}")
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                con.execute(f"UPDATE bins SET {sets}, updated_at=? WHERE id=?", [*fields.values(), now_iso(), bin_id])
                self._audit(con, "bin", bin_id, "update", actor, fields)
            return dict(con.execute("SELECT * FROM bins WHERE id=?", (bin_id,)).fetchone())

    def delete_bin(self, bin_id: str, *, actor: str = "") -> int:
        """Remove an EMPTY bin. A bin with items in it is refused — move them first."""
        bin_id = self._bin_id(bin_id)
        with self._tx() as con:
            n = con.execute("SELECT COUNT(*) FROM items WHERE bin_id=?", (bin_id,)).fetchone()[0]
            if n:
                raise InventoryError(f"bin {bin_id!r} still holds {n} item(s); move them out first")
            row = con.execute("SELECT * FROM bins WHERE id=?", (bin_id,)).fetchone()
            if row is None:
                return 0
            con.execute("DELETE FROM bins WHERE id=?", (bin_id,))
            self._audit(con, "bin", bin_id, "delete", actor, {"snapshot": dict(row)})
            return 1

    def mark_labels_printed(self, bin_ids, *, actor: str = "") -> list[str]:
        """Record that these bins' labels went onto a printable sheet today (audited)."""
        ids = list(dict.fromkeys(self._bin_id(b) for b in (bin_ids or [])))
        if not ids:
            return []
        today, ts = today_iso(), now_iso()
        with self._tx() as con:
            for b in ids:
                if con.execute("SELECT 1 FROM bins WHERE id=?", (b,)).fetchone() is None:
                    raise InventoryError(f"no bin {b!r}")
            for b in ids:
                con.execute("UPDATE bins SET labels_printed_on=?, updated_at=? WHERE id=?", (today, ts, b))
                self._audit(con, "bin", b, "labels", actor, {"labels_printed_on": today})
        return ids

    def mark_item_labels_printed(self, item_ids, *, actor: str = "") -> list[str]:
        """Record that these items' stickers went onto a printable sheet today (audited)."""
        ids = list(dict.fromkeys(check_id("item", i) for i in (item_ids or [])))
        if not ids:
            return []
        today, ts = today_iso(), now_iso()
        with self._tx() as con:
            for i in ids:
                if con.execute("SELECT 1 FROM items WHERE id=?", (i,)).fetchone() is None:
                    raise InventoryError(f"no item {i!r}")
            for i in ids:
                con.execute("UPDATE items SET labels_printed_on=?, updated_at=? WHERE id=?", (today, ts, i))
                self._audit(con, "item", i, "labels", actor, {"labels_printed_on": today})
        return ids

    def list_bins(self) -> dict:
        """Every bin with what is in it (count of items, and the unsold value at target), plus
        how many unsold items are in no bin at all."""
        with self._read() as con:
            bins = [dict(r) for r in con.execute("SELECT * FROM bins ORDER BY id")]
            stats = {
                r["bin_id"]: r
                for r in con.execute(
                    "SELECT bin_id, COUNT(*) AS n, "
                    f"SUM(CASE WHEN status IN ({','.join('?' * len(UNSOLD))}) THEN COALESCE(target_cents, 0) * "
                    "MAX(quantity, 1) ELSE 0 END) AS value FROM items WHERE bin_id != '' GROUP BY bin_id",
                    UNSOLD,
                )
            }
            unbinned = con.execute(
                f"SELECT COUNT(*) FROM items WHERE bin_id='' AND status IN ({','.join('?' * len(UNSOLD))})", UNSOLD
            ).fetchone()[0]
        for b in bins:
            s = stats.get(b["id"])
            b["items"] = s["n"] if s else 0
            b["value_at_target"] = dollars(s["value"] if s else 0)
        return {"bins": bins, "unbinned_unsold": unbinned}

    def move_items(self, item_ids, bin_id: str, *, actor: str = "", put_away: bool = False) -> dict:
        """Put items in ``bin_id`` ('' = take them out of any bin). All or nothing: an unknown
        item or bin refuses the whole move. Each item that actually moves gets an audit row
        with ``{"bin": [from, to]}``.

        ``put_away`` (0.11.0, the label-first flow): a ``planned`` piece put in a bin is split
        out and on the shelf, so it becomes ``available``, audited, the way finishing a scan
        with a bin did before. A sold item is refused: it has left the shelf."""
        if isinstance(item_ids, str):
            item_ids = [item_ids]
        ids = list(dict.fromkeys(str(i).strip() for i in (item_ids or []) if str(i).strip()))
        if not ids:
            raise InventoryError("name at least one item to move")
        target = self._bin_id(bin_id) if str(bin_id or "").strip() else ""
        moved = []
        with self._tx() as con:
            if target and con.execute("SELECT 1 FROM bins WHERE id=?", (target,)).fetchone() is None:
                raise InventoryError(f"no bin {target!r}; create it first")
            rows = {
                r["id"]: r
                for r in con.execute(
                    f"SELECT id, bin_id, status FROM items WHERE id IN ({','.join('?' * len(ids))})", ids
                )
            }
            missing = [i for i in ids if i not in rows]
            if missing:
                raise InventoryError(f"no item(s) {', '.join(repr(m) for m in missing)}")
            if put_away:
                if not target:
                    raise InventoryError("put away needs a bin")
                sold = [i for i in ids if rows[i]["status"] == "sold"]
                if sold:
                    raise InventoryError(f"sold item(s) can't be put away: {', '.join(repr(i) for i in sold)}")
            ts = now_iso()
            available = []
            for i in ids:
                was = rows[i]["bin_id"] or ""
                if was != target:
                    con.execute("UPDATE items SET bin_id=?, updated_at=? WHERE id=?", (target, ts, i))
                    self._audit(
                        con,
                        "item",
                        i,
                        "move",
                        actor,
                        {"bin": [was, target], **({"put_away": True} if put_away else {})},
                    )
                    moved.append(i)
                if put_away and rows[i]["status"] == "planned":
                    con.execute("UPDATE items SET status='available', updated_at=? WHERE id=?", (ts, i))
                    self._audit(con, "item", i, "update", actor, {"status": "available", "reason": "put away"})
                    available.append(i)
        return {
            "bin_id": target,
            "moved": moved,
            "unchanged": [i for i in ids if i not in moved],
            "now_available": available,
        }

    def to_pack(self) -> list[dict]:
        """The pick list: every sale not yet packed, with the item's name and the bin to pull
        it from ('' when none was recorded), grouped by bin then oldest sale first."""
        with self._read() as con:
            rows = con.execute(
                "SELECT s.*, i.name AS item_name, i.bin_id AS bin_id, i.condition AS item_condition "
                "FROM sales s JOIN items i ON i.id = s.item_id WHERE s.packed_on = '' "
                "ORDER BY i.bin_id = '', i.bin_id, s.sold_on, s.id"
            ).fetchall()
        return [_sale_row_to_dict(r) for r in rows]

    def mark_packed(self, sale_id: int, *, actor: str = "") -> dict:
        """Tick a sale off the pick list. When the item has no stock left (status sold) it has
        left its bin, so its bin is cleared — audited as a move."""
        with self._tx() as con:
            sale = con.execute("SELECT * FROM sales WHERE id=?", (int(sale_id),)).fetchone()
            if sale is None:
                raise InventoryError(f"no sale {sale_id}")
            if sale["packed_on"]:
                raise InventoryError(f"sale {sale_id} was already packed on {sale['packed_on']}")
            today = today_iso()
            con.execute("UPDATE sales SET packed_on=? WHERE id=?", (today, sale["id"]))
            self._audit(con, "sale", sale["id"], "packed", actor, {"item_id": sale["item_id"]})
            item = con.execute("SELECT status, bin_id FROM items WHERE id=?", (sale["item_id"],)).fetchone()
            if item is not None and item["status"] == "sold" and item["bin_id"]:
                con.execute("UPDATE items SET bin_id='', updated_at=? WHERE id=?", (now_iso(), sale["item_id"]))
                self._audit(
                    con, "item", sale["item_id"], "move", actor, {"bin": [item["bin_id"], ""], "reason": "packed"}
                )
            out = con.execute("SELECT * FROM sales WHERE id=?", (sale["id"],)).fetchone()
        return _sale_row_to_dict(out)

    # ── roll-ups ──────────────────────────────────────────────────────────────
    def summary(self, lot_id: str = "") -> dict:
        """Per-lot P&L: what it cost, what is left to sell (at low/target/high), what has
        been realized (gross and net), and where the lot lands if the rest sells at target.
        Items with no lot (or a lot that no longer exists) roll up under ``unassigned`` and
        count toward the totals — nothing with money on it is left out."""
        with self._read() as con:
            lots = [_lot_row_to_dict(r) for r in con.execute("SELECT * FROM lots ORDER BY id")]
            if lot_id:
                lots = [lot for lot in lots if lot["id"] == lot_id]
                if not lots:
                    raise InventoryError(f"no lot {lot_id!r}")
            items = [dict(r) for r in con.execute("SELECT * FROM items")]
            sales = [dict(r) for r in con.execute("SELECT * FROM sales")]
        known = {lot["id"] for lot in lots}
        by_lot: dict[str, list[dict]] = {}
        for it in items:
            by_lot.setdefault(it["lot_id"], []).append(it)
        sales_by_item: dict[str, list[dict]] = {}
        for s in sales:
            sales_by_item.setdefault(s["item_id"], []).append(s)
        out_lots = [self._lot_rollup(lot, by_lot.get(lot["id"], []), sales_by_item) for lot in lots]
        orphans = [] if lot_id else [it for k, v in by_lot.items() if k not in known for it in v]
        unassigned = None
        if orphans:
            unassigned = self._lot_rollup(
                {"id": "", "name": "(no lot)", "acquisition_cost": 0.0, "acquired_on": ""}, orphans, sales_by_item
            )
        rollups = out_lots + ([unassigned] if unassigned else [])
        totals = {
            "lots": len(out_lots),
            "items": sum(lr["counts"]["total"] for lr in rollups),
            "acquisition_cost": round(sum(lr["acquisition_cost"] for lr in rollups), 2),
            "realized_gross": round(sum(lr["realized"]["gross"] for lr in rollups), 2),
            "realized_net": round(sum(lr["realized"]["net"] for lr in rollups), 2),
            "remaining": {k: round(sum(lr["remaining"][k] for lr in rollups), 2) for k in ("low", "target", "high")},
            "unpriced_items": sum(lr["remaining"]["unpriced_items"] for lr in rollups),
        }
        totals["projected_net_at_target"] = round(
            totals["realized_net"] + totals["remaining"]["target"] - totals["acquisition_cost"], 2
        )
        return {"lots": out_lots, "unassigned": unassigned, "totals": totals, "items_without_lot": len(orphans)}

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
            q = it["quantity"] if it["quantity"] is not None else 1
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
        whose price evidence is older than ``price_days`` (or missing). Dates are ISO
        strings (YYYY-MM-DD or YYYY-MM) and compare as text."""
        cutoff_listed = _days_ago(listed_days)
        cutoff_price = _days_ago(price_days)
        with self._read() as con:
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

    # ── aging stock ──────────────────────────────────────────────────────────────
    #: Statuses that can sit unsold on a shelf or a marketplace. `pending` is a sale in progress
    #: and `planned` is not split yet, so neither ages.
    AGING_STATUSES = ("available", "listed")

    def aging(self, tiers: tuple[int, int, int] = (45, 90, 120), *, today: str | None = None) -> dict:
        """Unsold stock that has sat at least the first tier's days, oldest first.

        Days count from the live listing's ``listed_on`` when there is one ("listed"). Without a
        listing date they count from when the item last became listed/available in the audit
        trail ("listed" / "not listed"), and failing that from when the item was added ("added").
        Each row says which basis it used, so the operator knows how solid the number is.
        Snoozed items are left out until their snooze ends."""
        today = today or today_iso()
        t0 = datetime.strptime(today[:10], "%Y-%m-%d").date()
        tiers = tuple(sorted(int(t) for t in tiers))
        rows: list[dict] = []
        with self._read() as con:
            items = con.execute(
                f"SELECT * FROM items WHERE status IN ({','.join('?' * len(self.AGING_STATUSES))})",
                self.AGING_STATUSES,
            ).fetchall()
            for r in items:
                if (r["aging_snoozed_until"] or "") > today:
                    continue
                listing = con.execute(
                    "SELECT channel, url, listed_on FROM listings WHERE item_id=? AND state='active' "
                    "ORDER BY listed_on LIMIT 1",
                    (r["id"],),
                ).fetchone()
                since, basis, channel, url = "", "", "", ""
                if listing is not None:
                    channel, url = listing["channel"], listing["url"]
                    if listing["listed_on"]:
                        since, basis = listing["listed_on"][:10], "listed"
                if not since:
                    became = con.execute(
                        "SELECT ts FROM audit WHERE entity='item' AND entity_id=? AND action IN ('update','create') "
                        "AND json_valid(changes) AND json_extract(changes, '$.status') = ? ORDER BY id DESC LIMIT 1",
                        (r["id"], r["status"]),
                    ).fetchone()
                    if became is not None:
                        since = became["ts"][:10]
                        basis = "listed" if r["status"] == "listed" else "not listed"
                if not since:
                    since = (r["created_at"] or "")[:10]
                    basis = "added"
                try:
                    days = (t0 - datetime.strptime(since, "%Y-%m-%d").date()).days
                except ValueError:
                    continue
                tier = max((t for t in tiers if days >= t), default=0)
                if not tier:
                    continue
                item = _item_row_to_dict(r)
                rows.append(
                    {
                        "item_id": r["id"],
                        "name": r["name"],
                        "status": r["status"],
                        "bin_id": r["bin_id"],
                        "lot_id": r["lot_id"],
                        "target": item.get("target"),
                        "since": since,
                        "days": days,
                        "basis": basis,
                        "channel": channel,
                        "url": url,
                        "tier": tier,
                        "tier_index": tiers.index(tier),
                    }
                )
        rows.sort(key=lambda x: (-x["days"], x["name"]))
        return {"items": rows, "tiers": list(tiers), "as_of": today}

    def snooze_aging(self, item_id: str, *, days: int = 14, actor: str = "") -> dict:
        """Put off the aging decision on one item for ``days`` (1..90). Audited."""
        try:
            n = int(days)
        except (TypeError, ValueError) as exc:
            raise InventoryError(f"days must be a whole number, got {days!r}") from exc
        if not 1 <= n <= 90:
            raise InventoryError("snooze for 1 to 90 days")
        until = (datetime.now(UTC) + timedelta(days=n)).strftime("%Y-%m-%d")
        with self._tx() as con:
            row = con.execute("SELECT id FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise InventoryError(f"no item {item_id!r}")
            con.execute("UPDATE items SET aging_snoozed_until=?, updated_at=? WHERE id=?", (until, now_iso(), item_id))
            self._audit(con, "item", item_id, "update", actor, {"aging_snoozed_until": until, "reason": "aging snooze"})
        return {"item_id": item_id, "snoozed_until": until}

    # ── scans: photo intake ──────────────────────────────────────────────────────
    # A scan is a photo (up to MAX_SCAN_PHOTOS) the agent identifies in the background. A BOX
    # scan is something bought to break down: it creates its lot at once (cost known, name
    # provisional) and proposes the units it splits into; a UNIT scan is one piece, alone or
    # photographed during a box's breakdown (parent_id). Confirming writes the items; pricing
    # and binning follow. Nothing here talks to the agent — the API enqueues those turns.
    @property
    def scans_dir(self) -> Path:
        return self.path.parent / "scans"

    @staticmethod
    def _scan_row(con, scan_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if row is None:
            raise InventoryError(f"no scan {scan_id!r}")
        return row

    def _scan_dict(self, con, row, timeout_minutes: int, *, children: bool = False) -> dict:
        d = _scan_row_to_dict(row, timeout_minutes)
        sealed = (d.get("pricing") or {}).get("sealed")
        if sealed and row["kind"] == "box" and not sealed.get("chosen"):
            sell = [_item_row_to_dict(r) for r in self._scan_items(con, row) if r["status"] in UNSOLD]
            d["sealed_vs_split"] = self.sealed_vs_split(sealed, sell) if sell else None
        if children:
            d["units"] = [
                _scan_row_to_dict(r, timeout_minutes)
                for r in con.execute("SELECT * FROM scans WHERE parent_id=? ORDER BY created_at, id", (row["id"],))
            ]
        return d

    def create_scan(
        self,
        kind: str,
        *,
        paid=None,
        source: str = "",
        name: str = "",
        lot_id: str = "",
        parent_id: str = "",
        actor: str = "",
        timeout_minutes: int = SCAN_TIMEOUT_MINUTES,
    ) -> dict:
        """Start a scan. A box needs what was paid (it becomes the lot cost) and gets its lot now,
        under a provisional name, so nothing is lost if the operator walks away mid-flow."""
        kind = str(kind or "").strip().lower()
        if kind not in SCAN_KINDS:
            raise InventoryError("a scan is a 'box' (something bought to break down) or a 'unit' (one piece)")
        sid, ts = new_scan_id(), now_iso()
        with self._tx() as con:
            parent = ""
            if kind == "box":
                cents = money_field("paid", paid) if paid is not None else None
                if cents is None or cents is _UNSET or cents <= 0:
                    raise InventoryError("say what you paid for the box — it becomes the lot cost")
                lot_id = "SCAN-" + sid[1:]
                fields = {
                    "name": _clean(name, 120) or "Box scanned " + today_iso(),
                    "acquisition_cost_cents": cents,
                    "acquired_on": today_iso(),
                    "source": _clean(source, 120),
                    "notes": f"Started from scan {sid}.",
                }
                cols = ["id", "created_at", "updated_at", *fields]
                con.execute(
                    f"INSERT INTO lots({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [lot_id, ts, ts, *fields.values()],
                )
                self._audit(con, "lot", lot_id, "create", actor, {**fields, "scan": sid})
            elif parent_id:
                p = self._scan_row(con, scan_key(parent_id))
                if p["kind"] != "box":
                    raise InventoryError("a piece can only be added to a box scan")
                if p["state"] == "confirmed":
                    raise InventoryError("that box is already broken down; scan this as a single piece instead")
                parent, lot_id = p["id"], p["lot_id"]
            elif str(lot_id or "").strip():
                lot_id = check_id("lot", lot_id)
                if con.execute("SELECT 1 FROM lots WHERE id=?", (lot_id,)).fetchone() is None:
                    raise InventoryError(f"no lot {lot_id!r}")
            else:
                lot_id = ""
            con.execute(
                "INSERT INTO scans(id, kind, state, parent_id, lot_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (sid, kind, "new", parent, lot_id, ts, ts),
            )
            self._audit(con, "scan", sid, "create", actor, {"kind": kind, "lot_id": lot_id, "parent_id": parent})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes)

    def add_scan_photo(
        self, scan_id: str, data: bytes, *, actor: str = "", timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> dict:
        """Store a photo for a scan — sniffed, converted (HEIC → JPEG) and stripped of metadata
        (photos.py) — and (re)start identification. The file is renamed into place inside the
        transaction that records it, so a failed write leaves neither a row nor a file."""
        from .photos import PhotoError, prepare_photo

        sid = scan_key(scan_id)
        with self._read() as con:
            self._photo_room(self._scan_row(con, sid))
        try:
            clean, ext = prepare_photo(bytes(data or b""))
        except PhotoError as exc:
            raise InventoryError(str(exc)) from exc
        pid = uuid.uuid4().hex
        folder = self.scans_dir / sid
        folder.mkdir(parents=True, exist_ok=True)
        final, tmp = folder / f"{pid}.{ext}", folder / f".{pid}.{ext}.tmp"
        try:
            tmp.write_bytes(clean)
            with self._tx() as con:
                row = self._scan_row(con, sid)
                photos = self._photo_room(row)
                photos.append({"id": pid, "ext": ext, "bytes": len(clean)})
                ts = now_iso()
                con.execute(
                    "UPDATE scans SET photos=?, state='identifying', proposal='', reason='', identify_started_at=?, "
                    "updated_at=? WHERE id=?",
                    (json.dumps(photos), ts, ts, sid),
                )
                self._audit(con, "scan", sid, "photo", actor, {"photo": pid, "bytes": len(clean)})
                os.replace(tmp, final)
                return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _photo_room(row) -> list:
        if row["state"] == "confirmed":
            raise InventoryError("this scan is already confirmed; start a new one")
        photos = _loads(row["photos"], [])
        if len(photos) >= MAX_SCAN_PHOTOS:
            raise InventoryError(f"a scan holds at most {MAX_SCAN_PHOTOS} photos")
        return photos

    def scan_photo_file(self, scan_id: str, photo_id: str) -> tuple[Path, str] | None:
        """``(path, content type)`` of one of a scan's photos, or None. Both ids are checked
        against strict patterns before they touch the filesystem."""
        try:
            sid = scan_key(scan_id)
        except InventoryError:
            return None
        if not _PHOTO_ID_RE.match(str(photo_id or "")):
            return None
        with self._read() as con:
            row = con.execute("SELECT photos FROM scans WHERE id=?", (sid,)).fetchone()
        if row is None:
            return None
        for p in _loads(row["photos"], []):
            if p.get("id") == photo_id and p.get("ext") in _PHOTO_TYPES:
                path = self.scans_dir / sid / f"{photo_id}.{p['ext']}"
                return (path, _PHOTO_TYPES[p["ext"]]) if path.is_file() else None
        return None

    def get_scan(self, scan_id: str, *, timeout_minutes: int = SCAN_TIMEOUT_MINUTES) -> dict:
        sid = scan_key(scan_id)
        with self._read() as con:
            row = self._scan_row(con, sid)
            return self._scan_dict(con, row, timeout_minutes, children=row["kind"] == "box")

    def list_scans(
        self, *, open_only: bool = True, limit: int = 50, timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> list[dict]:
        """Top-level scans (a box, or a piece scanned on its own), newest first. ``open_only`` =
        not finished: what Next ▸ Needs a look resumes."""
        sql = "SELECT * FROM scans WHERE parent_id=''" + (" AND finished_at=''" if open_only else "")
        with self._read() as con:
            rows = con.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (int(limit),)).fetchall()
            return [self._scan_dict(con, r, timeout_minutes, children=r["kind"] == "box") for r in rows]

    def propose_scan(
        self, scan_id: str, proposal: dict, *, actor: str = "agent", timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> dict:
        """The agent's identification. Accepted only while the scan is waiting for one (a late
        answer to a timed-out scan still counts: it is better than a retry)."""
        sid = scan_key(scan_id)
        p = proposal or {}
        name = _clean(p.get("name"), 200)
        if not name:
            raise InventoryError(
                "a proposal needs the product's name; if you can't tell, call inventory_scan_fail instead"
            )
        try:
            confidence = float(p.get("confidence") if p.get("confidence") is not None else 0)
        except (TypeError, ValueError) as exc:
            raise InventoryError(f"confidence must be a number from 0 to 1, got {p.get('confidence')!r}") from exc
        if math.isnan(confidence):
            raise InventoryError("confidence must be a number from 0 to 1")
        alternatives = []
        for a in p.get("alternatives") or []:
            alt = (
                {"name": _clean(a, 200), "system": ""}
                if isinstance(a, str)
                else {"name": _clean((a or {}).get("name"), 200), "system": _clean((a or {}).get("system"), 80)}
            )
            if (
                alt["name"]
                and alt["name"].lower() != name.lower()
                and all(alt["name"] != x["name"] for x in alternatives)
            ):
                alternatives.append(alt)
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["state"] != "identifying":
                raise InventoryError(f"scan {sid} isn't waiting for identification (it is {row['state']})")
            contents = []
            raw = p.get("contents") or []
            if row["kind"] == "box":
                if not isinstance(raw, list) or len(raw) > MAX_SCAN_CONTENTS:
                    raise InventoryError(f"contents must be a list of at most {MAX_SCAN_CONTENTS} units")
                for u in raw:
                    u = u if isinstance(u, dict) else {"name": u}
                    uname = _clean(u.get("name"), 200)
                    if not uname:
                        continue
                    contents.append(
                        {
                            "name": uname,
                            "quantity": max(1, _int_field("quantity", u.get("quantity") or 1)),
                            "model_count": max(0, _int_field("model_count", u.get("model_count") or 0)),
                            "category": _clean(u.get("category"), 80),
                            "system": _clean(u.get("system"), 80),
                            "condition": condition_label(u.get("condition"))[0],
                        }
                    )
            cond_label, cond_note = condition_label(p.get("condition"))
            out = {
                "name": name,
                "system": _clean(p.get("system"), 80),
                "category": _clean(p.get("category"), 80),
                "condition": cond_label,
                "confidence": min(1.0, max(0.0, confidence)),
                "alternatives": alternatives[:3],
                "contents": contents,
                "notes": _clean(
                    " ".join(x for x in (f"Condition: {cond_note}." if cond_note else "", p.get("notes") or "") if x),
                    500,
                ),
            }
            ts = now_iso()
            con.execute(
                "UPDATE scans SET proposal=?, state='proposed', reason='', updated_at=? WHERE id=?",
                (json.dumps(out), ts, sid),
            )
            self._audit(
                con,
                "scan",
                sid,
                "propose",
                actor,
                {"name": name, "confidence": out["confidence"], "units": len(contents)},
            )
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes, children=row["kind"] == "box")

    def fail_scan(
        self, scan_id: str, reason: str, *, actor: str = "agent", timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> dict:
        """Identification gave up (or never started). The operator types the name or retries."""
        sid = scan_key(scan_id)
        why = _clean(reason, 300) or "Couldn't identify it."
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["state"] != "identifying":
                raise InventoryError(f"scan {sid} isn't waiting for identification (it is {row['state']})")
            con.execute("UPDATE scans SET state='failed', reason=?, updated_at=? WHERE id=?", (why, now_iso(), sid))
            self._audit(con, "scan", sid, "fail", actor, {"reason": why})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes)

    def retry_scan(self, scan_id: str, *, actor: str = "", timeout_minutes: int = SCAN_TIMEOUT_MINUTES) -> dict:
        sid = scan_key(scan_id)
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["state"] == "confirmed":
                raise InventoryError("this scan is already confirmed")
            if not _loads(row["photos"], []):
                raise InventoryError("take a photo first")
            ts = now_iso()
            con.execute(
                "UPDATE scans SET state='identifying', proposal='', reason='', identify_started_at=?, updated_at=? WHERE id=?",
                (ts, ts, sid),
            )
            self._audit(con, "scan", sid, "retry", actor, {})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes)

    def confirm_scan(
        self, scan_id: str, body: dict, *, actor: str = "", timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> dict:
        """Write what the operator settled on. A box: the lot gets its real name and every unit
        becomes an item — Keep → ``kept``, Sell → ``planned`` (``available`` once put away in a bin). A piece
        on its own: one item. All in one transaction; the correction (what the agent proposed vs
        what was chosen, and how) is kept on the scan."""
        sid = scan_key(scan_id)
        b = body or {}
        name = _clean(b.get("name"), 200)
        if not name:
            raise InventoryError("name what it is first — pick the agent's answer, an alternative, or type it")
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["state"] == "confirmed":
                raise InventoryError("this scan is already confirmed")
            if row["kind"] == "unit" and row["parent_id"]:
                raise InventoryError("a piece photographed during a breakdown is confirmed with its box")
            proposal = _loads(row["proposal"], {})
            how = str(b.get("how") or "").strip()
            if how not in ("accepted", "alternative", "typed"):
                how = "accepted" if name == proposal.get("name") else "typed"
            system, condition = _clean(b.get("system"), 80), _clean(b.get("condition"), 40)
            created: list[str] = []
            note = f"From scan {sid}."

            def make(u: dict, lot: str) -> str:
                uname = _clean(u.get("name"), 200)
                if not uname:
                    raise InventoryError("every piece needs a name — type one for any that are still identifying")
                fields = {
                    "name": uname,
                    "lot_id": lot,
                    "system": _clean(u.get("system"), 80) or system,
                    "category": _clean(u.get("category"), 80),
                    # A box's own condition (NIB, Sealed) never passes to the pieces split out of it.
                    "condition": _clean(u.get("condition"), 40) or (condition if row["kind"] == "unit" else ""),
                    "quantity": max(1, _int_field("quantity", u.get("quantity") or 1)),
                    "model_count": max(0, _int_field("model_count", u.get("model_count") or 0)),
                    "status": "kept" if as_flag(u.get("keep")) else "planned",
                    "notes": note,
                }
                return self._upsert_item_rows(con, fields, actor=actor)["id"]

            ts = now_iso()
            if row["kind"] == "box":
                units = b.get("units")
                if not isinstance(units, list) or not units:
                    raise InventoryError("list at least one piece — the box itself counts, if you sell it whole")
                children = {r["id"]: r for r in con.execute("SELECT * FROM scans WHERE parent_id=?", (sid,))}
                con.execute("UPDATE lots SET name=?, updated_at=? WHERE id=?", (name, ts, row["lot_id"]))
                self._audit(con, "lot", row["lot_id"], "update", actor, {"name": name, "scan": sid})
                for u in units:
                    u = u if isinstance(u, dict) else {}
                    item_id = make(u, row["lot_id"])
                    created.append(item_id)
                    child = children.get(str(u.get("scan_id") or "").strip().upper())
                    if child is not None:
                        cp = _loads(child["proposal"], {})
                        corr = {"proposed": cp.get("name", ""), "chosen": _clean(u.get("name"), 200)}
                        con.execute(
                            "UPDATE scans SET state='confirmed', item_ids=?, correction=?, updated_at=? WHERE id=?",
                            (json.dumps([item_id]), json.dumps(corr), ts, child["id"]),
                        )
            else:
                lot = row["lot_id"] or (check_id("lot", b["lot_id"]) if str(b.get("lot_id") or "").strip() else "")
                created.append(make({**b, "name": name}, lot))
            correction = {"proposed": proposal.get("name", ""), "chosen": name, "how": how}
            con.execute(
                "UPDATE scans SET state='confirmed', item_ids=?, correction=?, reason='', updated_at=? WHERE id=?",
                (json.dumps(created), json.dumps(correction), ts, sid),
            )
            self._audit(con, "scan", sid, "confirm", actor, {"name": name, "how": how, "items": created})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes, children=row["kind"] == "box")

    def _scan_items(self, con, row) -> list[sqlite3.Row]:
        ids = _loads(row["item_ids"], [])
        if not ids:
            return []
        rows = {r["id"]: r for r in con.execute(f"SELECT * FROM items WHERE id IN ({','.join('?' * len(ids))})", ids)}
        return [rows[i] for i in ids if i in rows]

    def request_pricing(
        self, scan_id: str, *, actor: str = "", timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> tuple[dict, list[dict]]:
        """Mark a confirmed scan's sell pile as being priced; returns the scan and the items to
        price (the API enqueues the agent turn)."""
        sid = scan_key(scan_id)
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["state"] != "confirmed":
                raise InventoryError("confirm the scan before pricing it")
            sell = [r for r in self._scan_items(con, row) if r["status"] in UNSOLD]
            if not sell:
                raise InventoryError("nothing from this scan is for sale")
            pricing = {"state": "queued", "requested_at": now_iso(), "items": [r["id"] for r in sell], "unpriced": []}
            con.execute("UPDATE scans SET pricing=?, updated_at=? WHERE id=?", (json.dumps(pricing), now_iso(), sid))
            self._audit(con, "scan", sid, "price_requested", actor, {"items": pricing["items"]})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes), [_item_row_to_dict(r) for r in sell]

    def pricing_done(
        self, scan_id: str, unpriced=None, *, actor: str = "agent", timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> dict:
        """The pricing turn's closing report: which items it could not price, and why."""
        sid = scan_key(scan_id)
        with self._tx() as con:
            row = self._scan_row(con, sid)
            pricing = _loads(row["pricing"], {})
            if pricing.get("state") != "queued":
                raise InventoryError(f"scan {sid} has no pricing in progress")
            mine = set(pricing.get("items") or [])
            out = []
            for u in unpriced or []:
                u = u if isinstance(u, dict) else {}
                iid = str(u.get("item_id") or "").strip()
                if iid not in mine:
                    raise InventoryError(f"item {iid!r} is not part of scan {sid}'s pricing")
                out.append({"item_id": iid, "reason": _clean(u.get("reason"), 300) or "No usable comps."})
            pricing.update(state="done", done_at=now_iso(), unpriced=out)
            con.execute("UPDATE scans SET pricing=?, updated_at=? WHERE id=?", (json.dumps(pricing), now_iso(), sid))
            self._audit(con, "scan", sid, "priced", actor, {"unpriced": [u["item_id"] for u in out]})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes, children=row["kind"] == "box")

    def pricing_failed(
        self, scan_id: str, reason: str, *, actor: str = "", timeout_minutes: int = SCAN_TIMEOUT_MINUTES
    ) -> dict:
        sid = scan_key(scan_id)
        with self._tx() as con:
            row = self._scan_row(con, sid)
            pricing = _loads(row["pricing"], {})
            pricing.update(state="failed", reason=_clean(reason, 300) or "Pricing could not start.")
            con.execute("UPDATE scans SET pricing=?, updated_at=? WHERE id=?", (json.dumps(pricing), now_iso(), sid))
            self._audit(con, "scan", sid, "price_failed", actor, {"reason": pricing["reason"]})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes)

    # ── sealed vs split ─────────────────────────────────────────────────────────
    #: Sealed has to beat split by at least this much (and this share of the split total) before
    #: the view suggests selling the box sealed: a sealed sale is one listing, one buyer, one parcel.
    SEALED_MARGIN_MIN = 10.0
    SEALED_MARGIN_SHARE = 0.05

    def record_sealed_price(
        self,
        scan_id: str,
        *,
        low=None,
        target=None,
        high=None,
        n: int = 0,
        query: str = "",
        basis: str = "",
        sealed_fees=None,
        split_fees=None,
        actor: str = "agent",
        timeout_minutes: int = SCAN_TIMEOUT_MINUTES,
    ) -> dict:
        """The pricing turn's sealed price for a BOX scan, kept on the scan (not as an item, since
        the box is being split). ``target`` None = no usable sealed comps (``basis`` says why).
        ``sealed_fees`` / ``split_fees`` are the marketplace fees on the sealed target and on the sum
        of the pieces' targets, as the fee tool reported them; None = unknown (the view then
        compares gross and says so)."""
        sid = scan_key(scan_id)
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["kind"] != "box":
                raise InventoryError("only a box scan has a sealed price")
            pricing = _loads(row["pricing"], {})
            if pricing.get("state") != "queued":
                raise InventoryError(f"scan {sid} has no pricing in progress")
            vals = {}
            for k, v in (
                ("low", low),
                ("target", target),
                ("high", high),
                ("sealed_fees", sealed_fees),
                ("split_fees", split_fees),
            ):
                c = to_cents(v)
                if c is not None and c < 0:
                    raise InventoryError(f"{k} can't be negative")
                vals[k] = dollars(c)
            if vals["target"] is not None and not _clean(basis, 300):
                raise InventoryError("a sealed price needs its basis (the comps it rests on)")
            try:
                n_comps = max(0, int(n or 0))
            except (TypeError, ValueError) as exc:
                raise InventoryError(f"n must be a whole number, got {n!r}") from exc
            pricing["sealed"] = {
                **vals,
                "n": n_comps,
                "query": _clean(query, 200),
                "basis": _clean(basis, 300) or "No usable sealed comps.",
                "observed_on": today_iso(),
            }
            con.execute("UPDATE scans SET pricing=?, updated_at=? WHERE id=?", (json.dumps(pricing), now_iso(), sid))
            self._audit(con, "scan", sid, "sealed_priced", actor, pricing["sealed"])
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes, children=True)

    @classmethod
    def sealed_vs_split(cls, sealed: dict | None, pieces: list[dict]) -> dict | None:
        """Compare selling a box sealed with selling its pieces at their targets. ``pieces`` are the
        sell pieces (``target``, ``quantity``). Net when both fee figures are known, gross otherwise
        (``basis`` says which). None when there's no sealed price to compare."""
        if not sealed or sealed.get("target") is None:
            return None
        priced = [p for p in pieces if p.get("target") is not None]
        split = round(sum(float(p["target"]) * int(p.get("quantity") or 1) for p in priced), 2)
        unpriced = len(pieces) - len(priced)
        fees_known = sealed.get("sealed_fees") is not None and sealed.get("split_fees") is not None
        s_val = float(sealed["target"]) - (float(sealed["sealed_fees"]) if fees_known else 0.0)
        p_val = split - (float(sealed["split_fees"]) if fees_known else 0.0)
        diff = round(s_val - p_val, 2)
        margin = max(cls.SEALED_MARGIN_MIN, cls.SEALED_MARGIN_SHARE * max(p_val, 0.0))
        if unpriced:
            verdict, line = (
                "incomplete",
                f"{unpriced} piece{'s' if unpriced != 1 else ''} still unpriced, so the split total is low.",
            )
        elif diff >= margin:
            verdict, line = "sealed", f"Sealed wins by ${diff:,.2f}: one listing, one parcel."
        elif diff <= -margin:
            verdict, line = "split", f"Split wins by ${-diff:,.2f}."
        else:
            verdict, line = "even", "About even. Sealed is less work."
        return {
            "sealed": round(s_val, 2),
            "split": round(p_val, 2),
            "sealed_gross": float(sealed["target"]),
            "split_gross": split,
            "basis": "net of fees" if fees_known else "gross (fees not known)",
            "difference": diff,
            "unpriced": unpriced,
            "verdict": verdict,
            "recommendation": line,
            "sealed_basis": sealed.get("basis", ""),
            "observed_on": sealed.get("observed_on", ""),
        }

    def split_check(self, lot_id: str, *, timeout_minutes: int = SCAN_TIMEOUT_MINUTES) -> dict | None:
        """Sealed vs split for a lot that came from a box scan with a sealed price and whose
        pieces are ALL still planned (nothing split or sold yet). None otherwise."""
        with self._read() as con:
            rows = con.execute(
                "SELECT * FROM scans WHERE lot_id=? AND kind='box' AND parent_id='' ORDER BY created_at DESC", (lot_id,)
            ).fetchall()
            for row in rows:
                sealed = (_loads(row["pricing"], {}) or {}).get("sealed")
                if not sealed or sealed.get("chosen"):
                    continue
                items = self._scan_items(con, row)
                lot_items = con.execute("SELECT status FROM items WHERE lot_id=?", (lot_id,)).fetchall()
                if not items or any(r["status"] != "planned" for r in lot_items):
                    return None
                cmp = self.sealed_vs_split(sealed, [_item_row_to_dict(r) for r in items])
                return {**cmp, "scan_id": row["id"]} if cmp else None
        return None

    def sell_sealed(self, scan_id: str, *, actor: str = "", timeout_minutes: int = SCAN_TIMEOUT_MINUTES) -> dict:
        """Turn a confirmed box scan back into ONE sealed item: the planned pieces are removed
        (snapshotted into the audit trail), and a sealed item (NIB) takes the lot, priced at the
        sealed target with its basis and evidence. Refused once any piece is kept, for sale,
        listed or sold, because then the box is already open."""
        sid = scan_key(scan_id)
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["kind"] != "box" or row["state"] != "confirmed":
                raise InventoryError("only a confirmed box scan can be sold sealed")
            sealed = (_loads(row["pricing"], {}) or {}).get("sealed") or {}
            if sealed.get("target") is None:
                raise InventoryError("there's no sealed price for this box yet")
            if sealed.get("chosen"):
                raise InventoryError("this box is already being sold sealed")
            pieces = self._scan_items(con, row)
            opened = [r["name"] for r in pieces if r["status"] != "planned"]
            if opened:
                raise InventoryError("the box is already broken down (" + ", ".join(opened[:3]) + "); sell the pieces")
            lot = con.execute("SELECT * FROM lots WHERE id=?", (row["lot_id"],)).fetchone()
            if lot is None:
                raise InventoryError("this scan's lot is gone")
            system = next((r["system"] for r in pieces if r["system"]), "")
            for r in pieces:
                self._delete_item_rows(con, r["id"], actor)
            base = "SEALED-" + sid[1:]
            iid, k = base, 2
            while con.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone() is not None:
                iid, k = f"{base}-{k}", k + 1
            ts, day = now_iso(), today_iso()
            con.execute(
                "INSERT INTO items(id, lot_id, category, name, condition, quantity, model_count, notes, status, "
                "target_low_cents, target_cents, target_high_cents, price_basis, price_updated_on, system, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    iid,
                    lot["id"],
                    "Box set",
                    lot["name"],
                    "NIB",
                    1,
                    0,
                    f"Sold sealed instead of split (scan {sid}).",
                    "available",
                    to_cents(sealed.get("low")),
                    to_cents(sealed["target"]),
                    to_cents(sealed.get("high")),
                    sealed.get("basis", ""),
                    sealed.get("observed_on") or day,
                    system,
                    ts,
                    ts,
                ),
            )
            self._insert_observation(
                con,
                iid,
                observed_on=sealed.get("observed_on") or day,
                source="ebay_sold",
                query=sealed.get("query", ""),
                n=sealed.get("n", 0),
                median=sealed["target"],
                p25=sealed.get("low"),
                p75=sealed.get("high"),
                basis=sealed.get("basis", ""),
            )
            self._audit(
                con,
                "item",
                iid,
                "create",
                actor,
                {"status": "available", "name": lot["name"], "reason": "sold sealed", "scan": sid},
            )
            pricing = _loads(row["pricing"], {})
            pricing["sealed"] = {**sealed, "chosen": "sealed", "item_id": iid}
            con.execute(
                "UPDATE scans SET item_ids=?, pricing=?, updated_at=? WHERE id=?",
                (json.dumps([iid]), json.dumps(pricing), ts, sid),
            )
            self._audit(con, "scan", sid, "sell_sealed", actor, {"removed": [r["id"] for r in pieces], "item": iid})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes, children=True)

    def finish_scan(
        self,
        scan_id: str,
        placements: dict | None = None,
        *,
        actor: str = "",
        timeout_minutes: int = SCAN_TIMEOUT_MINUTES,
    ) -> dict:
        """Close the scan. Since 0.11.0 the view finishes with no ``placements``: pieces are
        labelled first and binned later by Put away (``move_items(put_away=True)``). The API
        still takes ``placements`` = {item_id: bin_id}; a sell piece that lands in a bin becomes
        ``available`` (it is split out and on the shelf); one left out stays ``planned``. Moves are
        audited like any other move."""
        sid = scan_key(scan_id)
        placements = placements or {}
        if not isinstance(placements, dict):
            raise InventoryError("placements must map item ids to bin ids")
        with self._tx() as con:
            row = self._scan_row(con, sid)
            if row["state"] != "confirmed":
                raise InventoryError("confirm the scan before binning it")
            items = {r["id"]: r for r in self._scan_items(con, row)}
            ts = now_iso()
            placed = 0
            for item_id, bin_raw in placements.items():
                if item_id not in items:
                    raise InventoryError(f"item {item_id!r} is not part of scan {sid}")
                target = self._bin_id(bin_raw) if str(bin_raw or "").strip() else ""
                if not target:
                    continue
                if con.execute("SELECT 1 FROM bins WHERE id=?", (target,)).fetchone() is None:
                    raise InventoryError(f"no bin {target!r}; create it first")
                it = items[item_id]
                if (it["bin_id"] or "") != target:
                    con.execute("UPDATE items SET bin_id=?, updated_at=? WHERE id=?", (target, ts, item_id))
                    self._audit(con, "item", item_id, "move", actor, {"bin": [it["bin_id"] or "", target]})
                if it["status"] == "planned":
                    con.execute("UPDATE items SET status='available', updated_at=? WHERE id=?", (ts, item_id))
                    self._audit(con, "item", item_id, "update", actor, {"status": "available"})
                placed += 1
            con.execute("UPDATE scans SET finished_at=?, updated_at=? WHERE id=?", (ts, ts, sid))
            self._audit(con, "scan", sid, "finish", actor, {"placed": placed})
            return self._scan_dict(con, self._scan_row(con, sid), timeout_minutes, children=row["kind"] == "box")

    def discard_scan(self, scan_id: str, *, actor: str = "") -> int:
        """Throw away an unconfirmed scan, its pieces and its photos — and a box's provisional
        lot when nothing else landed in it."""
        sid = scan_key(scan_id)
        with self._tx() as con:
            row = con.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
            if row is None:
                return 0
            if row["state"] == "confirmed":
                raise InventoryError("a confirmed scan already made items; delete or withdraw those instead")
            kids = [r["id"] for r in con.execute("SELECT id FROM scans WHERE parent_id=?", (sid,))]
            lot_removed = False
            if row["kind"] == "box" and row["lot_id"]:
                n = con.execute("SELECT COUNT(*) FROM items WHERE lot_id=?", (row["lot_id"],)).fetchone()[0]
                lot = con.execute("SELECT * FROM lots WHERE id=?", (row["lot_id"],)).fetchone()
                if not n and lot is not None:
                    con.execute("DELETE FROM lots WHERE id=?", (row["lot_id"],))
                    self._audit(con, "lot", row["lot_id"], "delete", actor, {"scan": sid, "snapshot": dict(lot)})
                    lot_removed = True
            con.execute(f"DELETE FROM scans WHERE id IN ({','.join('?' * (1 + len(kids)))})", [sid, *kids])
            self._audit(
                con, "scan", sid, "delete", actor, {"kind": row["kind"], "units": len(kids), "lot_removed": lot_removed}
            )
        for x in (sid, *kids):
            shutil.rmtree(self.scans_dir / x, ignore_errors=True)
        return 1

    # ── listing drafts (0.9.0) ────────────────────────────────────────────────
    # A draft is the agent's eBay copy for one item: title, description, the item's scan photos.
    # The price is never stored here: it is the item's target, so it always carries its basis.
    # Nothing is posted anywhere; the operator pastes the copy into eBay and records the URL.
    def _draft_row(self, con, item_id: str):
        return con.execute("SELECT * FROM drafts WHERE item_id=?", (item_id,)).fetchone()

    @staticmethod
    def _item_photos(con, item_id: str) -> list[dict]:
        """An item's photos in order: its own piece scan(s) first, then the box it came from."""
        own, box = [], []
        for r in con.execute(
            # instr, not LIKE: an id may hold "_" or "%", which LIKE would read as wildcards.
            "SELECT id, kind, item_ids, photos FROM scans WHERE instr(item_ids, ?) > 0 ORDER BY created_at, id",
            (json.dumps(item_id),),
        ):
            ids = _loads(r["item_ids"], [])
            if item_id not in ids:
                continue
            target = own if (r["kind"] == "unit" or ids == [item_id]) else box
            target.extend(
                {"scan_id": r["id"], "photo_id": p["id"]}
                for p in _loads(r["photos"], [])
                if p.get("ext") in _PHOTO_TYPES and _PHOTO_ID_RE.match(str(p.get("id") or ""))
            )
        return own + box

    def _draft_dict(self, con, item, row, timeout_minutes: int) -> dict:
        listing = None
        if row is not None and row["listing_id"] is not None:
            lr = con.execute("SELECT * FROM listings WHERE id=?", (row["listing_id"],)).fetchone()
            listing = _listing_row_to_dict(lr) if lr is not None else None
        state = row["state"] if row is not None else "none"
        reason = row["reason"] if row is not None else ""
        if state == "drafting" and row["requested_at"] and _age_over(row["requested_at"], minutes=timeout_minutes):
            state, reason = "failed", reason or "The draft didn't finish. Try again."
        if item["status"] == "sold":
            listing_state = "sold"
        elif listing is not None and listing["state"] in ("ended", "sold"):
            listing_state = listing["state"]
        elif state == "live":
            listing_state = "live"
        else:
            listing_state = state
        copied_at = row["copied_at"] if row is not None else ""
        sku = custom_label(item["bin_id"], item["id"])
        sku_copied = row["sku_copied"] if row is not None else ""
        return {
            "sku": sku,
            "sku_copied": sku_copied,
            # The label pasted into eBay no longer says where the item is: ask to update it there.
            "sku_stale": bool(sku_copied)
            and sku_copied != sku
            and state in ("copied", "live")
            and item["status"] != "sold",
            "item_id": item["id"],
            "state": state,
            "listing_state": listing_state,
            "title": row["title"] if row is not None else "",
            "description": row["description"] if row is not None else "",
            "notes": row["notes"] if row is not None else "",
            "reason": reason,
            "requested_at": row["requested_at"] if row is not None else "",
            "drafted_at": row["drafted_at"] if row is not None else "",
            "copied_at": copied_at,
            "copied_stale": state == "copied" and bool(copied_at) and _age_over(copied_at, minutes=24 * 60),
            "live_at": row["live_at"] if row is not None else "",
            "listing": listing,
            "photos": self._item_photos(con, item["id"]),
        }

    def get_draft(self, item_id: str, *, timeout_minutes: int = DRAFT_TIMEOUT_MINUTES) -> dict:
        with self._read() as con:
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if item is None:
                raise InventoryError(f"no item {item_id!r}")
            return self._draft_dict(con, item, self._draft_row(con, item_id), timeout_minutes)

    def list_drafts(self, *, timeout_minutes: int = DRAFT_TIMEOUT_MINUTES) -> list[dict]:
        """Every draft, without its text or photos: what the Next tab needs per row."""
        with self._read() as con:
            out = []
            for row in con.execute("SELECT * FROM drafts ORDER BY updated_at DESC"):
                item = con.execute("SELECT * FROM items WHERE id=?", (row["item_id"],)).fetchone()
                if item is None:
                    continue
                d = self._draft_dict(con, item, row, timeout_minutes)
                out.append(
                    {
                        k: d[k]
                        for k in (
                            "item_id",
                            "state",
                            "listing_state",
                            "reason",
                            "copied_at",
                            "copied_stale",
                            "sku",
                            "sku_stale",
                        )
                    }
                )
            return out

    def request_draft(self, item_id: str, *, actor: str = "") -> dict:
        """Mark an item as being drafted (the API then starts the agent turn). Needs a price:
        the draft's price IS the item's target, with its basis."""
        with self._tx() as con:
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if item is None:
                raise InventoryError(f"no item {item_id!r}")
            if item["status"] not in ("planned", "available"):
                raise InventoryError(f"item {item_id!r} is {item['status']}; only unlisted stock gets a draft")
            if item["target_cents"] is None:
                raise InventoryError("set a price first: a listing draft uses the item's target and its basis")
            row = self._draft_row(con, item_id)
            if row is not None and row["state"] == "live":
                raise InventoryError("this item is already live on eBay")
            ts = now_iso()
            if row is None:
                con.execute(
                    "INSERT INTO drafts(item_id, state, requested_at, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (item_id, "drafting", ts, ts, ts),
                )
            else:
                con.execute(
                    "UPDATE drafts SET state='drafting', reason='', requested_at=?, updated_at=? WHERE item_id=?",
                    (ts, ts, item_id),
                )
            self._audit(con, "draft", item_id, "request", actor, {"regenerate": row is not None})
            return self._draft_dict(con, item, self._draft_row(con, item_id), DRAFT_TIMEOUT_MINUTES)

    def write_draft(self, item_id: str, title: str, description: str, *, notes: str = "", actor: str = "agent") -> dict:
        """The agent's copy. Refused (with the fix) rather than silently trimmed, so the agent
        rewrites a long title instead of the operator pasting a cut one."""
        t = " ".join(str(title or "").split())
        if not t:
            raise InventoryError("a draft needs a title")
        if len(t) > TITLE_MAX:
            raise InventoryError(f"the title is {len(t)} characters; eBay allows {TITLE_MAX}. Shorten it and try again")
        lines = [" ".join(ln.split()) for ln in str(description or "").replace("\r\n", "\n").split("\n")]
        desc = "\n".join(lines).strip()
        while "\n\n\n" in desc:
            desc = desc.replace("\n\n\n", "\n\n")
        if not desc:
            raise InventoryError("a draft needs a description")
        if len(desc) > DESCRIPTION_MAX:
            raise InventoryError(f"the description is {len(desc)} characters; keep it under {DESCRIPTION_MAX}")
        if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", desc):
            raise InventoryError("write the description as plain text, without HTML tags")
        with self._tx() as con:
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if item is None:
                raise InventoryError(f"no item {item_id!r}")
            row = self._draft_row(con, item_id)
            if row is not None and row["state"] == "live":
                raise InventoryError("this item is already live on eBay; its draft is closed")
            ts = now_iso()
            if row is None:
                con.execute(
                    "INSERT INTO drafts(item_id, state, title, description, notes, drafted_at, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (item_id, "draft", t, desc, _clean(notes, 500), ts, ts, ts),
                )
            else:
                con.execute(
                    "UPDATE drafts SET state='draft', title=?, description=?, notes=?, reason='', drafted_at=?, "
                    "copied_at='', updated_at=? WHERE item_id=?",
                    (t, desc, _clean(notes, 500), ts, ts, item_id),
                )
            self._audit(con, "draft", item_id, "write", actor, {"title": t, "chars": len(desc)})
            return self._draft_dict(con, item, self._draft_row(con, item_id), DRAFT_TIMEOUT_MINUTES)

    def fail_draft(self, item_id: str, reason: str, *, actor: str = "agent") -> dict:
        why = _clean(reason, 300) or "The agent couldn't write a listing for this item."
        with self._tx() as con:
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            row = self._draft_row(con, item_id)
            if item is None or row is None:
                raise InventoryError(f"no draft for {item_id!r}")
            if row["state"] != "drafting":
                raise InventoryError("this draft isn't being written")
            con.execute(
                "UPDATE drafts SET state='failed', reason=?, updated_at=? WHERE item_id=?", (why, now_iso(), item_id)
            )
            self._audit(con, "draft", item_id, "fail", actor, {"reason": why})
            return self._draft_dict(con, item, self._draft_row(con, item_id), DRAFT_TIMEOUT_MINUTES)

    def draft_copied(self, item_id: str, what: str, *, actor: str = "") -> dict:
        """The operator copied part of the draft. The first copy moves it to ``copied``, so Next
        can ask a day later whether it went live."""
        if what not in ("title", "price", "description", "sku"):
            raise InventoryError("copied what? title, price, description or sku")
        with self._tx() as con:
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            row = self._draft_row(con, item_id)
            if item is None or row is None or row["state"] not in ("draft", "copied"):
                raise InventoryError(f"no draft to copy for {item_id!r}")
            if what == "sku":
                sku = custom_label(item["bin_id"], item["id"])
                con.execute("UPDATE drafts SET sku_copied=?, updated_at=? WHERE item_id=?", (sku, now_iso(), item_id))
            if row["state"] == "draft":
                ts = now_iso()
                con.execute(
                    "UPDATE drafts SET state='copied', copied_at=?, updated_at=? WHERE item_id=?", (ts, ts, item_id)
                )
                self._audit(con, "draft", item_id, "copy", actor, {"what": what})
            return self._draft_dict(con, item, self._draft_row(con, item_id), DRAFT_TIMEOUT_MINUTES)

    def draft_sku_updated(self, item_id: str, *, actor: str = "") -> dict:
        """The operator put the item's current Custom label into eBay (after it moved bins)."""
        with self._tx() as con:
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            row = self._draft_row(con, item_id)
            if item is None or row is None:
                raise InventoryError(f"no draft for {item_id!r}")
            sku = custom_label(item["bin_id"], item["id"])
            con.execute("UPDATE drafts SET sku_copied=?, updated_at=? WHERE item_id=?", (sku, now_iso(), item_id))
            self._audit(con, "draft", item_id, "sku", actor, {"sku": [row["sku_copied"], sku]})
            return self._draft_dict(con, item, self._draft_row(con, item_id), DRAFT_TIMEOUT_MINUTES)

    def draft_live(self, item_id: str, url: str, *, actor: str = "") -> dict:
        """The operator listed it on eBay: record the listing (at the item's target) and mark the
        item listed, in one transaction with the audit rows."""
        clean_url = ebay_item_url(url)
        with self._tx() as con:
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            row = self._draft_row(con, item_id)
            if item is None or row is None or row["state"] not in ("draft", "copied"):
                raise InventoryError(f"no draft to mark live for {item_id!r}")
            if item["status"] in CLOSED:
                raise InventoryError(
                    f"item {item_id!r} is {item['status']}; set it back to available before listing it"
                )
            ts, today = now_iso(), today_iso()
            cur = con.execute(
                "INSERT INTO listings(item_id, channel, url, price_cents, listed_on, notes) VALUES (?,?,?,?,?,?)",
                (item_id, "eBay", clean_url, item["target_cents"], today, "From the listing draft."),
            )
            if item["status"] in ("planned", "available"):
                con.execute("UPDATE items SET status='listed', updated_at=? WHERE id=?", (ts, item_id))
            con.execute(
                "UPDATE drafts SET state='live', listing_id=?, live_at=?, updated_at=? WHERE item_id=?",
                (cur.lastrowid, ts, ts, item_id),
            )
            self._audit(con, "listing", cur.lastrowid, "create", actor, {"item_id": item_id, "channel": "eBay"})
            self._audit(con, "draft", item_id, "live", actor, {"url": clean_url, "listing_id": cur.lastrowid})
            item = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._draft_dict(con, item, self._draft_row(con, item_id), DRAFT_TIMEOUT_MINUTES)

    def draft_photo_files(self, item_id: str) -> list[tuple[Path, str]]:
        with self._read() as con:
            refs = self._item_photos(con, item_id)
        out = []
        for ref in refs:
            found = self.scan_photo_file(ref["scan_id"], ref["photo_id"])
            if found:
                out.append(found)
        return out

    # ── pasted sales: the operator pastes an order, an agent turn proposes the sale(s) ────
    def _paste_dict(self, row, timeout_minutes: int = PASTE_TIMEOUT_MINUTES) -> dict:
        state, reason = row["state"], row["reason"]
        if state == "parsing" and row["started_at"] and _age_over(row["started_at"], minutes=timeout_minutes):
            state, reason = "failed", reason or "Reading the order didn't finish. Try again."
        return {
            "id": row["id"],
            "state": state,
            "text": row["text"],
            "item_hint": row["item_hint"],
            "proposal": _loads(row["proposal"], {}) or None,
            "reason": reason,
            "sale_ids": _loads(row["sale_ids"], []),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "created_at": row["created_at"],
        }

    def _paste_row(self, con, paste_id: str):
        row = con.execute("SELECT * FROM sale_pastes WHERE id=?", (paste_key(paste_id),)).fetchone()
        if row is None:
            raise InventoryError(f"no pasted sale {paste_id!r}")
        return row

    def create_paste(self, text: str, *, item_hint: str = "", actor: str = "") -> dict:
        body = str(text or "").strip()
        if not body:
            raise InventoryError("paste the order first")
        if len(body) > MAX_PASTE_CHARS:
            raise InventoryError(f"that paste is over {MAX_PASTE_CHARS} characters; paste one order at a time")
        hint = str(item_hint or "").strip()
        with self._tx() as con:
            if hint and con.execute("SELECT 1 FROM items WHERE id=?", (hint,)).fetchone() is None:
                raise InventoryError(f"no item {hint!r}")
            pid, ts = new_paste_id(), now_iso()
            con.execute(
                "INSERT INTO sale_pastes(id, state, text, item_hint, started_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (pid, "parsing", body, hint, ts, ts, ts),
            )
            self._audit(con, "paste", pid, "create", actor, {"chars": len(body), **({"item_id": hint} if hint else {})})
            return self._paste_dict(self._paste_row(con, pid))

    def get_paste(self, paste_id: str, *, timeout_minutes: int = PASTE_TIMEOUT_MINUTES) -> dict:
        with self._read() as con:
            return self._paste_dict(self._paste_row(con, paste_id), timeout_minutes)

    def list_pastes(self, *, timeout_minutes: int = PASTE_TIMEOUT_MINUTES) -> list[dict]:
        """Pastes still waiting on the operator (reading, ready to record, or failed in the last
        week), newest first, without their text."""
        with self._read() as con:
            rows = con.execute(
                "SELECT * FROM sale_pastes WHERE state IN ('parsing','proposed','failed') AND created_at >= ? "
                "ORDER BY created_at DESC",
                ((datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
            ).fetchall()
            out = []
            for r in rows:
                d = self._paste_dict(r, timeout_minutes)
                d.pop("text")
                out.append(d)
        return out

    def retry_paste(self, paste_id: str, *, actor: str = "") -> dict:
        with self._tx() as con:
            row = self._paste_row(con, paste_id)
            if row["state"] in ("recorded", "discarded"):
                raise InventoryError(f"that paste is already {row['state']}")
            ts = now_iso()
            con.execute(
                "UPDATE sale_pastes SET state='parsing', reason='', started_at=?, updated_at=? WHERE id=?",
                (ts, ts, row["id"]),
            )
            self._audit(con, "paste", row["id"], "retry", actor)
            return self._paste_dict(self._paste_row(con, row["id"]))

    def discard_paste(self, paste_id: str, *, actor: str = "") -> dict:
        with self._tx() as con:
            row = self._paste_row(con, paste_id)
            if row["state"] == "recorded":
                raise InventoryError("those sales are recorded; un-sell an item from its drawer instead")
            ts = now_iso()
            con.execute(
                "UPDATE sale_pastes SET state='discarded', finished_at=?, updated_at=? WHERE id=?", (ts, ts, row["id"])
            )
            self._audit(con, "paste", row["id"], "discard", actor)
            return self._paste_dict(self._paste_row(con, row["id"]))

    def fail_paste(self, paste_id: str, reason: str, *, actor: str = "agent", already_recorded: str = "") -> dict:
        """Give up on a paste with a reason. ``already_recorded`` = the id of an item whose sale
        this paste repeats: the view then says so and links the item instead of offering a retry."""
        why = _clean(reason, 300) or "Couldn't read that order."
        with self._tx() as con:
            row = self._paste_row(con, paste_id)
            if row["state"] not in ("parsing", "failed"):
                raise InventoryError(f"that paste isn't waiting to be read (it is {row['state']})")
            already = None
            if already_recorded:
                it = con.execute("SELECT id, name, status FROM items WHERE id=?", (str(already_recorded),)).fetchone()
                if it is None:
                    raise InventoryError(f"no item {already_recorded!r}; leave already_recorded empty")
                if it["status"] != "sold":
                    raise InventoryError(f"{it['name']} isn't sold, so this can't repeat a recorded sale")
                already = {"already_recorded": {"item_id": it["id"], "name": it["name"]}}
            ts = now_iso()
            con.execute(
                "UPDATE sale_pastes SET state='failed', reason=?, proposal=?, finished_at=?, updated_at=? WHERE id=?",
                (why, json.dumps(already) if already else row["proposal"], ts, ts, row["id"]),
            )
            self._audit(
                con,
                "paste",
                row["id"],
                "fail",
                actor,
                {"reason": why, **({"already_recorded": already_recorded} if already else {})},
            )
            return self._paste_dict(self._paste_row(con, row["id"]))

    def sale_candidates(self, query: str, *, limit: int = 8, include: str = "") -> list[dict]:
        """Items that best match a free-text description of what sold ("human dice",
        "Big Mek Shokk Attack Gun 99120103031"), best first. Scores words shared with the name,
        id, category, system and notes, plus a fuzzy match on the name. ``include`` (an item id)
        is always returned first when it exists — the item the operator opened.

        Unsold items, plus items SOLD in the last ``SOLD_LOOKBACK_DAYS`` days flagged with that
        sale (``status: "sold"``, ``sold_on``, ``sale_id``, ``channel``, ``price``), so a paste of
        a sale that is already recorded reads as "already recorded", never as "not in
        inventory" (0.13.0; two real pastes did exactly that)."""
        import difflib

        words = [w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if len(w) > 1]
        since = (datetime.now(UTC) - timedelta(days=SOLD_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        with self._read() as con:
            rows = con.execute(
                "SELECT * FROM items WHERE status IN ('planned','available','listed','pending')"
                " OR (status='sold' AND id IN (SELECT item_id FROM sales WHERE sold_on >= ?))",
                (since,),
            ).fetchall()
            scored = []
            q = " ".join(words)
            for r in rows:
                hay = " ".join(str(r[k] or "") for k in ("name", "id", "category", "system", "notes")).lower()
                toks = set(re.findall(r"[a-z0-9]+", hay))
                hits = sum(1 for w in words if w in toks or (len(w) > 3 and any(t.startswith(w) for t in toks)))
                ratio = difflib.SequenceMatcher(None, q, str(r["name"] or "").lower()).ratio() if q else 0.0
                score = hits / max(len(words), 1) + 0.5 * ratio + (5 if include and r["id"] == include else 0)
                if score > 0.15 or (include and r["id"] == include):
                    scored.append((score, r))
            scored.sort(key=lambda x: (-x[0], x[1]["name"]))
            out = []
            for score, r in scored[: max(1, min(int(limit or 8), 25))]:
                live = con.execute(
                    "SELECT channel, url FROM listings WHERE item_id=? AND state='active' ORDER BY id DESC LIMIT 1",
                    (r["id"],),
                ).fetchone()
                lot = (
                    con.execute("SELECT name FROM lots WHERE id=?", (r["lot_id"],)).fetchone() if r["lot_id"] else None
                )
                entry = {
                    "item_id": r["id"],
                    "name": r["name"],
                    "condition": r["condition"],
                    "system": r["system"],
                    "category": r["category"],
                    "bin_id": r["bin_id"],
                    "status": r["status"],
                    "quantity": r["quantity"],
                    "target": dollars(r["target_cents"]),
                    "lot": lot["name"] if lot else "",
                    "live_listing": {"channel": live["channel"], "url": live["url"]} if live else None,
                    "score": round(score, 3),
                }
                if r["status"] == "sold":
                    last = con.execute(
                        "SELECT id, sold_on, channel, price_cents FROM sales WHERE item_id=?"
                        " ORDER BY sold_on DESC, id DESC LIMIT 1",
                        (r["id"],),
                    ).fetchone()
                    entry.update(
                        sale_id=last["id"] if last else None,
                        sold_on=last["sold_on"] if last else "",
                        channel=last["channel"] if last else "",
                        price=dollars(last["price_cents"]) if last else None,
                        live_listing=None,
                    )
                out.append(entry)
        return out

    @staticmethod
    def _sale_line(con, raw: dict, n: int, *, text: str = "", check_weekday: bool = True, force: bool = False) -> dict:
        """One proposed (or operator-edited) sale line, validated against the store."""
        if not isinstance(raw, dict):
            raise InventoryError(f"line {n}: expected an object")
        iid = str(raw.get("item_id") or "").strip()
        item = con.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone() if iid else None
        if item is None:
            raise InventoryError(
                f"line {n}: no item {iid!r}; use inventory_sale_candidates to find it, or ask in questions"
            )
        if item["status"] in CLOSED and not force:
            hint = (
                "; if the paste repeats a recorded sale, call inventory_sale_fail with already_recorded"
                if item["status"] == "sold"
                else ""
            )
            raise InventoryError(f"line {n}: {item['name']} ({iid}) is already {item['status']}{hint}")
        qty = _int_field("quantity", raw.get("quantity") if raw.get("quantity") not in (None, "") else 1)
        if qty < 1 or qty > max(int(item["quantity"] or 1), 1):
            raise InventoryError(f"line {n}: quantity {qty} but the item holds {item['quantity'] or 1}")
        price = money_field("price", raw.get("price"))
        if price is None or price is _UNSET or price < 0:
            raise InventoryError(f"line {n}: a sale needs a price")
        money = {}
        for k in ("shipping_charged", "fees", "shipping_cost"):
            v = money_field(k, raw.get(k))
            v = 0 if v in (None, _UNSET) else v
            if v < 0:
                raise InventoryError(f"line {n}: {k.replace('_', ' ')} can't be negative")
            money[k] = v
        channel = _clean(raw.get("channel"), 80)
        if not channel:
            raise InventoryError(f"line {n}: where did it sell? (eBay, Facebook, Personal connection …)")
        sold_on = str(raw.get("sold_on") or "").strip() or today_iso()
        if not _ISO_DATE_RE.match(sold_on):
            raise InventoryError(f"line {n}: sold_on must be YYYY-MM-DD, got {sold_on!r}")
        try:
            day = datetime.strptime(sold_on, "%Y-%m-%d").date()
        except ValueError as exc:
            raise InventoryError(f"line {n}: {sold_on!r} isn't a real date") from exc
        if day > (datetime.now(UTC) + timedelta(days=1)).date():
            raise InventoryError(
                f"line {n}: {sold_on} is in the future; check the year (a paste without one is usually this year "
                "or last)"
            )
        if check_weekday:
            clash = weekday_conflict(text, sold_on)
            if clash:
                raise InventoryError(f"line {n}: {clash}")
        net = price + money["shipping_charged"] - money["fees"] - money["shipping_cost"]
        return {
            "item_id": iid,
            "item_name": item["name"],
            "bin_id": item["bin_id"],
            "quantity": qty,
            "price": dollars(price),
            "shipping_charged": dollars(money["shipping_charged"]),
            "fees": dollars(money["fees"]),
            "fees_estimated": as_flag(raw.get("fees_estimated")),
            "shipping_cost": dollars(money["shipping_cost"]),
            "net": dollars(net),
            "channel": channel,
            "sold_on": sold_on,
            "buyer_ref": _clean(raw.get("buyer_ref"), 80),
            "order_ref": _clean(raw.get("order_ref"), 80),
            "notes": _clean(raw.get("notes"), 300),
            "recorded_sale_id": raw.get("recorded_sale_id") if isinstance(raw.get("recorded_sale_id"), int) else None,
        }

    def propose_paste(
        self,
        paste_id: str,
        lines: list,
        *,
        confidence=0.0,
        questions: list | None = None,
        notes: str = "",
        total=None,
        actor: str = "agent",
        timeout_minutes: int = PASTE_TIMEOUT_MINUTES,
    ) -> dict:
        """The agent's reading of a pasted order: one line per item that sold. ``total`` (the
        price the paste states for everything together, before shipping) is optional; when given,
        the line prices must add up to it — a bundle split must not invent or lose money."""
        if not isinstance(lines, list) or not lines:
            raise InventoryError("propose at least one line, or call inventory_sale_fail")
        if len(lines) > MAX_PASTE_LINES:
            raise InventoryError(f"at most {MAX_PASTE_LINES} lines per paste")
        try:
            conf = float(confidence if confidence is not None else 0)
        except (TypeError, ValueError) as exc:
            raise InventoryError("confidence must be a number from 0 to 1") from exc
        with self._tx() as con:
            row = self._paste_row(con, paste_id)
            st = self._paste_dict(row, timeout_minutes)["state"]
            if row["state"] != "parsing" and st != "failed":
                raise InventoryError(f"that paste isn't waiting to be read (it is {row['state']})")
            clean = [self._sale_line(con, raw, i + 1, text=row["text"]) for i, raw in enumerate(lines)]
            ids = [ln["item_id"] for ln in clean]
            if len(set(ids)) != len(ids):
                raise InventoryError("the same item is on two lines; put the quantity on one line")
            if total not in (None, ""):
                want = money_field("total", total)
                got = sum(to_cents(ln["price"]) or 0 for ln in clean)
                if want not in (None, _UNSET) and abs(got - want) > len(clean):
                    raise InventoryError(
                        f"the line prices add up to {dollars(got)} but the paste's total is {dollars(want)}; "
                        "split the total so it adds up exactly"
                    )
            proposal = {
                "lines": clean,
                "confidence": min(1.0, max(0.0, conf)),
                "questions": [_clean(q, 300) for q in (questions or []) if _clean(q, 300)][:5],
                "notes": _clean(notes, 500),
                "total": dollars(money_field("total", total)) if total not in (None, "") else None,
            }
            ts = now_iso()
            con.execute(
                "UPDATE sale_pastes SET state='proposed', proposal=?, reason='', finished_at=?, updated_at=? WHERE id=?",
                (json.dumps(proposal), ts, ts, row["id"]),
            )
            self._audit(
                con, "paste", row["id"], "propose", actor, {"lines": len(clean), "confidence": proposal["confidence"]}
            )
            return self._paste_dict(self._paste_row(con, row["id"]), timeout_minutes)

    def record_paste(self, paste_id: str, lines: list | None = None, *, force: bool = False, actor: str = "") -> dict:
        """Record the (operator-checked) lines as sales: one ``mark_sold`` per line — each its own
        audited transaction — then the paste is ``recorded``. ``lines`` defaults to the proposal.
        If a line fails part-way, the ones already recorded stay recorded (and are skipped on the
        next try) and the error names the line."""
        with self._read() as con:
            row = self._paste_row(con, paste_id)
            if row["state"] != "proposed":
                raise InventoryError(f"that paste isn't ready to record (it is {row['state']})")
            proposal = _loads(row["proposal"], {}) or {}
            raw_lines = lines if lines is not None else proposal.get("lines") or []
            if not isinstance(raw_lines, list) or not raw_lines:
                raise InventoryError("nothing to record")
            # The operator has read these lines; a weekday clash was the agent's to catch.
            checked = [
                self._sale_line(
                    con,
                    raw,
                    i + 1,
                    text=row["text"],
                    check_weekday=False,
                    force=force or bool(raw.get("recorded_sale_id")),
                )
                for i, raw in enumerate(raw_lines)
            ]
        ids = [ln["item_id"] for ln in checked]
        if len(set(ids)) != len(ids):
            raise InventoryError("the same item is on two lines; put the quantity on one line")
        sales = []
        for i, ln in enumerate(checked):
            if ln["recorded_sale_id"]:
                continue
            extra = ["From a pasted order"]
            if ln["order_ref"]:
                extra.append("order " + ln["order_ref"])
            if ln["fees_estimated"]:
                extra.append("fees estimated")
            note = "; ".join(extra) + "." + (" " + ln["notes"] if ln["notes"] else "")
            try:
                sale = self.mark_sold(
                    ln["item_id"],
                    price=ln["price"],
                    channel=ln["channel"],
                    sold_on=ln["sold_on"],
                    fees=ln["fees"],
                    shipping_charged=ln["shipping_charged"],
                    shipping_cost=ln["shipping_cost"],
                    quantity=ln["quantity"],
                    notes=note,
                    actor=actor,
                    force=force,
                )
            except InventoryError as exc:
                self._save_paste_progress(row["id"], checked, sales, actor, final=False)
                done = f" ({len(sales)} already recorded)" if sales else ""
                raise InventoryError(f"line {i + 1}, {ln['item_name']}: {exc}{done}") from exc
            ln["recorded_sale_id"] = sale["id"]
            sales.append(sale)
        out = self._save_paste_progress(row["id"], checked, sales, actor, final=True)
        out["sales"] = sales
        return out

    def _save_paste_progress(self, pid: str, lines: list, sales: list, actor: str, *, final: bool) -> dict:
        with self._tx() as con:
            row = self._paste_row(con, pid)
            proposal = _loads(row["proposal"], {}) or {}
            proposal["lines"] = lines
            ids = _loads(row["sale_ids"], []) + [s["id"] for s in sales]
            ts = now_iso()
            con.execute(
                "UPDATE sale_pastes SET state=?, proposal=?, sale_ids=?, finished_at=?, updated_at=? WHERE id=?",
                ("recorded" if final else "proposed", json.dumps(proposal), json.dumps(ids), ts, ts, pid),
            )
            if final:
                self._audit(con, "paste", pid, "record", actor, {"sales": ids})
            return self._paste_dict(self._paste_row(con, pid))


def _days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=int(days))).strftime("%Y-%m-%d")
