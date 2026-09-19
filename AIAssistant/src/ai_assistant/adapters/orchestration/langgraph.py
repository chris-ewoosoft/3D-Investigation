"""Compatibility adapter around LangGraph during the phased migration."""
from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from ...domain.tasks import AgentTask


class LegacyLangGraphOrchestrator:
    """Converts a platform task into the existing LangGraph runtime contract.

    No protocol, persistence, permission, plugin, or routing policy lives in
    this adapter. Replacing LangGraph therefore changes only this module's
    implementation and bootstrap wiring.
    """

    def __init__(self, run: Callable[..., dict[str, Any]], build_system_prompt: Callable[[str], str],
                 is_available: Callable[[], bool]) -> None:
        self._run = run
        self._build_system_prompt = build_system_prompt
        self._is_available = is_available

    def execute(self, task: AgentTask) -> dict[str, Any]:
        if not self._is_available():
            raise RuntimeError("Agent orchestration is unavailable")
        language = str(task.metadata.get("language", "vi"))
        return self._run(
            system_prompt=self._build_system_prompt(language), task=task.message, session_id=task.id,
            temperature=float(task.metadata.get("temperature", 0.2)), language=language,
            request_started=monotonic(),
        )
