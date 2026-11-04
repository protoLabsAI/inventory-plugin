"""Short-lived links to a rendered label sheet, so the sheet opens in a real browser.

The console view lives in a sandboxed iframe. On the web it could open a blank tab and write the
sheet into it, but the desktop app serves every new-window request itself: an http(s) address that
isn't a console route goes to the system browser, and a blank window is ignored. So the gated
``/labels/sheet`` route renders the sheet once and parks it here under an unguessable token, and
the public ``GET /plugins/inventory/sheet/<token>`` serves it to whichever browser opens the link.

The sheet holds what a printed label shows (names, conditions, bin ids and the label URLs), never
prices or costs. A token is 32 random bytes, lives 15 minutes (reloads and print previews are
fine inside that window), and at most ``cap`` live sheets are kept: the oldest goes first.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections import OrderedDict

SHEET_PREFIX = "/sheet"  # under the public /plugins/inventory prefix
SHEET_TTL_S = 15 * 60
SHEET_CAP = 50
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

# What any response from the sheet route carries: never cached, never leaks its URL onward, never indexed.
SHEET_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}


class SheetLinks:
    def __init__(self, *, ttl_s: float = SHEET_TTL_S, cap: int = SHEET_CAP, clock=time.monotonic):
        self._ttl, self._cap, self._clock = ttl_s, cap, clock
        self._lock = threading.Lock()
        self._sheets: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def put(self, html: str) -> str:
        """Park a rendered sheet and return its token."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._sheets[token] = (self._clock() + self._ttl, html)
            while len(self._sheets) > self._cap:
                self._sheets.popitem(last=False)
        return token

    def get(self, token: str) -> tuple[str | None, bool]:
        """``(html, False)`` for a live sheet; ``(None, True)`` for one that just expired;
        ``(None, False)`` for a token that was never issued (or was evicted)."""
        if not _TOKEN_RE.match(token or ""):
            return None, False
        with self._lock:
            hit = self._sheets.get(token)
            if hit is None:
                return None, False
            expires, html = hit
            if self._clock() >= expires:
                del self._sheets[token]
                return None, True
            return html, False

    def _purge(self) -> None:
        now = self._clock()
        for t in [t for t, (exp, _) in self._sheets.items() if now >= exp]:
            del self._sheets[t]

    def clear(self) -> None:
        with self._lock:
            self._sheets.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sheets)


# One store per process: the gated router mints into it, the public router serves from it.
SHEETS = SheetLinks()


def sheet_link(token: str) -> str:
    """The sheet's path under the plugin prefix. The view puts its own slug-aware base in front,
    so the link works directly and through the hub's /agents/<slug>/ path."""
    return "/plugins/inventory" + SHEET_PREFIX + "/" + token


def expired_page() -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
        "<title>Label sheet expired</title><style>body{font-family:-apple-system,Helvetica,Arial,sans-serif;"
        "margin:2rem;line-height:1.5;max-width:36rem}</style></head><body>"
        "<h1>This label sheet link has expired.</h1>"
        "<p>Print labels again from Inventory. A sheet link works for 15 minutes.</p></body></html>"
    )
