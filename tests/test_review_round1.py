"""Regressions for PR #9's first adversarial review: each test reproduces a finding (the
reviewers' scripts, made permanent) and pins the fix."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import struct
import subprocess
import time

import inventory_plugin
import pytest
from inventory_plugin import photos as ph
from inventory_plugin import publish as pub
from inventory_plugin.store import InventoryError, InventoryStore

from tests.conftest import FakeRegistry
from tests.test_photos import FRAME, SCAN, jpeg, png_chunk, png_types, riff_chunk, seg, webp_chunks

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A site checkout with an upstream, like ~/dev/nerdsville-site: no src/assets/catalog tracked."""
    remote, site = tmp_path / "remote.git", tmp_path / "gitsite"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", str(site)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(site, "config", k, v)
    (site / "src" / "pages").mkdir(parents=True)
    (site / "src" / "pages" / "index.astro").write_text("home")
    (site / "package.json").write_text("{}")
    _git(site, "add", "-A")
    _git(site, "commit", "-qm", "init")
    _git(site, "branch", "-M", "main")
    _git(site, "remote", "add", "origin", str(remote))
    _git(site, "push", "-q", "-u", "origin", "main")
    return site, remote


def _item(store, iid, target=10, photo=False, **kw):
    store.upsert_item({"id": iid, "name": iid, "public": True, "notes": "SECRET", **kw}, actor="t")
    if target is not None:
        store.set_price(iid, target=target, basis="b", actor="t")
    return store.add_photo(iid, jpeg(), actor="t")["id"] if photo else None


def _publish(store, cfg):
    return pub.publish(store, cfg, pub.preview(store, cfg)["hash"])


def _count_files(path):
    return sum(len(files) for _, _, files in os.walk(path))


# ── 1. a publish with no photos commits ────────────────────────────────────────
@needs_git
def test_a_first_publish_without_any_photo_commits_and_so_does_removing_the_last_photo(store, repo):
    site, remote = repo
    _item(store, "A")
    out = _publish(store, {"site_dir": str(site)})
    assert out["git_error"] is None and out["commit"] and out["pushed"] is True, out
    assert _git(site, "log", "-1", "--format=%s") == "catalog: publish 1 item (+1 −0 ~0)"
    assert _git(site, "status", "--porcelain") == ""  # nothing left staged or dangling
    pid = store.add_photo("A", jpeg(), actor="t")["id"]
    assert _publish(store, {"site_dir": str(site)})["commit"]
    assert _git(site, "ls-files", "src/assets/catalog") == f"src/assets/catalog/A/{pid}.jpg"
    store.delete_photo("A", pid, actor="t")
    out = _publish(store, {"site_dir": str(site)})
    assert out["git_error"] is None and out["commit"] and out["pushed"] is True
    assert _git(site, "ls-files", "src/assets/catalog") == ""
    assert _git(remote, "rev-parse", "main") == _git(site, "rev-parse", "HEAD")


# ── 2. no writing through symlinks; the sweep deletes only its own photos ──────
@needs_git
@pytest.mark.parametrize(
    ("link", "target"),
    [("src/assets/catalog", "../../.git"), ("src/assets/catalog", ".."), ("src/data", "../.git/hooks")],
)
def test_a_symlink_inside_the_site_is_refused_and_nothing_is_touched(store, repo, link, target):
    site, _ = repo
    _item(store, "P1", photo=True)
    (site / link).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, site / link)
    git_files, src_files = _count_files(site / ".git"), _count_files(site / "src")
    with pytest.raises(InventoryError, match="symbolic link"):
        _publish(store, {"site_dir": str(site)})
    assert _count_files(site / ".git") == git_files and _count_files(site / "src") == src_files
    assert not (site / ".git" / "hooks" / "catalog.json").exists()
    assert subprocess.run(["git", "-C", str(site), "status"], capture_output=True).returncode == 0


