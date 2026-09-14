"""The public catalog: the allowlist, membership and order, the preview → publish handshake,
the photo mirror (and its containment), git, and the same through the API, the agent tools,
CSV and the view."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import inventory_plugin
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inventory_plugin import photos as ph
from inventory_plugin import publish as pub
from inventory_plugin.store import InventoryError

from tests.conftest import FakeRegistry
from tests.test_photos import jpeg, png_file

API = "/api/plugins/inventory"
SECRET_TEXT = ("SECRET-NOTE", "SECRET-BASIS", "SECRET-LOT", "Estate sale")
#: cents that must never appear: cost basis, lot cost, P1's low/high band and retail.
SECRET_CENTS = {1234, 9999, 2000, 4000, 5000}


@pytest.fixture
def shop(store):
    s = store
    s.upsert_lot({"id": "SECRET-LOT", "name": "Estate sale", "acquisition_cost": 99.99}, actor="t")

    def item(iid, target=None, **kw):
        base = {"id": iid, "lot_id": "SECRET-LOT", "notes": "SECRET-NOTE", "cost_basis": 12.34, "retail": 50}
        s.upsert_item({**base, "public": True, **kw}, actor="t")
        if target is not None:
            s.set_price(iid, target=target, basis="SECRET-BASIS", actor="t")

    item("P1", name="Griff Oberwald", system="Blood Bowl", category="Star Player", condition="NIB", blurb=" Sealed. ")
    s.set_price("P1", low=20, target=26, high=40, basis="SECRET-BASIS", actor="t")
    s.cover = s.add_photo("P1", jpeg(), alt="front", actor="t")["id"]
    s.second = s.add_photo("P1", png_file(), actor="t")["id"]
    s.add_listing("P1", channel="eBay", url="https://www.ebay.com/itm/1", price=26, actor="t")
    s.add_listing("P1", channel="local", url="", actor="t")
    s.add_listing("P1", channel="bad", url="javascript:alert(1)", actor="t")
    old = s.add_listing("P1", channel="Etsy", url="https://etsy.com/listing/9", actor="t")
    s.end_listing(old["id"], actor="t")
    item("P2", 15, name="Chaos Dice", quantity=2)
    item("P3", 30, name="Planned split", system="Blood Bowl", status="planned")
    item("P4", None, name="Unpriced", system="Blood Bowl")
    item("P5", 20, name="Private thing", system="Blood Bowl", public=False)
    item("P6", 20, name="None left", system="Blood Bowl", quantity=0)
    item("P7", 35, name="Amazon Warriors", system="Blood Bowl")
    item("P8", 20, name="Withdrawn", system="Blood Bowl", status="withdrawn")
    item("P9", 72, name="Hierotek Circle", system="Kill Team", condition="NIB")
    return s


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    (root / "src").mkdir(parents=True)
    return root


def _cfg(site, **kw):
    return {"site_dir": str(site), "publish_git": False, **kw}


def _publish(store, cfg):
    return pub.publish(store, cfg, pub.preview(store, cfg)["hash"])


def _scalars(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _scalars(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _scalars(v)
    else:
        yield obj


# ── the catalog ────────────────────────────────────────────────────────────────
def test_the_catalog_carries_exactly_the_contract_keys_and_nothing_private(shop):
    items, _, _ = pub.build_catalog(shop)
    for e in items:
        assert tuple(e) == pub.ITEM_KEYS
        assert all(list(p) == ["file", "alt"] for p in e["photos"])
        assert all(list(li) == ["channel", "url"] for li in e["links"])
    text = json.dumps(items)
    for secret in SECRET_TEXT:
        assert secret not in text, secret
    assert not SECRET_CENTS & {v for v in _scalars(items) if isinstance(v, int)}


def test_membership_order_and_the_reasons_for_leaving_items_out(shop):
    items, skipped, warnings = pub.build_catalog(shop)
    assert [e["id"] for e in items] == ["P7", "P1", "P9", "P2"]  # system (blank last), then name
    assert {s["id"]: s["reason"] for s in skipped} == {
        "P3": "not for sale (status planned)",
        "P4": "no asking price — set a target",
        "P6": "quantity is 0",
        "P8": "not for sale (status withdrawn)",
    }
    assert warnings == []  # P5 is private: neither in the catalog nor reported


def test_an_entry_has_the_asking_price_live_links_and_photos_cover_first(shop):
    p1 = next(e for e in pub.build_catalog(shop)[0] if e["id"] == "P1")
    assert (p1["price_cents"], p1["status"], p1["condition"], p1["blurb"]) == (2600, "listed", "NIB", "Sealed.")
    assert p1["links"] == [{"channel": "eBay", "url": "https://www.ebay.com/itm/1"}]
    assert p1["photos"] == [
        {"file": f"P1/{shop.cover}.jpg", "alt": "front"},
        {"file": f"P1/{shop.second}.png", "alt": ""},
    ]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", p1["updated"])


def test_a_photo_missing_on_disk_is_left_out_with_a_warning(shop):
    (shop.photos_dir / "P1" / f"{shop.second}.png").unlink()
    items, _, warnings = pub.build_catalog(shop)
    assert [p["file"] for p in next(e for e in items if e["id"] == "P1")["photos"]] == [f"P1/{shop.cover}.jpg"]
    assert len(warnings) == 1 and shop.second in warnings[0]


def test_the_preview_without_a_site_dir_is_read_only_and_says_why(shop):
    p = pub.preview(shop, {})
    assert p["site_dir_ok"] is False and "no site directory" in p["site_dir_problem"]
    assert [a["id"] for a in p["added"]] == ["P7", "P1", "P9", "P2"] and p["count"] == 4
    assert p["hash"] == pub.canonical_hash(p["items_preview"])
    with pytest.raises(InventoryError, match="no site directory"):
        pub.publish(shop, {}, p["hash"])


# ── publishing ─────────────────────────────────────────────────────────────────
def test_publish_writes_the_contract_file_and_mirrors_the_photos(shop, site):
    cfg = _cfg(site)
    p = pub.preview(shop, cfg)
    out = pub.publish(shop, cfg, p["hash"])
    text = (site / "src/data/catalog.json").read_text()
    doc = json.loads(text)
    assert text == json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    assert list(doc) == ["version", "generated_at", "items"] and doc["version"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc["generated_at"])
    assert doc["items"] == p["items_preview"]
    for photo in doc["items"][1]["photos"]:
        assert (site / "src/assets/catalog" / photo["file"]).read_bytes() == (
            shop.photos_dir / photo["file"]
        ).read_bytes()
    stored = (site / "src/assets/catalog" / f"P1/{shop.cover}.jpg").read_bytes()
    assert 0x8825 not in ph.exif_ifd0_tags(stored)  # what reaches the site has no GPS
    assert (out["count"], out["photos_copied"], out["photos_deleted"], out["commit"]) == (4, 2, 0, None)
    assert [a["id"] for a in out["added"]] == ["P7", "P1", "P9", "P2"]
    audit = shop.audit_log(limit=1)[0]
    assert (audit["entity"], audit["action"], audit["changes"]["count"]) == ("publish", "publish", 4)


def test_republishing_an_unchanged_catalog_does_not_touch_the_file(shop, site):
    cfg = _cfg(site)
    _publish(shop, cfg)
    before = (site / "src/data/catalog.json").read_bytes()
    out = _publish(shop, cfg)
    assert out["wrote_catalog"] is False and out["photos_copied"] == 0
    assert (site / "src/data/catalog.json").read_bytes() == before  # generated_at alone is not a change


def test_a_stale_or_missing_hash_writes_nothing(shop, site):
    cfg = _cfg(site)
    p = pub.preview(shop, cfg)
    shop.set_price("P2", target=16, basis="b", actor="t")
    with pytest.raises(pub.PublishConflict, match="changed since the preview"):
        pub.publish(shop, cfg, p["hash"])
    with pytest.raises(pub.PublishConflict):
        pub.publish(shop, cfg, "")
    assert not (site / "src/data").exists() and not (site / "src/assets").exists()


def test_the_diff_is_against_what_the_site_has(shop, site):
    cfg = _cfg(site)
    _publish(shop, cfg)
    shop.upsert_item({"id": "P2", "public": False}, actor="t")
    shop.set_price("P1", low=20, target=27, high=40, basis="b", actor="t")
    shop.delete_photo("P1", shop.second, actor="t")
    p = pub.preview(shop, cfg)
    assert p["added"] == [] and [r["id"] for r in p["removed"]] == ["P2"]
    (changed,) = p["changed"]
    assert changed["id"] == "P1" and {"price_cents", "photos"} <= set(changed["fields"])
    out = pub.publish(shop, cfg, p["hash"])
    assert out["photos_deleted"] == 1
    assert not (site / "src/assets/catalog" / f"P1/{shop.second}.png").exists()


def test_the_mirror_only_ever_touches_its_own_folder(shop, site, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    root = site / "src/assets/catalog"
    (root / "STRAY").mkdir(parents=True)
    (root / "STRAY" / "x.jpg").write_bytes(b"x")
    (root / "loose.txt").write_text("x")
    os.symlink(outside, root / "link.txt")
    os.symlink(tmp_path, root / "dirlink")
    (site / "src" / "keep.txt").write_text("mine")
    _publish(shop, _cfg(site))
    assert not (root / "STRAY").exists() and not (root / "loose.txt").exists()
    assert not os.path.lexists(root / "link.txt") and not os.path.lexists(root / "dirlink")
    assert outside.read_text() == "keep" and tmp_path.is_dir()
    assert (site / "src" / "keep.txt").read_text() == "mine"
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert files == sorted([f"P1/{shop.cover}.jpg", f"P1/{shop.second}.png"])


@pytest.mark.parametrize("linked", ["src/assets", "src/data"])
def test_a_destination_that_escapes_the_site_is_refused_before_any_write(shop, site, tmp_path, linked):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_text("x")
    os.symlink(elsewhere, site / linked)
    with pytest.raises(InventoryError, match="outside the site"):
        _publish(shop, _cfg(site))
    assert sorted(p.name for p in elsewhere.iterdir()) == ["precious.txt"]
    assert not (site / "src/data/catalog.json").exists()


def test_a_site_dir_without_src_is_refused(shop, tmp_path):
    (tmp_path / "home").mkdir()
    p = pub.preview(shop, {"site_dir": str(tmp_path / "home")})
    assert not p["site_dir_ok"] and "no src/" in p["site_dir_problem"]


# ── git ────────────────────────────────────────────────────────────────────────
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote, site = tmp_path / "remote.git", tmp_path / "gitsite"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", str(site)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(site, "config", k, v)
    (site / "src").mkdir()
    (site / "src" / "index.astro").write_text("x")
    _git(site, "add", "-A")
    _git(site, "commit", "-qm", "init")
    _git(site, "branch", "-M", "main")
    _git(site, "remote", "add", "origin", str(remote))
    _git(site, "push", "-q", "-u", "origin", "main")
    return site, remote


@needs_git
def test_publish_commits_only_its_two_paths_and_pushes(shop, repo):
    site, remote = repo
    (site / "unrelated.txt").write_text("work in progress")
    _git(site, "add", "unrelated.txt")
    cfg = {"site_dir": str(site)}
    out = _publish(shop, cfg)
    assert out["commit"] and out["pushed"] is True and out["push_error"] is None and out["git_error"] is None
    assert _git(site, "log", "-1", "--format=%s") == "catalog: publish 4 items (+4 −0 ~0)"
    committed = _git(site, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert committed and all(f == "src/data/catalog.json" or f.startswith("src/assets/catalog/") for f in committed)
    assert _git(site, "diff", "--cached", "--name-only") == "unrelated.txt"  # still staged, not committed
    assert _git(remote, "rev-parse", "main") == _git(site, "rev-parse", "HEAD")
    again = _publish(shop, cfg)
    assert again["commit"] is None and again["pushed"] is False


@needs_git
def test_a_failed_push_is_reported_and_the_commit_stays(shop, repo):
    site, remote = repo
    shutil.rmtree(remote)
    out = _publish(shop, {"site_dir": str(site)})
    assert out["commit"] and out["pushed"] is False and out["push_error"]
    assert (site / "src/data/catalog.json").exists()
    assert shop.audit_log(limit=1)[0]["changes"]["push_error"]


@needs_git
def test_publish_git_off_makes_no_commit(shop, repo):
    site, _ = repo
    head = _git(site, "rev-parse", "HEAD")
    out = _publish(shop, {"site_dir": str(site), "publish_git": "false"})
    assert out["commit"] is None and _git(site, "rev-parse", "HEAD") == head


# ── the API ────────────────────────────────────────────────────────────────────
def _client(tmp_path, **cfg):
    reg = FakeRegistry({"db_path": str(tmp_path / "inv.db"), "workspace_dir": str(tmp_path), **cfg})
    inventory_plugin.register(reg)
    app = FastAPI()
    for router, prefix in reg.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app), reg


def test_photo_endpoints_round_trip(tmp_path):
    c, _ = _client(tmp_path)
    assert c.post(API + "/items", json={"id": "A", "name": "A"}).status_code == 200
    r = c.post(API + "/items/A/photos?alt=front", content=jpeg(), headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200, r.text
    pid = r.json()["photo"]["id"]
    r = c.get(API + f"/items/A/photos/{pid}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == ph.sanitize_jpeg(jpeg()) and r.headers["x-content-type-options"] == "nosniff"
    assert c.get(API + "/items/A/photos").json()["photos"][0]["alt"] == "front"
    pid2 = c.post(API + "/items/A/photos", content=png_file(), headers={"Content-Type": "image/png"}).json()["photo"][
        "id"
    ]
    r = c.patch(API + f"/items/A/photos/{pid2}", json={"position": 0, "alt": "top"})
    assert r.json()["photo"]["position"] == 0 and r.json()["photo"]["alt"] == "top"
    assert c.get(API + "/items/A").json()["item"]["photos"][0]["id"] == pid2
    assert c.delete(API + f"/items/A/photos/{pid}").json() == {"ok": True}
    assert c.delete(API + f"/items/A/photos/{pid}").status_code == 404
    assert c.get(API + "/items/NOPE/photos").status_code == 404
    assert c.post(API + "/items/NOPE/photos", content=jpeg()).status_code == 404
    assert c.post(API + "/items/A/photos", content=b"GIF89a....").status_code == 400
    assert c.patch(API + f"/items/A/photos/{'e' * 32}", json={"alt": "x"}).status_code == 404
    assert c.get(API + "/items/A/photos/..%2F..%2Fetc").status_code == 404
    r = c.put(API + "/items/A", json={"public": True, "blurb": "Sealed."})
    assert r.json()["item"]["public"] is True and r.json()["item"]["blurb"] == "Sealed."


def test_an_oversized_upload_is_413(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "MAX_BYTES", 1000)
    c, _ = _client(tmp_path)
    c.post(API + "/items", json={"id": "A", "name": "A"})
    r = c.post(API + "/items/A/photos", content=b"\xff\xd8\xff" + bytes(5000), headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 413


def test_publish_endpoints_preview_refuse_and_publish(tmp_path, site):
    c, reg = _client(tmp_path)
    c.post(API + "/items", json={"id": "A", "name": "A", "public": True})
    c.post(API + "/items/A/price", json={"target": 10, "basis": "b"})
    p = c.get(API + "/publish/preview").json()
    assert p["site_dir_ok"] is False and p["count"] == 1
    assert c.post(API + "/publish", json={"hash": p["hash"]}).status_code == 400  # no site dir
    c, reg = _client(tmp_path, site_dir=str(site), publish_git=False)
    p = c.get(API + "/publish/preview").json()
    assert p["site_dir_ok"] is True
    assert c.post(API + "/publish", json={"hash": "0" * 64}).status_code == 409
    r = c.post(API + "/publish", json={"hash": p["hash"]})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert json.loads((site / "src/data/catalog.json").read_text())["items"][0]["id"] == "A"
    assert ("published", {"count": 1, "commit": None, "pushed": False}) in reg.events


# ── the agent tools ────────────────────────────────────────────────────────────
def _tools(tmp_path):
    reg = FakeRegistry({"db_path": str(tmp_path / "inv.db"), "workspace_dir": str(tmp_path)})
    inventory_plugin.register(reg)
    return {t.name: t for t in reg.tools}


def test_the_agent_can_preview_but_has_no_way_to_publish(tmp_path):
    tools = _tools(tmp_path)
    assert [n for n in tools if "publish" in n] == ["inventory_publish_preview"]
    out = json.loads(tools["inventory_publish_preview"].invoke({}))
    assert out["ok"] is True and "hash" not in out and out["site_dir_ok"] is False


def test_the_photo_tool_reads_only_inside_the_workspace(tmp_path):
    tools = _tools(tmp_path)
    tools["inventory_upsert_item"].invoke({"id": "A", "name": "A", "public": True, "blurb": "Nice."})
    (tmp_path / "p.jpg").write_bytes(jpeg())
    out = json.loads(tools["inventory_add_photo"].invoke({"item_id": "A", "path": "p.jpg", "alt": "front"}))
    assert out["ok"] is True and out["photo"]["ext"] == "jpg"
    outside = tmp_path.parent / f"outside-{tmp_path.name}.jpg"
    outside.write_bytes(jpeg())
    out = json.loads(tools["inventory_add_photo"].invoke({"item_id": "A", "path": str(outside)}))
    assert out["ok"] is False and "outside the workspace" in out["error"]
    item = json.loads(tools["inventory_get"].invoke({"item_id": "A"}))["item"]
    assert item["public"] is True and item["blurb"] == "Nice." and item["photo_count"] == 1
    preview = json.loads(tools["inventory_publish_preview"].invoke({}))
    assert preview["skipped"] == [{"id": "A", "name": "A", "reason": "no asking price — set a target"}]


# ── CSV ────────────────────────────────────────────────────────────────────────
def test_csv_carries_public_and_blurb(store):
    from inventory_plugin.csvio import export_csv, import_csv

    out = import_csv(store, "inventory_id,item,on_site,blurb\nA,Team A,yes,Sealed box\nB,Team B,,\n", actor="t")
    assert out["ok"] and out["created"] == 2
    assert store.get_item("A")["public"] is True and store.get_item("A")["blurb"] == "Sealed box"
    assert store.get_item("B")["public"] is False
    header = export_csv(store).splitlines()[0].split(",")
    assert "public" in header and "blurb" in header


# ── the view ───────────────────────────────────────────────────────────────────
def test_the_view_has_photos_publish_and_no_drag_and_drop():
    from inventory_plugin.view import PAGE

    for needle in ('id="btn-publish"', 'id="photo-file"', "image/heic", "function mountPhotos", "publishDialog"):
        assert needle in PAGE, needle
    assert not re.search(r"dragover|dragenter|addEventListener\(\s*[\"']drop", PAGE)  # Tauri swallows HTML5 drops
    assert "URL.revokeObjectURL" in PAGE  # thumbnails are released when the dialog closes


def test_the_new_view_helpers_escape_and_the_copied_post_is_unchanged():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    from inventory_plugin.view import PAGE

    module = re.search(r'<script type="module">(.*?)</script>', PAGE, re.S).group(1)
    harness = (
        "globalThis.BASE = ''; globalThis.location = { pathname: '/plugins/inventory/view' };\n"
        "const el = () => new Proxy({}, { get: (t, k) => (k === 'addEventListener' || k === 'removeEventListener' || k === 'appendChild' || k === 'remove' || k === 'focus' ? () => {} : k === 'querySelectorAll' ? () => [] : k === 'querySelector' || k === 'closest' ? () => el() : k === 'classList' ? { toggle() {}, add() {}, remove() {} } : k === 'dataset' ? {} : k === 'hidden' ? true : ''), set: () => true });\n"
        "globalThis.document = { querySelector: () => el(), querySelectorAll: () => [], getElementById: () => el(), createElement: () => el(), addEventListener() {}, removeEventListener() {}, body: el() };\n"
        "globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => 0; globalThis.fetch = async () => ({ ok: false, status: 0, statusText: 'stub', text: async () => '' });\n"
    )
    probe = (
        "\nconsole.log(JSON.stringify({"
        " chips: itemChips({ public: true, photo_count: 2 }), one: itemChips({ public: false, photo_count: 1 }), none: itemChips({ public: false, photo_count: 0 }),"
        " card: photoCard({ id: 'x\"><img', alt: '\"><script>' }, 1, 'blob:abc'), cover: photoCard({ id: 'a', alt: '' }, 0, ''),"
        " pv: previewHtml({ site_dir_ok: false, site_dir_problem: '<b>no</b>', count: 1, site_dir: '', added: [{ id: 'A', name: '<img src=x onerror=1>' }], removed: [],"
        " changed: [{ id: 'B', name: 'B', fields: ['price_cents'] }], skipped: [{ id: 'C', name: 'C', reason: 'no asking price' }], warnings: ['w<'] }),"
        " res: publishResultText({ count: 2, added: [1], removed: [], changed: [], wrote_catalog: true, photos_copied: 1, photos_deleted: 0, commit: 'abc123', pushed: true, push_error: null, git_error: null }),"
        " doc: chatDoc([{ name: 'Wartrakk', condition: 'NoS', target: 20, system: 'Warhammer 40K', status: 'available' }, { name: 'Land Speeder', condition: 'nos', target: 40, system: 'Warhammer 40K', status: 'available' },"
        " { name: 'Intercessor Squad', condition: 'NoS', target: 30, system: 'Warhammer 40K', status: 'pending' }, { name: 'Helsmiths of Hashut Army Box', condition: 'NIB', target: 170, system: 'Warhammer AoS', status: 'available' },"
        " { name: 'Human  Team', condition: '', target: 57.69, system: 'Blood Bowl', status: 'listed' }, { name: 'Dice', condition: 'Sealed', target: null, system: 'Blood Bowl', status: 'available' }, { name: 'Loose bits', condition: '', target: 5, system: '', status: 'available' }]) }));\n"
    )
    r = subprocess.run(
        [node, "--input-type=module"], input=harness + module + probe, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr[:1200]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "site" in out["chips"] and "2 photos" in out["chips"] and "1 photo<" in out["one"] and out["none"] == ""
    assert "<script" not in out["card"] and out["card"].count("<img") == 1 and 'src="blob:abc"' in out["card"]
    assert 'data-photo="x&quot;&gt;&lt;img"' in out["card"]  # a hostile id stays inside its attribute
    assert "Make cover" in out["card"] and "cover</span>" in out["cover"] and "Make cover" not in out["cover"]
    assert "<img" not in out["pv"] and "&lt;b&gt;no" in out["pv"] and "w&lt;" in out["pv"]
    for needle in ("Added (1)", "Changed (1)", "price_cents", "Marked public but left out (1)", "no asking price"):
        assert needle in out["pv"], needle
    assert out["res"] == "Published 2 items (+1 −0 ~0) · photos copied 1, removed 0\ncommit abc123 · pushed"
    # The chat post Josh pastes into his group is pinned byte for byte; 0.5.0 must not move it.
    assert out["doc"] == (
        "Blood Bowl\nHuman Team — $57.69\nDice Sealed\n\nWarhammer 40K\nWartrakk NoS — $20\nLand Speeder nos — $40\n"
        "Intercessor Squad NoS — $30\n\nWarhammer AoS\nHelsmiths of Hashut Army Box NIB — $170\n\nOther\nLoose bits — $5\n"
    )
