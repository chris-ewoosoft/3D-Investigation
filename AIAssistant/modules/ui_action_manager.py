"""Pure helpers for sequential Qt application-action workflows."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def prepare_next_action(action: dict[str, Any], steps: list[dict[str, Any]],
                        next_actions: list[dict[str, Any]],
                        validate: Callable[[dict[str, Any]], tuple[dict[str, Any] | None, str | None]],
                        generate_id: Callable[[], str]) -> tuple[dict[str, Any] | None, str | None]:
    """Build the next pending action and full snapshot after a Qt ACK."""
    if not next_actions:
        return None, None
    params, error = validate(next_actions[0])
    if error or params is None:
        return None, error or "Invalid desktop action"
    params["request_id"] = generate_id()
    steps.append({"type": "tool_call", "tool": "application_action", "params": params,
                  "iteration": action.get("iteration", 0) + 1})
    return {
        **action,
        "params": params,
        "next_actions": next_actions[1:],
        "steps": action.get("steps", []) + steps,
    }, None
