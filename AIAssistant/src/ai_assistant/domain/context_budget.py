"""Context-window budget policy — one home for the token arithmetic.

``adapters/http/chat_routes.py`` (refuse an over-long conversation before the
backend is touched) and ``agents/graph_wiring.py`` (hand back the
context-exhausted sentinel instead of calling the model) used to spell the same
comparison and the same three constants out independently: the two halves of one
policy could drift, and neither home was the domain even though "a run that
overruns its window stops" is a rule about the agent, not about a transport.

The domain owns it now (ADR 0009-style policy ownership: one authority, both
call sites read it). The numbers keep their historical values — behaviour is
unchanged, only the address is.
"""
from __future__ import annotations

#: Refuse a prompt that leaves less room than this to the completion.
CONTEXT_TOKEN_MARGIN = 512

#: Headroom kept for the completion on top of the refusal margin.
CONTEXT_COMPLETION_MARGIN = 400

#: The ReAct loop never asks the backend for more than this in one shot.
CONTEXT_COMPLETION_CAP = 2048

#: Floor for a completion request: always leave the model room to answer.
CONTEXT_COMPLETION_FLOOR = 512


def context_exhausted(estimated_tokens: int, llm_n_ctx: int) -> bool:
    """Whether the prompt leaves no usable room inside the context window."""
    return estimated_tokens >= llm_n_ctx - CONTEXT_TOKEN_MARGIN


def completion_token_budget(llm_n_ctx: int, estimated_tokens: int) -> int:
    """Tokens the backend may generate, before the caller's own cap.

    Callers apply their remaining limit on top: the HTTP chat route honours
    ``request.max_tokens``, the ReAct loop clamps to
    :data:`CONTEXT_COMPLETION_CAP`.
    """
    return max(CONTEXT_COMPLETION_FLOOR, llm_n_ctx - estimated_tokens - CONTEXT_COMPLETION_MARGIN)


__all__ = [
    "CONTEXT_COMPLETION_CAP",
    "CONTEXT_COMPLETION_FLOOR",
    "CONTEXT_COMPLETION_MARGIN",
    "CONTEXT_TOKEN_MARGIN",
    "completion_token_budget",
    "context_exhausted",
]
