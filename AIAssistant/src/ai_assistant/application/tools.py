from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from ..domain.security import DataClassification, Principal
from ..domain.tools import ToolRequest, ToolResult
from ..plugins.registry import PluginRegistry
from ..ports import ToolEventSink  # noqa: I001
from .validation import validate_input

_CLASSIFICATION_ORDER = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.RESTRICTED: 2,
    DataClassification.REGULATED: 3,
}


class ToolExecutionService:
    """The only application path allowed to execute registered tools."""

    def __init__(self, registry: PluginRegistry, event_sink: ToolEventSink | None = None) -> None:
        self._registry = registry
        self._event_sink = event_sink

    def execute(self, tool_name: str, parameters: Mapping, principal: Principal,
                correlation_id: str | None = None, *, approval_granted: bool = False) -> ToolResult:
        request = ToolRequest(tool_name, dict(parameters), correlation_id or str(uuid4()))
        item = self._registry.tool(tool_name)
        if item is None:
            return self._finish(request, ToolResult(False, error_code="tool_not_found", message=f"Unknown tool: {tool_name}"))
        spec = item.spec
        validation_error = validate_input(spec.input_schema, parameters)
        if validation_error:
            return self._finish(request, ToolResult(False, error_code="invalid_input", message=validation_error))
        if not principal.permits(spec.required_scope):
            return self._finish(request, ToolResult(False, error_code="forbidden", message="Required tool scope is missing"))
        if _CLASSIFICATION_ORDER[principal.classification] > _CLASSIFICATION_ORDER[spec.maximum_classification]:
            return self._finish(request, ToolResult(False, error_code="data_policy_denied", message="Data classification is not permitted"))
        if spec.requires_approval and not approval_granted:
            return self._finish(request, ToolResult(
                False, {"approval_required": True, "tool": spec.name}, "approval_required", "User approval is required",
            ))
        try:
            payload = item.executor(dict(parameters))
        except Exception as error:  # Boundary converts plugin failure to structured result.
            return self._finish(request, ToolResult(False, error_code="tool_failure", message=str(error)))
        if not isinstance(payload, dict):
            return self._finish(request, ToolResult(False, error_code="invalid_tool_result", message="Tool returned a non-object"))
        success = not bool(payload.get("error")) and payload.get("success") is not False
        return self._finish(request, ToolResult(success, payload, None if success else "tool_failure"))

    def _finish(self, request: ToolRequest, result: ToolResult) -> ToolResult:
        if self._event_sink:
            self._event_sink(request, result)
        return result
