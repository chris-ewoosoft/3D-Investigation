"""Operating-system custody for the locally generated at-rest key.

The service's AES key is data, not a password-derived secret.  On Windows the
default desktop key file therefore stores only a DPAPI user-scope envelope;
the raw 32-byte key is recoverable only by the same Windows account.  Explicit
``AI_STORAGE_KEY`` and ``AI_STORAGE_KEY_FILE`` sources remain deployment-owned
KMS/secret mounts and never pass through this adapter.

POSIX has no single standard-library equivalent to DPAPI.  The caller keeps
the existing owner-only ``0600`` file there and the deployment runbook assigns
stronger custody to a mounted secret or a KMS integration.
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes
from typing import Any

from ai_assistant.ports import StorageKeyError

DPAPI_PREFIX = "dpapi:v1:"
_DPAPI_ENTROPY = b"3d-reconstruction.ai-assistant.storage-key.v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def os_key_protection_available() -> bool:
    """Return whether this platform supplies the supported OS key backend."""
    return sys.platform == "win32"


def protect_local_key(key: bytes) -> str:
    """Wrap ``key`` for the current Windows account and return its envelope."""
    if not os_key_protection_available():
        message = "OS key protection is unavailable on this platform"
        raise StorageKeyError(message)
    protected = _call_dpapi("CryptProtectData", key)
    return DPAPI_PREFIX + base64.urlsafe_b64encode(protected).decode("ascii")


def unprotect_local_key(envelope: str) -> bytes:
    """Open a versioned DPAPI envelope for the current Windows account."""
    if not envelope.startswith(DPAPI_PREFIX):
        message = "local at-rest key is not a DPAPI envelope"
        raise StorageKeyError(message)
    if not os_key_protection_available():
        message = "a DPAPI key envelope cannot be opened on this platform"
        raise StorageKeyError(message)
    encoded = envelope[len(DPAPI_PREFIX):].strip()
    try:
        protected = base64.b64decode(
            encoded.encode("ascii") + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError) as error:
        message = "local at-rest key has an invalid DPAPI envelope"
        raise StorageKeyError(message) from error
    if not protected:
        message = "local at-rest key has an empty DPAPI envelope"
        raise StorageKeyError(message)
    return _call_dpapi("CryptUnprotectData", protected)


def _blob(value: bytes) -> tuple[Any, _DataBlob]:
    """Keep a ctypes buffer alive beside the DATA_BLOB that points into it."""
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return buffer, _DataBlob(len(value), pointer)


def _call_dpapi(function_name: str, value: bytes) -> bytes:
    """Invoke one DPAPI transform and always release its LocalAlloc output."""
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as error:
        message = "Windows DPAPI is unavailable"
        raise StorageKeyError(message) from error

    transform = getattr(crypt32, function_name)
    transform.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    transform.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    input_buffer, input_blob = _blob(value)
    entropy_buffer, entropy_blob = _blob(_DPAPI_ENTROPY)
    output_blob = _DataBlob()
    # The local variables intentionally keep both input buffers alive until
    # the native call has returned.
    _ = input_buffer, entropy_buffer
    if not transform(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        code = ctypes.get_last_error()
        message = f"Windows DPAPI {function_name} failed (error {code})"
        raise StorageKeyError(message)
    if not output_blob.pbData or output_blob.cbData == 0:
        message = f"Windows DPAPI {function_name} returned an empty result"
        raise StorageKeyError(message)
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