def test_the_sweep_deletes_only_its_own_photo_files(store, tmp_path):
    site = tmp_path / "site"
    (site / "src").mkdir(parents=True)
    (site / "package.json").write_text("{}")
    root = site / "src/assets/catalog"
    (root / "OLD").mkdir(parents=True)
    old_photo = root / "OLD" / ("a" * 32 + ".jpg")
    old_photo.write_bytes(b"x")
    (root / "OLD" / (".%s.png.tmp" % ("b" * 32))).write_bytes(b"x")
    (root / ".DS_Store").write_bytes(b"x")
    (root / "notes.txt").write_text("mine")
    (root / "OLD" / "cover.jpg").write_bytes(b"not ours")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.jpg").write_bytes(b"x")
    os.symlink(outside, root / "linked")
    pid = _item(store, "P1", photo=True)
    out = _publish(store, {"site_dir": str(site), "publish_git": False})
    left = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink())
    assert left == sorted([f"P1/{pid}.jpg", "OLD/cover.jpg", "notes.txt", "linked"])
    assert out["photos_deleted"] == 3 and (outside / "keep.jpg").exists()
    assert sum("left in place" in w for w in out["warnings"]) == 3


def test_a_photo_that_vanishes_during_publish_is_a_conflict_and_nothing_changes(store, tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "src").mkdir(parents=True)
    (site / "package.json").write_text("{}")
    _item(store, "P1", photo=True)
    cfg = {"site_dir": str(site), "publish_git": False}
    h = pub.preview(store, cfg)["hash"]
    real = pub.build_catalog

    def racy(s, published=None):
        items, skipped, warnings = real(s, published)
        for e in items:
            for p in e["photos"]:
                os.unlink(s.photos_dir / p["file"])
        return items, skipped, warnings

    monkeypatch.setattr(pub, "build_catalog", racy)
    with pytest.raises(pub.PublishConflict, match="photo changed during publish"):
        pub.publish(store, cfg, h)
    assert not (site / "src/data/catalog.json").exists()
    assert not any(p.is_file() for p in (site / "src").rglob("*"))


# ── 3. ids that differ only by case ────────────────────────────────────────────
def test_the_store_refuses_an_id_that_differs_only_by_case(store):
    store.upsert_item({"id": "ABC", "name": "a"}, actor="t")
    with pytest.raises(InventoryError, match="only by letter case"):
        store.upsert_item({"id": "abc", "name": "b"}, actor="t")
    store.upsert_item({"id": "ABC", "name": "renamed"}, actor="t")  # updating the item itself is fine


def test_a_legacy_case_twin_is_left_out_of_the_catalog(store):
    _item(store, "ABC")
    with sqlite3.connect(store.path) as con:  # a pair that predates the rule
        con.execute(
            "INSERT INTO items(id, name, public, status, quantity, target_cents, created_at, updated_at) "
            "VALUES ('abc', 'twin', 1, 'available', 1, 1000, '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z')"
        )
    items, skipped, _ = pub.build_catalog(store)
    assert [e["id"] for e in items] == ["ABC"]
    assert skipped == [
        {"id": "abc", "name": "twin", "reason": "its id differs from ABC only by letter case — rename one"}
    ]


# ── 4. push only Publish's own commits ─────────────────────────────────────────
@needs_git
def test_someone_elses_unpushed_commit_blocks_the_push_but_not_the_commit(store, repo):
    site, remote = repo
    (site / "src/pages/draft.astro").write_text("private draft")
    _git(site, "add", "src/pages/draft.astro")
    _git(site, "commit", "-qm", "PRIVATE unpushed local commit")
    _item(store, "A")
    out = _publish(store, {"site_dir": str(site)})
    assert out["commit"] and out["pushed"] is False
    assert "1 unpushed commit(s) that weren't made by Publish" in out["push_error"]
    assert "PRIVATE" not in _git(remote, "log", "--format=%s", "main")


@needs_git
def test_an_earlier_publish_whose_push_failed_is_pushed_next_time(store, repo, tmp_path):
    site, remote = repo
    _item(store, "A")
    moved = tmp_path / "moved.git"
    remote.rename(moved)
    assert _publish(store, {"site_dir": str(site)})["push_error"]
    moved.rename(remote)
    store.set_price("A", target=11, basis="b", actor="t")
    out = _publish(store, {"site_dir": str(site)})
    assert out["pushed"] is True and out["push_error"] is None
    assert _git(remote, "rev-parse", "main") == _git(site, "rev-parse", "HEAD")


@needs_git
def test_a_branch_without_an_upstream_commits_and_says_so(store, repo):
    site, _ = repo
    _git(site, "checkout", "-q", "-b", "local-only")
    _item(store, "A")
    out = _publish(store, {"site_dir": str(site)})
    assert out["commit"] and out["pushed"] is False and "no upstream" in out["push_error"]


