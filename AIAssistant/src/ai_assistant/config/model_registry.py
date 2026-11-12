"""Model registry and configuration.

Every registry entry pins the exact remote artifact it will load: the
Hugging Face commit (``revision``) and the SHA-256 of the GGUF file. The
loader refuses to load weights that do not match the pin, so a moved tag or
a corrupted download fails closed instead of silently swapping the model
(ADR 0023). The values below were verified against the Hugging Face blob
metadata and, for the models present locally, against the on-disk SHA-256.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    """A declared model that can be loaded by the LLM backend."""

    repo_id: str
    filename: str
    desc: str
    is_vision: bool = False
    mmproj_repo_id: str | None = None
    mmproj_filename: str | None = None
    #: Hugging Face commit the download is pinned to (empty = floating ``main``).
    revision: str = ""
    #: SHA-256 of ``filename`` (empty = unpinned, verification skipped).
    sha256: str = ""
    #: Pin for the vision projector; falls back to ``revision`` when the
    #: projector lives in the same repository as the model.
    mmproj_revision: str = ""
    mmproj_sha256: str = ""


# ── Defaults (used when config/models.toml is absent) ────────────────────────
# Kept in one place so the missing-config path and the documented fallback can
# never drift apart. Pins are the values verified in ADR 0023.
_FALLBACK = ModelDefinition(
    "bartowski/Qwen2.5-3B-Instruct-GGUF",
    "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
    "Qwen2.5-3B (Q4_K_M) — Text Fallback",
    revision="f302c64a2269a69fb27b2f9473b362f5bb8e78d8",
    sha256="9c9f56a391a3abbd5b89d0245bf6106081bcc3173119d4229235dd9d23253f94",
)

_DEFAULT_MODELS = (
    ModelDefinition(
        "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf",
        "Qwen3-8B (Q4_K_M) — Text / Agent / Coder",
        revision="7c41481f57cb95916b40956ab2f0b139b296d974",
        sha256="d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
    ),
    ModelDefinition(
        "bartowski/Qwen2.5-7B-Instruct-GGUF", "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "Qwen2.5-7B (Q4_K_M) — Text",
        revision="8911e8a47f92bac19d6f5c64a2e2095bd2f7d031",
        sha256="65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423",
    ),
    ModelDefinition(
        "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "Qwen2.5-coder-7B (Q4_K_M) — Coder",
        revision="13fb94bfda8c8cf22497dc57b78f391a9acb426a",
        sha256="509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c",
    ),
    ModelDefinition(
        "bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF", "Qwen_Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
        "Qwen2.5-VL-7B (Q4_K_M) — Vision", is_vision=True,
        mmproj_repo_id="bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF",
        mmproj_filename="mmproj-Qwen_Qwen2.5-VL-7B-Instruct-f16.gguf",
        revision="956c2bbb32ce10ac80761c127daa18f77063fab1",
        sha256="3f4513330aa7f109922bd701d773575484ae2b4a4090d6511260a2a4f8e3d069",
        mmproj_revision="956c2bbb32ce10ac80761c127daa18f77063fab1",
        mmproj_sha256="c24a7f5fcfc68286f0a217023b6738e73bea4f11787a43e8238d4bb1b8604cde",
    ),
)


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    """Collection of available models and the configured fallback."""

    models: tuple[ModelDefinition, ...]
    fallback: ModelDefinition

    def get_by_index(self, index: int) -> ModelDefinition:
        """Get a model by its index, defaulting to 0 if out of bounds."""
        if not self.models:
            return self.fallback
        if 0 <= index < len(self.models):
            return self.models[index]
        return self.models[0]


def load_model_registry(config_dir: Path | str) -> ModelRegistry:
    """Load model definitions from config/models.toml."""
    config_path = Path(config_dir) / "models.toml"

    if not config_path.exists():
        # Fallback defaults if toml doesn't exist
        return ModelRegistry(models=_DEFAULT_MODELS, fallback=_FALLBACK)

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    models = tuple(ModelDefinition(**m) for m in data.get("models", []))
    fallback_data = data.get("fallback", {})
    if fallback_data:
        fallback = ModelDefinition(**fallback_data)
    else:
        fallback = _FALLBACK

    return ModelRegistry(models=models, fallback=fallback)
