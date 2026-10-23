"""The plugin tool bridge: one catalogue, one explicit policy row.

Regression coverage for the architecture review's first finding: a plugin tool
was executable by a direct gateway caller but invisible to the agent catalogue
and refused by ``authorise`` because it had no specialist row.  These tests pin
the bridge that makes plugin tools first-class without weakening the builtin
least-privilege policy.
"""
from __future__ import annotations

import unittest

from ai_assistant.bootstrap.container import (
    merge_plugin_tools,
    tool_definition_from_plugin,
)
from ai_assistant.domain.specialists import Specialist
from ai_assistant.domain.tools import BUILTIN_PLUGIN_ID, SideEffect, ToolSpec
from ai_assistant.orchestration.supervisor import (
    ALLOWED_TOOLS,
    authorise,
    delegate,
)
from ai_assistant.plugins.registry import PluginRegistry
from ai_assistant.tools.definition import ToolDefinition, parameters_from_json_schema
from ai_assistant.tools.registry import ToolRegistry


def _spec(
    name: str,
    *,
    plugin_id: str = "acme",
    side_effect: SideEffect = SideEffect.READ,
    requires_approval: bool = False,
    required_scope: str | None = None,
    schema: dict | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} description",
        input_schema=schema or {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Query"}},
            "required": ["query"],
        },
        timeout_seconds=7,
        side_effect=side_effect,
        requires_approval=requires_approval,
        required_scope=required_scope,
        plugin_id=plugin_id,
    )


class MergePluginToolsTests(unittest.TestCase):
    def test_a_malformed_json_schema_flattens_to_no_parameters(self) -> None:
        self.assertEqual(parameters_from_json_schema({"properties": []}), {})
        self.assertEqual(
            parameters_from_json_schema({"properties": {"bad": "not a mapping"}}),
            {},
        )

    def test_an_enabled_plugin_tool_becomes_an_agent_tool(self) -> None:
        registry = ToolRegistry()
        plugins = PluginRegistry(frozenset({"acme"}))
        spec = _spec("plugin_echo")
        calls: list[dict] = []

        def executor(params: dict) -> dict:
            calls.append(params)
            return {"success": True}

        plugins.register_tool(spec, executor)

        merged = merge_plugin_tools(registry, plugins)

        self.assertEqual(merged, frozenset({"plugin_echo"}))
        definition = registry.get("plugin_echo")
        self.assertIsInstance(definition, ToolDefinition)
        self.assertIs(definition.handler, executor)
        self.assertIs(definition.spec, spec)
        self.assertEqual(definition.specialists, frozenset({Specialist.PLUGIN}))
        self.assertEqual(definition.plugin_id, "acme")
        self.assertEqual(definition.input_schema, spec.input_schema)
        self.assertEqual(definition.json_schema["required"], ["query"])
        self.assertEqual(definition.json_schema["properties"]["query"]["description"], "Query")
        self.assertEqual(definition.parameters["query"]["required"], True)
        self.assertEqual(definition.timeout_seconds, 7)

    def test_builtins_are_not_counted_as_plugin_tools(self) -> None:
        registry = ToolRegistry()
        plugins = PluginRegistry(frozenset({"acme"}))
        plugins.register_tool(_spec("builtin_tool", plugin_id=BUILTIN_PLUGIN_ID), object())

        self.assertEqual(merge_plugin_tools(registry, plugins), frozenset())
        self.assertIsNone(registry.get("builtin_tool"))

    def test_a_plugin_cannot_shadow_a_builtin_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="read_file", description="builtin", parameters={}))
        plugins = PluginRegistry(frozenset({"acme"}))
        plugins.register_tool(_spec("read_file"), object())

        self.assertEqual(merge_plugin_tools(registry, plugins), frozenset())
        original = registry.get("read_file")
        self.assertIsInstance(original, ToolDefinition)
        self.assertEqual(original.description, "builtin")

    def test_an_alias_is_not_a_second_execution_surface(self) -> None:
        registry = ToolRegistry()
        plugins = PluginRegistry(frozenset({"acme"}))
        entry = type("Entry", (), {
            "spec": _spec("app_action_viewer"),
            "executor": object(),
        })()
        plugins.register_tool(entry.spec, entry.executor)

        self.assertIsNone(tool_definition_from_plugin(entry))
        self.assertEqual(merge_plugin_tools(registry, plugins), frozenset())

    def test_a_malformed_registry_entry_is_skipped(self) -> None:
        entry = type("Entry", (), {"spec": object(), "executor": object()})()
        self.assertIsNone(tool_definition_from_plugin(entry))
        self.assertIsNone(tool_definition_from_plugin(object()))


class PluginPolicyRoutingTests(unittest.TestCase):
    def test_a_plugin_tool_routes_only_when_the_live_row_contains_it(self) -> None:
        plugin_tools = frozenset({"plugin_echo"})
        routed = delegate("use plugin", "s1", tool="plugin_echo", plugin_tools=plugin_tools)
        self.assertIs(routed.specialist, Specialist.PLUGIN)

        unknown = delegate("use plugin", "s1", tool="plugin_echo")
        self.assertIs(unknown.specialist, Specialist.SUPERVISOR)

    def test_a_plugin_tool_is_refused_without_an_explicit_dynamic_row(self) -> None:
        routed = delegate(
            "use plugin", "s1", tool="plugin_echo",
            plugin_tools=frozenset({"plugin_echo"}),
        )
        allowed, reason = authorise(routed, needs_approval=False)
        self.assertFalse(allowed)
        self.assertTrue(reason)

    def test_the_composition_root_row_authorises_the_plugin_tool(self) -> None:
        routed = delegate(
            "use plugin", "s1", tool="plugin_echo",
            plugin_tools=frozenset({"plugin_echo"}),
        )
        policy = {**ALLOWED_TOOLS, Specialist.PLUGIN: frozenset({"plugin_echo"})}
        self.assertEqual(authorise(routed, False, allowed_tools=policy), (True, None))

    def test_a_dynamic_row_cannot_grant_a_builtin_to_the_supervisor(self) -> None:
        routed = delegate("direct", "s1", tool="read_file")
        self.assertIs(routed.specialist, Specialist.RESEARCH)
        # The caller cannot turn a fallback/supervisor route into a plugin one
        # by adding the tool to the dynamic row: ``authorise`` checks the
        # route's specialist first.
        allowed, _ = authorise(routed, False, allowed_tools={Specialist.SUPERVISOR: frozenset({"read_file"})})
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
