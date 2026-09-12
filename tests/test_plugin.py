"""Manifest coherence + the register() failure modes."""

from __future__ import annotations

from pathlib import Path

import inventory_plugin
import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert f'version = "{manifest["version"]}"' in (ROOT / "pyproject.toml").read_text()


def test_ships_disabled_and_declares_events():
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert manifest["enabled"] is False
    assert manifest["id"] == "inventory" and manifest["config_section"] == "inventory"
    assert "inventory.sale.recorded" in manifest["events"]
    assert manifest["config"]["db_path"] == ""


def test_skill_is_discoverable():
    assert (ROOT / "skills" / "inventory-ops" / "SKILL.md").read_text().startswith("---\nname: inventory-ops")


def test_db_path_resolution_prefers_config_and_never_fails_without_a_host(tmp_path):
    assert inventory_plugin.resolve_db_path({"db_path": str(tmp_path / "x.db")}) == tmp_path / "x.db"
    fallback = inventory_plugin.resolve_db_path({})  # no host importable here → a home-dir fallback, no exception
    assert fallback.name == "inventory.db"


def test_an_unopenable_database_does_not_raise_out_of_register(tmp_path, monkeypatch):
    from tests.conftest import FakeRegistry

    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    reg = FakeRegistry({"db_path": str(blocker / "inventory.db")})  # parent is a file → mkdir fails
    inventory_plugin.register(reg)  # must not raise
    assert reg.tools == []
