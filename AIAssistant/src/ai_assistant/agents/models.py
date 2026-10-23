"""Agent domain models: request/response schemas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentExecuteRequest(BaseModel):
    task:                str   = Field(..., min_length=1, max_length=4000)
    #: Session ids become lock-pool keys and checkpointer thread ids.  They
    #: are bounded at the transport boundary so an arbitrarily long id cannot
    #: turn one request into a large retained key.
    session_id:          str   = Field(default="", max_length=128)
    temperature:         float = Field(0.3, ge=0.0, le=1.5)
    language:            str   = Field(default="vi", pattern="^(vi|en)$")
    # The Qt client sends the whole conversation (``buildAgentPayload`` walks
    # every prior message, no cap), so this bound only protects the transport:
    # rejecting at 20 entries made every long session answer with HTTP 422.
    # Per-message content is trimmed where the prompt is built (service.py).
    history:             list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    attachments:         list[str]  = Field(default_factory=list, max_length=10)
    # retry_message_index: index of the Qt history message being retried.
    retry_message_index: int | None = Field(default=None)


class AgentApproveRequest(BaseModel):
    action_id:  str = Field(..., min_length=1)
    approved:   bool = Field(...)
    session_id: str = Field(default="", max_length=128)


class AgentUiActionResultRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)


class AgentCancelRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    request_id: str = Field(default="")
