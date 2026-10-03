"""Model registry and configuration."""
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
        return ModelRegistry(
            models=(
                ModelDefinition("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", "Qwen3-8B (Q4_K_M) — Text / Agent / Coder"),
                ModelDefinition("bartowski/Qwen2.5-7B-Instruct-GGUF", "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "Qwen2.5-7B (Q4_K_M) — Text"),
                ModelDefinition("Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "qwen2.5-coder-7b-instruct-q4_k_m.gguf", "Qwen2.5-coder-7B (Q4_K_M) — Coder"),
                ModelDefinition("bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF", "Qwen_Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf", "Qwen2.5-VL-7B (Q4_K_M) — Vision", is_vision=True, mmproj_repo_id="bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF", mmproj_filename="mmproj-Qwen_Qwen2.5-VL-7B-Instruct-f16.gguf"),
            ),
            fallback=ModelDefinition("bartowski/Qwen2.5-3B-Instruct-GGUF", "Qwen2.5-3B-Instruct-Q4_K_M.gguf", "Qwen2.5-3B (Q4_K_M) — Text Fallback"),
        )
        
    with config_path.open("rb") as f:
        data = tomllib.load(f)
        
    models = tuple(ModelDefinition(**m) for m in data.get("models", []))
    fallback_data = data.get("fallback", {})
    if fallback_data:
        fallback = ModelDefinition(**fallback_data)
    else:
        fallback = ModelDefinition("bartowski/Qwen2.5-3B-Instruct-GGUF", "Qwen2.5-3B-Instruct-Q4_K_M.gguf", "Qwen2.5-3B (Q4_K_M) — Text Fallback")
        
    return ModelRegistry(models=models, fallback=fallback)
