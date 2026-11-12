"""Outbound web-search tool bound to an injected provider."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ai_assistant.domain.governance import has_sensitive_content, scrub_pii
from ai_assistant.ports import WebSearchPort

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def web_search_tool(provider: WebSearchPort | None) -> Handler:
    """Build a source-preserving web-search handler."""

    def tool_web_search(params: dict[str, Any]) -> dict[str, Any]:
        query = str(params.get("query", "")).strip()
        if not query:
            return {"error": "Web search query must not be empty."}
        if has_sensitive_content(query):
            return {"error": "Web search refused a query containing credential material."}
        if provider is None:
            return {"error": "Web search provider is not configured."}
        max_results = max(1, min(int(params.get("max_results", 5)), 10))
        safe_query = scrub_pii(query)
        try:
            results = provider.search(safe_query, max_results=max_results)
        except Exception as error:
            return {"error": f"Web search failed: {error}"}
        return {
            "query": safe_query,
            "found": bool(results),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "results": results,
        }

    return tool_web_search


__all__ = ["web_search_tool"]
