"""Inference backend selection for local llama.cpp, vLLM/TGI and cloud policy."""
from __future__ import annotations

import json
import os
import re
from urllib.request import Request, urlopen


def strip_think_tags(text: str) -> str:
    """Remove ``<think>…</think>`` blocks emitted by reasoning-mode models.

    Qwen3 and similar models may emit a ``<think>`` block before the actual
    content even when ``enable_thinking`` is set to ``False`` at the
    llama-cpp-python level (the flag only controls the *chat format*; the
    model weights still tend to produce thinking tokens).  Stripping these
    blocks prevents wasted context and avoids breaking JSON parsers that
    expect a clean envelope.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def backend_mode() -> str:
    return os.getenv("AGENT_INFERENCE_BACKEND", "llama_cpp").casefold()


def cloud_allowed(data_classification: str) -> bool:
    """Privacy gate for hybrid routing; local is the secure default."""
    policy = os.getenv("AGENT_HYBRID_POLICY", "local_only").casefold()
    return policy == "cloud_allowed" and data_classification.casefold() in {"public", "non_sensitive"}


def openai_compatible_completion(messages: list[dict], **params) -> dict:
    """Call a separately deployed vLLM/TGI OpenAI-compatible server."""
    endpoint = os.getenv("AGENT_INFERENCE_URL", "").rstrip("/")
    if not endpoint:
        raise RuntimeError("AGENT_INFERENCE_URL is required for remote inference")
        
    try:
        from .data_governance import scrub_messages
        messages = scrub_messages(messages)
    except ImportError:
        pass
        
    body = json.dumps({"model": os.getenv("AGENT_INFERENCE_MODEL", "default"),
                       "messages": messages, "stream": False, **params}).encode()
    request = Request(endpoint + "/v1/chat/completions", data=body,
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=120) as response:  # noqa: S310 -- configured endpoint
        return json.loads(response.read())
