"""Manifest coherence + the register() failure modes."""

from __future__ import annotations

from pathlib import Path

import inventory_plugin
import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert f'version = "{manifest["version"]}"' in (ROOT / "pyproject.toml").read_text()


def test_ships_disabled_and_declares_emits_the_way_the_host_reads_them():
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert manifest["enabled"] is False
    assert manifest["id"] == "inventory" and manifest["config_section"] == "inventory"
    assert "events" not in manifest  # the host reads `emits:` (graph/plugins/manifest.py); `events:` is silently dead
    topics = {e["topic"] for e in manifest["emits"]}
    assert "inventory.sale.recorded" in topics and all(t.startswith("inventory.") for t in topics)
    assert manifest["subscribes"] == []
    assert manifest["config"]["db_path"] == ""
    assert manifest["min_protoagent_version"] >= "0.148.0"  # sdk.plugin_store first shipped in 0.148


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


def test_runtime_dependencies_are_the_hosts_by_contract():
    """protoAgent provides fastapi / langchain-core and installs a plugin's packages only from the
    manifest's requires_pip. Declaring them in pyproject would be inert at best and a version
    fight with the host at worst; keep both empty on purpose."""
    import tomllib
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
    manifest = yaml.safe_load((root / "protoagent.plugin.yaml").read_text())
    assert not manifest.get("requires_pip")
