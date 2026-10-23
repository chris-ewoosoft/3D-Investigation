"""Agent-catalog tool definition.

This is the *executable* counterpart to the pure policy descriptor
:class:`ai_assistant.domain.tools.ToolSpec`. It carries the argument schema, the
runtime policy label and the in-process handler used to populate the shared
:class:`~ai_assistant.tools.registry.ToolRegistry`.

The two models are deliberately distinct responsibilities:

* :class:`~ai_assistant.domain.tools.ToolSpec` — framework-free policy record
  (side effect, required scope, classification) consumed by the single
  execution core (:class:`ai_assistant.application.tools.ToolExecutionService`).
* :class:`ToolDefinition` — agent-facing catalog entry (JSON-schema parameters,
  handler, serialization helpers) rendered into prompts/grammars.

ADR 0003: there is exactly one type named ``ToolSpec`` in the codebase.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from ai_assistant.domain.specialists import Specialist
from ai_assistant.domain.tools import BUILTIN_PLUGIN_ID, ToolSpec


def parameters_from_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the object properties a JSON Schema supplies into the catalog
    parameter shape used by prompts and Pydantic model generation.

    Plugin tools bring a JSON Schema, while builtins declare the smaller
    ``name -> constraints`` mapping.  One adapter is enough for the basic
    scalar schema plugins use; unknown/nested shapes are left to ``Any`` in
    ``build_tool_models`` and are still preserved verbatim by ``input_schema``.
    """
    required = set(schema.get("required", ()))
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return {}
    parameters: dict[str, Any] = {}
    for name, definition in properties.items():
        if not isinstance(definition, Mapping):
            continue
        parameters[str(name)] = {
            **definition,
            "required": str(name) in required,
        }
    return parameters


@dataclass(frozen=True)
class ToolDefinition:
    """A registrable agent tool with its parameter schema and handler."""
    name: str
    description: str
    parameters: dict[str, Any]
    timeout_seconds: int = 10
    policy: str = "read_only"
    requires_approval: bool = False
    #: The tool's data ceiling as a classification *label*
    #: (``internal``/``restricted``/``regulated``). The composition root
    #: converts it to the domain enum; the label keeps this layer free of
    #: the domain model, exactly like ``policy``.
    classification: str = "internal"
    handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    #: Provenance: the plugin that contributed the tool.  Builtins keep the
    #: reserved id so the operator's third-party allow-list cannot disable
    #: the product's own catalogue.
    plugin_id: str = BUILTIN_PLUGIN_ID
    #: Specialists allowed to execute this tool.  Builtins get this from the
    #: domain reverse index; plugin tools are owned by ``Specialist.PLUGIN``.
    specialists: frozenset[Specialist] = frozenset()
    #: Optional verbatim JSON Schema (plugin tools).  ``properties`` also feed
    #: ``parameters`` for prompts/model generation; preserving the original
    #: document avoids a lossy round-trip through the smaller shape.
    input_schema: dict[str, Any] | None = None
    #: Optional exact domain policy record (plugin tools).  When present the
    #: composition root uses it unchanged instead of re-deriving policy from
    #: display labels.
    spec: ToolSpec | None = None

    @property
    def json_schema(self) -> dict[str, Any]:
        """Return the canonical input JSON Schema for this tool."""
        if self.input_schema is not None:
            return dict(self.input_schema)
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


__all__ = ["ToolDefinition"]
