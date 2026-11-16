from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_assistant.tools.verified_write import (
    DirectoryIdentityError,
    DirectoryInspectionError,
    NonEmptyPathError,
    ProjectRelativePathError,
    UnsafeDirectoryError,
    VerifiedWriteProgressError,
    _ByHandleFileInformation,
    _DirectoryChain,
    _final_windows_path,
    _open_directory_chain,
    _open_posix_chain,
    _open_windows_chain,
    _verify_windows_directory,
    _write_all,
    _write_posix,
    _write_windows,
    atomic_write_beneath,
    create_directory_beneath,
)


class VerifiedWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()

    def test_atomic_write_creates_parents_and_replaces_the_leaf(self) -> None:
        target = self.root / "nested" / "file.txt"
        atomic_write_beneath(self.root, Path("nested/file.txt"), b"first")
        atomic_write_beneath(self.root, Path("nested/file.txt"), b"second")
        self.assertEqual(target.read_bytes(), b"second")
        self.assertFalse(any(target.parent.glob(".*.tmp")))

    def test_directory_creation_reports_leaf_ownership(self) -> None:
        relative = Path("one/two")
        self.assertTrue(create_directory_beneath(self.root, relative))
        self.assertFalse(create_directory_beneath(self.root, relative))
        self.assertTrue((self.root / relative).is_dir())

    def test_invalid_relative_names_are_refused_before_io(self) -> None:
        with self.assertRaises(ProjectRelativePathError):
            atomic_write_beneath(self.root, self.root / "absolute.txt", b"x")
        with self.assertRaises(ValueError):
            create_directory_beneath(self.root, Path("."))
        with self.assertRaises(NonEmptyPathError):
            create_directory_beneath(self.root, Path(""))

    def test_failed_replace_closes_and_removes_the_temporary(self) -> None:
        with mock.patch(
            "ai_assistant.tools.verified_write.os.replace",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                atomic_write_beneath(self.root, Path("nested/file.txt"), b"payload")
        nested = self.root / "nested"
        self.assertFalse(any(nested.glob(".*.tmp")))

    def test_a_directory_link_is_not_followed(self) -> None:
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        # POSIX kernels reject an O_NOFOLLOW directory open with either
        # ELOOP or ENOTDIR; both are the required fail-closed outcome.
        with self.assertRaises(OSError):
            atomic_write_beneath(self.root, Path("link/file.txt"), b"x")
        self.assertFalse((real / "file.txt").exists())

    def test_missing_root_is_an_os_error(self) -> None:
        with self.assertRaises(OSError):
            atomic_write_beneath(self.root / "missing", Path("file.txt"), b"x")

    def test_write_all_handles_partial_progress_and_refuses_zero(self) -> None:
        with mock.patch(
            "ai_assistant.tools.verified_write.os.write",
            side_effect=[1, 2],
        ) as write:
            _write_all(7, b"abc")
        self.assertEqual(write.call_count, 2)
        with mock.patch("ai_assistant.tools.verified_write.os.write", return_value=0):
            with self.assertRaises(VerifiedWriteProgressError):
                _write_all(7, b"x")

    def test_platform_dispatch_and_posix_chain_are_descriptor_relative(self) -> None:
        chain = _DirectoryChain([10, 11], self.root / "a", True)
        with mock.patch("ai_assistant.tools.verified_write.sys.platform", "linux"), mock.patch(
            "ai_assistant.tools.verified_write._open_posix_chain", return_value=chain,
        ) as opened:
            self.assertIs(_open_directory_chain(self.root, Path("a"), create=True), chain)
        opened.assert_called_once_with(self.root, Path("a"), create=True)

        directory = type("Stat", (), {"st_mode": stat.S_IFDIR | 0o700})()
        with mock.patch("ai_assistant.tools.verified_write.os.open", side_effect=[20, 21, 22]), mock.patch(
            "ai_assistant.tools.verified_write.os.mkdir",
        ), mock.patch(
            "ai_assistant.tools.verified_write.os.fstat", return_value=directory,
        ), mock.patch("ai_assistant.tools.verified_write.os.close") as close:
            result = _open_posix_chain(self.root, Path("a/b"), create=True)
            self.assertTrue(result.created)
            self.assertEqual(result.final, 22)
            with mock.patch("ai_assistant.tools.verified_write.sys.platform", "linux"):
                result.close()
        self.assertEqual(close.call_count, 3)

    def test_posix_chain_reuses_directories_and_closes_on_an_unsafe_leaf(self) -> None:
        regular = type("Stat", (), {"st_mode": stat.S_IFREG | 0o600})()
        with mock.patch("ai_assistant.tools.verified_write.os.open", side_effect=[20, 21]), mock.patch(
            "ai_assistant.tools.verified_write.os.mkdir", side_effect=FileExistsError,
        ), mock.patch(
            "ai_assistant.tools.verified_write.os.fstat", return_value=regular,
        ), mock.patch("ai_assistant.tools.verified_write.os.close") as close:
            with self.assertRaises(UnsafeDirectoryError):
                _open_posix_chain(self.root, Path("a"), create=True)
        self.assertGreaterEqual(close.call_count, 2)

        with mock.patch("ai_assistant.tools.verified_write.os.open", side_effect=[20, OSError("race")]), mock.patch(
            "ai_assistant.tools.verified_write.os.close",
        ) as close:
            with self.assertRaisesRegex(OSError, "race"):
                _open_posix_chain(self.root, Path("a"), create=False)
        close.assert_called_once_with(20)

    def test_posix_atomic_commit_uses_one_parent_descriptor(self) -> None:
        with mock.patch("ai_assistant.tools.verified_write.os.open", return_value=30), mock.patch(
            "ai_assistant.tools.verified_write._write_all",
        ), mock.patch("ai_assistant.tools.verified_write.os.fsync") as fsync, mock.patch(
            "ai_assistant.tools.verified_write.os.close",
        ), mock.patch("ai_assistant.tools.verified_write.os.replace") as replace, mock.patch(
            "ai_assistant.tools.verified_write.os.unlink",
        ):
            _write_posix(10, ".tmp", "target", b"x")
        replace.assert_called_once_with(".tmp", "target", src_dir_fd=10, dst_dir_fd=10)
        self.assertEqual(fsync.call_args_list[-1], mock.call(10))

        with mock.patch("ai_assistant.tools.verified_write.os.open", return_value=30), mock.patch(
            "ai_assistant.tools.verified_write._write_all", side_effect=OSError("full"),
        ), mock.patch("ai_assistant.tools.verified_write.os.close") as close, mock.patch(
            "ai_assistant.tools.verified_write.os.unlink", side_effect=FileNotFoundError,
        ):
            with self.assertRaisesRegex(OSError, "full"):
                _write_posix(10, ".tmp", "target", b"x")
        close.assert_called_once_with(30)

    def test_atomic_write_dispatches_to_the_posix_commit(self) -> None:
        chain = mock.Mock(final=10)
        with mock.patch("ai_assistant.tools.verified_write.sys.platform", "linux"), mock.patch(
            "ai_assistant.tools.verified_write._open_directory_chain", return_value=chain,
        ), mock.patch("ai_assistant.tools.verified_write._write_posix") as write:
            atomic_write_beneath(self.root, Path("a.txt"), b"x")
        write.assert_called_once()
        chain.close.assert_called_once()

    def test_windows_chain_closes_every_handle_when_a_child_open_fails(self) -> None:
        with mock.patch(
            "ai_assistant.tools.verified_write._open_windows_directory",
            side_effect=[1, OSError("swapped")],
        ), mock.patch("ai_assistant.tools.verified_write._close_windows_handle") as close:
            with self.assertRaisesRegex(OSError, "swapped"):
                _open_windows_chain(self.root, Path("child"), create=False)
        close.assert_called_once_with(1)

    def test_windows_handle_verification_rejects_failed_or_reparse_metadata(self) -> None:
        kernel = mock.Mock()
        kernel.GetFileInformationByHandle.return_value = False
        with self.assertRaises(DirectoryInspectionError):
            _verify_windows_directory(kernel, 1, 1, _ByHandleFileInformation(), self.root)

        kernel.GetFileInformationByHandle.return_value = True
        information = _ByHandleFileInformation()
        information.dwFileAttributes = 0x10 | 0x400
        with self.assertRaises(UnsafeDirectoryError):
            _verify_windows_directory(kernel, 1, 1, information, self.root)

    def test_windows_final_path_native_failures_and_prefixes(self) -> None:
        kernel = mock.Mock()
        kernel.GetFinalPathNameByHandleW.return_value = 0
        with mock.patch(
            "ai_assistant.tools.verified_write.ctypes.WinDLL", return_value=kernel, create=True,
        ), mock.patch(
            "ai_assistant.tools.verified_write.ctypes.get_last_error", return_value=5, create=True,
        ):
            with self.assertRaises(OSError):
                _final_windows_path(1)

        def second_call_fails(_handle, buffer, _size, _flags):
            return 4 if buffer is None else 0

        kernel.GetFinalPathNameByHandleW.side_effect = second_call_fails
        with mock.patch(
            "ai_assistant.tools.verified_write.ctypes.WinDLL", return_value=kernel, create=True,
        ), mock.patch(
            "ai_assistant.tools.verified_write.ctypes.get_last_error", return_value=5, create=True,
        ):
            with self.assertRaises(OSError):
                _final_windows_path(1)

        def unprefixed_path(_handle, buffer, _size, _flags):
            if buffer is None:
                return 8
            buffer.value = "C:\\plain"
            return 8

        kernel.GetFinalPathNameByHandleW.side_effect = unprefixed_path
        with mock.patch(
            "ai_assistant.tools.verified_write.ctypes.WinDLL", return_value=kernel, create=True,
        ):
            self.assertEqual(_final_windows_path(1), Path("C:/plain"))

        def return_unc(_handle, buffer, _size, _flags):
            if buffer is None:
                return 24
            buffer.value = r"\\?\UNC\server\share\dir"
            return len(buffer.value)

        kernel.GetFinalPathNameByHandleW.side_effect = return_unc
        with mock.patch(
            "ai_assistant.tools.verified_write.ctypes.WinDLL", return_value=kernel, create=True,
        ):
            self.assertEqual(str(_final_windows_path(1)), r"\\server\share\dir")

    def test_windows_write_closes_an_open_temporary_on_failure(self) -> None:
        chain = _DirectoryChain([1], self.root, False)
        with mock.patch("ai_assistant.tools.verified_write.os.open", return_value=40), mock.patch(
            "ai_assistant.tools.verified_write._write_all", side_effect=OSError("full"),
        ), mock.patch("ai_assistant.tools.verified_write.os.close") as close, mock.patch(
            "ai_assistant.tools.verified_write.Path.unlink", side_effect=FileNotFoundError,
        ):
            with self.assertRaisesRegex(OSError, "full"):
                _write_windows(chain, ".tmp", "target", b"x")
        close.assert_called_once_with(40)

    @unittest.skipUnless(os.name == "nt", "Windows directory-handle identity test")
    def test_windows_identity_mismatch_is_refused(self) -> None:
        with mock.patch(
            "ai_assistant.tools.verified_write._final_windows_path",
            return_value=self.root / "different",
        ):
            with self.assertRaises(DirectoryIdentityError):
                create_directory_beneath(self.root, Path("child"))


if __name__ == "__main__":
    unittest.main()
