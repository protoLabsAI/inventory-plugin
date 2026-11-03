"""Bin labels: the address a printed QR code opens, the public ``/b/<bin>`` hop, and the
printable sheet.

A label is paper, so the URL it encodes must outlive any change to how the console opens a
view. Every label encodes ``<base>/plugins/inventory/b/<BIN>``; that small public route then
forwards to whatever the host says opens the view (``sdk.view_url``, protoAgent #4712), or,
on an older host, explains that the console needs an update. Only the route moves; the
paper never does.

Host-only imports stay lazy so the suite runs with no protoAgent present.
"""

from __future__ import annotations

import html
import os
import re
from urllib.parse import quote, urlsplit

PLUGIN_ID = "inventory"
LABEL_PREFIX = "/b"  # under the public /plugins/inventory prefix
LABEL_PATH = f"/plugins/{PLUGIN_ID}{LABEL_PREFIX}/"

# The store's own id rule (store._ID_RE), so any bin the store can hold has a label URL. A
# bin id is the only thing the public route ever sees or echoes.
BIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$")
# A fleet member's public URL: {hub}/agents/<id> (the hub's tenant path for that member).
_HUB_TENANT = re.compile(
    r"^(?P<origin>https?://[^/?#]+)(?P<prefix>(?:/[^?#]*)?)/agents/(?P<id>[A-Za-z0-9][A-Za-z0-9_-]{0,99})$"
)
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}


def _sdk():
    try:
        from graph import sdk  # host-only

        return sdk
    except Exception:  # noqa: BLE001 — no host (tests, scripts)
        return None


def norm_bin_id(value) -> str | None:
    """The bin id upper-cased (the store keeps them upper-case), or ``None`` when it can't be one."""
    v = str(value or "").strip().upper()
    return v if BIN_ID_RE.match(v) else None


def _clean_base(value: str) -> str | None:
    """An absolute http(s) URL with no credentials, query or fragment, trailing slash trimmed."""
    v = str(value or "").strip().rstrip("/")
    if not v:
        return None
    try:
        u = urlsplit(v)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
        return None
    return v


def phone_reachable(base: str) -> bool:
    """False for a loopback address: a phone that scans the label can't open it."""
    try:
        host = (urlsplit(base).hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and host not in _LOOPBACK and not host.startswith("127.")


def label_base(cfg: dict) -> dict:
    """Where printed labels point, and how that was decided.

    1. ``label_base_url`` in config: the operator's word, used as given.
    2. ``A2A_PUBLIC_URL``: the agent's public address. On a fleet member that is the hub's
       tenant path ``{hub}/agents/<id>``, which the hub proxies, plugin pages included, so a
       paired phone on the tailnet can open it.
    3. ``sdk.console_url()``: this agent's own console. Often a loopback port, which a phone
       can't reach; ``phone_reachable`` says so.
    """
    raw = str((cfg or {}).get("label_base_url") or "").strip()
    if raw:
        base = _clean_base(raw)
        if base is None:
            return {
                "base": "",
                "source": "config",
                "phone_reachable": False,
                "problem": "Bin label address in Settings isn't a plain http(s) address. Fix it or clear it.",
            }
        return {"base": base, "source": "config", "phone_reachable": phone_reachable(base), "problem": ""}
    base = _clean_base(os.environ.get("A2A_PUBLIC_URL") or "")
    if base:
        return {"base": base, "source": "public_url", "phone_reachable": phone_reachable(base), "problem": ""}
    sdk = _sdk()
    console = getattr(sdk, "console_url", None) if sdk else None
    if console:
        try:
            base = _clean_base(console() or "")
        except Exception:  # noqa: BLE001
            base = None
        if base:
            return {"base": base, "source": "console", "phone_reachable": phone_reachable(base), "problem": ""}
    return {
        "base": "",
        "source": "",
        "phone_reachable": False,
        "problem": "This agent doesn't know its own address. Set Bin label address in Settings.",
    }


def label_url(base: str, bin_id: str) -> str:
    return base.rstrip("/") + LABEL_PATH + quote(bin_id, safe="")


def view_target(bin_id: str) -> str | None:
    """The console link that opens this bin, from a host that has ``sdk.view_url`` (#4712)."""
    sdk = _sdk()
    fn = getattr(sdk, "view_url", None) if sdk else None
    if fn is None:
        return None
    try:
        return fn(PLUGIN_ID, None, {"bin": bin_id}) or None
    except Exception:  # noqa: BLE001 — a refusal means no link, never a broken redirect
        return None


def console_root() -> str:
    """The console's front door, for the older-host page: a fleet member's hub slug window when
    the public URL is a hub tenant path, else this agent's console, else ''."""
    pub = _clean_base(os.environ.get("A2A_PUBLIC_URL") or "")
    if pub:
        m = _HUB_TENANT.match(pub)
        if m:
            return f"{m['origin']}{m['prefix']}/app/agent/{m['id']}/"
    sdk = _sdk()
    console = getattr(sdk, "console_url", None) if sdk else None
    if console:
        try:
            base = _clean_base(console() or "")
        except Exception:  # noqa: BLE001
            base = None
        if base:
            return base + "/app/"
    return ""


def qr_svg(text: str, *, css_class: str = "qr") -> str:
    """One QR code as an inline SVG, quiet zone included. Medium error correction survives a
    crease or a smudge on plain paper."""
    from .vendor.qrcodegen import QrCode

    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n, border = qr.get_size(), 4
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y in range(n) for x in range(n) if qr.get_module(x, y))
    dim = n + 2 * border
    return (
        f'<svg class="{css_class}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR code"><rect width="100%" height="100%" fill="#fff"/>'
        f'<path d="{path}" fill="#000"/></svg>'
    )


