from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..application.tasks import TaskService
from ..domain.security import DataClassification
from ..domain.tasks import AgentTask


class A2AMessagePart(BaseModel):
    kind: str = "text"
    text: str = Field(min_length=1, max_length=32000)


class A2AMessage(BaseModel):
    role: str = "user"
    parts: list[A2AMessagePart] = Field(min_length=1)


class SendTaskParams(BaseModel):
    message: A2AMessage
    capability: str = "supervisor"
    context_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    classification: DataClassification = DataClassification.INTERNAL


class ResumeTaskParams(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


def task_payload(task: AgentTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "contextId": task.context_id,
        "status": {"state": task.status},
        "metadata": task.metadata,
        "artifacts": [{"name": "result", "parts": [{"kind": "data", "data": task.result}]}] if task.result else [],
        "error": task.error,
    }


def build_a2a_router(service: TaskService, agent_name: str, agent_version: str) -> APIRouter:
    router = APIRouter(tags=["a2a"])
    skills = [
        {"id": capability, "name": capability.replace("_", " ").title(),
         "description": f"Execute {capability} tasks through the 3D-Reconstruction agent platform."}
        for capability in sorted(service.capabilities)
    ]

    @router.get("/.well-known/agent.json")
    def agent_card(request: Request) -> dict[str, Any]:
        return {
            "name": agent_name,
            "description": "3D-Reconstruction AI Agent Platform",
            "version": agent_version,
            "protocolVersion": "1.0",
            "url": str(request.base_url).rstrip("/") + "/a2a",
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text"], "defaultOutputModes": ["text"], "skills": skills,
        }

    @router.post("/a2a/tasks/send")
    def send_task(params: SendTaskParams) -> dict[str, Any]:
        message = "\n".join(part.text for part in params.message.parts if part.kind == "text").strip()
        task = service.submit(params.capability, message, params.metadata, params.context_id, params.classification)
        return {"task": task_payload(task)}

    @router.get("/a2a/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        task = service.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="A2A task not found")
        return {"task": task_payload(task)}

    @router.get("/a2a/tasks")
    def list_tasks(context_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"tasks": [task_payload(task) for task in service.list(context_id=context_id, limit=limit)]}

    @router.post("/a2a/tasks/{task_id}:cancel")
    def cancel_task(task_id: str) -> dict[str, Any]:
        task = service.cancel(task_id)
        if task is None:
            raise HTTPException(status_code=409, detail="A2A task cannot be cancelled")
        return {"task": task_payload(task)}

    @router.post("/a2a/tasks/{task_id}:resume")
    def resume_task(task_id: str, params: ResumeTaskParams) -> dict[str, Any]:
        task = service.resume(task_id, params.input)
        if task is None:
            raise HTTPException(status_code=409, detail="A2A task cannot be resumed")
        return {"task": task_payload(task)}

    @router.get("/a2a/tasks/{task_id}/events")
    def stream_task(task_id: str):
        if service.get(task_id) is None:
            raise HTTPException(status_code=404, detail="A2A task not found")

        def stream():
            cursor = 0
            while True:
                events = service.events(task_id)
                for event in events[cursor:]:
                    yield f"event: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                cursor = len(events)
                task = service.get(task_id)
                if task and task.status.value in {"completed", "failed", "canceled", "rejected"}:
                    yield f"event: done\ndata: {json.dumps(task_payload(task), ensure_ascii=False)}\n\n"
                    return
                time.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.post("/a2a")
    async def json_rpc(request: Request) -> dict[str, Any]:
        payload = await request.json()
        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params", {})
        try:
            if method == "tasks/send":
                typed = SendTaskParams.model_validate(params)
                message = "\n".join(part.text for part in typed.message.parts if part.kind == "text").strip()
                task = service.submit(typed.capability, message, typed.metadata, typed.context_id, typed.classification)
                result = {"task": task_payload(task)}
            elif method == "tasks/get":
                task = service.get(str(params.get("id", "")))
                if task is None:
                    raise KeyError("A2A task not found")
                result = {"task": task_payload(task)}
            elif method == "tasks/list":
                result = {
                    "tasks": [
                        task_payload(task)
                        for task in service.list(context_id=params.get("context_id"), limit=int(params.get("limit", 100)))
                    ]
                }
            elif method == "tasks/cancel":
                task = service.cancel(str(params.get("id", "")))
                if task is None:
                    raise ValueError("A2A task cannot be cancelled")
                result = {"task": task_payload(task)}
            elif method == "tasks/resume":
                task = service.resume(str(params.get("id", "")), dict(params.get("input", {})))
                if task is None:
                    raise ValueError("A2A task cannot be resumed")
                result = {"task": task_payload(task)}
            else:
                raise NotImplementedError(f"Unsupported A2A method: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (KeyError, ValueError, NotImplementedError) as error:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(error)}}

    return router