# ── 5. the operator sees what they approve; the photo tool needs a workspace ───
def test_the_photo_tool_refuses_without_a_workspace(tmp_path):
    reg = FakeRegistry({"db_path": str(tmp_path / "inv.db")})
    inventory_plugin.register(reg)
    tools = {t.name: t for t in reg.tools}
    tools["inventory_upsert_item"].invoke({"id": "A", "name": "A"})
    (tmp_path / "p.jpg").write_bytes(jpeg())
    out = json.loads(tools["inventory_add_photo"].invoke({"item_id": "A", "path": str(tmp_path / "p.jpg")}))
    assert out["ok"] is False and "no agent workspace" in out["error"]


def test_the_publish_preview_shows_blurb_price_condition_and_photos():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    import re

    from inventory_plugin.view import PAGE

    module = re.search(r'<script type="module">(.*?)</script>', PAGE, re.S).group(1)
    harness = (
        "globalThis.BASE = ''; globalThis.location = { pathname: '/plugins/inventory/view' };\n"
        "const el = () => new Proxy({}, { get: (t, k) => (k === 'addEventListener' || k === 'removeEventListener' || k === 'appendChild' || k === 'remove' || k === 'focus' ? () => {} : k === 'querySelectorAll' ? () => [] : k === 'querySelector' || k === 'closest' ? () => el() : k === 'classList' ? { toggle() {}, add() {}, remove() {} } : k === 'dataset' ? {} : k === 'hidden' ? true : ''), set: () => true });\n"
        "globalThis.document = { querySelector: () => el(), querySelectorAll: () => [], getElementById: () => el(), createElement: () => el(), addEventListener() {}, removeEventListener() {}, body: el() };\n"
        "globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => 0; globalThis.fetch = async () => ({ ok: false, status: 0, statusText: 'stub', text: async () => '' });\n"
    )
    hexes = [f"{k:032x}" for k in range(8)]
    entry = {
        "id": "A",
        "name": "Griff",
        "price_cents": 2650,
        "condition": "NoS",
        "quantity": 2,
        "blurb": "<b>Sealed</b>\nline two",
        "photos": [{"file": f"A/{h}.jpg", "alt": '"><x'} for h in hexes],
        "links": [{"channel": "eBay", "url": "https://x.test"}],
    }
    plain = dict(entry, id="B", name="Plain", price_cents=2000, blurb="", photos=[], links=[], quantity=1, condition="")
    probe = (
        "\nconsole.log(JSON.stringify(previewHtml({ site_dir_ok: true, count: 2, site_dir: '/s', added: [{ id: 'A', name: 'Griff' }], removed: [],"
        " changed: [{ id: 'B', name: 'Plain', fields: ['price_cents'] }], skipped: [], warnings: [], items_preview: "
        + json.dumps([entry, plain])
        + " })));\n"
    )
    r = subprocess.run(
        [node, "--input-type=module"], input=harness + module + probe, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr[:1200]
    pv = json.loads(r.stdout.strip().splitlines()[-1])
    assert "$26.50" in pv and "$20" in pv and "NoS" in pv and "×2" in pv and "Buy links: eBay" in pv
    assert "&lt;b&gt;Sealed&lt;/b&gt;" in pv and "<b>Sealed" not in pv  # the blurb is shown, escaped
    assert pv.count("data-thumb-photo=") == 6 and "+2 more" in pv  # thumbnails capped, the rest counted
    assert f'data-thumb-item="A" data-thumb-photo="{hexes[0]}"' in pv
    assert "<img" not in pv and "No blurb." in pv and "No photos." in pv


# ── 6. the sanitizers keep only what a decoder needs ───────────────────────────
@pytest.mark.parametrize("marker", [0xF7, 0xF1, 0xFD, 0xC8, 0xDE, 0xDF])
def test_a_jpeg_with_an_unusual_marker_is_refused(marker):
    src = b"".join(
        [b"\xff\xd8", seg(marker, b"LEAK-sentinel"), *FRAME, seg(0xDA, b"\x01\x01\x00\x00\x3f\x00"), SCAN, b"\xff\xd9"]
    )
    with pytest.raises(ph.PhotoError, match="unusual encoding"):
        ph.sanitize_jpeg(src)


def test_jfif_and_its_thumbnail_are_dropped_but_colour_markers_stay():
    jfif_thumb = (
        b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x02\x02" + b"THUMB-sentinel" + bytes(12 - len(b"THUMB-sentinel") % 3)
    )
    adobe = b"Adobe\x00\x64\x00\x00\x00\x00\x01"  # the standard 12 bytes
    long_adobe = adobe + b"ADOBE-sentinel"
    src = b"".join(
        [
            b"\xff\xd8",
            seg(0xE0, jfif_thumb),
            seg(0xEE, adobe),
            seg(0xEE, long_adobe),
            seg(0xE2, b"ICC_PROFILE\x00\x01\x01icc"),
            *FRAME,
            seg(0xDA, b"\x01\x01\x00\x00\x3f\x00"),
            SCAN,
            b"\xff\xd9",
        ]
    )
    out = ph.sanitize_jpeg(src)
    assert b"JFIF" not in out and b"THUMB" not in out and b"ADOBE-sentinel" not in out
    assert out.count(b"Adobe") == 1 and b"ICC_PROFILE" in out
    assert [m for m, _ in ph.header_segments(out)] == [0xEE, 0xE2, 0xDB, 0xC0, 0xC4]


def test_pathological_jpegs_sanitize_quickly():
    fill = b"\xff" * (6 * 1024 * 1024)  # a run of fill bytes before a marker
    stuffed = b"\xff\x00" * (3 * 1024 * 1024)  # an entropy segment that is all stuffed bytes
    src = b"".join([b"\xff\xd8", *FRAME, fill, seg(0xDA, b"\x01\x01\x00\x00\x3f\x00"), stuffed, b"\xff\xd9"])
    t = time.perf_counter()
    out = ph.sanitize_jpeg(src)
    assert time.perf_counter() - t < 2.0
    assert out.endswith(b"\xff\xd9") and stuffed in out


def test_png_drops_splt_and_renames_the_icc_profile():
    import zlib

    body = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    src = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", body)
        + png_chunk(b"iCCP", b"Josh's MacBook Pro\x00\x00" + zlib.compress(b"icc"))
        + png_chunk(b"sPLT", b"HOME-sentinel\x00\x08")
        + png_chunk(b"cICP", b"\x01\x0d\x00\x01")
        + png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + png_chunk(b"IEND", b"")
    )
    out = ph.sanitize_png(src)
    assert png_types(out) == [b"IHDR", b"iCCP", b"IDAT", b"IEND"]  # CRCs checked by png_types
    assert b"MacBook" not in out and b"HOME-sentinel" not in out and b"ICC profile\x00" in out