_E = html.escape

_PAGE_CSS = """
  @page{size:letter;margin:0.5in}
  *{box-sizing:border-box}
  html,body{margin:0;background:#fff;color:#000;font-family:Helvetica,Arial,sans-serif}
  .bar{display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid #ccc;font-size:14px}
  .bar button{font:inherit;padding:6px 14px;border:1px solid #000;background:#000;color:#fff;border-radius:4px;cursor:pointer}
  .sheet{display:grid;grid-template-columns:repeat(3,2.5in);grid-auto-rows:2.5in;width:7.5in;margin:0.25in auto}
  .label{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0.06in;border:1px dashed #bbb;padding:0.12in;overflow:hidden;break-inside:avoid}
  .qr{width:1.55in;height:1.55in}
  .id{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:28pt;font-weight:700;line-height:1}
  .text{font-size:9pt;text-align:center;max-width:2.2in;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  @media print{.bar{display:none}.sheet{margin:0}.label{border-color:#ddd}}
"""


def sheet_html(bins: list[dict], base: str) -> str:
    """A US Letter page of labels, 3 × 4 to a sheet, black on white whatever the console theme."""
    cells = []
    for b in bins:
        url = label_url(base, b["id"])
        cells.append(
            '<div class="label">'
            + qr_svg(url)
            + f'<div class="id">{_E(b["id"])}</div>'
            + (f'<div class="text">{_E(b.get("label") or "")}</div>' if b.get("label") else "")
            + "</div>"
        )
    count = len(bins)
    pages = -(-count // 12)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bin labels</title>'
        f"<style>{_PAGE_CSS}</style></head><body>"
        '<div class="bar"><button type="button" onclick="window.print()">Print</button>'
        f"<span>{count} label{'s' if count != 1 else ''} on {pages} sheet{'s' if pages != 1 else ''} of US Letter. "
        f"Each opens its bin at {_E(base)}{_E(LABEL_PATH)}&lt;bin&gt;</span></div>"
        f'<div class="sheet">{"".join(cells)}</div></body></html>'
    )


def older_host_page(bin_id: str, root: str) -> str:
    """What a scanned label shows when this console can't open a view from a link yet."""
    link = f'<p><a href="{_E(root, quote=True)}">Open the console</a></p>' if root else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Bin '
        + _E(bin_id)
        + "</title><style>body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:2rem;line-height:1.5}"
        ".id{font-family:ui-monospace,Menlo,monospace;font-size:3rem;font-weight:700;margin:0}</style></head><body>"
        f'<p class="id">{_E(bin_id)}</p>'
        "<p>This label is for bin " + _E(bin_id) + ". This agent's console needs a protoAgent update before a "
        "label can open the bin directly. Until then, open Inventory, go to Bins and tap "
        + _E(bin_id)
        + ".</p>"
        + link
        + "</body></html>"
    )
