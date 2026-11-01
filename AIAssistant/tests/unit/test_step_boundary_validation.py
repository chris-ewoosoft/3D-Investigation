"""The step-vocabulary trust boundary (ADR 0019).

Three claims, each pinned here:

1. ``validate_steps`` is the runtime twin of the ``Step`` TypedDict — it
   re-derives the discriminator check from ``get_args(StepType)``, so the
   compile-time and runtime gates cannot drift, and it keeps payload keys it
   does not know (forward compatibility across a downgrade).
2. Both crossings a persisted step list can take into a run — the
   pending-approval file and the A2A continuation metadata — refuse malformed
   elements instead of forwarding them to resume or to the client switch.
3. No inbound HTTP/A2A request model carries a ``steps`` field: a future one
   would silently become a third, ungated crossing, so its addition fails
   here first.
"""
from __future__ import annotations

import json
import tempfile
import typing
import unittest
from pathlib import Path
from typing import get_args

from ai_assistant.adapters.orchestration.deterministic import DeterministicAgentOrchestrator
from ai_assistant.adapters.persistence.pending_store import PendingActionStore
from ai_assistant.application.agent_runs import AgentRunResult
from ai_assistant.domain.steps import Step, StepType, validate_steps


class ValidateStepsTests(unittest.TestCase):
    """Shape gate: list-of-dict with a closed discriminator, keys kept as-is."""

    def test_every_vocabulary_member_is_accepted(self) -> None:
        """Validator ↔ vocabulary coupling: growing StepType grows acceptance."""
        for kind in get_args(StepType):
            validated = validate_steps([{"type": kind}])
            self.assertEqual(validated[0]["type"], kind)

    def test_unknown_payload_keys_are_kept(self) -> None:
        """A newer writer's keys survive a downgrade — only type is fail-closed."""
        raw = [{"type": "plan", "steps": ["a"], "future_key": 42}]
        validated = validate_steps(raw)
        self.assertEqual(validated[0]["future_key"], 42)
        self.assertIs(validated[0], raw[0])

    def test_non_list_container_raises_value_error(self) -> None:
        for raw in (None, {"type": "plan"}, "steps", 7):
            with self.subTest(raw=raw), self.assertRaises(ValueError) as raised:
                validate_steps(raw)
            self.assertIn("must be a list", str(raised.exception))

    def test_non_dict_entry_raises_value_error_with_index(self) -> None:
        with self.assertRaises(ValueError) as raised:
            validate_steps([{"type": "plan"}, "not-a-step"])
        self.assertIn("step[1]", str(raised.exception))

    def test_missing_or_unknown_type_raises_value_error(self) -> None:
        for entry in ({}, {"type": "not_a_step"}, {"type": None}):
            with self.subTest(entry=entry), self.assertRaises(ValueError) as raised:
                validate_steps([entry])
            self.assertIn("outside the step vocabulary", str(raised.exception))

    def test_state_reexports_the_domain_vocabulary(self) -> None:
        """One vocabulary, two import paths: orchestration.state re-exports."""
        from ai_assistant.orchestration import state

        self.assertIs(state.Step, Step)
        self.assertIs(state.StepType, StepType)


