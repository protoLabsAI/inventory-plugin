"""The public catalog — what the Nerdsville site shows, built from the inventory through an
allowlist and written into the site checkout ONLY when the operator presses Publish.

The contract with the site (``src/data/catalog.json``)::

    {"version": 1, "generated_at": "<UTC ISO8601 Z>", "items": [
      {"id", "name", "system", "category", "condition", "price_cents", "quantity", "status",
       "blurb", "photos": [{"file", "alt"}], "links": [{"channel", "url"}], "updated"}]}

An item is in it iff it is marked public, is available or listed, has an asking price above
$0 and a quantity above zero. Nothing else ever leaves: not the cost, the lot, the notes, the
low/high band, the retail anchor, the price basis, the sales or the audit trail — the item
dict is BUILT key by key from :data:`ITEM_KEYS`, never filtered down from a row.

Photos are copied to ``src/assets/catalog/<item_id>/<photo_id>.<ext>``. No directory Publish
writes through may be a symbolic link (one pointing inside the checkout is as dangerous as one
pointing out: ``src/assets/catalog -> ../../.git`` would have the sweep empty the repository),
and the sweep deletes only files shaped like its own photos. The preview hashes the items;
Publish recomputes and refuses (409) when the hash moved, so what is published is exactly what
the operator reviewed. When the site is a git checkout the changed files under the two paths
are committed (``--only``, so nothing else the operator staged rides along) and pushed — but
only when every unpushed commit is a Publish commit; anything else is the operator's to push.
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
from urllib.parse import urlsplit

from .store import _ID_RE, InventoryError, InventoryStore, now_iso

log = logging.getLogger("protoagent.plugins.inventory")

CATALOG_REL = "src/data/catalog.json"
ASSETS_REL = "src/assets/catalog"
#: Every directory Publish writes through (and the catalog file): none may be a symlink.
_WRITE_PATHS = ("src", "src/data", "src/assets", ASSETS_REL, CATALOG_REL)
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
_PUBLISH_LOCK = threading.Lock()
#: What the sweep may delete inside src/assets/catalog: its own photos, their temp names from
#: an interrupted run, and the junk Finder/Explorer leave. Anything else is reported, not touched.
_ITEM_ID = _ID_RE.pattern.lstrip("^").rstrip("$")
_OWN_FILE_RE = re.compile(rf"^{_ITEM_ID}/(?:[0-9a-f]{{32}}\.(?:jpg|png|webp)|\.[0-9a-f]{{32}}\.(?:jpg|png|webp)\.tmp)$")
_JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}
_HOST_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*\.?$"
)
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


class PublishConflict(InventoryError):
    """The inventory moved between the preview and the publish."""


def _text(v) -> str:
    return str(v or "").strip()


def public_url(raw) -> str:
    """The listing URL when it is safe to put on a public page (and the site's own check will
    accept it), else "". http(s) only, a plain hostname, no user:password@, no whitespace or
    control characters, and every ``%`` a real escape."""
    url = _text(raw)
    if not url or any(ord(c) <= 32 or ord(c) == 127 for c in url) or _BAD_PERCENT.search(url):
        return ""
    try:
        parts = urlsplit(url)
        _ = parts.port  # a malformed port raises
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or "@" in parts.netloc:
        return ""
    if not parts.hostname or not _HOST_RE.match(parts.hostname):
        return ""
    return url


def build_catalog(
    store: InventoryStore, published: list[dict] | None = None
) -> tuple[list[dict], list[dict], list[str]]:
    """``(items, skipped, warnings)``: the catalog items in contract order and shape; the
    public items left out and why; photos that are missing on disk (left out, not fatal).

    ``published`` is the catalog the site has now: an entry whose every shown field is
    unchanged keeps its published ``updated`` date, so editing a private field (notes, cost)
    is not a change on the site. Preview and Publish both pass it, so their hashes agree."""
    items: list[dict] = []
    skipped: list[dict] = []
    warnings: list[str] = []
    for it in sorted(store.publish_source(), key=lambda r: r["id"]):
        if it["status"] not in FOR_SALE:
            reason = f"not for sale (status {it['status']})"
        elif it["target_cents"] is None:
            reason = "no asking price — set a target"
        elif int(it["target_cents"]) <= 0:
            reason = "asking price must be above $0"
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
        links = []
        for li in it["listings"]:
            channel, url = _text(li["channel"]), public_url(li["url"])
            if channel and url:
                links.append({"channel": channel, "url": url})
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
    # Ids that differ only by letter case share one photo folder on a Mac and one page URL on
    # the site (the store refuses new ones; this covers any that predate that rule).
    kept: list[dict] = []
    seen: dict[str, str] = {}
    for e in items:
        key = e["id"].casefold()
        if key in seen:
            skipped.append(
                {
                    "id": e["id"],
                    "name": e["name"],
                    "reason": f"its id differs from {seen[key]} only by letter case — rename one",
                }
            )
            continue
        seen[key] = e["id"]
        kept.append(e)
    old_by = {e.get("id"): e for e in (published or []) if isinstance(e, dict)}
    for e in kept:
        old = old_by.get(e["id"])
        if old and isinstance(old.get("updated"), str) and all(old.get(k) == e[k] for k in ITEM_KEYS if k != "updated"):
            e["updated"] = old["updated"]
    kept.sort(key=lambda e: (e["system"] == "", e["system"].casefold(), e["name"].casefold(), e["id"]))
    return kept, skipped, warnings


def canonical_hash(items: list[dict]) -> str:
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def site_dir_of(cfg: dict) -> Path | None:
    raw = _text(cfg.get("site_dir"))
    return Path(raw).expanduser() if raw else None


_SITE_MARKERS = ("astro.config.mjs", "astro.config.ts", "astro.config.js", "package.json")


def check_site_dir(site: Path | None) -> str:
    """ "" when the site checkout is usable, else what is wrong with it (for the operator)."""
    if site is None:
        return "no site directory set — point Settings ▸ Plugins ▸ Inventory ▸ Site directory at the site checkout"
    if not site.is_dir():
        return f"the site directory {site} does not exist"
    missing = []
    if not (site / "src").is_dir():
        missing.append("a src/ folder")
    if not any((site / name).is_file() for name in _SITE_MARKERS):
        missing.append("an astro.config.mjs or package.json")
    if missing:
        return f"{site} doesn't look like the site checkout: it is missing {' and '.join(missing)}"
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
    site = site_dir_of(cfg)
    problem = check_site_dir(site)
    old = read_published(site) if not problem else None
    items, skipped, warnings = build_catalog(store, old)
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


def check_targets(site: Path) -> None:
    """Refuse BEFORE writing anything when any path Publish writes through is a symbolic link
    (checked component by component below the site) or resolves anywhere but its own plain
    place in the checkout."""
    site_real = site.resolve()
    for rel in _WRITE_PATHS:
        p = site
        for part in rel.split("/"):
            p = p / part
            if p.is_symlink():
                raise InventoryError(
                    f"{p.relative_to(site).as_posix()} in the site checkout is a symbolic link — "
                    "Publish won't write through links; make it a plain folder"
                )
        if p.exists() and p.resolve() != site_real / rel:
            raise InventoryError(f"{rel} resolves outside its place in the site checkout ({p.resolve()})")


def _copy_photos(store: InventoryStore, root: Path, expected: list[str]) -> int:
    """Copy the catalog's photos in — first, before anything else in the site changes. A source
    that vanished means the inventory moved under the preview: this run's new copies are undone
    and Publish refuses, so nothing is written or deleted."""
    for rel in expected:
        if not (store.photos_dir / rel).is_file():
            raise PublishConflict("a photo changed during publish — review again")
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    copied = 0
    tmp: Path | None = None
    try:
        for rel in expected:
            src, dst = store.photos_dir / rel, root / rel
            if dst.parent.parent != root:
                raise InventoryError(f"{rel} does not map to a folder directly under {ASSETS_REL}")
            if dst.parent.is_symlink():
                dst.parent.unlink()  # a stray link in our own folder: remove the link, never its target
            if dst.is_symlink():
                dst.unlink()
            if dst.is_file() and dst.stat().st_size == src.stat().st_size:
                continue  # photo files are immutable per id; same size = same file
            dst.parent.mkdir(exist_ok=True)
            existed = dst.exists()
            tmp = dst.with_name(f".{dst.name}.tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
            tmp = None
            if not existed:
                created.append(dst)
            copied += 1
    except BaseException as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        for p in created:
            p.unlink(missing_ok=True)
        if isinstance(exc, FileNotFoundError):
            raise PublishConflict("a photo changed during publish — review again") from exc
        if isinstance(exc, OSError):
            raise InventoryError(f"could not copy the photos into the site: {exc}") from exc
        raise
    return copied


def _sweep(root: Path, expected: set[str]) -> tuple[int, list[str]]:
    """Delete photos the catalog no longer lists (and empty folders). Only files shaped like
    Publish's own photos, their temp files and OS junk are deleted; anything else is reported
    and left alone. Never follows a link."""
    deleted, notes = 0, []
    if not root.is_dir():
        return 0, []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        here = Path(dirpath)
        for name in filenames:
            path = here / name
            rel = path.relative_to(root).as_posix()
            if rel in expected:
                continue
            if _OWN_FILE_RE.match(rel) or name in _JUNK or name.startswith("._"):
                path.unlink()
                deleted += 1
            else:
                notes.append(f"{ASSETS_REL}/{rel} isn't a photo Publish manages — left in place")
        for name in dirnames:
            sub = here / name
            if sub.is_symlink():
                notes.append(f"{ASSETS_REL}/{sub.relative_to(root).as_posix()} is a symbolic link — left in place")
                continue
            with contextlib.suppress(OSError):
                sub.rmdir()  # only when empty
    return deleted, notes


def _git(site: Path, *args: str, timeout: int = GIT_TIMEOUT, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(site), *args],
        capture_output=True,
        text=True,
        input=stdin,
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


def _own_path(name: str) -> bool:
    return name == CATALOG_REL or name.startswith(ASSETS_REL + "/")


def _foreign_unpushed(site: Path) -> int:
    """How many unpushed commits touch anything besides the catalog and its photos (merges and
    empty commits count as foreign). Earlier Publish commits whose push failed don't count."""
    r = _git(site, "log", "--format=%x00%H", "--name-only", "@{u}..HEAD")
    if r.returncode:
        return 0
    foreign = 0
    for block in r.stdout.split("\0")[1:]:
        files = [ln for ln in block.splitlines()[1:] if ln.strip()]
        if not files or not all(_own_path(f) for f in files):
            foreign += 1
    return foreign


