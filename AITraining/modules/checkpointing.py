"""Selectable LangGraph checkpoint backend with safe local fallback."""
from __future__ import annotations

import logging
import os
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


def build_checkpointer() -> Any:
    """Return Postgres/Redis saver when explicitly configured, otherwise memory.

    External services are opt-in so the desktop app remains self-contained.
    Required optional packages: langgraph-checkpoint-postgres or
    langgraph-checkpoint-redis.
    """
    backend = os.getenv("AGENT_CHECKPOINT_BACKEND", "memory").casefold()
    url = os.getenv("AGENT_CHECKPOINT_URL", "")
    try:
        if backend == "postgres" and url:
            from langgraph.checkpoint.postgres import PostgresSaver
            saver = PostgresSaver.from_conn_string(url)
            saver.setup()
            logger.info("Using Postgres LangGraph checkpointer")
            return saver
        if backend == "redis" and url:
            from langgraph.checkpoint.redis import RedisSaver
            saver = RedisSaver.from_conn_string(url)
            saver.setup()
            logger.info("Using Redis LangGraph checkpointer")
            return saver
        if backend != "memory":
            logger.warning("Checkpoint backend %s is not configured; using MemorySaver", backend)
    except Exception as error:  # noqa: BLE001
        logger.exception("Checkpoint backend unavailable; using MemorySaver: %s", error)
    return MemorySaver()
