"""Configurable, key-free HTML web-search adapter."""
from __future__ import annotations

from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ai_assistant.settings import WebSearchSettings


class WebSearchDisabledError(RuntimeError):
    """The deployment has no enabled outbound search endpoint."""


class WebSearchResponseTooLargeError(RuntimeError):
    """The search endpoint exceeded the bounded response size."""


class WebSearchChallengeError(RuntimeError):
    """The search endpoint returned an interactive bot challenge."""


class _NoRedirect(HTTPRedirectHandler):
    """Do not send a user's query to a search endpoint's redirect target."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class _ResultParser(HTMLParser):
    """Extract DuckDuckGo-compatible result anchors and snippets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._field = ""
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", "div"}:
            return
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._field, self._href, self._text = "title", values.get("href") or "", []
        elif "result__snippet" in classes:
            self._field, self._text = "snippet", []

    def handle_data(self, data: str) -> None:
        if self._field:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._field == "title" and tag == "a":
            title = " ".join("".join(self._text).split())
            if title and self._href:
                self.results.append({"title": title, "url": _source_url(self._href), "snippet": ""})
            self._field, self._href, self._text = "", "", []
        elif self._field == "snippet" and tag in {"a", "div"}:
            snippet = " ".join("".join(self._text).split())
            if self.results and snippet:
                self.results[-1]["snippet"] = snippet
            self._field, self._text = "", []


def _source_url(value: str) -> str:
    """Unwrap DuckDuckGo's result redirect without following it."""
    parts = urlsplit(value)
    target = parse_qs(parts.query).get("uddg", [])
    return target[0] if target else value


def _validated_endpoint(endpoint: str) -> str:
    """Require an explicit HTTP(S) URL so env configuration cannot open file:// targets."""
    parts = urlsplit(endpoint)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        msg = "Web search endpoint must be an http:// or https:// URL"
        raise ValueError(msg)
    return endpoint


def _looks_like_challenge(html: str) -> bool:
    """Detect anti-bot challenge pages so zero results are not mistaken for absence."""
    lowered = html.casefold()
    return "anomaly-modal" in lowered or "challenge-form" in lowered


class HtmlWebSearchClient:
    """Search client whose network and limits are supplied by configuration."""

    def __init__(self, settings: WebSearchSettings,
                 open_request: Callable[..., Any] | None = None) -> None:
        self._settings = settings
        self._open = open_request or build_opener(_NoRedirect()).open

    @property
    def enabled(self) -> bool:
        """Whether outbound search is enabled and configured."""
        return bool(self._settings.enabled and self._settings.endpoint)

    def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        if not self.enabled:
            raise WebSearchDisabledError
        endpoint = _validated_endpoint(self._settings.endpoint)
        body = urlencode({"q": query}).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self._settings.user_agent,
            },
            method="POST",
        )
        with self._open(request, timeout=self._settings.timeout_seconds) as response:
            payload = response.read(1_000_001)
        if len(payload) > 1_000_000:
            raise WebSearchResponseTooLargeError
        html = payload.decode("utf-8", errors="replace")
        parser = _ResultParser()
        parser.feed(html)
        if not parser.results and _looks_like_challenge(html):
            raise WebSearchChallengeError
        limit = min(max_results, self._settings.max_results)
        return parser.results[:limit]


__all__ = ["HtmlWebSearchClient"]
