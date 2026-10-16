"""Per-session run serialization (architecture review, finding 3).

``AgentService.execute`` must let at most one run occupy a session at a time:
concurrent runs for one session share a checkpointer ``thread_id`` and clobber
the coordinator's running record.  Distinct sessions must stay independent —
the lock is per session, never global — and ``cancel`` must keep working while
a run holds its session's lock (it takes no session lock at all).

The two paths that *resume* a run — HITL approval and the desktop-ack
continuation — re-enter the same per-session lock: they share the checkpointer
``thread_id`` and the coordinator record with any concurrent execute of their
session, so letting them run outside the lock would recreate the very overlap
the lock exists to prevent.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import ai_assistant.agents.context as agents_context
from ai_assistant.agents.models import (
    AgentApproveRequest,
    AgentExecuteRequest,
    AgentUiActionResultRequest,
)
from ai_assistant.agents.service import AgentService

# Generous on purpose: a gate timeout means the lock itself broke, not that
# the machine is slow.
_GATE_TIMEOUT = 30.0


class SessionSerializationTests(unittest.TestCase):
    def _service(self) -> AgentService:
        llm = MagicMock()
        llm.llm = MagicMock()
        return AgentService(llm_runtime=llm)

    def test_the_lock_is_reused_per_session_and_never_shared(self):
        service = self._service()
        self.assertIs(service._session_lock("s1"), service._session_lock("s1"))
        self.assertIsNot(service._session_lock("s1"), service._session_lock("s2"))

    def test_two_runs_for_one_session_never_overlap(self):
        service = self._service()
        first_inside = threading.Event()
        release_first = threading.Event()
        self.addCleanup(release_first.set)
        guard = threading.Lock()
        state = {"active": 0, "runs": 0, "max_active": 0}

        def fake_run(**kwargs):
            with guard:
                state["runs"] += 1
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                if state["runs"] == 1:
                    first_inside.set()
                    if not release_first.wait(timeout=_GATE_TIMEOUT):
                        msg = "gate was never released"
                        raise RuntimeError(msg)
            finally:
                with guard:
                    state["active"] -= 1
            return {"status": "completed"}

        with patch.object(agents_context, "run_langgraph_agent", side_effect=fake_run):
            first = threading.Thread(
                target=service.execute,
                args=(AgentExecuteRequest(task="first", session_id="s1"),),
            )
            first.start()
            self.assertTrue(first_inside.wait(timeout=_GATE_TIMEOUT))
            second = threading.Thread(
                target=service.execute,
                args=(AgentExecuteRequest(task="second", session_id="s1"),),
            )
            second.start()
            # Give the second run every chance to break in: while the first
            # still holds the session's lock it must stay outside.  A slow
            # scheduler can only make this assertion pass, never fail it.
            time.sleep(0.2)
            with guard:
                self.assertEqual(state["max_active"], 1)
            release_first.set()
            first.join(timeout=_GATE_TIMEOUT)
            second.join(timeout=_GATE_TIMEOUT)
        with guard:
            self.assertEqual((state["runs"], state["max_active"]), (2, 1))
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())

    def test_a_second_session_runs_while_the_first_is_held(self) -> None:
        service = self._service()
        first_inside = threading.Event()
        release_first = threading.Event()
        self.addCleanup(release_first.set)
        second_finished = threading.Event()

        def fake_run(**kwargs):
            if kwargs["session_id"] == "s1":
                first_inside.set()
                if not release_first.wait(timeout=_GATE_TIMEOUT):
                    msg = "gate was never released"
                    raise RuntimeError(msg)
                return {"status": "completed"}
            second_finished.set()
            return {"status": "completed"}

        with patch.object(agents_context, "run_langgraph_agent", side_effect=fake_run):
            first = threading.Thread(
                target=service.execute,
                args=(AgentExecuteRequest(task="blocked", session_id="s1"),),
            )
            first.start()
            self.assertTrue(first_inside.wait(timeout=_GATE_TIMEOUT))
            second = threading.Thread(
                target=service.execute,
                args=(AgentExecuteRequest(task="parallel", session_id="s2"),),
            )
            second.start()
            second.join(timeout=_GATE_TIMEOUT)
            # s2 completed while s1 is provably still inside its run: the
            # serialization is per session, not a global gate.
            self.assertTrue(second_finished.is_set())
            self.assertTrue(first.is_alive(), "s1 must still hold its run")
            release_first.set()
            first.join(timeout=_GATE_TIMEOUT)
        self.assertFalse(first.is_alive())

    def test_approve_resumes_under_the_sessions_run_lock(self) -> None:
        """An approval *resumes the run*: it serializes with execute.

        The resumed graph shares the checkpointer ``thread_id`` and the
        coordinator record with a concurrent execute of the same session, so
        the approval must queue on the session's lock instead of interleaving.
        """
        service = self._service()
        run_inside = threading.Event()
        release_run = threading.Event()
        self.addCleanup(release_run.set)
        guard = threading.Lock()
        state = {"active": 0, "max_active": 0, "approved": False}

        def enter() -> None:
            with guard:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])

        def leave() -> None:
            with guard:
                state["active"] -= 1

        def fake_run(**kwargs):
            enter()
            try:
                run_inside.set()
                if not release_run.wait(timeout=_GATE_TIMEOUT):
                    msg = "gate was never released"
                    raise RuntimeError(msg)
            finally:
                leave()
            return {"status": "completed"}

        def fake_approve(request):
            enter()
            try:
                with guard:
                    state["approved"] = True
            finally:
                leave()
            return {"status": "completed"}

        with patch.object(agents_context, "run_langgraph_agent", side_effect=fake_run):
            with patch.object(service._approval_service, "approve", side_effect=fake_approve):
                run_thread = threading.Thread(
                    target=service.execute,
                    args=(AgentExecuteRequest(task="block", session_id="s1"),),
                )
                run_thread.start()
                self.assertTrue(run_inside.wait(timeout=_GATE_TIMEOUT))
                approve_thread = threading.Thread(
                    target=service.approve,
                    args=(AgentApproveRequest(action_id="a-1", approved=True, session_id="s1"),),
                )
                approve_thread.start()
                # While the run provably holds the session's lock the resumed
                # run must stay outside it.
                time.sleep(0.2)
                with guard:
                    self.assertFalse(state["approved"])
                    self.assertEqual(state["max_active"], 1)
                release_run.set()
                run_thread.join(timeout=_GATE_TIMEOUT)
                approve_thread.join(timeout=_GATE_TIMEOUT)
        self.assertTrue(state["approved"])
        self.assertEqual(state["max_active"], 1)
        self.assertFalse(approve_thread.is_alive())

    def test_the_desktop_ack_resumes_under_the_owning_sessions_lock(self) -> None:
        """The ack request carries only the request id, so the owning session
        is read from the parked action and its lock is the one taken."""
        service = self._service()
        service.pending_actions["req-1"] = {"session_id": "s1", "ui_ack": True}
        run_inside = threading.Event()
        release_run = threading.Event()
        self.addCleanup(release_run.set)
        ack_done = threading.Event()

        def fake_run(**kwargs):
            run_inside.set()
            if not release_run.wait(timeout=_GATE_TIMEOUT):
                msg = "gate was never released"
                raise RuntimeError(msg)
            return {"status": "completed"}

        def fake_ack(request):
            ack_done.set()
            return {"status": "completed"}

        with patch.object(agents_context, "run_langgraph_agent", side_effect=fake_run):
            run_thread = threading.Thread(
                target=service.execute,
                args=(AgentExecuteRequest(task="block", session_id="s1"),),
            )
            run_thread.start()
            self.assertTrue(run_inside.wait(timeout=_GATE_TIMEOUT))
            with patch.object(service._ui_action_service, "ui_action_result", side_effect=fake_ack):
                ack_thread = threading.Thread(
                    target=service.ui_action_result,
                    args=(AgentUiActionResultRequest(request_id="req-1", success=True),),
                )
                ack_thread.start()
                time.sleep(0.2)
                self.assertFalse(ack_done.is_set(), "the ack must queue on s1's run lock")
                release_run.set()
                ack_thread.join(timeout=_GATE_TIMEOUT)
            run_thread.join(timeout=_GATE_TIMEOUT)
        self.assertTrue(ack_done.is_set())
        self.assertFalse(ack_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
