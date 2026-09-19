from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .security import DataClassification


class SideEffect(StrEnum):
    READ = "read"
    DESKTOP = "desktop"
    WRITE = "write"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    timeout_seconds: int
    side_effect: SideEffect = SideEffect.READ
    requires_approval: bool = False
    required_scope: str | None = None
    idempotent: bool = True
    maximum_classification: DataClassification = DataClassification.RESTRICTED
    plugin_id: str = "builtin"


@dataclass(frozen=True, slots=True)
class ToolRequest:
    tool_name: str
    parameters: Mapping[str, Any]
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    success: bool
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.payload)
        result.setdefault("success", self.success)
        if self.error_code:
            result["error_code"] = self.error_code
        if self.message:
            result.setdefault("error", self.message)
        return result