class PendingStoreRehydrationTests(unittest.TestCase):
    """The disk crossing: malformed step logs drop their entry, never resume."""

    def _store(self, payload: dict) -> tuple[PendingActionStore, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "pending.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return PendingActionStore(path), path

    def test_valid_steps_are_loaded_intact(self) -> None:
        steps = [{"type": "plan", "steps": ["one"]}, {"type": "tool_call", "tool": "read_file"}]
        store, _ = self._store({"act-1": {"action_id": "act-1", "steps": steps}})
        store.load()
        self.assertEqual(store["act-1"]["steps"], steps)

    def test_unknown_step_type_drops_the_entry_with_a_warning(self) -> None:
        payload = {"act-1": {"action_id": "act-1", "steps": [{"type": "from_the_future"}]}}
        store, _ = self._store(payload)
        with self.assertLogs("ai_assistant.persistence.pending_store", level="WARNING") as logs:
            store.load()
        self.assertNotIn("act-1", list(store))
        self.assertIn("Dropping pending action act-1", logs.output[0])
        self.assertIn("outside the step vocabulary", logs.output[0])

    def test_non_list_steps_drop_the_entry(self) -> None:
        store, _ = self._store({"act-1": {"action_id": "act-1", "steps": "not-a-list"}})
        with self.assertLogs("ai_assistant.persistence.pending_store", level="WARNING"):
            store.load()
        self.assertEqual(len(store), 0)

    def test_non_dict_step_element_drops_the_entry(self) -> None:
        store, _ = self._store({"act-1": {"action_id": "act-1", "steps": [42]}})
        with self.assertLogs("ai_assistant.persistence.pending_store", level="WARNING"):
            store.load()
        self.assertEqual(len(store), 0)

    def test_one_corrupt_entry_does_not_sink_its_neighbours(self) -> None:
        payload = {
            "bad": {"steps": [{"type": "nope"}]},
            "good": {"steps": [{"type": "final_answer", "content": "ok"}]},
            "no_steps": {"kind": "plain"},
        }
        store, _ = self._store(payload)
        with self.assertLogs("ai_assistant.persistence.pending_store", level="WARNING"):
            store.load()
        self.assertNotIn("bad", list(store))
        self.assertIn("good", list(store))
        self.assertIn("no_steps", list(store))
        self.assertEqual(store["good"]["steps"][0]["type"], "final_answer")

    def test_non_dict_values_stay_skipped(self) -> None:
        """Pre-existing contract: only dict entries are ever store state."""
        store, _ = self._store({"junk": ["not", "a", "dict"], "ok": {"steps": []}})
        store.load()
        self.assertEqual(list(store), ["ok"])


class ContinuationRestoreTests(unittest.TestCase):
    """The metadata crossing: submitter-controlled continuation re-gated."""

    @staticmethod
    def _continuation(steps: object) -> dict:
        return {
            "messages": [{"role": "user", "content": "hi"}],
            "steps": steps,
            "iteration": 3,
            "pending": {"kind": "approval", "tool": "run_command", "params": {}, "raw": "{}"},
        }

    def test_valid_continuation_restores_with_typed_steps(self) -> None:
        restored = DeterministicAgentOrchestrator._restore(
            self._continuation([{"type": "tool_call", "tool": "run_command"}])
        )
        self.assertIsNotNone(restored)
        _, steps, iteration, pending = restored  # type: ignore[union-attr] — pinned above
        self.assertEqual(steps[0]["type"], "tool_call")
        self.assertEqual(iteration, 3)
        self.assertEqual(pending["kind"], "approval")

    def test_unknown_step_type_refuses_restore(self) -> None:
        restored = DeterministicAgentOrchestrator._restore(
            self._continuation([{"type": "injected"}])
        )
        self.assertIsNone(restored)

    def test_malformed_step_container_refuses_restore(self) -> None:
        for steps in ("not-a-list", [{"not": "a step"}], [42]):
            with self.subTest(steps=steps):
                self.assertIsNone(DeterministicAgentOrchestrator._restore(self._continuation(steps)))


class InboundModelPinTests(unittest.TestCase):
    """No request model may carry a ``steps`` field (ADR 0019)."""

    def test_http_request_models_have_no_steps_field(self) -> None:
        from ai_assistant.agents.models import (
            AgentApproveRequest,
            AgentCancelRequest,
            AgentExecuteRequest,
            AgentUiActionResultRequest,
        )

        for model in (AgentExecuteRequest, AgentApproveRequest, AgentUiActionResultRequest,
                      AgentCancelRequest):
            with self.subTest(model=model.__name__):
                self.assertNotIn("steps", model.model_fields)

    def test_a2a_request_models_have_no_steps_field(self) -> None:
        from ai_assistant.adapters.a2a_models import (
            A2AMessage,
            A2AMessagePart,
            ResumeTaskParams,
            SendMessageConfiguration,
            SendMessageParams,
            SendTaskParams,
        )

        for model in (A2AMessagePart, A2AMessage, SendTaskParams, SendMessageConfiguration,
                      SendMessageParams, ResumeTaskParams):
            with self.subTest(model=model.__name__):
                self.assertNotIn("steps", model.model_fields)

    def test_agent_run_result_steps_are_typed(self) -> None:
        """The application→HTTP tuple is ``tuple[Step, ...]``, not ``dict[str, Any]``."""
        hints = typing.get_type_hints(AgentRunResult)
        self.assertEqual(hints["steps"], tuple[Step, ...])