def _commit_and_push(site: Path, message: str, *, push: bool) -> dict:
    out = {"commit": None, "pushed": False, "push_error": None, "git_error": None}
    try:
        # Measured BEFORE our commit: is there an upstream, and does the branch already carry
        # someone's unpushed work? Publish pushes only its own commits.
        has_upstream = _git(site, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode == 0
        foreign = _foreign_unpushed(site) if has_upstream else 0
        paths = [
            p
            for p in (CATALOG_REL, ASSETS_REL)
            if (site / p).exists() or _git(site, "ls-files", "--", p).stdout.strip()
        ]
        if paths:
            r = _git(site, "add", "-A", "--", *paths)
            if r.returncode:
                out["git_error"] = _tail(r)
                return out
            names = [
                n for n in _git(site, "diff", "--cached", "--name-only", "-z", "--", *paths).stdout.split("\0") if n
            ]
            if names:
                r = _git(
                    site,
                    "commit",
                    "--only",
                    "-m",
                    message,
                    "--pathspec-from-file=-",
                    "--pathspec-file-nul",
                    stdin="\0".join(names) + "\0",
                )
                if r.returncode:
                    out["git_error"] = _tail(r)
                    return out
                out["commit"] = _git(site, "rev-parse", "--short", "HEAD").stdout.strip() or None
        if not push:
            return out
        if not has_upstream:
            if out["commit"]:
                out["push_error"] = (
                    "the site checkout's branch has no upstream — push it yourself once (git push -u origin main); "
                    "later publishes push on their own"
                )
            return out
        if foreign:
            out["push_error"] = (
                f"your site checkout has {foreign} unpushed commit(s) that weren't made by Publish — "
                "review and push them yourself"
            )
            return out
        ahead = _git(site, "rev-list", "--count", "@{u}..HEAD")
        if ahead.returncode != 0 or ahead.stdout.strip() == "0":
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
    the preview (nothing is written), InventoryError when the site directory is unusable.
    Order: check every target, copy photos in, write catalog.json, sweep old photos, git."""
    from .tools import as_bool

    site = site_dir_of(cfg)
    problem = check_site_dir(site)
    if problem:
        raise InventoryError(problem)
    with _PUBLISH_LOCK:
        old = read_published(site)
        items, _skipped, warnings = build_catalog(store, old)
        digest = canonical_hash(items)
        if not expected_hash or digest != str(expected_hash):
            raise PublishConflict("inventory changed since the preview — review it again")
        check_targets(site)
        changes = diff(old, items)
        root = site / ASSETS_REL
        expected = sorted({p["file"] for e in items for p in e["photos"]})
        copied = _copy_photos(store, root, expected)
        wrote = _write_catalog(site, items, old)
        deleted, notes = _sweep(root, set(expected))
        warnings = [*warnings, *notes]
        git = {"commit": None, "pushed": False, "push_error": None, "git_error": None}
        if as_bool(cfg.get("publish_git"), True):
            if shutil.which("git") is None:
                git["git_error"] = "git is not installed or not on PATH — files were written but not committed"
            elif not _is_git_repo(site):
                git["git_error"] = "the site directory isn't a git checkout — files were written but not committed"
            else:
                n = len(items)
                a, r, c = len(changes["added"]), len(changes["removed"]), len(changes["changed"])
                message = f"catalog: publish {n} item{'' if n == 1 else 's'} (+{a} −{r} ~{c})"
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
