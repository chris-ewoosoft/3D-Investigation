"""LangSmith integration for tracing LLM execution.

Enabled only if LANGSMITH_TRACING is set and LANGSMITH_API_KEY is provided.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

_langsmith_enabled = False
_langsmith_client = None
_active_langsmith_run: ContextVar[str | None] = ContextVar(
    "active_langsmith_run", default=None,
)
_ls_project = os.getenv("LANGSMITH_PROJECT", "3d-reconstruction")

if (os.getenv("LANGSMITH_TRACING", "").lower() in {"true", "1", "yes"}
        and os.getenv("LANGSMITH_API_KEY", "")):
    try:
        import langsmith

        _langsmith_client = langsmith.Client(
            api_key=os.environ["LANGSMITH_API_KEY"],
            api_url=os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
        )
        _langsmith_enabled = True
    except (ImportError, Exception):
        # Observability must never prevent the server from starting.
        pass


def langsmith_available() -> bool:
    """Return whether LangSmith tracing has an enabled, usable client."""
    return _langsmith_enabled


def get_langsmith_client() -> Any:
    """Return the LangSmith client, or ``None`` when tracing is disabled."""
    return _langsmith_client


def start_langsmith_run(name: str, run_type: str, inputs: dict[str, Any],
                        metadata: dict[str, Any] | None = None) -> str | None:
    """Start a nested run without allowing telemetry failures to escape."""
    if not _langsmith_enabled or _langsmith_client is None:
        return None
    run_id = str(uuid4())
    payload: dict[str, Any] = {
        "name": name,
        "run_type": run_type,
        "id": run_id,
        "project_name": _ls_project,
        "inputs": inputs,
    }
    parent_run_id = _active_langsmith_run.get()
    if parent_run_id:
        payload["parent_run_id"] = parent_run_id
    if metadata:
        payload["extra"] = {"metadata": metadata}
    try:
        _langsmith_client.create_run(**payload)
        return run_id
    except TypeError:
        # Compatibility with older clients that accepted ``run_id`` instead.
        payload["run_id"] = payload.pop("id")
        try:
            _langsmith_client.create_run(**payload)
            return run_id
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None


def finish_langsmith_run(run_id: str, outputs: dict[str, Any],
                         error: Exception | None = None) -> None:
    """End a run using the current or a compatible older SDK signature."""
    if _langsmith_client is None:
        return
    payload: dict[str, Any] = {"run_id": run_id, "outputs": outputs}
    if error is not None:
        payload["error"] = str(error)
    try:
        _langsmith_client.update_run(**payload)
    except TypeError:
        payload["id"] = payload.pop("run_id")
        try:
            _langsmith_client.update_run(**payload)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def record_langsmith_feedback(key: str, score: float | None = None,
                              value: Any | None = None, comment: str | None = None,
                              run_id: str | None = None) -> bool:
    """Attach evaluation feedback to the active (or supplied) LangSmith run."""
    if not _langsmith_enabled or _langsmith_client is None:
        return False
    target_run_id = run_id or _active_langsmith_run.get()
    if not target_run_id:
        return False
    payload: dict[str, Any] = {"run_id": target_run_id, "key": key}
    if score is not None:
        payload["score"] = score
    if value is not None:
        payload["value"] = value
    if comment:
        payload["comment"] = comment
    try:
        _langsmith_client.create_feedback(**payload)
        return True
    except Exception:  # noqa: BLE001
        return False
