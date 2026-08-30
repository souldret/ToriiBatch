"""
theme.py - Uygulama genelinde kullanılan koyu tema tanımları.

Sorumluluğu:
- Renk sabitleri (Colors) ve boyut/mesafe sabitlerini (Metrics) tanımlamak.
- Kapsamlı Qt Style Sheet (QSS) string'i üretmek.
- apply_theme(app) fonksiyonu ile QApplication'a tema uygulamak.
"""

from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

_CHECK_SVG = (Path(__file__).resolve().parent.parent / "assets" / "check.svg").as_posix()


# ---------------------------------------------------------------------------
# Renk paleti
# ---------------------------------------------------------------------------

class Colors:
    """Uygulamada kullanılan tüm renk sabitleri (Premium Dark Theme)."""

    # Arka planlar (Sleek dark gray/blue tones)
    BG_BASE     = "#0e1116"   # En koyu — ana pencere zemini
    BG_SURFACE  = "#151a21"   # Kart, panel, header zemini
    BG_ELEVATED = "#1d242d"   # Yükseltilmiş alan (tooltip, popup, menü)
    BG_INPUT    = "#10151b"   # Input / combobox zemini

    # Aksan (Vibrant Indigo/Violet)
    ACCENT          = "#2dd4bf"
    ACCENT_HOVER    = "#5eead4"
    ACCENT_PRESSED  = "#14b8a6"
    ACCENT_DIM      = "#123b38"   # Aksan arka plan vurgusu (semi-transparent feel)

    # Metin
    TEXT_PRIMARY   = "#f5f7fa"
    TEXT_SECONDARY = "#a6afb9"
    TEXT_DISABLED  = "#7a8593"
    TEXT_ON_ACCENT = "#071412"

    # Kenarlıklar
    BORDER        = "#29313b"
    BORDER_FOCUS  = "#5eead4"

    # Durum renkleri
    SUCCESS  = "#10b981"
    WARNING  = "#f59e0b"
    ERROR    = "#ef4444"
    INFO     = "#3b82f6"

    # Scrollbar
    SCROLL_HANDLE = "#36414d"
    SCROLL_HOVER  = "#4a5866"


_DARK = {k: v for k, v in Colors.__dict__.items() if k.isupper()}
_LIGHT = {
    "BG_BASE": "#f4f6f8",
    "BG_SURFACE": "#ffffff",
    "BG_ELEVATED": "#eef1f4",
    "BG_INPUT": "#ffffff",
    "ACCENT": "#0d9488",
    "ACCENT_HOVER": "#14b8a6",
    "ACCENT_PRESSED": "#0f766e",
    "ACCENT_DIM": "#ccfbf1",
    "TEXT_PRIMARY": "#111827",
    "TEXT_SECONDARY": "#4b5563",
    "TEXT_DISABLED": "#9ca3af",
    "TEXT_ON_ACCENT": "#ffffff",
    "BORDER": "#d1d5db",
    "BORDER_FOCUS": "#0d9488",
    "SUCCESS": "#059669",
    "WARNING": "#d97706",
    "ERROR": "#dc2626",
    "INFO": "#2563eb",
    "SCROLL_HANDLE": "#cbd5e1",
    "SCROLL_HOVER": "#94a3b8",
}


def set_theme_name(name: str) -> None:
    palette = _LIGHT if name == "light" else _DARK
    for key, value in palette.items():
        setattr(Colors, key, value)


# ---------------------------------------------------------------------------
# Boyut / mesafe sabitleri
# ---------------------------------------------------------------------------

class Metrics:
    """Aralık, köşe yarıçapı ve yazı tipi büyüklük sabitleri."""

    SPACING_XS  = 4
    SPACING_SM  = 8
    SPACING_MD  = 16
    SPACING_LG  = 24

    RADIUS_SM   = 8
    RADIUS_MD   = 10
    RADIUS_LG   = 14

    FONT_SIZE_SM    = 9
    FONT_SIZE_BASE  = 10
    FONT_SIZE_MD    = 11
    FONT_SIZE_TITLE = 15


# ---------------------------------------------------------------------------
# QSS (Qt Style Sheet)
# ---------------------------------------------------------------------------

