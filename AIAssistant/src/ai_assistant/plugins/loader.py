"""Entry-point loader for independently versioned third-party plugins."""
from __future__ import annotations

import logging
from importlib.metadata import entry_points

from .registry import PluginRegistry

logger = logging.getLogger("ai_assistant.plugins.loader")


def _declared_names(entry_point: object) -> frozenset[str]:
    """Names the distribution declares in its metadata — readable without import.

    ``entry_point.load()`` *executes* the plugin module, so the enablement
    gate has to answer from metadata alone: the entry-point name and the
    owning distribution's name. A plugin author keeps the gate open by naming
    the entry point after its ``plugin_id`` (or shipping a distribution named
    after it); anything the operator did not enable is never instantiated.
    """
    names = {str(getattr(entry_point, "name", ""))}
    distribution = getattr(entry_point, "dist", None)
    distribution_name = str(getattr(distribution, "name", "") or "")
    if distribution_name:
        names.add(distribution_name)
    return frozenset(name for name in names if name)


def load_entrypoint_plugins(registry: PluginRegistry, group: str = "ai_assistant.plugins") -> tuple[str, ...]:
    """Load only enabled plugins; invalid plugins fail bootstrap explicitly.

    An entry point is instantiated only when the operator's allowlist already
    names it in the *metadata* (ADR 0003): a merely installed — but disabled —
    distribution must not get to run its module code at bootstrap. The
    ``plugin_id`` check afterwards still binds the loaded object to the id the
    registry will authorise, so a mismatched entry point is skipped rather
    than trusted on its declared name alone.
    """
    loaded: list[str] = []
    for entry_point in entry_points().select(group=group):
        if not any(registry.allows(name) for name in _declared_names(entry_point)):
            continue
        plugin = entry_point.load()()
        plugin_id = str(getattr(plugin, "plugin_id", ""))
        if not registry.allows(plugin_id):
            logger.warning(
                "Entry point %r loaded plugin_id %r, which is not enabled; skipped.",
                getattr(entry_point, "name", ""), plugin_id,
            )
            continue
        register = getattr(plugin, "register", None)
        if not callable(register):
            msg = f"Plugin {plugin_id} has no register method"
            raise TypeError(msg)
        with registry.registration_scope(plugin_id):
            register(registry)
        loaded.append(plugin_id)
    return tuple(loaded)
