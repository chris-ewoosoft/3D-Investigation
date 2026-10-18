"""Pre-flight integrity check for local model artifacts (ADR 0023).

The loader verifies every artifact it touches, but a deployment may want to
check a prepared ``Models/`` directory *before* the server starts. This script
hashes each pinned artifact that is present and reports ``OK`` / ``MISSING`` /
``MISMATCH`` against ``config/models.toml``:

* any ``MISMATCH`` exits 1 — the loader would refuse those weights;
* ``MISSING`` exits 0 unless ``--require-all`` is passed, because a fresh
  install legitimately has nothing downloaded yet.

Usage::

    python AIAssistant/scripts/verify_models.py
    python AIAssistant/scripts/verify_models.py --require-all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_assistant.config.model_registry import load_model_registry
from ai_assistant.llm.model_loader import _matches_pin


def _check(path: Path, sha256: str, kind: str) -> dict[str, str]:
    """Classify one artifact against its pin (missing, ok or mismatch)."""
    if not path.exists():
        status = "missing"
    elif _matches_pin(str(path), sha256):
        status = "ok"
    else:
        status = "mismatch"
    return {
        "artifact": path.name,
        "kind": kind,
        "status": status,
        "pinned_sha256": sha256 or "unpinned",
    }


def verify(models_dir: Path, config_dir: Path) -> list[dict[str, str]]:
    """The pinned models and vision projectors, classified against ``models_dir``."""
    registry = load_model_registry(config_dir)
    results: list[dict[str, str]] = []
    for entry in (*registry.models, registry.fallback):
        results.append(_check(models_dir / entry.filename, entry.sha256, "model"))
        if entry.is_vision and entry.mmproj_filename:
            results.append(_check(models_dir / entry.mmproj_filename,
                                  entry.mmproj_sha256, "mmproj"))
    return results


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify GGUF artifacts against the pinned registry.")
    parser.add_argument("--models-dir", default=str(ROOT.parent / "Models"),
                        help="Directory holding the GGUF files (default: <repo>/Models).")
    parser.add_argument("--config-dir", default=str(ROOT / "config"),
                        help="Directory holding models.toml.")
    parser.add_argument("--require-all", action="store_true",
                        help="Fail when a pinned artifact is not downloaded yet.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    results = verify(Path(args.models_dir), Path(args.config_dir))
    mismatched = [r for r in results if r["status"] == "mismatch"]
    missing = [r for r in results if r["status"] == "missing"]
    for entry in results:
        print(f"{entry['status'].upper():<9} {entry['artifact']} ({entry['kind']})")
    if mismatched:
        print(f"FAIL: {len(mismatched)} artifact(s) do not match the pinned SHA-256",
              file=sys.stderr)
        return 1
    if missing and args.require_all:
        print(f"FAIL: {len(missing)} pinned artifact(s) missing (--require-all)",
              file=sys.stderr)
        return 1
    print(f"OK: {len(results) - len(missing)} verified, {len(missing)} not downloaded yet, "
          "0 mismatched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