def get_stylesheet() -> str:
    """
    Uygulamanın tüm widget'ları için kapsamlı koyu tema QSS string'ini döndürür.

    Kullanım
    --------
    app.setStyleSheet(get_stylesheet())
    """
    c = Colors
    m = Metrics

    return f"""

/* ===== GENEL ===== */

QWidget {{
    background-color: transparent;
    color: {c.TEXT_PRIMARY};
    font-family: "Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: {m.FONT_SIZE_BASE}pt;
    selection-background-color: {c.ACCENT_DIM};
    selection-color: {c.ACCENT};
}}

QMainWindow {{
    background-color: {c.BG_BASE};
}}

QWidget#AppHeader {{
    background-color: {c.BG_SURFACE};
    border-bottom: 1px solid {c.BORDER};
}}

QWidget#AppFooter {{
    background-color: {c.BG_SURFACE};
    border-top: 1px solid {c.BORDER};
}}

QWidget#LeftPanel {{
    background-color: {c.BG_BASE};
}}

QWidget#RightPanel {{
    background-color: {c.BG_SURFACE};
    border-left: 1px solid {c.BORDER};
}}

QWidget#PanelCard {{
    background-color: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_LG}px;
}}

QWidget#ToastBar {{
    background-color: {c.BG_ELEVATED};
    border-top: 1px solid {c.ACCENT};
}}

QDialog {{
    background-color: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
}}

/* ===== MENÜ ===== */

/* ===== SETTINGS DIALOG ===== */

QWidget#SettingsHeader {{
    background-color: {c.BG_SURFACE};
    border-bottom: 1px solid {c.BORDER};
}}

QLabel[class="dialogTitle"] {{
    color: {c.TEXT_PRIMARY};
    font-size: 17pt;
    font-weight: 700;
}}

QLabel[class="dialogSubtitle"],
QLabel[class="pageDescription"] {{
    color: {c.TEXT_SECONDARY};
    font-size: {m.FONT_SIZE_SM}pt;
}}

QLabel[class="pageTitle"] {{
    color: {c.TEXT_PRIMARY};
    font-size: 15pt;
    font-weight: 700;
}}

QWidget#SettingsBody,
QStackedWidget#SettingsStack {{
    background-color: {c.BG_SURFACE};
}}

QListWidget#SettingsNav {{
    background-color: {c.BG_BASE};
    border: none;
    border-right: 1px solid {c.BORDER};
    border-radius: 0;
    padding: 12px 8px;
}}

QListWidget#SettingsNav::item {{
    color: {c.TEXT_SECONDARY};
    border: none;
    border-radius: {m.RADIUS_SM}px;
    padding: 0 12px;
    margin: 2px 0;
}}

QListWidget#SettingsNav::item:hover {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_PRIMARY};
}}

QListWidget#SettingsNav::item:selected {{
    background-color: {c.ACCENT_DIM};
    color: {c.ACCENT_HOVER};
    font-weight: 600;
}}

QWidget#SettingsFooter {{
    background-color: {c.BG_BASE};
    border-top: 1px solid {c.BORDER};
}}

QWidget#SettingsPageIntro {{
    background: transparent;
    border-bottom: 1px solid {c.BORDER};
}}

QMenuBar {{
    background-color: {c.BG_SURFACE};
    color: {c.TEXT_PRIMARY};
    border-bottom: 1px solid {c.BORDER};
    padding: 2px 4px;
}}

QMenuBar::item {{
    background: transparent;
    padding: 6px 12px;
    border-radius: {m.RADIUS_SM}px;
}}

QMenuBar::item:selected {{
    background-color: {c.BG_ELEVATED};
}}

QMenu {{
    background-color: {c.BG_ELEVATED};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    padding: 6px;
}}

QMenu::item {{
    padding: 8px 28px 8px 16px;
    border-radius: {m.RADIUS_SM}px;
}}

QMenu::item:selected {{
    background-color: {c.ACCENT_DIM};
    color: {c.ACCENT};
}}

QMenu::separator {{
    height: 1px;
    background-color: {c.BORDER};
    margin: 6px 8px;
}}

/* ===== BUTONLAR ===== */

QPushButton {{
    background-color: {c.ACCENT};
    color: {c.TEXT_ON_ACCENT};
    font-weight: 600;
    font-size: {m.FONT_SIZE_BASE}pt;
    border: none;
    border-radius: {m.RADIUS_MD}px;
    padding: 8px 16px;
    min-height: 34px;
    icon-size: 16px;
}}

QPushButton:hover {{
    background-color: {c.ACCENT_HOVER};
}}

QPushButton:pressed {{
    background-color: {c.ACCENT_PRESSED};
    padding-top: 9px;
    padding-bottom: 7px;
}}

QPushButton:disabled {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_DISABLED};
}}

QPushButton[class="secondary"] {{
    background-color: {c.BG_SURFACE};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    font-weight: 500;
}}

QPushButton[class="secondary"]:hover {{
    background-color: {c.BG_ELEVATED};
    border-color: {c.ACCENT_HOVER};
    color: {c.TEXT_PRIMARY};
}}

QPushButton[class="secondary"]:pressed {{
    background-color: {c.BG_INPUT};
    border-color: {c.ACCENT};
}}

QPushButton[class="secondary"]:disabled {{
    background-color: {c.BG_BASE};
    color: {c.TEXT_DISABLED};
    border-color: {c.BORDER};
}}

QPushButton[class="danger"] {{
    background-color: {c.BG_SURFACE};
    color: {c.ERROR};
    border: 1px solid {c.BORDER};
    font-weight: 600;
}}

QPushButton[class="danger"]:hover {{
    background-color: rgba(239, 68, 68, 0.15);
    border-color: {c.ERROR};
}}

QPushButton[class="danger"]:pressed {{
    background-color: rgba(239, 68, 68, 0.25);
}}

QPushButton[class="danger"]:disabled {{
    background-color: transparent;
    color: {c.TEXT_DISABLED};
    border-color: {c.BORDER};
}}

/* ===== BÜYÜK EYLEM BUTONLARI (footer: Başlat / Duraklat / İptal) ===== */

QPushButton[class="cta"] {{
    background-color: {c.ACCENT};
    color: {c.TEXT_ON_ACCENT};
    border: none;
    border-radius: {m.RADIUS_MD}px;
    padding: 0px 24px;
    font-weight: 700;
    font-size: {m.FONT_SIZE_MD}pt;
    min-height: 46px;
}}

QPushButton[class="cta"]:hover {{
    background-color: {c.ACCENT_HOVER};
}}

QPushButton[class="cta"]:pressed {{
    background-color: {c.ACCENT_PRESSED};
}}

QPushButton[class="cta"]:disabled {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_DISABLED};
}}

QPushButton[class="ctaSecondary"] {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    padding: 0px 20px;
    font-weight: 600;
    font-size: {m.FONT_SIZE_MD}pt;
    min-height: 46px;
}}

QPushButton[class="ctaSecondary"]:hover {{
    background-color: {c.BG_INPUT};
    border-color: {c.ACCENT_HOVER};
    color: {c.ACCENT_HOVER};
}}

QPushButton[class="ctaSecondary"]:pressed {{
    background-color: {c.BG_BASE};
    border-color: {c.ACCENT};
}}

QPushButton[class="ctaSecondary"]:disabled {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_DISABLED};
    border-color: {c.BORDER};
}}

QPushButton[class="ctaDanger"] {{
    background-color: {c.BG_ELEVATED};
    color: {c.ERROR};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    padding: 0px 20px;
    font-weight: 600;
    font-size: {m.FONT_SIZE_MD}pt;
    min-height: 46px;
}}

QPushButton[class="ctaDanger"]:hover {{
    background-color: rgba(239, 68, 68, 0.12);
    border-color: {c.ERROR};
}}

QPushButton[class="ctaDanger"]:pressed {{
    background-color: rgba(239, 68, 68, 0.22);
}}

QPushButton[class="ctaDanger"]:disabled {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_DISABLED};
    border-color: {c.BORDER};
}}

/* ===== INPUT ALANLARI ===== */

QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {c.BG_INPUT};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    padding: 8px 12px;
    font-size: {m.FONT_SIZE_BASE}pt;
    selection-background-color: {c.ACCENT_DIM};
    selection-color: {c.TEXT_PRIMARY};
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {c.BORDER_FOCUS};
    background-color: {c.BG_BASE};
}}

QLineEdit:disabled, QTextEdit:disabled {{
    background-color: {c.BG_BASE};
    color: {c.TEXT_DISABLED};
    border-color: {c.BORDER};
}}

/* ===== COMBO BOX ===== */

QComboBox {{
    background-color: {c.BG_INPUT};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    padding: 6px 12px;
    font-size: {m.FONT_SIZE_BASE}pt;
    min-height: 32px;
}}

QComboBox:hover {{
    border-color: {c.ACCENT_HOVER};
}}

QComboBox:focus {{
    border-color: {c.BORDER_FOCUS};
}}

QComboBox::drop-down {{
    border: none;
    width: 28px;
    background: transparent;
}}

QComboBox::down-arrow {{
    width: 10px;
    height: 10px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c.TEXT_SECONDARY};
    margin-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    selection-background-color: {c.ACCENT_DIM};
    selection-color: {c.ACCENT};
    outline: none;
    padding: 6px;
}}

/* ===== SPIN BOX ===== */

QSpinBox {{
    background-color: {c.BG_INPUT};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    padding: 6px 10px;
    font-size: {m.FONT_SIZE_BASE}pt;
    min-height: 32px;
}}

QSpinBox:focus {{
    border-color: {c.BORDER_FOCUS};
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {c.BG_SURFACE};
    border: none;
    width: 24px;
}}

QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {c.ACCENT_DIM};
}}

/* ===== CHECKBOX ===== */

QCheckBox {{
    color: {c.TEXT_PRIMARY};
    spacing: 10px;
    font-size: {m.FONT_SIZE_BASE}pt;
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {c.BORDER};
    border-radius: 4px;
    background-color: {c.BG_INPUT};
}}

QCheckBox::indicator:hover {{
    border-color: {c.ACCENT_HOVER};
}}

QCheckBox::indicator:checked {{
    background-color: {c.ACCENT};
    border-color: {c.ACCENT};
    image: url("{_CHECK_SVG}");
}}

QCheckBox:disabled {{
    color: {c.TEXT_DISABLED};
}}

QCheckBox::indicator:disabled {{
    border-color: {c.TEXT_DISABLED};
    background-color: {c.BG_BASE};
}}

/* ===== PROGRESS BAR ===== */

QProgressBar {{
    background-color: {c.BG_ELEVATED};
    border: none;
    border-radius: {m.RADIUS_SM}px;
    text-align: center;
    color: {c.TEXT_PRIMARY};
    font-size: {m.FONT_SIZE_SM}pt;
    font-weight: 600;
    min-height: 16px;
}}

QProgressBar::chunk {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {c.ACCENT_PRESSED},
        stop: 1 {c.ACCENT_HOVER}
    );
    border-radius: {m.RADIUS_SM}px;
}}

/* ===== SCROLL BAR ===== */

QScrollBar:vertical {{
    background-color: {c.BG_BASE};
    width: 10px;
    border-radius: 5px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: {c.SCROLL_HANDLE};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {c.SCROLL_HOVER};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background-color: {c.BG_BASE};
    height: 10px;
    border-radius: 5px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background-color: {c.SCROLL_HANDLE};
    border-radius: 5px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {c.SCROLL_HOVER};
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

/* ===== SCROLL AREA ===== */

QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

/* ===== TAB WIDGET ===== */

QTabWidget::pane {{
    background-color: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    top: -1px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {c.TEXT_SECONDARY};
    padding: 10px 20px;
    border-bottom: 2px solid transparent;
    font-size: {m.FONT_SIZE_BASE}pt;
    font-weight: 500;
}}

QTabBar::tab:hover {{
    color: {c.TEXT_PRIMARY};
    border-bottom-color: {c.BORDER};
}}

QTabBar::tab:selected {{
    color: {c.ACCENT};
    border-bottom-color: {c.ACCENT};
    font-weight: 600;
}}

/* ===== LABEL ===== */

QLabel {{
    background-color: transparent;
    color: {c.TEXT_PRIMARY};
}}

/* ===== FRAME / SEPARATOR ===== */

QFrame[frameShape="4"],
QFrame[frameShape="5"] {{
    color: {c.BORDER};
    background-color: {c.BORDER};
}}

/* ===== LIST WIDGET ===== */

QListWidget {{
    background-color: {c.BG_INPUT};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    outline: none;
    padding: 6px;
}}

QListWidget::item {{
    padding: 8px 12px;
    border-radius: {m.RADIUS_SM}px;
}}

QListWidget::item:hover {{
    background-color: {c.BG_ELEVATED};
}}

QListWidget::item:selected {{
    background-color: {c.ACCENT_DIM};
    color: {c.ACCENT};
}}

/* ===== SPLITTER ===== */

QSplitter::handle {{
    background-color: {c.BORDER};
    width: 1px;
    height: 1px;
    margin: 0;
}}

QSplitter::handle:hover {{
    background-color: {c.ACCENT};
}}

/* ===== DIALOG BUTONLARI ===== */

QDialogButtonBox QPushButton {{
    min-width: 90px;
}}

/* ===== TOOL BUTTON ===== */

QToolButton {{
    background-color: {c.BG_SURFACE};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    padding: 6px 12px;
    font-size: {m.FONT_SIZE_BASE}pt;
    min-height: 32px;
}}

QToolButton:hover {{
    background-color: {c.BG_ELEVATED};
    border-color: {c.ACCENT_HOVER};
    color: {c.TEXT_PRIMARY};
}}

QToolButton:pressed {{
    background-color: {c.BG_INPUT};
    border-color: {c.ACCENT};
}}

/* ===== STATUS BAR ===== */

QStatusBar {{
    background-color: {c.BG_SURFACE};
    color: {c.TEXT_SECONDARY};
    border-top: 1px solid {c.BORDER};
    font-size: {m.FONT_SIZE_SM}pt;
}}

QStatusBar::item {{
    border: none;
}}

/* ===== TOOLTIP ===== */

QToolTip {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_SM}px;
    padding: 6px 10px;
    font-size: {m.FONT_SIZE_SM}pt;
}}

/* ===== MESSAGE BOX ===== */

QMessageBox {{
    background-color: {c.BG_SURFACE};
}}

QMessageBox QLabel {{
    color: {c.TEXT_PRIMARY};
    font-size: {m.FONT_SIZE_BASE}pt;
}}

/* Ek olarak group box vb varsa */
QGroupBox {{
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    margin-top: 1ex;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 3px;
    color: {c.ACCENT};
    font-weight: bold;
}}

QTableWidget {{
    background-color: {c.BG_SURFACE};
    alternate-background-color: {c.BG_ELEVATED};
    color: {c.TEXT_PRIMARY};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS_MD}px;
    gridline-color: {c.BORDER};
    outline: none;
}}

QTableWidget::item {{
    padding: 6px;
}}

QTableWidget::item:selected {{
    background-color: {c.ACCENT_DIM};
    color: {c.ACCENT};
}}

QHeaderView::section {{
    background-color: {c.BG_ELEVATED};
    color: {c.TEXT_SECONDARY};
    padding: 8px;
    border: none;
    border-bottom: 1px solid {c.BORDER};
    font-weight: 600;
}}
"""


