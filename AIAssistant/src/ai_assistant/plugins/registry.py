from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ..domain.tools import BUILTIN_PLUGIN_ID, SideEffect, ToolSpec
from ..ports import ToolExecutor


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ToolSpec
    executor: ToolExecutor


class PluginRegistry:
    """Explicit capability registry; duplicate tool names fail during bootstrap.

    The builtin catalogue is always allowed: the operator's ``plugins.toml``
    allow-list decides which third-party distributions may register, never
    whether the product's own tools exist.  Registration is additionally
    scoped to the plugin the loader just instantiated, so a plugin cannot
    name another enabled plugin in ``ToolSpec.plugin_id`` and borrow its
    permission.
    """

    def __init__(self, allowed_plugins: frozenset[str]) -> None:
        self._allowed_plugins = frozenset(allowed_plugins) | {BUILTIN_PLUGIN_ID}
        self._tools: dict[str, RegisteredTool] = {}
        self._active_plugin_id: str | None = None

    @contextmanager
    def registration_scope(self, plugin_id: str) -> Iterator[None]:
        """Bind registrations in the block to ``plugin_id``.

        Nested scopes restore the outer id on exit; bootstrap is single
        threaded, and the context manager makes the identity explicit at the
        only production call site.
        """
        previous = self._active_plugin_id
        self._active_plugin_id = plugin_id
        try:
            yield
        finally:
            self._active_plugin_id = previous

    def register_tool(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        if self._active_plugin_id is not None and spec.plugin_id != self._active_plugin_id:
            msg = (
                f"Plugin {self._active_plugin_id} may not register a tool "
                f"declaring plugin_id {spec.plugin_id}"
            )
            raise PermissionError(msg)
        if spec.plugin_id not in self._allowed_plugins:
            msg = f"Plugin is not enabled: {spec.plugin_id}"
            raise PermissionError(msg)
        if spec.side_effect in {SideEffect.DESKTOP, SideEffect.WRITE, SideEffect.EXECUTE}:
            if not spec.required_scope:
                msg = f"Tool {spec.name} declares a side effect without a required scope"
                raise ValueError(msg)
            if spec.side_effect in {SideEffect.WRITE, SideEffect.EXECUTE} and not spec.requires_approval:
                msg = f"Mutating tool {spec.name} must require approval"
                raise ValueError(msg)
        if spec.name in self._tools:
            msg = f"Duplicate tool registration: {spec.name}"
            raise ValueError(msg)
        self._tools[spec.name] = RegisteredTool(spec, executor)

    def tool(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def tools(self) -> tuple[RegisteredTool, ...]:
        return tuple(self._tools.values())

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(item.spec for item in self._tools.values())

    def allows(self, plugin_id: str) -> bool:
        return plugin_id in self._allowed_plugins
