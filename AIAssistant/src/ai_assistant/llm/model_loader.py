"""Model registry loading and management."""
from __future__ import annotations

import hashlib
import logging
import os

from ai_assistant.config import ModelDefinition, ModelRegistry

from .contracts import LLMBackend
from .llama_cpp_backend import LlamaCppBackend

logger = logging.getLogger("ai_assistant.llm.loader")

#: Streaming hash block; large enough that hashing a multi-GB GGUF stays I/O-bound.
_HASH_CHUNK_BYTES = 8 * 1024 * 1024


class ModelIntegrityError(RuntimeError):
    """A signed/pinned artifact failed verification (fail closed, ADR 0023)."""


def _sha256_of(path: str) -> str:
    """SHA-256 of one local file, streamed so a GGUF never has to fit in RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_pin(path: str, expected_sha256: str) -> bool:
    """True when the file matches the pin; an unpinned artifact always matches.

    An unreadable file (locked, vanished mid-run) is a mismatch, never an
    exception: the caller re-downloads and then decides fail-closed.
    """
    if not expected_sha256:
        return True
    try:
        return _sha256_of(path).casefold() == expected_sha256.casefold()
    except OSError:
        return False


def _ensure_artifact(*, repo_id: str, filename: str, revision: str, sha256: str,
                     models_dir: str, desc: str) -> str:
    """Return a local artifact path, downloading and verifying it if needed.

    The registry pins the commit (``revision``) and the content hash
    (``sha256``): a previously downloaded file that no longer matches the pin
    is discarded and downloaded once more, and a second mismatch raises
    ``ModelIntegrityError`` instead of loading unverified weights.
    """
    from huggingface_hub import hf_hub_download

    path = os.path.join(models_dir, filename)
    if os.path.exists(path):
        if _matches_pin(path, sha256):
            return path
        logger.warning("Discarding %s: SHA-256 does not match the pinned artifact", path)
        os.remove(path)
    logger.info("Downloading %s", desc)
    hf_hub_download(repo_id=repo_id, filename=filename, local_dir=models_dir,
                    revision=revision or None)
    if not _matches_pin(path, sha256):
        msg = (f"{filename} does not match the pinned SHA-256 for {repo_id}; "
               "refusing to load unverified weights.")
        raise ModelIntegrityError(msg)
    return path


def download_if_missing(model: ModelDefinition, models_dir: str) -> str:
    """Return a verified local model path, downloading the pinned artifact if needed."""
    return _ensure_artifact(
        repo_id=model.repo_id, filename=model.filename, revision=model.revision,
        sha256=model.sha256, models_dir=models_dir, desc=model.desc,
    )


def _mmproj_revision(model: ModelDefinition) -> str:
    """Revision for the vision projector.

    An explicit ``mmproj_revision`` wins; a projector hosted in the model's
    own repository shares the model pin when none is declared; a projector in
    a foreign repository without its own pin stays unpinned rather than
    guessing the model's commit (which would point at the wrong repo).
    """
    if model.mmproj_revision:
        return model.mmproj_revision
    if model.mmproj_repo_id == model.repo_id:
        return model.revision
    return ""


def load_backend(
    registry: ModelRegistry,
    models_dir: str,
    model_idx: int = 0,
    enable_vision: bool = True,
    n_ctx: int = 8192
) -> LLMBackend:
    """Build the backend for one registry entry (pure factory).

    The caller owns the returned object — ``LlmModule`` in the composition
    root. There is deliberately **no** module-level "current backend": the
    process used to keep a second reference next to the runtime module, so
    ``/admin/release-vram`` dropped one owner while the global kept the
    weights resident and VRAM was never reclaimed. One owner is the only
    shape in which "released" can mean released.
    """
    selected = registry.get_by_index(model_idx)
    if selected.is_vision and not enable_vision:
        logger.warning("Vision LLM disabled; using text fallback")
        selected = registry.fallback

    model_path = download_if_missing(selected, models_dir)
    mmproj_path = None
    if selected.is_vision and selected.mmproj_filename and selected.mmproj_repo_id:
        mmproj_path = _ensure_artifact(
            repo_id=selected.mmproj_repo_id, filename=selected.mmproj_filename,
            revision=_mmproj_revision(selected), sha256=selected.mmproj_sha256,
            models_dir=models_dir, desc=f"{selected.desc} (vision projector)",
        )

    return LlamaCppBackend(
        model_path=model_path,
        is_vision=selected.is_vision,
        mmproj_path=mmproj_path,
        n_ctx=n_ctx,
        desc=selected.desc
    )

