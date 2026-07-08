"""Shared monochrome icon helpers for the desktop UI."""

from PyQt6.QtGui import QIcon
import qtawesome as qta

from ui.theme import Colors


def icon(name: str, color: str | None = None) -> QIcon:
    """Return a consistent Font Awesome icon for normal, active and disabled states."""
    return qta.icon(
        name,
        color=color or Colors.TEXT_SECONDARY,
        color_active=Colors.TEXT_PRIMARY,
        color_disabled=Colors.TEXT_DISABLED,
    )
