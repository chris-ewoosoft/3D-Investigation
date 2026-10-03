"""Retrieval-Augmented Generation (RAG) configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# Defaults kept as module-level constants so from_env_and_dict can safely reference
# them without triggering the member_descriptor bug that occurs when accessing
# `cls.field_name` on a frozen dataclass with slots=True.
_DEFAULTS: dict[str, Any] = {
    "embed_model_name": "intfloat/multilingual-e5-small",
    "embedding_query_prefix": "query: ",
    "embedding_passage_prefix": "passage: ",
    "embedding_supports_images": False,
    "embedding_dimension": 384,
    "use_reranker": True,
    "reranker_model": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    "reranker_top_k": 8,
    "chunk_chars": 1200,
    "overlap_chars": 300,
    "similarity_threshold": 0.30,
    "max_context_chars": 9000,
    "cache_version": 6,
}


@dataclass(frozen=True, slots=True)
class RAGSettings:
    """Typed configuration for the RAG pipeline."""

    enabled: bool = True

    # Embedding Model Settings
    embed_model_name: str = "intfloat/multilingual-e5-small"
    embedding_query_prefix: str = "query: "
    embedding_passage_prefix: str = "passage: "
    embedding_supports_images: bool = False
    embedding_dimension: int = 384

    # Reranker Settings
    use_reranker: bool = True
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_top_k: int = 8

    # Chunking & Retrieval Settings
    chunk_chars: int = 1200
    overlap_chars: int = 300
    similarity_threshold: float = 0.30
    max_context_chars: int = 9000

    # Cache format version — bump to invalidate old caches on config changes
    cache_version: int = 6

    @classmethod
    def from_env_and_dict(cls, data: dict[str, Any]) -> "RAGSettings":
        """Load from environment variables and an optional override dictionary.

        .. warning::
            Do NOT access ``cls.field_name`` to get default values when the
            dataclass uses ``slots=True``.  In that configuration Python returns
            a ``member_descriptor`` object rather than the field's default value,
            causing ``TypeError`` downstream (e.g. in ``os.path.exists``).
            Use the module-level ``_DEFAULTS`` dict instead.
        """
        enabled = os.environ.get("AI_ENABLE_RAG", "1").strip().lower() in {
            "1", "true", "yes", "on"
        }
        d = _DEFAULTS
        return cls(
            enabled=enabled,
            embed_model_name=str(data.get("embed_model_name", d["embed_model_name"])),
            use_reranker=bool(data.get("use_reranker", d["use_reranker"])),
            chunk_chars=int(data.get("chunk_chars", d["chunk_chars"])),
            overlap_chars=int(data.get("overlap_chars", d["overlap_chars"])),
            similarity_threshold=float(data.get("similarity_threshold", d["similarity_threshold"])),
            max_context_chars=int(data.get("max_context_chars", d["max_context_chars"])),
        )
