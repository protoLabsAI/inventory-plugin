"""inventory — the resale inventory as a source of truth: lots, items, listings, sales,
price observations, and an audit trail, in a SQLite file this plugin owns.

``register()`` is the only place plugin code runs. Host-only imports stay lazy so the test
suite imports every module with no protoAgent host present.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("protoagent.plugins.inventory")

PLUGIN_ID = "inventory"


def resolve_db_path(cfg: dict, plugin_id: str = PLUGIN_ID) -> Path:
    """Where the database lives: an explicit ``db_path`` in config, else the host's
    instance-scoped plugin store (so the dev sandbox and every fleet member get their own)."""
    explicit = str(cfg.get("db_path") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    try:
        from graph import sdk  # host-only; absent in the test suite

        return sdk.plugin_store(plugin_id=plugin_id) / "inventory.db"
    except Exception:  # noqa: BLE001 — older host or no host: fall back to the instance root
        try:
            from infra.paths import instance_paths  # host-only

            return Path(instance_paths().store(plugin_id)) / "inventory.db"
        except Exception:  # noqa: BLE001
            return Path.home() / ".protoagent" / "plugins-data" / plugin_id / "inventory.db"


def register(registry) -> None:
    cfg = registry.config or {}
    plugin_id = getattr(registry, "plugin_id", PLUGIN_ID) or PLUGIN_ID
    db_path = resolve_db_path(cfg, plugin_id)

    from .store import InventoryStore

    try:
        store = InventoryStore(db_path)
    except Exception:  # noqa: BLE001 — without a store nothing below can work
        log.exception("[inventory] could not open the inventory database at %s", db_path)
        return
    log.info("[inventory] database at %s", db_path)

    def emit(topic: str, data: dict) -> None:
        try:
            registry.emit(topic, data)  # published as inventory.<topic>
        except Exception:  # noqa: BLE001 — the bus is best-effort
            log.debug("[inventory] emit %s failed", topic, exc_info=True)

    try:
        from .tools import build_tools

        for t in build_tools(store, cfg, emit=emit):
            registry.register_tool(t)
    except Exception:  # noqa: BLE001 — one bad contribution must not sink the rest
        log.exception("[inventory] registering tools failed")

    try:
        from .api import build_data_router

        registry.register_router(build_data_router(store, cfg, emit=emit), prefix=f"/api/plugins/{plugin_id}")
    except Exception:  # noqa: BLE001
        log.exception("[inventory] mounting the data router failed")

    # The console view: a PUBLIC page (an iframe navigation carries no bearer) on its own
    # prefix; every byte of data it shows comes through the gated router above.
    try:
        from .view import build_view_router

        registry.register_router(build_view_router(cfg), prefix=f"/plugins/{plugin_id}")
    except Exception:  # noqa: BLE001
        log.exception("[inventory] mounting the view failed")

    try:
        registry.register_skill_dir("skills")
    except Exception:  # noqa: BLE001
        log.exception("[inventory] registering skills failed")

    log.info("[inventory] registered")
