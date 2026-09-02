"""Per-agent rotating loggers, separate from the server aggregate log."""
from __future__ import annotations

import logging
import logging.handlers
import os
from functools import lru_cache

from .config import LOGS_DIR


@lru_cache(maxsize=None)
def get_agent_logger(agent_name: str) -> logging.Logger:
    """Return a logger writing this agent's events to its own file."""
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in agent_name.lower())
    agent_logger = logging.getLogger(f"agent.{safe_name}")
    agent_logger.setLevel(logging.DEBUG)
    agent_logger.propagate = True
    if not agent_logger.handlers:
        os.makedirs(LOGS_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOGS_DIR, f"agent_{safe_name}.log"),
            maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S",
        ))
        handler.setLevel(logging.DEBUG)
        agent_logger.addHandler(handler)
    return agent_logger
