from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from time import time

from ..domain.security import DataClassification, ExecutionOrigin
from ..domain.tasks import AgentTask, TaskStatus


class SqliteTaskStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id TEXT PRIMARY KEY, capability TEXT NOT NULL, message TEXT NOT NULL, context_id TEXT,
                    metadata TEXT NOT NULL, classification TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, result TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                    data TEXT NOT NULL, created_at REAL NOT NULL
                );
            """)

    def create(self, task: AgentTask) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO agent_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", self._values(task),
            )

    def get(self, task_id: str) -> AgentTask | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._to_task(row) if row else None

    def save(self, task: AgentTask) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE agent_tasks SET metadata=?, status=?, updated_at=?, result=?, error=? WHERE id=?",
                (json.dumps(task.metadata), task.status, task.updated_at, json.dumps(task.result), task.error, task.id),
            )

    def append_event(self, task_id: str, kind: str, data: dict) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO agent_task_events(task_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
                (task_id, kind, json.dumps(data), time()),
            )

    def events(self, task_id: str) -> Iterable[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, kind, data, created_at FROM agent_task_events WHERE task_id=? ORDER BY sequence", (task_id,),
            ).fetchall()
        return tuple({"sequence": row[0], "kind": row[1], "data": json.loads(row[2]), "created_at": row[3]} for row in rows)

    def list(self, *, context_id: str | None = None, limit: int = 100) -> Iterable[AgentTask]:
        limit = max(1, min(limit, 500))
        query = "SELECT * FROM agent_tasks"
        values: tuple = ()
        if context_id:
            query += " WHERE context_id = ?"
            values = (context_id,)
        query += " ORDER BY updated_at DESC LIMIT ?"
        with self._lock:
            rows = self._connection.execute(query, (*values, limit)).fetchall()
        return tuple(self._to_task(row) for row in rows)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _values(task: AgentTask) -> tuple:
        return (
            task.id, task.capability, task.message, task.context_id, json.dumps(task.metadata), task.classification,
            task.status, task.origin, task.created_at, task.updated_at, json.dumps(task.result), task.error,
        )

    @staticmethod
    def _to_task(row: tuple) -> AgentTask:
        return AgentTask(
            id=row[0], capability=row[1], message=row[2], context_id=row[3], metadata=json.loads(row[4]),
            classification=DataClassification(row[5]), status=TaskStatus(row[6]), origin=ExecutionOrigin(row[7]),
            created_at=row[8], updated_at=row[9], result=json.loads(row[10]) if row[10] else None, error=row[11],
        )