@pytest.mark.parametrize("kind", ["chunk", "flag"])
def test_animated_webp_is_refused(kind):
    if kind == "chunk":
        body = (
            b"WEBP"
            + riff_chunk(b"VP8X", bytes([0x02]) + bytes(9))
            + riff_chunk(b"ANIM", bytes(6))
            + riff_chunk(b"ANMF", bytes(16) + riff_chunk(b"EXIF", b"GPS"))
        )
    else:
        body = b"WEBP" + riff_chunk(b"VP8X", bytes([0x02]) + bytes(9)) + riff_chunk(b"VP8 ", b"abc")
    with pytest.raises(ph.PhotoError, match="animated WebP"):
        ph.sanitize_webp(b"RIFF" + struct.pack("<I", len(body)) + body)
    still = b"WEBP" + riff_chunk(b"VP8 ", b"abcd")
    assert [c for c, _ in webp_chunks(ph.sanitize_webp(b"RIFF" + struct.pack("<I", len(still)) + still))] == [b"VP8 "]


def test_a_failed_temp_write_leaves_no_file(store, monkeypatch):
    from pathlib import Path

    store.upsert_item({"id": "A", "name": "A"}, actor="t")
    real = Path.write_bytes

    def half_written(self, data):
        real(self, data[:10])
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", half_written)
    with pytest.raises(OSError, match="disk full"):
        store.add_photo("A", jpeg(), actor="t")
    monkeypatch.setattr(Path, "write_bytes", real)
    assert not store.photos_dir.exists() or not any(store.photos_dir.rglob("*.tmp"))
    assert store.list_photos("A") == []


