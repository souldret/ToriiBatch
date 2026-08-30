"""Windows Credential Manager backed secret storage with Fernet fallback."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_TARGET_API = "ToriiBatch/api_key"
_TARGET_BYOK = "ToriiBatch/byok_key"


def _win_cred_set(target: str, value: str) -> bool:
    if sys.platform != "win32":
        return False
    if not value:
        return _win_cred_delete(target)
    try:
        import win32cred  # type: ignore
        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": "ToriiBatch",
                "CredentialBlob": value.encode("utf-16-le"),
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )
        return True
    except Exception as exc:
        logger.debug("Credential Manager yazılamadı: %s", exc)
        return False


def _win_cred_delete(target: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32cred  # type: ignore
        win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)
        return True
    except Exception:
        return True


def _win_cred_get(target: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import win32cred  # type: ignore
        cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC, 0)
        blob = cred.get("CredentialBlob")
        if isinstance(blob, bytes):
            text = blob.decode("utf-16-le", errors="ignore").rstrip("\x00")
            return text or None
        if isinstance(blob, str):
            return blob
    except Exception:
        return None
    return None


def store_secret(kind: str, value: str) -> bool:
    target = _TARGET_API if kind == "api_key" else _TARGET_BYOK
    return _win_cred_set(target, value)


def load_secret(kind: str) -> str | None:
    target = _TARGET_API if kind == "api_key" else _TARGET_BYOK
    return _win_cred_get(target)
