from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AgentDecisionKind(StrEnum):
    FINAL = "final"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class AgentDecision:
    kind: AgentDecisionKind
    content: str = ""
    tool_name: str | None = None
    parameters: dict[str, Any] | None = None
