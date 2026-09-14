"""Test bootstrap — import the plugin with NO protoAgent host present.

The host loads a plugin under a synthetic package; the suite does the same so the modules'
relative imports (``from .store import ...``) resolve standalone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "inventory_plugin"

if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)


class FakeRegistry:
    def __init__(self, config=None):
        self.config = config or {}
        self.plugin_id = "inventory"
        self.tools, self.routers, self.skill_dirs, self.events = [], [], [], []

    def register_tool(self, t):
        self.tools.append(t)

    def register_router(self, router, prefix=None):
        self.routers.append((router, prefix))

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)

    def emit(self, topic, data=None):
        self.events.append((topic, data))


@pytest.fixture
def store(tmp_path):
    from inventory_plugin.store import InventoryStore

    return InventoryStore(tmp_path / "inventory.db")


@pytest.fixture
def registry(tmp_path):
    return FakeRegistry({"db_path": str(tmp_path / "inv.db"), "workspace_dir": str(tmp_path)})


@pytest.fixture
def seeded(store):
    """One lot, three items in different states, one sale — enough for the roll-ups."""
    store.upsert_lot({"id": "LOT-1", "name": "Box", "acquisition_cost": 100, "acquired_on": "2026-09-01"}, actor="t")
    store.upsert_item({"id": "A", "lot_id": "LOT-1", "name": "Team A", "category": "Teams"}, actor="t")
    store.set_price("A", low=20, target=30, high=40, basis="test basis", actor="t")
    store.upsert_item({"id": "B", "lot_id": "LOT-1", "name": "Dice B", "category": "Dice", "quantity": 2}, actor="t")
    store.set_price("B", low=10, target=15, high=20, basis="test basis", actor="t")
    store.upsert_item({"id": "C", "lot_id": "LOT-1", "name": "Book C", "category": "Books"}, actor="t")
    store.set_price("C", low=25, target=25, high=25, basis="test basis", actor="t")
    store.mark_sold("C", price=25, channel="eBay", fees=3.5, shipping_charged=5, shipping_cost=4, actor="t")
    store.upsert_item({"id": "D", "lot_id": "LOT-1", "name": "Unpriced D"}, actor="t")
    return store


def _cgit(cwd, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    import subprocess

    """A site checkout with an upstream, like ~/dev/nerdsville-site: no src/assets/catalog tracked."""
    remote, site = tmp_path / "remote.git", tmp_path / "gitsite"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", str(site)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _cgit(site, "config", k, v)
    (site / "src" / "pages").mkdir(parents=True)
    (site / "src" / "pages" / "index.astro").write_text("home")
    (site / "package.json").write_text("{}")
    _cgit(site, "add", "-A")
    _cgit(site, "commit", "-qm", "init")
    _cgit(site, "branch", "-M", "main")
    _cgit(site, "remote", "add", "origin", str(remote))
    _cgit(site, "push", "-q", "-u", "origin", "main")
    return site, remote
