"""Core RAG domain models."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChunkResult:
    """A semantic chunk of text or an image extracted from a document."""
    text: str
    source_path: str
    loader_type: str
    is_image: bool = False
    image_b64: str | None = None
    metadata: dict = field(default_factory=dict)
    
    # ── Hierarchical Chunking fields ──────────────────────────────────────────
    # parent_text: the broader context that contains this chunk (e.g. the
    # enclosing class body for a method chunk, or the H1 section for a
    # sub-heading chunk).
    parent_text: str | None = None
    hierarchy_level: int = 0   # 0 = flat/root, 1 = class/H1, 2 = method/H2+
