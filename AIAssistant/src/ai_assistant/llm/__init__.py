"""LLM orchestration and prompt formatting."""

from .contracts import LLMBackend, PromptBuilder
from .hardware import release_ml_memory
from .llama_cpp_backend import LlamaCppBackend
from .model_loader import download_if_missing, load_backend
from .prompts import (
    CHARACTER_QUERY_PATTERNS,
    PROJECT_CHARACTER_NAMES,
    ROLE_QUERY_PATTERN,
    DefaultPromptBuilder,
    estimate_tokens,
    trim_history,
)

__all__ = [
    "CHARACTER_QUERY_PATTERNS",
    "PROJECT_CHARACTER_NAMES",
    "ROLE_QUERY_PATTERN",
    "DefaultPromptBuilder",
    "LLMBackend",
    "LlamaCppBackend",
    "PromptBuilder",
    "download_if_missing",
    "estimate_tokens",
    "load_backend",
    "release_ml_memory",
    "trim_history",
]
