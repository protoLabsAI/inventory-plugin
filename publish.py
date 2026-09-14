"""The public catalog — what the Nerdsville site shows, built from the inventory through an
allowlist and written into the site checkout ONLY when the operator presses Publish.

The contract with the site (``src/data/catalog.json``)::

    {"version": 1, "generated_at": "<UTC ISO8601 Z>", "items": [
      {"id", "name", "system", "category", "condition", "price_cents", "quantity", "status",
       "blurb", "photos": [{"file", "alt"}], "links": [{"channel", "url"}], "updated"}]}

An item is in it iff it is marked public, is available or listed, has a target price and
a quantity above zero. Nothing else ever leaves: not the cost, the lot, the notes, the
low/high band, the retail anchor, the price basis, the sales or the audit trail — the item
dict is BUILT key by key from :data:`ITEM_KEYS`, never filtered down from a row.

Photos are copied to ``src/assets/catalog/<item_id>/<photo_id>.<ext>`` and that directory is
made an exact mirror (only inside it: every path is checked to resolve under it). The
preview hashes the items; Publish recomputes and refuses (409) when the hash moved, so what
is published is exactly what the operator reviewed. When the site is a git checkout the
two paths are committed (``--only``, so nothing else the operator staged rides along) and
pushed; a push failure is reported, never raised — the files and the commit stay.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .store import InventoryError, InventoryStore, now_iso

log = logging.getLogger("protoagent.plugins.inventory")

CATALOG_REL = "src/data/catalog.json"
ASSETS_REL = "src/assets/catalog"
CATALOG_VERSION = 1
#: The ONLY keys an item carries on the site, in this order.
ITEM_KEYS = (
    "id",
    "name",
    "system",
    "category",
    "condition",
    "price_cents",
    "quantity",
    "status",
    "blurb",
    "photos",
    "links",
    "updated",
)
FOR_SALE = ("available", "listed")
GIT_TIMEOUT = 30
PUSH_TIMEOUT = 90
_URL_RE = re.compile(r"^https?://[^\s]+$", re.I)
_PUBLISH_LOCK = threading.Lock()


class PublishConflict(InventoryError):
    """The inventory moved between the preview and the publish."""


def _text(v) -> str:
    return str(v or "").strip()


def build_catalog(store: InventoryStore) -> tuple[list[dict], list[dict], list[str]]:
    """``(items, skipped, warnings)``: the catalog items in contract order and shape; the
    public items left out and why; photos that are missing on disk (left out, not fatal)."""
    items: list[dict] = []
    skipped: list[dict] = []
    warnings: list[str] = []
    for it in store.publish_source():
        if it["status"] not in FOR_SALE:
            reason = f"not for sale (status {it['status']})"
        elif it["target_cents"] is None:
            reason = "no asking price — set a target"
        elif int(it["quantity"] or 0) <= 0:
            reason = "quantity is 0"
        else:
            reason = ""
        if reason:
            skipped.append({"id": it["id"], "name": it["name"], "reason": reason})
            continue
        photos = []
        for p in it["photos"]:
            rel = f"{it['id']}/{p['id']}.{p['ext']}"
            if not (store.photos_dir / rel).is_file():
                warnings.append(f"{it['id']}: photo {p['id']} is missing on disk — left out")
                continue
            photos.append({"file": rel, "alt": _text(p["alt"])})
        links = [
            {"channel": _text(li["channel"]), "url": _text(li["url"])}
            for li in it["listings"]
            if _URL_RE.match(_text(li["url"]))
        ]
        items.append(
            {
                "id": it["id"],
                "name": _text(it["name"]),
                "system": _text(it["system"]),
                "category": _text(it["category"]),
                "condition": _text(it["condition"]),
                "price_cents": int(it["target_cents"]),
                "quantity": int(it["quantity"]),
                "status": it["status"],
                "blurb": _text(it["blurb"]),
                "photos": photos,
                "links": links,
                "updated": _text(it["updated_at"])[:10],
            }
        )
    items.sort(key=lambda e: (e["system"] == "", e["system"].casefold(), e["name"].casefold(), e["id"]))
    return items, skipped, warnings


def canonical_hash(items: list[dict]) -> str:
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def site_dir_of(cfg: dict) -> Path | None:
    raw = _text(cfg.get("site_dir"))
    return Path(raw).expanduser() if raw else None


def check_site_dir(site: Path | None) -> str:
    """ "" when the site checkout is usable, else what is wrong with it (for the operator)."""
    if site is None:
        return "no site directory set — point Settings ▸ Plugins ▸ Inventory ▸ Site directory at the site checkout"
    if not site.is_dir():
        return f"the site directory {site} does not exist"
    if not (site / "src").is_dir():
        return f"{site} has no src/ folder — is it the site checkout?"
    return ""


def read_published(site: Path | None) -> list[dict] | None:
    """The items of the catalog currently in the site checkout; None when there is none (or
    it can't be read — then everything counts as added)."""
    if site is None:
        return None
    path = site / CATALOG_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else None


def diff(old: list[dict] | None, new: list[dict]) -> dict:
    old_by = {e.get("id"): e for e in (old or []) if isinstance(e, dict)}
    new_by = {e["id"]: e for e in new}
    return {
        "added": [{"id": i, "name": e["name"]} for i, e in new_by.items() if i not in old_by],
        "removed": [{"id": i, "name": e.get("name", "")} for i, e in old_by.items() if i not in new_by],
        "changed": [
            {"id": i, "name": e["name"], "fields": [k for k in ITEM_KEYS if old_by[i].get(k) != e.get(k)]}
            for i, e in new_by.items()
            if i in old_by and old_by[i] != e
        ],
    }


def preview(store: InventoryStore, cfg: dict) -> dict:
    """What Publish would do right now — read-only; safe for the agent to call."""
    items, skipped, warnings = build_catalog(store)
    site = site_dir_of(cfg)
    problem = check_site_dir(site)
    old = read_published(site) if not problem else None
    return {
        "ok": True,
        "site_dir": str(site) if site else "",
        "site_dir_ok": not problem,
        "site_dir_problem": problem,
        "hash": canonical_hash(items),
        "count": len(items),
        "published_count": len(old) if old is not None else 0,
        **diff(old, items),
        "skipped": skipped,
        "warnings": warnings,
        "items_preview": items,
    }


def _write_catalog(site: Path, items: list[dict], old: list[dict] | None) -> bool:
    """Write catalog.json — unless the items are unchanged (a fresh generated_at alone must not
    make a diff). Temp file + rename, so the site never sees half a file."""
    if old is not None and old == items:
        return False
    path = site / CATALOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": CATALOG_VERSION, "generated_at": now_iso(), "items": items}
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True


def _inside(child: Path, root: Path) -> bool:
    return child == root or root in child.parents


def check_targets(site: Path) -> None:
    """Refuse BEFORE writing anything when either destination resolves outside the site
    checkout (a symlinked src/data or src/assets would otherwise carry a write elsewhere).
    ``resolve()`` follows every existing link in the chain, even when the leaf is missing."""
    site_real = site.resolve()
    for rel in (CATALOG_REL, ASSETS_REL):
        real = (site / rel).resolve()
        if not _inside(real, site_real) or real == site_real:
            raise InventoryError(f"{rel} resolves outside the site directory ({real}) — refusing to write there")


def _mirror_photos(store: InventoryStore, site: Path, items: list[dict]) -> tuple[int, int]:
    """Make ``src/assets/catalog`` hold exactly the catalog's photos. Touches nothing outside
    it: the directory itself must resolve inside the site, every write target inside it, and
    the sweep never follows a symlink (a stray link is removed, its target left alone)."""
    root = site / ASSETS_REL
    root.mkdir(parents=True, exist_ok=True)
    site_real, root_real = site.resolve(), root.resolve()
    if not _inside(root_real, site_real) or root_real == site_real:
        raise InventoryError(f"{ASSETS_REL} resolves outside the site directory — refusing to write there")
    expected = {p["file"] for e in items for p in e["photos"]}
    copied = 0
    for rel in sorted(expected):
        src, dst = store.photos_dir / rel, root / rel
        if dst.is_symlink():
            dst.unlink()
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            continue  # photo files are immutable per id; same size = same file
        if dst.parent.is_symlink():
            dst.parent.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not _inside(dst.parent.resolve(), root_real):
            raise InventoryError(f"{rel} resolves outside {ASSETS_REL} — refusing to write there")
        tmp = dst.with_name(f".{dst.name}.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        copied += 1
    deleted = 0
    for dirpath, dirnames, filenames in os.walk(root_real, topdown=False, followlinks=False):
        here = Path(dirpath)
        for name in filenames:
            path = here / name
            if path.relative_to(root_real).as_posix() not in expected:
                path.unlink()
                deleted += 1
        for name in dirnames:
            sub = here / name
            if sub.is_symlink():
                sub.unlink()
                deleted += 1
                continue
            with contextlib.suppress(OSError):
                sub.rmdir()  # only when empty
    return copied, deleted


def _git(site: Path, *args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(site), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # never hang on a credentials prompt
    )


def _tail(proc: subprocess.CompletedProcess) -> str:
    return ((proc.stderr or "") + (proc.stdout or "")).strip()[-400:] or f"git exited {proc.returncode}"


def _is_git_repo(site: Path) -> bool:
    try:
        r = _git(site, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _commit_and_push(site: Path, message: str, *, push: bool) -> dict:
    out = {"commit": None, "pushed": False, "push_error": None, "git_error": None}
    paths = [CATALOG_REL, ASSETS_REL]
    try:
        r = _git(site, "add", "-A", "--", *paths)
        if r.returncode:
            out["git_error"] = _tail(r)
            return out
        staged = _git(site, "diff", "--cached", "--quiet", "--", *paths).returncode == 1
        if staged:
            r = _git(site, "commit", "--only", "-m", message, "--", *paths)
            if r.returncode:
                out["git_error"] = _tail(r)
                return out
            out["commit"] = _git(site, "rev-parse", "--short", "HEAD").stdout.strip() or None
        if not push:
            return out
        ahead = _git(site, "rev-list", "--count", "@{u}..HEAD")
        if not staged and ahead.returncode == 0 and ahead.stdout.strip() == "0":
            return out  # nothing new, nothing unpushed
        try:
            r = _git(site, "push", timeout=PUSH_TIMEOUT)
        except subprocess.TimeoutExpired:
            out["push_error"] = f"git push timed out after {PUSH_TIMEOUT}s"
            return out
        out["pushed"] = r.returncode == 0
        if r.returncode:
            out["push_error"] = _tail(r)
    except (OSError, subprocess.SubprocessError) as exc:
        out["git_error"] = f"git failed: {exc}"
    return out


def publish(store: InventoryStore, cfg: dict, expected_hash: str, *, actor: str = "console") -> dict:
    """Write the reviewed catalog into the site checkout. Operator-only: the agent has a
    preview tool and no publish tool. Raises PublishConflict when the inventory moved since
    the preview (nothing is written), InventoryError when the site directory is unusable."""
    from .tools import as_bool

    site = site_dir_of(cfg)
    problem = check_site_dir(site)
    if problem:
        raise InventoryError(problem)
    with _PUBLISH_LOCK:
        items, _skipped, warnings = build_catalog(store)
        digest = canonical_hash(items)
        if not expected_hash or digest != str(expected_hash):
            raise PublishConflict("inventory changed since the preview — review it again")
        check_targets(site)
        old = read_published(site)
        changes = diff(old, items)
        wrote = _write_catalog(site, items, old)
        copied, deleted = _mirror_photos(store, site, items)
        git = {"commit": None, "pushed": False, "push_error": None, "git_error": None}
        if as_bool(cfg.get("publish_git"), True) and _is_git_repo(site):
            a, r, c = len(changes["added"]), len(changes["removed"]), len(changes["changed"])
            message = f"catalog: publish {len(items)} items (+{a} −{r} ~{c})"
            git = _commit_and_push(site, message, push=as_bool(cfg.get("publish_push"), True))
        store.record_audit(
            "publish",
            digest[:12],
            "publish",
            actor,
            {
                "count": len(items),
                "added": [x["id"] for x in changes["added"]],
                "removed": [x["id"] for x in changes["removed"]],
                "changed": [x["id"] for x in changes["changed"]],
                "site_dir": str(site),
                "wrote_catalog": wrote,
                "photos_copied": copied,
                "photos_deleted": deleted,
                **git,
            },
        )
    if git["push_error"] or git["git_error"]:
        log.warning("[inventory] publish wrote the files but git reported: %s", git["git_error"] or git["push_error"])
    return {
        "ok": True,
        "hash": digest,
        "count": len(items),
        **changes,
        "wrote_catalog": wrote,
        "photos_copied": copied,
        "photos_deleted": deleted,
        "warnings": warnings,
        **git,
    }
