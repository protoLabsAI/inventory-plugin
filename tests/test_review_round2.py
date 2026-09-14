"""Regression tests for the round-2 review of PR #9 (0.5.0)."""

from __future__ import annotations

import subprocess

from inventory_plugin import photos as ph
from inventory_plugin import publish as pub

from tests.test_photos import FRAME, SCAN, jpeg, seg
from tests.test_review_round1 import _git, _item, _publish, needs_git


def _bare(*middle: bytes) -> bytes:
    return b"".join([b"\xff\xd8", *middle, *FRAME, seg(0xDA, b"\x01\x01\x00\x00\x3f\x00"), SCAN, b"\xff\xd9"])


# ── B1: the first segment after SOI is never a table or SOF ───────────────────
def test_a_jpeg_that_starts_with_a_table_gains_a_bare_jfif_header():
    out = ph.sanitize_jpeg(_bare())
    assert out.startswith(b"\xff\xd8" + ph._JFIF_APP0)
    assert ph.sanitize_jpeg(out) == out


def test_an_adobe_jpeg_leads_with_its_adobe_marker_and_gets_no_jfif():
    adobe = seg(0xEE, b"Adobe" + bytes([0, 100, 0, 0, 0, 0, 0]))  # transform 0 (RGB)
    out = ph.sanitize_jpeg(_bare(adobe))
    assert out[2:4] == b"\xff\xee"
    assert b"JFIF" not in out


def test_a_rotated_photo_leads_with_its_orientation_block():
    out = ph.sanitize_jpeg(jpeg(orientation=6))
    assert out[2:4] == b"\xff\xe1"
    assert ph.read_orientation(out) == 6


# ── B2: stray files in the photo folder are never committed or pushed ─────────
@needs_git
def test_stray_files_in_the_photo_folder_are_never_committed(store, repo):
    site, remote = repo
    cfg = {"site_dir": str(site)}
    _item(store, "P1", photo=True)
    stray_dir = site / "src" / "assets" / "catalog" / "P1"
    stray_dir.mkdir(parents=True)
    (stray_dir / "raw-phone.jpg").write_bytes(jpeg())
    (site / "src" / "assets" / "catalog" / "notes.txt").write_text("PRIVATE")
    out = _publish(store, cfg)
    assert out["commit"] and out["pushed"], out
    committed = _git(site, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    pushed = _git(remote, "ls-tree", "-r", "--name-only", "main").splitlines()
    for listing in (committed, pushed):
        assert not any(n.endswith(("raw-phone.jpg", "notes.txt")) for n in listing), listing
        assert any(n.startswith("src/assets/catalog/P1/") and n.endswith(".jpg") for n in listing)
    assert any("left in place" in w for w in out["warnings"]), out["warnings"]
    assert (stray_dir / "raw-phone.jpg").exists()  # reported, never deleted


# ── B3: a symlink at a temp name is never written through ──────────────────────
@needs_git
def test_a_symlink_at_a_temp_name_is_never_written_through(store, repo, tmp_path):
    site, _remote = repo
    cfg = {"site_dir": str(site)}
    pid = _item(store, "P1", photo=True)
    outside_a, outside_b = tmp_path / "outside-a.txt", tmp_path / "outside-b.txt"
    outside_a.write_text("keep-a")
    outside_b.write_text("keep-b")
    (site / "src" / "data").mkdir(parents=True)
    (site / "src" / "data" / ".catalog.json.tmp").symlink_to(outside_a)
    photo_dir = site / "src" / "assets" / "catalog" / "P1"
    photo_dir.mkdir(parents=True)
    (photo_dir / f".{pid}.jpg.tmp").symlink_to(outside_b)
    out = _publish(store, cfg)
    assert out["ok"] and out["commit"], out
    assert outside_a.read_text() == "keep-a" and outside_b.read_text() == "keep-b"
    catalog = site / "src" / "data" / "catalog.json"
    assert catalog.is_file() and not catalog.is_symlink()
    photo = photo_dir / f"{pid}.jpg"
    assert photo.is_file() and not photo.is_symlink()


# ── push notes: explicit upstream push; refuse when the log can't be read ────────
@needs_git
def test_the_push_goes_to_the_upstream_branch_only(store, repo):
    site, remote = repo
    _git(site, "config", "push.default", "matching")
    _git(site, "checkout", "-q", "-b", "other")
    (site / "other.txt").write_text("v1")
    _git(site, "add", "other.txt")
    _git(site, "commit", "-qm", "other v1")
    _git(site, "push", "-q", "-u", "origin", "other")
    (site / "other.txt").write_text("v2 unpushed")
    _git(site, "commit", "-qam", "other v2 (not for the site)")
    _git(site, "checkout", "-q", "main")
    _item(store, "P1")
    out = _publish(store, {"site_dir": str(site)})
    assert out["pushed"], out
    assert _git(remote, "log", "-1", "--format=%s", "other") == "other v1"


@needs_git
def test_an_unreadable_unpushed_log_refuses_to_push(store, repo, monkeypatch):
    site, remote = repo
    monkeypatch.setattr(pub, "_foreign_unpushed", lambda _site: None)
    _item(store, "P1")
    before = _git(remote, "rev-parse", "main")
    out = _publish(store, {"site_dir": str(site)})
    assert out["commit"] and not out["pushed"]
    assert "couldn't check" in (out["push_error"] or "")
    assert _git(remote, "rev-parse", "main") == before


def test_git_runs_with_literal_pathspecs(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    pub._git(tmp_path, "status")
    assert seen.get("GIT_LITERAL_PATHSPECS") == "1"
