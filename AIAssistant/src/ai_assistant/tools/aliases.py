"""Canonical names for legacy/desktop-action tool aliases.

The model sees a few friendly spellings (``app_action_viewer`` and friends);
every execution path must see the canonical tool instead.  Keeping the map in
the tool layer gives the completion parser, the policy contract and the
composition root one source instead of three copies that can drift.
"""
from __future__ import annotations

TOOL_NAME_ALIASES: dict[str, str] = {
    "app_action_reconstruction": "application_action",
    "app_action_general": "application_action",
    "app_action_ai": "application_action",
    "app_action_viewer": "application_action",
    "application_actions": "application_action",
    "app_action": "application_action",
    "desktop_action": "application_action",
    "ui_action": "application_action",
}


def canonical_tool_name(name: str) -> str:
    """Return the canonical execution name for an advertised alias."""
    return TOOL_NAME_ALIASES.get(name, name)


__all__ = ["TOOL_NAME_ALIASES", "canonical_tool_name"]
