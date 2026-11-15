"""One owner for "resolve a path and confine it to a root" (ADR 0035).

Three rules lived in three layers, and each of them was subtly wrong:

* the **spelling** was compared where the **filesystem** decides — a Windows
  filesystem is case-insensitive, so ``.GIT`` **is** ``.git`` and
  ``PYPROJECT.TOML`` **is** ``pyproject.toml``. A case-sensitive comparison
  let the agent read ``.GIT/config`` (which carries the remote URL and its
  credentials) while refusing ``.git/config``;
* the **lexical** path was compared where a **link** decides — a junction
  inside the root that points elsewhere is an escape, and no lexical check
  can see it;
* each rule had its **own copy**, so fixing one left the siblings behind.

This module owns the primitives. It is a kernel leaf — the standard library
only, no layer of its own — so every layer may depend on it and it depends on
none. That is what lets the *same* rule serve the agent tools, the LSP read
gate and the HTTP attachment gate without any of them growing a variant.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import IO, Any, BinaryIO, Literal, TextIO, overload


class VerifiedFileError(OSError):
    """An opened path is not one safe regular file identity."""

    def __init__(self) -> None:
        super().__init__("refusing a non-regular, multiply-linked, or replaced file")


class VerifiedFileModeError(ValueError):
    """Verified file access is deliberately read-only."""

    def __init__(self) -> None:
        super().__init__("verified file access only supports 'r' and 'rb'")

#: Directories the tools never descend into: version-control metadata (which
#: carries the remote URL and often a credential), build output, and the
#: toolchain's own caches.
BLOCKED_DIR_NAMES = frozenset({".git", "build", "__pycache__", ".vs", "node_modules"})

#: The same set, folded to lower case, so the comparison can be a plain
#: membership test on the folded name.
_BLOCKED_DIR_NAMES = frozenset(name.lower() for name in BLOCKED_DIR_NAMES)

#: File names that carry secrets and must never be reachable by an agent
#: tool. Matched on the **name**, not on the extension: a dotfile has no
#: extension (``splitext(".env")`` is ``(".env", "")``), so an extension-based
#: block-list never fires for exactly the files it was written for.
SECRET_FILE_NAMES = frozenset({
    ".env", ".envrc", ".npmrc", ".pypirc", ".netrc", ".gitconfig",
    "secrets.json", "credentials.json", "id_rsa", "id_ed25519", "id_ecdsa",
})


def is_blocked_dir(name: str) -> bool:
    """Whether a directory *name* is one the tools refuse to descend into.

    The comparison is case-insensitive on purpose: a Windows filesystem is
    case-insensitive, so ``.GIT`` is the same directory as ``.git``. The
    case-sensitive form of this rule is why ``read_file(".GIT/config")``
    returned the repository's git configuration — remote URL and credential
    helper included — while ``read_file(".git/config")`` was refused.
    """
    lowered = name.lower()
    if lowered.startswith("build"):
        return True
    return name.lower() in _BLOCKED_DIR_NAMES


def is_secret_name(name: str) -> bool:
    """Whether a file *name* is one that carries a secret.

    Two rules, both name-based: the explicit set above, and the ``.env``
    family — ``.env``, ``.env.local``, ``.env.production`` — which is what
    the dotfile case actually looks like in a deployment.
    """
    lowered = name.lower()
    if lowered in SECRET_FILE_NAMES:
        return True
    return lowered == ".env" or lowered.startswith(".env.")


def canonical_path(path: Path) -> Path:
    """One canonical spelling of ``path``, with symlinks followed.

    ``Path.resolve()`` asks the OS for the final path of an existing path,
    which expands an 8.3 short name (``C:\\Users\\RUNNER~1``) into its long
    form (``C:\\Users\\runneradmin``). A symlink whose *target does not
    exist* cannot be opened, so it is resolved by reading the link — and the
    link keeps the spelling it was created with. Two spellings of one
    directory make ``relative_to`` fail, so a target that never left the
    root was reported as an escape, and a policy deny-list was matched
    against a spelling it never sees. Canonicalising the parent (the deepest
    part the OS can resolve) and re-appending the leaf gives both sides one
    spelling, so they are compared on the same terms (ADR 0033).
    """
    resolved = path.resolve()
    parent = resolved.parent
    if parent.exists():
        return parent.resolve() / resolved.name
    return resolved


def resolve_within(root: str, candidate: str) -> str | None:
    """Resolve ``candidate`` inside ``root``; return it only if it stays.

    ``candidate`` may be absolute or relative to ``root``. The returned path
    is the **caller's own spelling**, made absolute — not the canonical one.
    That matters: Windows hands out two spellings for one directory (the
    canonical long form and the 8.3 short form), and a caller that asked for
    a path under its own root must get *that* path back, not a re-spelled
    one that no longer starts with the root it was given.

    Two independent checks, and **both** must hold:

    * the **spelling** is inside the root — a path that only looks like it
      leaves the root is refused (``commonpath``, not a string prefix test,
      so ``C:\\project2`` does not pass as ``C:\\project``; a different
      Windows drive is refused too);
    * the **real target** is inside the root — a junction or symlink inside
      the root that points outside it is an escape, and no spelling check
      can see it.

    Returns ``None`` when either check fails, when the two paths cannot be
    compared at all, or when the candidate is not a path at all.
    """
    if not candidate:
        return None
    try:
        root_path = Path(root)
        target = Path(candidate)
        if not target.is_absolute():
            target = root_path / target
        lexical = os.path.normpath(str(target))
        lexical_root = os.path.normpath(str(root_path))
        if os.path.commonpath([lexical, lexical_root]) != lexical_root:
            return None
        # Both sides are canonicalised before they are compared, so the 8.3
        # short form and the canonical long form of one directory are not
        # mistaken for two (ADR 0033).
        canonical_path(target).relative_to(canonical_path(root_path))
    except (OSError, ValueError):
        return None
    return lexical


def is_single_link_regular_file(path: str | Path) -> bool:
    """Whether ``path`` names one ordinary file with one directory entry.

    A hard link keeps the harmless alias spelling while referring to the same
    inode/file-id as a blocked name such as ``.env``.  Path canonicalisation
    cannot expose that relationship: unlike a symbolic link, a hard link has
    no target path to resolve.  Readers therefore fail closed on multiply
    linked files.  The opened-handle gate below repeats the decision after
    ``open`` so this inexpensive predicate is only an enumeration/preflight
    filter, never the final authority.
    """
    try:
        info = os.stat(path)
    except (OSError, ValueError, TypeError):
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare the stable identity fields exposed by Python on POSIX/Windows."""
    return (
        left.st_ino != 0
        and right.st_ino != 0
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


def _verify_opened_file(path: str | Path, handle: IO[Any]) -> None:
    """Raise unless ``handle`` and the current path are one safe file."""
    opened = os.fstat(handle.fileno())
    current = os.stat(path)
    if not (
        stat.S_ISREG(opened.st_mode)
        and opened.st_nlink == 1
        and _same_file_identity(opened, current)
    ):
        raise VerifiedFileError


@overload
def open_verified_regular_file(
    path: str | Path,
    mode: Literal["rb"] = "rb",
    *,
    encoding: None = None,
    errors: None = None,
) -> BinaryIO: ...


@overload
def open_verified_regular_file(
    path: str | Path,
    mode: Literal["r"],
    *,
    encoding: str | None = None,
    errors: str | None = None,
) -> TextIO: ...


def open_verified_regular_file(
    path: str | Path,
    mode: str = "rb",
    *,
    encoding: str | None = None,
    errors: str | None = None,
) -> IO[Any]:
    """Open a single-link regular file and verify the handle before reading.

    The security decision is made on the already-open handle, not only on a
    path that is opened again later.  ``fstat`` rejects devices/FIFOs and hard
    links; a second ``stat`` must name the same file identity, so replacing a
    checked path before ``open`` cannot redirect the reader silently.  The
    caller owns the returned handle and normally uses it in a ``with`` block.

    Only read modes are accepted.  Write/create policy remains owned by the
    sandbox, whose atomicity boundary is documented separately.
    """
    if mode not in {"r", "rb"}:
        raise VerifiedFileModeError
    handle = open(path, mode, encoding=encoding, errors=errors)
    try:
        _verify_opened_file(path, handle)
    except BaseException:
        handle.close()
        raise
    return handle


__all__ = [
    "BLOCKED_DIR_NAMES",
    "SECRET_FILE_NAMES",
    "VerifiedFileError",
    "VerifiedFileModeError",
    "canonical_path",
    "is_blocked_dir",
    "is_secret_name",
    "is_single_link_regular_file",
    "open_verified_regular_file",
    "resolve_within",
]
