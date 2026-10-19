"""Dataset validation: is ``data.yaml`` present, well-formed and on disk?

Isolated from the rest of preflight because it is a different kind of work —
parsing and resolving a YAML document rather than inspecting the environment or
the hyper-parameters — and because it is the one check whose inputs come from
the user's filesystem rather than from :class:`~cvtrain.config.TrainConfig`.

Everything here answers with a single :class:`~cvtrain.checks.Check` named
``dataset``; :mod:`cvtrain.preflight` simply appends it to its report.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .checks import Check, module_available

__all__ = [
    "check_data_yaml",
    "dataset_base",
    "dataset_class_count",
    "dataset_content_fingerprint",
    "missing_dataset_paths",
    "ref_exists",
]


def dataset_base(parsed: dict[object, object], yaml_dir: Path) -> Path:
    """Directory that relative ``train``/``val`` references resolve against.

    Mirrors Ultralytics: an explicit top-level ``path:`` key relocates the
    dataset root (resolved relative to ``data.yaml``), and without it references
    are relative to the folder holding ``data.yaml``.
    """

    root = parsed.get("path")
    if isinstance(root, str) and root:
        candidate = Path(root).expanduser()
        return candidate if candidate.is_absolute() else yaml_dir / candidate
    return yaml_dir


def ref_exists(value: str, base: Path) -> bool:
    """Whether a single ``train``/``val`` reference points at something on disk.

    Globs (``images/*.jpg``) are checked at their static prefix: a wildcard has
    no literal path to test, so validating ``images/`` is the useful subset.
    """

    raw = Path(value)
    if raw.is_absolute():
        return raw.exists()
    if (base / raw).exists():
        return True
    if any(char in value for char in "*?["):
        head = value.split("?")[0].split("*")[0].split("[")[0].rstrip("\\/")
        return (base / head).exists() if head else base.exists()
    return False


def missing_dataset_paths(parsed: dict[object, object], yaml_dir: Path) -> list[str]:
    """Human-readable ``train``/``val`` references that are not on disk.

    Only string entries are validated: ``train`` can legitimately be a text file
    listing or a remote URL that the framework resolves, so anything that is not
    a plain path is left to the framework instead of being rejected here —
    ``ref_exists`` is *only* asked about references that name a local path.
    """

    base = dataset_base(parsed, yaml_dir)
    missing: list[str] = []
    for key in ("train", "val"):
        value = parsed.get(key)
        if not isinstance(value, str):
            continue
        if "://" in value:
            # Remote (http://, https://, s3://, ...): there is nothing on this
            # disk to check, and reporting it as "missing" would reject a
            # dataset shape Ultralytics downloads itself.
            continue
        if not ref_exists(value, base):
            missing.append(f"{key}='{value}' (resolved under {base})")
    return missing


def check_data_yaml(path: Path) -> Check:
    """Validate the dataset definition the run is going to train against."""

    if not path.exists():
        return Check(
            "dataset",
            False,
            f"not found: {path} - create it with 'train', 'val' and 'names' keys",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Check("dataset", False, f"unreadable: {path} ({exc})")

    if not text.strip():
        return Check("dataset", False, f"empty file: {path}")

    if not module_available("yaml"):
        return Check("dataset", True, f"{path} found (PyYAML missing, content not validated)")

    import yaml

    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return Check("dataset", False, f"invalid YAML: {exc}")

    if not isinstance(parsed, dict):
        return Check("dataset", False, "top level of data.yaml must be a mapping")
    # Both splits are required, exactly as README ("Setup") documents: a dataset
    # without ``val`` would pass preflight and then fail (or train without ever
    # validating) deep inside the framework, which is what this check exists to
    # prevent.
    missing = [key for key in ("train", "val", "names") if key not in parsed]
    if missing:
        return Check("dataset", False, f"missing required key(s): {', '.join(missing)} in {path}")
    if not parsed.get("names"):
        return Check("dataset", False, f"'names' must define at least one class in {path}")
    missing_paths = missing_dataset_paths(parsed, path.parent)
    if missing_paths:
        return Check("dataset", False, "missing path(s): " + "; ".join(missing_paths))
    return Check("dataset", True, f"{path} ({len(parsed.get('names') or [])} classes)")


def dataset_class_count(path: Path) -> int | None:
    """Number of classes declared by ``names`` in ``data.yaml``, or ``None``.

    The ONNX contract needs the class count to prove that an export exposes the
    channels the Qt parser expects (``4 + classes`` for detection,
    ``4 + classes + 32`` for segmentation). This deliberately re-reads the YAML
    instead of threading a count through every layer: it is the same document
    the framework trains from, so there is no second source of truth to drift.
    ``None`` means "cannot prove" — the caller relaxes only the exact channel
    check and keeps validating rank/count/dtype.
    """

    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not module_available("yaml"):
        return None

    import yaml

    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    names = parsed.get("names")
    if isinstance(names, dict):
        return len(names)
    if isinstance(names, (list, tuple)):
        return len(names)
    return None


def _digest_file(digest: Any, label: str, path: Path, *, relative_name: str | None = None) -> None:
    """Feed one file's identity and bytes into ``digest``.

    The relative name is part of the hash (moving an image between splits is a
    different dataset), and unreadable files contribute a stable marker instead
    of aborting the fingerprint: ``--skip-unchanged`` reads the filesystem
    opportunistically, and a transient sharing violation must not crash a run.
    """

    name = path.name if relative_name is None else relative_name
    digest.update(f"{label}:{name}:".encode("utf-8"))
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        digest.update(b"<unreadable>")
    digest.update(b"\n")


def dataset_content_fingerprint(path: Path) -> str | None:
    """Compute a content fingerprint of the files a dataset YAML references.

    Returns ``None`` when the YAML file is absent or unreadable. Combines the
    raw hash of ``data.yaml`` with the relative path and contents of every file
    in the referenced ``train``/``val`` splits. Hashing the bytes — not just
    ``(name, size)`` — is what detects an image replaced by different pixels
    under the same name and the same file size.

    The cost is O(dataset bytes) per check. That is deliberate: the fingerprint
    exists so ``--skip-unchanged`` can prove the data, and an inventory that a
    single same-size edit can fool is not proof.
    """

    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None

    digest = hashlib.sha256(text.encode("utf-8"))
    if not module_available("yaml"):
        return digest.hexdigest()

    import yaml

    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return digest.hexdigest()

    if not isinstance(parsed, dict):
        return digest.hexdigest()

    base = dataset_base(parsed, path.parent)
    for key in ("train", "val"):
        value = parsed.get(key)
        if not isinstance(value, str) or "://" in value:
            continue
        target = Path(value) if Path(value).is_absolute() else base / value
        if target.is_dir():
            def _relative(child: Path) -> str:
                return child.relative_to(target).as_posix()

            files = sorted((child for child in target.rglob("*") if child.is_file()), key=_relative)
            for child in files:
                _digest_file(digest, key, child, relative_name=_relative(child))
        elif target.is_file():
            _digest_file(digest, key, target)

    return digest.hexdigest()

