"""Shared, versioned desktop-action contract."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "Config" / "agent_action_manifest.json"

@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    with _MANIFEST_PATH.open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data.get("actions"), list):
        raise ValueError("agent_action_manifest.json requires an actions array")
    return data

@lru_cache(maxsize=1)
def _index() -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in manifest()["actions"]}

def canonical_action(action: str) -> str | None:
    return action if action in _index() else None

def canonicalise_action_params(params: dict[str, Any]) -> dict[str, Any] | None:
    action = canonical_action(str(params.get("action", "")))
    if action is None:
        return None
    entry = _index()[action]
    allowed = {"action", "request_id", *entry.get("parameters", {}).keys()}
    result = {key: value for key, value in params.items() if key in allowed}
    result["action"] = action
    return result

def validate_action_params(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    result = canonicalise_action_params(params)
    if result is None:
        valid_actions = ", ".join(sorted(action_ids()))
        return None, f"Unsupported desktop action: '{params.get('action', '')}'. Supported actions are: {valid_actions}"
    entry = _index()[result["action"]]
    for name, definition in entry.get("parameters", {}).items():
        value = result.get(name)
        if definition.get("required") and (value is None or value == ""):
            return None, f"{result['action']} requires parameter '{name}'"
        if value is not None and definition.get("enum") and value not in definition["enum"]:
            return None, f"{result['action']}.{name} must be one of {definition['enum']}"
    return result, None

def looks_like_ui_action(text: str) -> bool:
    # Deprecated: A2A routing replaces text-based intent matching.
    return False

def action_ids() -> set[str]:
    return set(_index())
