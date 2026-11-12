"""Hardware and memory management utilities."""
from __future__ import annotations

import gc
import sys


def release_ml_memory() -> None:
    """Collect Python objects and clear an already-loaded Torch CUDA cache.

    Teardown must not import the ML stack merely to release it. Importing
    Torch here is both a large late allocation and, through optional dill
    integration, can open process handles that survive until interpreter
    shutdown. If Torch never ran, there is no Torch-owned cache to clear.
    """
    gc.collect()
    torch_module = sys.modules.get("torch")
    if torch_module is None:
        return
    try:
        if torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()
    except Exception:
        pass
