"""Agent transfer tools."""
from __future__ import annotations

from typing import Any


def tool_transfer_to_code_agent(params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "transferred_to_code", "intent": params.get("intent")}


def tool_transfer_to_toolapp_agent(params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "transferred_to_toolapp", "intent": params.get("intent")}


def tool_transfer_to_chatbot_agent(params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "transferred_to_chatbot", "intent": params.get("intent")}
