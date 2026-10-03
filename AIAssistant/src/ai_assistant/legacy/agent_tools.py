"""Tool registry shared by Agent routes and the Coding/ToolApp specialists."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class ToolRegistry:
    """Small dependency-inversion layer around concrete tool executors.

    The registry keeps route code independent from how a tool is implemented
    (local Python, MCP, or a Qt request) and makes unknown tools fail closed.
    """

    def __init__(self, executors: Mapping[str, Callable[[dict], dict]]) -> None:
        self._executors = dict(executors)

    def get(self, name: str) -> Callable[[dict], dict] | None:
        return self._executors.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._executors)

    def execute(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        executor = self.get(name)
        if executor is None:
            return {"error": f"Tool không tồn tại: {name}"}
        return executor(params)