# ── 7. the snapshot: prices above $0, links the site will accept ───────────────
def test_zero_and_negative_asking_prices_are_left_out(store):
    _item(store, "ZERO", target=0)
    _item(store, "OK", target=1)
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE items SET target_cents=-500 WHERE id='OK'")
    items, skipped, _ = pub.build_catalog(store)
    assert items == []
    assert {s["id"]: s["reason"] for s in skipped} == {
        "ZERO": "asking price must be above $0",
        "OK": "asking price must be above $0",
    }


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://www.ebay.com/itm/123?hash=item1", True),
        ("http://etsy.com/listing/9", True),
        ("https://user:pw@ebay.com/itm/1", False),
        ("https://ebay.com@evil.test/x", False),
        ("https://%", False),
        ("https://ebay.com/%zz", False),
        ("https://eb ay.com/x", False),
        ("https://ebay.com/x\ny", False),
        ("https://ebay.com:99999/x", False),
        ("https://a<b>.test/", False),
        ("ftp://ebay.com/x", False),
        ("javascript:alert(1)", False),
        ("", False),
    ],
)
def test_public_url(url, ok):
    assert bool(pub.public_url(url)) is ok


def test_links_with_a_blank_channel_or_a_bad_url_are_dropped(store):
    _item(store, "A")
    store.add_listing("A", channel="   ", url="https://www.ebay.com/itm/1", actor="t")
    store.add_listing("A", channel="eBay", url="https://user:pw@www.ebay.com/itm/2?token=x", actor="t")
    store.add_listing("A", channel="Etsy", url="https://www.etsy.com/listing/3", actor="t")
    (entry,), _, _ = pub.build_catalog(store)
    assert entry["links"] == [{"channel": "Etsy", "url": "https://www.etsy.com/listing/3"}]


def test_a_published_column_in_a_sheet_is_not_the_public_flag(store):
    from inventory_plugin.csvio import import_csv

    out = import_csv(store, "inventory_id,item,published\nA,Team A,yes\n", actor="t")
    assert out["ok"] and store.get_item("A")["public"] is False


# ── 8. private edits aren't site changes ───────────────────────────────────────
def test_editing_only_private_fields_is_not_a_change_on_the_site(store, tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "src").mkdir(parents=True)
    (site / "package.json").write_text("{}")
    cfg = {"site_dir": str(site), "publish_git": False}
    _item(store, "A", photo=True)
    _publish(store, cfg)
    published = json.loads((site / "src/data/catalog.json").read_text())["items"][0]["updated"]
    with sqlite3.connect(store.path) as con:  # make "today" visibly different from the published date
        con.execute("UPDATE items SET updated_at='2030-01-01T00:00:00Z'")
    store.upsert_item({"id": "A", "notes": "new private note", "cost_basis": 3}, actor="t")
    p = pub.preview(store, cfg)
    assert (p["added"], p["removed"], p["changed"]) == ([], [], [])
    assert p["items_preview"][0]["updated"] == published
    out = pub.publish(store, cfg, p["hash"])
    assert out["wrote_catalog"] is False
    store.upsert_item({"id": "A", "blurb": "Now with a blurb"}, actor="t")  # a shown field: a real change
    (changed,) = pub.preview(store, cfg)["changed"]
    assert "blurb" in changed["fields"]  # (updated moves too unless it is still the published day)


# ── 9 + 10. the site dir must look like the site; git must be there ─────────────
def test_a_site_dir_must_have_src_and_a_site_config(store, tmp_path):
    (tmp_path / "home" / "src").mkdir(parents=True)
    p = pub.preview(store, {"site_dir": str(tmp_path / "home")})
    assert not p["site_dir_ok"] and "missing an astro.config.mjs or package.json" in p["site_dir_problem"]
    (tmp_path / "home" / "astro.config.mjs").write_text("")
    assert pub.preview(store, {"site_dir": str(tmp_path / "home")})["site_dir_ok"] is True


def test_no_git_on_path_is_reported(store, tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "src").mkdir(parents=True)
    (site / "package.json").write_text("{}")
    _item(store, "A")
    monkeypatch.setattr(pub.shutil, "which", lambda name: None)
    out = _publish(store, {"site_dir": str(site)})
    assert out["git_error"] == "git is not installed or not on PATH — files were written but not committed"
    assert (site / "src/data/catalog.json").exists()


def test_a_site_dir_that_is_not_a_git_checkout_says_so(store, tmp_path):
    site = tmp_path / "site"
    (site / "src").mkdir(parents=True)
    (site / "package.json").write_text("{}")
    _item(store, "A")
    out = _publish(store, {"site_dir": str(site)})
    assert "isn't a git checkout" in out["git_error"] and out["commit"] is None


def test_store_path_is_the_db(tmp_path):
    s = InventoryStore(tmp_path / "x" / "inventory.db")
    assert s.photos_dir == tmp_path / "x" / "photos"