# ---------------------------------------------------------------------------
# Tema uygulama
# ---------------------------------------------------------------------------

def apply_theme(app: QApplication, theme: str = "dark") -> None:
    """
    Verilen QApplication örneğine koyu temayı uygular.

    Hem QPalette (native widget'lar için) hem de QSS (Qt widget'lar için)
    birlikte ayarlanır.

    Parametreler
    ------------
    app : QApplication
        Tema uygulanacak uygulama örneği.
    """
    from PyQt6.QtWidgets import QStyleFactory

    set_theme_name(theme)
    app.setStyle(QStyleFactory.create("Fusion"))

    palette = QPalette()
    c = Colors

    def qc(hex_color: str) -> QColor:
        return QColor(hex_color)

    palette.setColor(QPalette.ColorRole.Window,          qc(c.BG_BASE))
    palette.setColor(QPalette.ColorRole.WindowText,      qc(c.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base,            qc(c.BG_INPUT))
    palette.setColor(QPalette.ColorRole.AlternateBase,   qc(c.BG_SURFACE))
    palette.setColor(QPalette.ColorRole.ToolTipBase,     qc(c.BG_ELEVATED))
    palette.setColor(QPalette.ColorRole.ToolTipText,     qc(c.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text,            qc(c.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button,          qc(c.BG_ELEVATED))
    palette.setColor(QPalette.ColorRole.ButtonText,      qc(c.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.BrightText,      qc(c.ACCENT))
    palette.setColor(QPalette.ColorRole.Link,            qc(c.ACCENT))
    palette.setColor(QPalette.ColorRole.Highlight,       qc(c.ACCENT_DIM))
    palette.setColor(QPalette.ColorRole.HighlightedText, qc(c.ACCENT))
    # Placeholder metin rengi — QLineEdit::placeholder QSS'de çalışmaz,
    # QPalette.PlaceholderText ile ayarlanmalıdır.
    palette.setColor(QPalette.ColorRole.PlaceholderText, qc(c.TEXT_DISABLED))

    # Disabled durumu
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       qc(c.TEXT_DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, qc(c.TEXT_DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, qc(c.TEXT_DISABLED))

    app.setPalette(palette)
    app.setStyleSheet(get_stylesheet())