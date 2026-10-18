"""Emit a CycloneDX-style SBOM for the AI Assistant runtime (ADR 0023).

The bill of materials has two halves:

* pinned Python distributions, read from ``requirements.lock`` — the exact
  versions ``uv pip compile`` resolved, not the floating ranges in
  ``requirements.txt``, each carrying the SHA-256 digests ``--require-hashes``
  makes pip enforce;
* the GGUF artifacts the model registry can load, each carrying the pinned
  Hugging Face commit and SHA-256 the loader enforces before use.

The script is stdlib-only and writes JSON to stdout so a CI step can redirect
it to an artifact; ``--output`` writes a file instead.

Usage::

    python AIAssistant/scripts/generate_sbom.py > AIAssistant/sbom.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_assistant.config.model_registry import load_model_registry

_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.\-]*)==(?P<version>[^\s;]+)")
_HASH = re.compile(r"^--hash=(?P<alg>[A-Za-z0-9]+):(?P<digest>[0-9a-fA-F]+)$")

# CycloneDX spells the algorithms with a dash; uv writes them without one.
_ALGORITHMS = {"sha1": "SHA-1", "sha224": "SHA-224", "sha256": "SHA-256",
               "sha384": "SHA-384", "sha512": "SHA-512"}


def parse_lock(lock_path: Path) -> list[dict[str, Any]]:
    """Pinned distributions from a ``uv pip compile --generate-hashes`` lock.

    Versions come from the ``name==version`` lines; the ``--hash=``
    continuation lines are attached to their component, so the SBOM names the
    exact artifacts pip verifies when the lock is installed with
    ``--require-hashes``.
    """
    components: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.endswith("\\"):
            line = line[:-1].rstrip()
        match = _PIN.match(line)
        if match:
            name, version = match.group("name"), match.group("version")
            current = {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
            }
            components.append(current)
            continue
        if current is None:
            continue
        hash_match = _HASH.match(line)
        if hash_match:
            algorithm = hash_match.group("alg").lower()
            current.setdefault("hashes", []).append({
                "alg": _ALGORITHMS.get(algorithm, algorithm.upper()),
                "content": hash_match.group("digest").lower(),
            })
    return components


def model_components(config_dir: Path) -> list[dict[str, Any]]:
    """The pinned model artifacts the loader will accept."""
    registry = load_model_registry(config_dir)
    entries = [*registry.models, registry.fallback]
    components: list[dict[str, Any]] = []
    for entry in entries:
        components.append({
            "type": "machine-learning-model",
            "name": entry.filename,
            "version": entry.sha256 or "unpinned",
            "properties": [
                {"name": "aiassistant:model:repo", "value": entry.repo_id},
                {"name": "aiassistant:model:revision", "value": entry.revision},
                {"name": "aiassistant:model:sha256", "value": entry.sha256},
            ],
        })
        if entry.is_vision and entry.mmproj_filename:
            components.append({
                "type": "machine-learning-model",
                "name": entry.mmproj_filename,
                "version": entry.mmproj_sha256 or "unpinned",
                "properties": [
                    {"name": "aiassistant:model:repo", "value": entry.mmproj_repo_id or entry.repo_id},
                    {"name": "aiassistant:model:revision",
                     "value": entry.mmproj_revision or entry.revision},
                    {"name": "aiassistant:model:sha256", "value": entry.mmproj_sha256},
                ],
            })
    return components


def generate(config_dir: Path, lock_path: Path) -> dict[str, Any]:
    """The complete SBOM document."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "component": {"type": "application", "name": "ai-assistant", "version": "dev"},
        },
        "components": [*parse_lock(lock_path), *model_components(config_dir)],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the AI Assistant SBOM.")
    parser.add_argument("--lock", default=str(ROOT / "requirements.lock"),
                        help="Lock file to read pinned distributions from.")
    parser.add_argument("--config-dir", default=str(ROOT / "config"),
                        help="Directory holding models.toml.")
    parser.add_argument("--output", default="", help="Write the SBOM here instead of stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    lock_path = Path(args.lock)
    if not lock_path.exists():
        print(f"SBOM FAIL: lock file not found: {lock_path}", file=sys.stderr)
        return 2
    document = generate(Path(args.config_dir), lock_path)
    payload = json.dumps(document, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
