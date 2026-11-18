from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from ai_assistant.agents.graph_wiring import _streaming_response
from ai_assistant.orchestration.state import CONTEXT_EXHAUSTED_MESSAGE


class StreamingResponseWiringTests(unittest.TestCase):
    @staticmethod
    def _deps(*, context_size: int = 1024):
        traces: list[dict] = []

        @contextmanager
        def span(*_args, **_kwargs):
            context: dict = {}
            traces.append(context)
            yield context

        deps = SimpleNamespace(
            chars_per_token=4.0,
            llm_n_ctx=context_size,
            llm_runtime=object(),
            backend_mode_fn=lambda: "llama_cpp",
            openai_compatible_fn=lambda *_args, **_kwargs: {},
            record_token_usage_fn=lambda *_args: None,
            task_coordinator=SimpleNamespace(is_cancelled=lambda _session: False),
            span_fn=span,
        )
        return deps, traces

    def test_context_exhaustion_returns_without_starting_a_stream(self) -> None:
        deps, _traces = self._deps(context_size=8)
        answer = _streaming_response(
            [{"role": "user", "content": "x" * 100}], 0.2,
            deps=deps, session_id="s1", event_sink=None,
        )
        self.assertEqual(answer, CONTEXT_EXHAUSTED_MESSAGE)

    def test_stream_publishes_nonempty_deltas_and_records_trace_output(self) -> None:
        deps, traces = self._deps()
        events: list[dict] = []

        def complete(*args):
            delta_sink = args[-2]
            delta_sink("")
            delta_sink("hello")
            self.assertFalse(args[-1]())
            return "hello"

        with patch(
            "ai_assistant.agents.completion.streaming_text_completion",
            side_effect=complete,
        ):
            answer = _streaming_response(
                [{"role": "user", "content": "hi"}], 0.2,
                deps=deps, session_id="s1", event_sink=events.append,
            )

        self.assertEqual(answer, "hello")
        self.assertEqual(events, [{
            "_stream_event": "delta", "message_id": "s1", "content": "hello",
        }])
        self.assertEqual(traces[0]["outputs"], {"answer_chars": 5, "streamed": True})


if __name__ == "__main__":
    unittest.main()
