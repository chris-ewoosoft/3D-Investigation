"""ToolApp Agent for canonical Qt desktop actions.

The agent owns intent matching and the desktop-action handoff. It never reads
repository files or invokes coding/RAG tools; execution still happens through
the existing Qt acknowledgement protocol.
"""
from __future__ import annotations

from collections.abc import Callable

from .agent_logging import get_agent_logger

logger = get_agent_logger("toolapp")


class ToolAppAgent:
    def __init__(self, match_single: Callable[[str], dict | None],
                 match_sequence: Callable[[str], list[dict] | None]) -> None:
        self._match_single = match_single
        self._match_sequence = match_sequence

    def match_sequence(self, task: str) -> list[dict] | None:
        """Return only workflows explicitly declared by the action contract."""
        return self._match_sequence(task)

    def match(self, task: str) -> tuple[dict | None, list[dict] | None]:
        sequence = self.match_sequence(task)
        if sequence:
            logger.info("ToolApp Agent matched workflow | steps=%s", [item.get("action") for item in sequence])
            return sequence[0], sequence
        action = self._match_single(task)
        if action:
            logger.info("ToolApp Agent matched action | action=%s", action.get("action"))
        return action, None

    @staticmethod
    def instruction() -> str:
        return (
            "You are the ToolApp Agent. For Qt/application requests, call only "
            "the canonical application_action tool, one action at a time, and "
            "wait for the Qt acknowledgement before continuing. Do not use "
            "RAG, repository research, or coding tools to discover UI actions."
        )
