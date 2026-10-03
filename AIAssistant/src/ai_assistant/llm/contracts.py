"""Domain contracts for LLM backends and prompt formatting."""
from __future__ import annotations

from typing import Any, Protocol


class LLMBackend(Protocol):
    """Abstract interface for LLM inference engines."""
    
    @property
    def is_vision_supported(self) -> bool:
        """Return True if this backend can process images."""
        ...
        
    @property
    def model_description(self) -> str:
        """Return a human-readable description of the loaded model."""
        ...

    def generate(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 1500,
        stream: bool = False,
        stop: list[str] | None = None,
        **kwargs: Any
    ) -> Any:
        """Generate a response synchronously. 
        
        Returns the raw backend response format (e.g., Llama-cpp chat completion dict) 
        during the migration phase.
        """
        ...


class PromptBuilder(Protocol):
    """Abstract interface for constructing LLM context."""
    
    def build_messages(
        self,
        messages: list[dict[str, Any]],
        doc_ctx: str,
        code_ctx: str,
        language: str = "vi",
        suppress_citations: bool = False,
    ) -> list[dict[str, Any]]:
        """Construct the full message list for a text model."""
        ...
        
    def build_vision_messages(
        self,
        messages: list[dict[str, Any]],
        doc_ctx: str,
        code_ctx: str,
        image_chunks: list[str] | None = None,
        language: str = "vi",
        suppress_citations: bool = False,
    ) -> list[dict[str, Any]]:
        """Construct the full message list for a vision-capable model."""
        ...

    def is_character_query(self, query: str) -> bool:
        """Detect if the query asks about project roles/characters."""
        ...

    def strip_citations(self, answer: str) -> str:
        """Remove source citations from an answer."""
        ...
