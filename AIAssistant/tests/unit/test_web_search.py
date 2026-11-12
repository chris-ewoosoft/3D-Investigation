from __future__ import annotations

import unittest
from typing import Any
from urllib.parse import parse_qs

from ai_assistant.adapters.web_search import (
    HtmlWebSearchClient,
    WebSearchChallengeError,
    WebSearchDisabledError,
    WebSearchResponseTooLargeError,
    _NoRedirect,
    _ResultParser,
    _source_url,
)
from ai_assistant.orchestration.helpers import normalise_plan_payload, plan_step_execution_contract
from ai_assistant.orchestration.nodes.reason_prepare import _directive_message, _forced_envelope
from ai_assistant.orchestration.specialists.chatbot import ChatbotAgent, classify_web_freshness
from ai_assistant.settings import WebSearchSettings
from ai_assistant.tools.builtin.web_tools import web_search_tool
from ai_assistant.tools.factory import create_tool_registry


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class _Provider:
    def __init__(self, results: list[dict[str, str]] | None = None,
                 error: Exception | None = None) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return self.results


class _Backend:
    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.calls = 0

    def generate(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {"choices": [{"message": {"content": self.decision}}]}


class _Runtime:
    is_vision_model = False


class _Rag:
    def get_context(self, *_args: Any, **_kwargs: Any) -> tuple[str, str, list[Any]]:
        return "rag", "", []


class _Prompts:
    def is_character_query(self, _query: str) -> bool:
        return False

    def build_messages(self, messages: list[dict[str, Any]], doc_ctx: str, *_args: Any,
                       **_kwargs: Any) -> list[dict[str, Any]]:
        return [*messages, {"role": "system", "content": doc_ctx}]

    def strip_citations(self, answer: str) -> str:
        return answer


class WebSearchSettingsTests(unittest.TestCase):
    def test_settings_are_deployment_configurable_and_bounded(self) -> None:
        settings = WebSearchSettings.from_env({
            "AI_WEB_SEARCH_ENABLED": "0",
            "AI_WEB_SEARCH_ENDPOINT": "https://search.example/",
            "AI_WEB_SEARCH_TIMEOUT": "99",
            "AI_WEB_SEARCH_MAX_RESULTS": "99",
            "AI_WEB_SEARCH_USER_AGENT": "agent-test",
        })
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.endpoint, "https://search.example/")
        self.assertEqual((settings.timeout_seconds, settings.max_results), (30, 10))
        self.assertEqual(settings.user_agent, "agent-test")


class HtmlWebSearchClientTests(unittest.TestCase):
    def _settings(self, *, enabled: bool = True) -> WebSearchSettings:
        return WebSearchSettings(enabled, "https://search.example/", 4, 3, "test-agent")

    def test_search_posts_encoded_query_and_preserves_sources(self) -> None:
        captured: dict[str, Any] = {}
        html = b'''<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fofficial.example%2Fp">Official</a>
        <div class="result__snippet">No official price announced.</div>'''

        def opener(request: Any, *, timeout: int) -> _Response:
            captured.update(data=request.data, timeout=timeout, agent=request.headers["User-agent"])
            return _Response(html)

        results = HtmlWebSearchClient(self._settings(), opener).search("future phone price", max_results=5)
        self.assertEqual(parse_qs(captured["data"].decode())["q"], ["future phone price"])
        self.assertEqual((captured["timeout"], captured["agent"]), (4, "test-agent"))
        self.assertEqual(results, [{
            "title": "Official", "url": "https://official.example/p",
            "snippet": "No official price announced.",
        }])

    def test_disabled_and_oversized_responses_fail_closed(self) -> None:
        with self.assertRaises(WebSearchDisabledError):
            HtmlWebSearchClient(self._settings(enabled=False)).search("q", max_results=1)
        client = HtmlWebSearchClient(self._settings(), lambda *_args, **_kwargs: _Response(b"x" * 1_000_001))
        with self.assertRaises(WebSearchResponseTooLargeError):
            client.search("q", max_results=1)

    def test_parser_ignores_non_results_and_handles_plain_urls(self) -> None:
        parser = _ResultParser()
        parser.feed(
            '<span>ignored</span><div class="other">ignored too</div><a class="result__a"></a>'
            '<div class="result__snippet">orphan</div>'
            '<a class="result__a" href="https://source.test"> Plain source </a>'
            '<a class="result__snippet"></a>'
        )
        self.assertEqual(parser.results, [{
            "title": "Plain source", "url": "https://source.test", "snippet": "",
        }])
        self.assertEqual(_source_url("https://source.test/no-query"), "https://source.test/no-query")
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", None, "https://other.test"))

    def test_default_opener_is_constructed_without_network_io(self) -> None:
        client = HtmlWebSearchClient(self._settings())
        self.assertTrue(callable(client._open))
        self.assertTrue(client.enabled)
        self.assertFalse(HtmlWebSearchClient(self._settings(enabled=False)).enabled)

    def test_non_http_endpoint_and_challenge_page_fail_closed(self) -> None:
        file_client = HtmlWebSearchClient(
            WebSearchSettings(True, "file:///etc/passwd", 4, 3, "test-agent"),
            lambda *_args, **_kwargs: _Response(b""),
        )
        with self.assertRaises(ValueError):
            file_client.search("query", max_results=1)

        challenge_html = b'<div class="anomaly-modal">Verify you are human</div>'
        challenge_client = HtmlWebSearchClient(
            self._settings(),
            lambda *_args, **_kwargs: _Response(challenge_html),
        )
        with self.assertRaises(WebSearchChallengeError):
            challenge_client.search("query", max_results=1)

        empty_client = HtmlWebSearchClient(
            self._settings(),
            lambda *_args, **_kwargs: _Response(b"<html><body>No matches</body></html>"),
        )
        self.assertEqual(empty_client.search("query", max_results=1), [])


