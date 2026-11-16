"""Descriptor-anchored filesystem mutations for the agent write sandbox.

Policy decides *which* project-relative name is writable.  This module owns
the later filesystem commit: every directory component is opened without
following a link and kept alive until the atomic replace has completed.
Consequently a concurrent path swap cannot redirect the already-authorised
write to a different directory.
"""
from __future__ import annotations

import ctypes
import os
import secrets
import stat
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast

_DIRECTORY_ACCESS = 0x0001 | 0x0080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class ProjectRelativePathError(ValueError):
    def __init__(self) -> None:
        super().__init__("verified write requires a project-relative path")


class NonEmptyPathError(ValueError):
    def __init__(self) -> None:
        super().__init__("verified write requires a non-empty path")


class DirectoryInspectionError(OSError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"cannot inspect verified directory: {path}")


class UnsafeDirectoryError(OSError):
    def __init__(self, path: Path | str) -> None:
        super().__init__(f"refusing reparse or non-directory component: {path}")


class DirectoryIdentityError(OSError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"verified directory identity changed: {path}")


class VerifiedWriteProgressError(OSError):
    def __init__(self) -> None:
        super().__init__("verified write made no progress")


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _DirectoryChain:
    def __init__(self, handles: list[int], path: Path, created: bool) -> None:
        self.handles = handles
        self.path = path
        self.created = created

    @property
    def final(self) -> int:
        return self.handles[-1]

    def close(self) -> None:
        closer = _close_windows_handle if sys.platform == "win32" else os.close
        for handle in reversed(self.handles):
            closer(handle)


def atomic_write_beneath(root: Path, relative: Path, payload: bytes) -> None:
    """Atomically replace ``relative`` under ``root`` using an anchored parent."""
    _validate_relative(relative, allow_empty=False)
    chain = _open_directory_chain(root, relative.parent, create=True)
    temporary_name = f".{relative.name}.{secrets.token_hex(8)}.tmp"
    try:
        if sys.platform == "win32":
            _write_windows(chain, temporary_name, relative.name, payload)
        else:
            _write_posix(chain.final, temporary_name, relative.name, payload)
    finally:
        chain.close()


def create_directory_beneath(root: Path, relative: Path) -> bool:
    """Create ``relative`` under ``root`` and return whether the leaf was new."""
    _validate_relative(relative, allow_empty=False)
    chain = _open_directory_chain(root, relative, create=True)
    try:
        return chain.created
    finally:
        chain.close()


def _validate_relative(relative: Path, *, allow_empty: bool) -> None:
    if relative.is_absolute() or ".." in relative.parts:
        raise ProjectRelativePathError
    if not allow_empty and (not relative.parts or str(relative) in {"", "."}):
        raise NonEmptyPathError


def _open_directory_chain(root: Path, relative: Path, *, create: bool) -> _DirectoryChain:
    if sys.platform == "win32":
        return _open_windows_chain(root, relative, create=create)
    return _open_posix_chain(root, relative, create=create)


def _open_windows_chain(root: Path, relative: Path, *, create: bool) -> _DirectoryChain:
    current = root.resolve()
    handles = [_open_windows_directory(current)]
    created_leaf = False
    try:
        for index, part in enumerate(relative.parts):
            candidate = current / part
            created = False
            if create:
                try:
                    candidate.mkdir()
                    created = True
                except FileExistsError:
                    pass
            handle = _open_windows_directory(candidate)
            handles.append(handle)
            current = _final_windows_path(handle)
            if index == len(relative.parts) - 1:
                created_leaf = created
        return _DirectoryChain(handles, current, created_leaf)
    except BaseException:
        for handle in reversed(handles):
            _close_windows_handle(handle)
        raise


def _open_windows_directory(path: Path) -> int:
    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        _DIRECTORY_ACCESS,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise OSError(
            cast(Any, ctypes).get_last_error(),
            f"cannot open verified directory {path}",
        )
    numeric = int(handle)
    try:
        information = _ByHandleFileInformation()
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
        get_information.restype = wintypes.BOOL
        _verify_windows_directory(kernel32, handle, numeric, information, path)
    except BaseException:
        _close_windows_handle(numeric)
        raise
    else:
        return numeric


def _verify_windows_directory(
    kernel32: object,
    handle: int,
    numeric: int,
    information: _ByHandleFileInformation,
    path: Path,
) -> None:
    get_information = kernel32.GetFileInformationByHandle  # type: ignore[attr-defined]
    if not get_information(handle, ctypes.byref(information)):
        raise DirectoryInspectionError(path)
    attributes = information.dwFileAttributes
    if not attributes & _FILE_ATTRIBUTE_DIRECTORY or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafeDirectoryError(path)
    actual = _final_windows_path(numeric)
    if os.path.normcase(str(actual)) != os.path.normcase(str(path.resolve())):
        raise DirectoryIdentityError(path)


def _final_windows_path(handle: int) -> Path:
    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    get_name = kernel32.GetFinalPathNameByHandleW
    get_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_name.restype = wintypes.DWORD
    size = get_name(handle, None, 0, 0)
    if size == 0:
        raise OSError(
            cast(Any, ctypes).get_last_error(),
            "cannot resolve verified directory handle",
        )
    buffer = ctypes.create_unicode_buffer(size + 1)
    if get_name(handle, buffer, len(buffer), 0) == 0:
        raise OSError(
            cast(Any, ctypes).get_last_error(),
            "cannot resolve verified directory handle",
        )
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _close_windows_handle(handle: int) -> None:
    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _open_posix_chain(root: Path, relative: Path, *, create: bool) -> _DirectoryChain:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    handles = [os.open(root, flags)]
    created_leaf = False
    try:
        for index, part in enumerate(relative.parts):
            created = False
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=handles[-1])
                    created = True
                except FileExistsError:
                    pass
            descriptor = os.open(part, flags, dir_fd=handles[-1])
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(descriptor)
                _raise_non_directory(part)
            handles.append(descriptor)
            if index == len(relative.parts) - 1:
                created_leaf = created
        return _DirectoryChain(handles, root / relative, created_leaf)
    except BaseException:
        for descriptor in reversed(handles):
            os.close(descriptor)
        raise


def _raise_non_directory(path: str) -> None:
    raise UnsafeDirectoryError(path)


def _write_windows(chain: _DirectoryChain, temporary: str, target: str, payload: bytes) -> None:
    temporary_path = chain.path / temporary
    target_path = chain.path / target
    descriptor = -1
    try:
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, target_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _write_posix(parent_fd: int, temporary: str, target: str, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise VerifiedWriteProgressError
        remaining = remaining[written:]


__all__ = ["atomic_write_beneath", "create_directory_beneath"]
