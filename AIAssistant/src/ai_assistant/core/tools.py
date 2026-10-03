"""Core tool domain models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """Domain model for a tool definition."""
    name: str
    description: str
    parameters: dict[str, Any]
    timeout_seconds: int = 10
    policy: str = "read_only"
    requires_approval: bool = False
    idempotent: bool = True
    handler: Callable | None = None

    @property
    def json_schema(self) -> dict[str, Any]:
        """Return the canonical input JSON Schema for this tool."""
        return {
            "type": "object",
            "properties": {
                name: {
                    key: value for key, value in definition.items()
                    if key in {"type", "description", "enum", "minimum", "maximum"}
                }
                for name, definition in self.parameters.items()
            },
            "required": [name for name, spec in self.parameters.items() if spec.get("required")],
            "additionalProperties": False,
        }

    def to_openai_format(self) -> dict[str, Any]:
        """Format for Llama.cpp / OpenAI function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema,
            }
        }


@dataclass
class ToolRequest:
    """A parsed request to execute a tool."""
    tool_name: str
    params: dict[str, Any]
    raw_text: str | None = None


@dataclass
class ToolResult:
    """The result of executing a tool."""
    output: str
    is_error: bool = False
    is_terminal: bool = False
    requires_approval: bool = False
    ui_ack_id: str | None = None