class WebSearchToolTests(unittest.TestCase):
    def test_tool_refuses_missing_private_or_unconfigured_queries(self) -> None:
        handler = web_search_tool(None)
        self.assertIn("error", handler({"query": ""}))
        self.assertIn("error", handler({"query": "current price"}))
        provider = _Provider()
        self.assertIn("error", web_search_tool(provider)({"query": "api_key=sk-abcdefghijklmnop"}))
        self.assertEqual(provider.calls, [])

    def test_tool_returns_timestamped_sources_and_contains_provider_failure(self) -> None:
        provider = _Provider([{"title": "Source", "url": "https://example.test", "snippet": "Fact"}])
        result = web_search_tool(provider)({"query": "price contact me@example.com", "max_results": 50})
        self.assertTrue(result["found"])
        self.assertIn("retrieved_at", result)
        self.assertEqual(provider.calls, [("price contact [REDACTED_EMAIL]", 10)])
        failed = web_search_tool(_Provider(error=RuntimeError("offline")))({"query": "current price"})
        self.assertIn("offline", failed["error"])


class WebSearchRoutingTests(unittest.TestCase):
    def test_freshness_classifier_exposes_only_closed_decisions(self) -> None:
        self.assertEqual(classify_web_freshness("current price", _Backend("WEB_SEARCH")), "WEB_SEARCH")
        self.assertEqual(classify_web_freshness("explain OOP", _Backend("DIRECT")), "DIRECT")
        self.assertIsNone(classify_web_freshness("ambiguous", _Backend("MAYBE")))

        class _NonTextBackend:
            def generate(self, **_kwargs: Any) -> dict[str, Any]:
                return {"choices": [{"message": {"content": object()}}]}

        self.assertIsNone(classify_web_freshness("invalid", _NonTextBackend()))

    def test_web_kind_survives_normalisation_and_builds_a_contract(self) -> None:
        _, spec = normalise_plan_payload({"step_kinds": ["web_search"]})
        self.assertEqual(spec["step_kinds"], ["web_search"])
        contract = plan_step_execution_contract("current external fact", lambda _text: [], lambda text: text,
                                                kind="web_search")
        self.assertEqual(contract, {"mode": "web_search", "query": "current external fact"})

    def test_chat_uses_model_decision_and_injects_untrusted_sources(self) -> None:
        backend = _Backend("WEB_SEARCH")
        provider = _Provider([{"title": "Official", "url": "https://official.test", "snippet": "Not announced"}])
        agent = ChatbotAgent(_Runtime(), _Rag(), _Prompts(), provider, lambda: backend)
        messages, metadata = agent.build_messages(
            [{"role": "user", "content": "price of an unannounced product"}],
            "price of an unannounced product", None, "en",
        )
        self.assertEqual(backend.calls, 1)
        self.assertEqual(provider.calls, [("price of an unannounced product", 5)])
        self.assertIn("UNTRUSTED WEB EVIDENCE", messages[-1]["content"])
        self.assertEqual(metadata["web_sources"][0]["url"], "https://official.test")

    def test_chat_does_not_search_when_model_classifies_stable_knowledge(self) -> None:
        backend = _Backend("DIRECT")
        provider = _Provider()
        agent = ChatbotAgent(_Runtime(), _Rag(), _Prompts(), provider, lambda: backend)
        agent.build_messages([{"role": "user", "content": "What is OOP?"}], "What is OOP?", None, "en")
        self.assertEqual(provider.calls, [])

    def test_chat_contains_search_failures_and_empty_results(self) -> None:
        backend = _Backend("WEB_SEARCH")
        failing = ChatbotAgent(_Runtime(), _Rag(), _Prompts(), _Provider(error=RuntimeError("offline")),
                               lambda: backend)
        _, metadata = failing.build_messages([], "current price", None, "en")
        self.assertNotIn("web_sources", metadata)
        empty = ChatbotAgent(_Runtime(), _Rag(), _Prompts(), _Provider(), lambda: backend)
        messages, metadata = empty.build_messages([], "current price", None, "en")
        self.assertEqual(messages[-1]["content"], "rag")
        self.assertNotIn("web_sources", metadata)

    def test_chat_does_not_egress_without_dependencies_or_with_a_secret(self) -> None:
        provider = _Provider()
        ChatbotAgent(_Runtime(), _Rag(), _Prompts(), provider).build_messages([], "current price", None, "en")
        agent = ChatbotAgent(_Runtime(), _Rag(), _Prompts(), provider, lambda: _Backend("WEB_SEARCH"))
        agent.build_messages([], "token=abcdefghijklmnop", None, "en")
        self.assertEqual(provider.calls, [])

    def test_chat_skips_freshness_model_call_when_web_search_is_disabled(self) -> None:
        backend = _Backend("WEB_SEARCH")
        disabled = HtmlWebSearchClient(WebSearchSettings(False, "https://search.example/", 4, 3, "test-agent"))
        agent = ChatbotAgent(_Runtime(), _Rag(), _Prompts(), disabled, lambda: backend)
        messages, metadata = agent.build_messages([], "Who is leading today?", None, "en")
        self.assertEqual(backend.calls, 0)
        self.assertEqual(messages[-1]["content"], "rag")
        self.assertNotIn("web_sources", metadata)

    def test_character_like_web_query_does_not_suppress_web_citations(self) -> None:
        class _CharPrompts(_Prompts):
            def is_character_query(self, _query: str) -> bool:
                return True

        backend = _Backend("WEB_SEARCH")
        provider = _Provider([{"title": "Official", "url": "https://official.test", "snippet": "CEO Bio"}])
        agent = ChatbotAgent(_Runtime(), _Rag(), _CharPrompts(), provider, lambda: backend)
        _messages, metadata = agent.build_messages([], "Who is the CEO of ExampleCo?", None, "en")
        self.assertFalse(metadata["suppress_citations"])
        self.assertEqual(metadata["web_sources"][0]["url"], "https://official.test")

    def test_factory_excludes_web_search_when_disabled_and_sets_30s_budget(self) -> None:
        enabled_registry = create_tool_registry()
        web_spec = enabled_registry.get("web_search")
        self.assertIsNotNone(web_spec)
        if web_spec is not None:
            self.assertEqual(web_spec.timeout_seconds, 30)

        disabled_registry = create_tool_registry(include_web_search=False)
        self.assertIsNone(disabled_registry.get("web_search"))

    def test_reason_preparation_forces_web_search_and_explains_it(self) -> None:
        contract = {"mode": "web_search", "query": "current price"}
        directive = _directive_message("verify current price", contract, {}, [], 0)
        self.assertIn("call web_search", directive["content"])
        self.assertEqual(
            _forced_envelope(contract, None, [], 0),
            {"kind": "tool", "tool": "web_search", "params": {"query": "current price"}},
        )

    def test_reason_preparation_yields_when_web_evidence_exists(self) -> None:
        contract = {"mode": "web_search", "query": "current price"}
        evidence = {
            "type": "tool_result", "tool": "web_search",
            "plan_step_index": 0, "result": {"results": [{"title": "Official"}]},
        }
        directive = _directive_message("verify current price", contract, {}, [evidence], 0)
        self.assertIn("Bằng chứng web cho bước này đã có", directive["content"])
        self.assertIsNone(_forced_envelope(contract, "verify current price", [evidence], 0))

        evidence_no_plan = {
            "type": "tool_result", "tool": "web_search",
            "result": {"results": [{"title": "Official"}]},
        }
        self.assertIsNone(_forced_envelope(contract, None, [evidence_no_plan], 0))


if __name__ == "__main__":
    unittest.main()
