"""What the RAG corpus may read — the boundary the scanner has to keep.

The corpus builder is a **second reader** of the same filesystem as the
agent's read tools, and it used to have its own policy: its own extension
list, its own directory list (missing two entries the shared kernel
refuses) and no link rule at all. A junction — on Windows created without
any privilege — or a symbolic link inside the tree therefore pointed
*outside* it, and the file it pointed at was indexed as if it were part of
the project: a private document, or a ``secrets.py`` with an API key, reached
the model and the sealed cache.

These cases pin the one rule the scanner now shares with the agent read
gate: the real target must stay inside the tree, the blocked directories
are the shared ones, a secret name is never read, and one file is indexed
once.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_assistant.config.paths import AppPaths
from ai_assistant.rag.index import (
    _indexable_files,
    get_file_system_hash,
    scan_documents,
)
from ai_assistant.rag.loaders import DocumentLoaderRegistry


def _paths(root: Path) -> AppPaths:
    project = root / "project"
    (project / "Docs").mkdir(parents=True, exist_ok=True)
    for name in ("Models", "Cache"):
        (root / name).mkdir(exist_ok=True)
    return AppPaths(
        project_root=project,
        base_dir=root,
        data_dir=root,
        models_dir=root / "Models",
        cache_dir=root / "Cache",
        logs_dir=root / "logs",
        docs_dirs=(project / "Docs",),
    )


def _registry() -> DocumentLoaderRegistry:
    return DocumentLoaderRegistry.create_default(1200, 300)


def _link_outside(link: Path, target: Path) -> bool:
    """Create a link at ``link`` that points at ``target``; report success.

    On Windows a **junction** is used: it needs no privilege, unlike a
    symbolic link — and ``os.walk`` follows it, which is exactly why the
    scanner needs the rule.
    """
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, check=False,
        )
        return result.returncode == 0
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


class CorpusBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.paths = _paths(self.root)
        self.project = self.paths.project_root

    def test_a_link_out_of_the_tree_is_not_indexed(self) -> None:
        """The real target decides, not the spelling.

        The file inside the tree is named like a source file; where it
        *points* is outside the project. The corpus builder used to follow
        it, so the target's content reached the model and the sealed cache.
        """
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.md").write_text(
            "PRIVATE DOCUMENT OUTSIDE THE PROJECT. " * 6, encoding="utf-8",
        )
        (outside / "secrets.py").write_text(
            "API_KEY = 'super-secret-value-1234567890'\n" * 4, encoding="utf-8",
        )
        link = self.project / "notes"
        if not _link_outside(link, outside):
            self.skipTest("this filesystem cannot create a link to a directory")

        chunks = scan_documents(self.paths, _registry())
        # The assertion is on the **content**, not on the path: the chunk's
        # ``source_path`` is the link's own spelling (inside the project), so
        # a path assertion would pass while the target's text was indexed.
        for chunk in chunks:
            self.assertNotIn("PRIVATE DOCUMENT OUTSIDE", chunk.text)
            self.assertNotIn("super-secret-value", chunk.text)

    def test_a_link_in_a_docs_directory_is_not_indexed(self) -> None:
        """The docs walk has the same rule as the project walk.

        The two walks are two readers of the same tree, and a docs directory
        is the one place a *document* (a ``.txt``, a ``.pdf``) can hide.
        """
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text(
            "PRIVATE DOCUMENT OUTSIDE THE PROJECT. " * 6, encoding="utf-8",
        )
        link = self.project / "Docs" / "notes"
        if not _link_outside(link, outside):
            self.skipTest("this filesystem cannot create a link to a directory")

        chunks = scan_documents(self.paths, _registry())
        for chunk in chunks:
            self.assertNotIn("PRIVATE DOCUMENT OUTSIDE", chunk.text)

    def test_a_backup_file_is_not_indexed(self) -> None:
        """``~``-prefixed and ``.user`` files are not part of the corpus.

        They are the editor's leftovers: the corpus has no use for them and
        they carry the state of a file the user was editing. The rule holds
        in both walks — a ``~draft.txt`` under ``Docs/`` and a ``~draft.md``
        in the project.
        """
        (self.project / "Docs" / "~draft.txt").write_text(
            "an editor leftover. " * 10, encoding="utf-8",
        )
        (self.project / "Docs" / "notes.user").write_text(
            "a user file. " * 10, encoding="utf-8",
        )
        (self.project / "~draft.md").write_text(
            "an editor leftover. " * 10, encoding="utf-8",
        )
        chunks = scan_documents(self.paths, _registry())
        self.assertFalse(
            [chunk for chunk in chunks if chunk.source_path.endswith((".txt", ".md", ".user"))],
            "an editor leftover must not be indexed",
        )

    def test_a_file_whose_real_target_leaves_the_tree_is_not_indexed(self) -> None:
        """The file-level rule, without needing a link on the filesystem.

        A *directory* junction is caught one level up (the walk does not
        descend into it); a file that points elsewhere is caught here. The
        junction case above needs a filesystem that can create one, so the
        rule itself is pinned directly: a file whose **real** target is
        outside the tree is not indexed.
        """
        inside = self.project / "notes.md"
        inside.write_text("# notes\n\ninside the project. " * 5, encoding="utf-8")
        outside = self.root / "outside.md"
        outside.write_text("# outside\n\noutside the project. " * 5, encoding="utf-8")

        real = Path.resolve

        def _fake_resolve(self_path: Path, *args, **kwargs) -> Path:
            resolved = real(self_path, *args, **kwargs)
            if resolved.name == "notes.md":
                return outside
            return resolved

        with mock.patch("ai_assistant.rag.index.canonical_path", _fake_resolve):
            chunks = scan_documents(self.paths, _registry())
        # The assertion is on the *file*: the loader still reads the file the
        # caller sees, so the content would look harmless while the row is
        # outside the tree.
        self.assertFalse(
            [chunk for chunk in chunks if chunk.source_path.endswith("notes.md")],
            "a file whose real target leaves the tree must not be indexed",
        )

    def test_the_shared_blocked_directories_are_not_walked(self) -> None:
        """The corpus and the agent read gate agree on the blocked directories.

        The corpus carried its own list and *omitted* two entries the kernel
        refuses — so a third-party ``node_modules`` tree was walked and its
        README ended up in the corpus.
        """
        blocked = self.project / "node_modules" / "pkg"
        blocked.mkdir(parents=True)
        (blocked / "readme.md").write_text("third party readme " * 10, encoding="utf-8")
        (self.project / "own.md").write_text("# ours\n\nour own content. " * 5, encoding="utf-8")

        chunks = scan_documents(self.paths, _registry())
        indexed = [chunk.source_path for chunk in chunks]
        self.assertTrue([path for path in indexed if path.endswith("own.md")])
        self.assertFalse(
            [path for path in indexed if "node_modules" in path],
            "a blocked directory must not be walked",
        )

    def test_a_secret_named_file_is_never_read(self) -> None:
        """The secret-name rule is the shared owner's, not an accident.

        The extension lists happen not to cover the secret names today; the
        rule is still applied, so a new list cannot quietly start reading
        them.
        """
        secret = self.project / "credentials.json"
        secret.write_text('{"token": "secret"}', encoding="utf-8")
        ordinary = self.project / "data.json"
        ordinary.write_text('{"ok": true}', encoding="utf-8")

        readable = {Path(path).name for path in _indexable_files(self.project, {".json"})}
        self.assertIn("data.json", readable)
        self.assertNotIn("credentials.json", readable)

    def test_a_file_is_indexed_once(self) -> None:
        """The two walks must not cover the same file.

        The image extensions were in both the source list and the document
        list, and the docs directories are inside the project root, so the
        two walks overlapped: an image under ``Docs/`` was chunked twice —
        the corpus (and the sealed cache) held it twice, and a retrieval
        could return it twice.
        """
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6360000002000100ffff030000060005"
            "57bfabd40000000049454e44ae426082",
        )
        (self.project / "Docs" / "pic.png").write_bytes(png)

        chunks = scan_documents(self.paths, _registry())
        images = [chunk for chunk in chunks if chunk.loader_type == "image"]
        self.assertEqual(len(images), 1, "an image must be indexed exactly once")

    def test_a_markdown_file_under_docs_is_still_indexed(self) -> None:
        """The docs walk must cover what the project walk used to cover.

        The project walk skips the docs directories (they have their own
        walk, because a docs directory may live outside the project root) —
        so the docs walk has to index the same files, or a ``.md`` under
        ``Docs/`` would simply stop being part of the corpus.
        """
        (self.project / "Docs" / "architecture.md").write_text(
            "# Architecture\n\nthe design of the thing. " * 5, encoding="utf-8",
        )
        chunks = scan_documents(self.paths, _registry())
        indexed = [chunk.source_path for chunk in chunks]
        self.assertTrue(
            [path for path in indexed if path.endswith("architecture.md")],
            "a markdown file under Docs/ must still be indexed",
        )

    def test_the_cache_hash_covers_exactly_the_indexed_files(self) -> None:
        """The hash and the scan must ask about the same files.

        They used to be two different walks: the hash covered *every* file
        under the docs directories while the scan indexed only the document
        types, so a stray file invalidated the cache for nothing.
        """
        settings = type("S", (), {
            "embed_model_name": "m", "chunk_chars": 1200, "cache_version": 2,
        })()
        before = get_file_system_hash(self.paths, settings)
        (self.project / "Docs" / "note.txt").write_text("A DOCUMENT. " * 20, encoding="utf-8")
        after = get_file_system_hash(self.paths, settings)
        self.assertNotEqual(before, after, "an indexed document must change the hash")

        (self.project / "Docs" / "stray.bin").write_bytes(b"\x00\x01")
        self.assertEqual(
            after, get_file_system_hash(self.paths, settings),
            "a file that is not indexed must not invalidate the cache",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
