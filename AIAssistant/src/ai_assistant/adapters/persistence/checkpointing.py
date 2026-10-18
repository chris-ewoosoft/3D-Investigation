"""Selectable LangGraph checkpoint backend with safe local fallback."""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from ai_assistant.settings import load_agent_runtime_settings

logger = logging.getLogger(__name__)


def _build_configured_saver(backend: str, url: str, path: str) -> Any | None:
    """Build the explicitly configured saver, or ``None`` when unusable here."""
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
    if backend in ("sqlite", "default"):
        from langgraph.checkpoint.sqlite import SqliteSaver

        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        logger.info("Using SQLite LangGraph checkpointer at %s", path)
        return saver
    return None


def build_checkpointer() -> Any:
    """Return Postgres/Redis saver when explicitly configured, otherwise memory.

    With ``AGENT_CHECKPOINT_REQUIRED=1`` (production gate, ADR 0023) the
    in-memory fallback is refused: a run that silently degrades to a
    process-local checkpointer is not durable, so startup fails instead.
    """
    runtime_settings = load_agent_runtime_settings()
    backend = runtime_settings.checkpoint_backend
    url = runtime_settings.checkpoint_url
    required = runtime_settings.checkpoint_required
    if required and backend == "memory":
        msg = ("AGENT_CHECKPOINT_REQUIRED=1 refuses the in-memory checkpointer; "
               "configure AGENT_CHECKPOINT_BACKEND=sqlite|postgres|redis.")
        raise RuntimeError(msg)
    try:
        saver = _build_configured_saver(backend, url, runtime_settings.checkpoint_path)
        if saver is not None:
            return saver
    except Exception as error:
        if required:
            msg = (f"Checkpoint backend {backend!r} failed to initialize; "
                   "AGENT_CHECKPOINT_REQUIRED=1 refuses the memory fallback.")
            raise RuntimeError(msg) from error
        logger.exception("Checkpoint backend unavailable; using MemorySaver")
        return MemorySaver()
    if required:
        msg = (f"Checkpoint backend {backend!r} is not usable; "
               "AGENT_CHECKPOINT_REQUIRED=1 refuses the memory fallback.")
        raise RuntimeError(msg)
    if backend != "memory":
        logger.warning("Checkpoint backend %s is not configured; using MemorySaver", backend)
    return MemorySaver()


def cleanup_old_checkpoints(max_days: int = 30, max_size_mb: int = 50) -> None:
    """Clean up old checkpoints when using SQLite."""
    runtime_settings = load_agent_runtime_settings()
    backend = runtime_settings.checkpoint_backend
    if backend not in ("sqlite", "default"):
        return

    db_path = runtime_settings.checkpoint_path
    if not os.path.exists(db_path):
        return

    try:
        stat = os.stat(db_path)
        size_mb = stat.st_size / (1024 * 1024)
        if size_mb > max_size_mb:
            logger.info("Checkpoint database size (%.1fMB) exceeds limit (%.1fMB); running VACUUM", size_mb, max_size_mb)
            _vacuum_database(db_path)
    except Exception as error:
        logger.warning("Could not clean up checkpoints: %s", error)


def _vacuum_database(db_path: str) -> None:
    """Prune old checkpoint rows and compact the SQLite file.

    The connection is always closed, including when ``VACUUM`` fails (for
    example inside an implicit transaction): a leaked handle keeps the file
    locked on Windows.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DELETE FROM checkpoints WHERE thread_id NOT IN (SELECT thread_id FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 100)")
            conn.execute("DELETE FROM writes WHERE thread_id NOT IN (SELECT thread_id FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 100)")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
        conn.execute("VACUUM")
    finally:
        if conn is not None:
            conn.close()


__all__ = [
    "build_checkpointer",
    "cleanup_old_checkpoints",
]
