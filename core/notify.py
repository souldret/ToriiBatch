"""Job-complete notifications: tray balloon, optional sound, Windows toast."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def play_sound() -> None:
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
    except Exception as exc:
        logger.debug("Ses çalınamadı: %s", exc)


def show_job_done(title: str, message: str, tray=None) -> None:
    play_sound()
    if tray is not None:
        try:
            from PyQt6.QtWidgets import QSystemTrayIcon
            tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)
            return
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            from win10toast import ToastNotifier  # type: ignore
            ToastNotifier().show_toast(title, message, duration=6, threaded=True)
        except Exception as exc:
            logger.debug("Windows toast yok: %s", exc)
