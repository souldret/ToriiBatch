"""
main_window.py - ToriiBatch ana penceresi.

Sorumluluğu:
- Uygulamanın ana QMainWindow sınıfını tanımlamak.
- Sol panel (klasör/bölüm seçimi + checkbox listesi), sağ panel (ilerleme + log + ayar özeti).
- Çeviri motoruna iş göndermek ve tüm sinyalleri UI'a yansıtmak.
- Toplu başlat / duraklat / iptal et kontrollerini sunmak.
"""

import asyncio
import logging
import os
import threading
from pathlib import Path

import aiohttp

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QCloseEvent, QFont, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
    QMenu,
    QSystemTrayIcon,
)

from core.file_scanner import (
    ChapterInfo,
    build_output_path,
    filter_already_translated,
    scan_root_folder,
)
from core.settings_manager import SettingsManager
from core.translator_engine import TranslatorEngine
from ui.icons import icon as make_icon
from ui.settings_dialog import SettingsDialog
from ui.theme import Colors, Metrics
from ui.widgets import (
    ChapterCard,
    CreditsBadge,
    DropZone,
    LogView,
    StatCard,
)
from core.history_manager import HistoryManager
from ui.history_dialog import HistoryDialog

logger = logging.getLogger(__name__)

# Uygulama ikon yolu
_ICON_PATH = Path(__file__).parent.parent / "assets" / "icon.ico"


# ---------------------------------------------------------------------------
# Toast bildirimi (geçici üst-bant mesajı)
# ---------------------------------------------------------------------------

class _ToastBar(QWidget):
    """Pencerenin altında beliren, birkaç saniye sonra kaybolan bildirim bandı."""

    _ICONS = {
        "info": ("fa5s.info-circle", Colors.INFO),
        "success": ("fa5s.check-circle", Colors.SUCCESS),
        "warning": ("fa5s.exclamation-triangle", Colors.WARNING),
        "error": ("fa5s.times-circle", Colors.ERROR),
    }

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedHeight(48)
        self.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Metrics.SPACING_LG, 0, Metrics.SPACING_LG, 0)
        layout.setSpacing(Metrics.SPACING_SM)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(20, 20)
        self._icon_lbl.setStyleSheet("background: transparent;")
        self._set_icon("info")

        self._msg_lbl = QLabel("")
        self._msg_lbl.setMinimumWidth(0)
        self._msg_lbl.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; "
            f"font-size: {Metrics.FONT_SIZE_BASE}pt; background: transparent;"
        )

        self._action_btn = QPushButton()
        self._action_btn.setProperty("class", "secondary")
        self._action_btn.setFixedHeight(30)
        self._action_btn.hide()

        close_btn = QPushButton()
        close_btn.setIcon(make_icon("fa5s.times"))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setProperty("class", "secondary")
        close_btn.setFixedSize(30, 30)
        close_btn.setToolTip("Bildirimi kapat")
        close_btn.clicked.connect(self.hide)

        layout.addWidget(self._icon_lbl)
        layout.addWidget(self._msg_lbl, stretch=1)
        layout.addWidget(self._action_btn)
        layout.addWidget(close_btn)

        self.setStyleSheet(
            f"background-color: {Colors.BG_ELEVATED}; "
            f"border-top: 1px solid {Colors.ACCENT};"
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def _set_icon(self, kind: str) -> None:
        icon_name, color = self._ICONS.get(kind, self._ICONS["info"])
        self._icon_lbl.setPixmap(make_icon(icon_name, color).pixmap(18, 18))

    def show_message(
        self,
        message: str,
        icon: str = "info",
        action_label: str = "",
        action_callback=None,
        duration_ms: int = 6000,
    ) -> None:
        """Toast mesajını gösterir."""
        self._set_icon(icon)
        self._msg_lbl.setText(message)

        if action_label and action_callback:
            self._action_btn.setText(action_label)
            try:
                self._action_btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._action_btn.clicked.connect(action_callback)
            self._action_btn.show()
        else:
            self._action_btn.hide()

        self.show()
        if duration_ms > 0:
            self._timer.start(duration_ms)


# ---------------------------------------------------------------------------
# Ana pencere
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """
    ToriiBatch ana penceresi.

    Düzen
    -----
    ┌──────────────────────────────────────────────────────────┐
    │  Başlık: logo  ·  CreditsBadge  ·  Ayarlar butonu       │
    ├────────────────────────┬─────────────────────────────────┤
    │  Sol (~%35)            │  Sağ (~%65)                     │
    │  DropZone (kaynak)     │  Genel ilerleme çubuğu          │
    │  Çıktı klasörü satırı  │  LogPanel                       │
    │  Tümünü Seç/Kaldır     │  Hızlı ayarlar özet çubuğu      │
    │  CheckBox bölüm listesi│                                 │
    │  ─────────────────     │                                 │
    │  Taramayı Yenile       │                                 │
    ├────────────────────────┴─────────────────────────────────┤
    │  Footer: Başlat · Duraklat · İptal Et                    │
    ├──────────────────────────────────────────────────────────┤
    │  Toast bildirim bandı (gizli)                            │
    └──────────────────────────────────────────────────────────┘
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._sm = SettingsManager()
        self._sm.load()
        self._sm.save_failed.connect(self._on_save_failed)

        self._engine = TranslatorEngine(self._sm, self)

        self._chapters: list[ChapterInfo] = []
        # chapter_name → (ChapterCard, QCheckBox)
        self._chapter_rows: dict[str, tuple[ChapterCard, QCheckBox]] = {}

        # Canlı sayaçlar
        self._done_pages: int = 0
        self._failed_pages: int = 0
        self._done_chapters: int = 0
        self._total_chapters: int = 0

        # Hatalı sayfaları yeniden denemek için: chapter_name → [source_path, ...]
        self._failed_page_paths: dict[str, list[str]] = {}


        # Geçmiş yöneticisi
        self._history = HistoryManager()

        # Oturum izleme
        self._session_started_at: float = 0.0
        self._session_start_credits: float | None = None

        self._setup_window()
        self._build_ui()
        self._connect_engine_signals()
        self._build_menu()
        self._restore_state()
        self._setup_tray()
        self._check_for_updates()

    # ------------------------------------------------------------------
    # Pencere ayarları
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        self.setWindowTitle("ToriiBatch")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 780)

        if _ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(_ICON_PATH)))

    # ------------------------------------------------------------------
    # UI kurulumu
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_body(), stretch=1)
        root.addWidget(self._build_footer())

        # Toast (üst-bant bildirim) — en altta, sabit yükseklik
        self._toast = _ToastBar(central)
        root.addWidget(self._toast)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_lbl = QLabel("Hazır")
        self._status_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt;"
        )
        sb.addWidget(self._status_lbl, 1)

    # ── Başlık ────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(70)
        header.setStyleSheet(
            f"background-color: {Colors.BG_SURFACE}; "
            f"border-bottom: 1px solid {Colors.BORDER};"
        )

        layout = QHBoxLayout(header)
        layout.setContentsMargins(Metrics.SPACING_LG, 0, Metrics.SPACING_LG, 0)
        layout.setSpacing(Metrics.SPACING_MD)

        brand_icon = QLabel()
        brand_icon.setPixmap(make_icon("fa5s.language", Colors.ACCENT).pixmap(24, 24))
        brand_icon.setFixedSize(28, 28)

        logo = QLabel("ToriiBatch")
        f = QFont()
        f.setPointSize(Metrics.FONT_SIZE_TITLE + 3)
        f.setBold(True)
        logo.setFont(f)
        logo.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")

        layout.addWidget(brand_icon)
        layout.addWidget(logo)
        layout.addStretch()

        # CreditsBadge
        self._credits_badge = CreditsBadge()
        layout.addWidget(self._credits_badge)

        # Ayarlar butonu
        self._settings_btn = QPushButton("Ayarlar")
        self._settings_btn.setIcon(make_icon("fa5s.cog"))
        self._settings_btn.setProperty("class", "secondary")
        self._settings_btn.setFixedHeight(38)
        self._settings_btn.setIconSize(QSize(16, 16))
        self._settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self._settings_btn)

        return header

    # ── Ana gövde (splitter) ──────────────────────────────────────────

    def _build_body(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet("QSplitter { background: transparent; }")

        left  = self._build_left_panel()
        right = self._build_right_panel()

        splitter.addWidget(left)
        splitter.addWidget(right)

        left.setMinimumWidth(380)
        right.setMinimumWidth(620)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([440, 840])
        return splitter

    # ── Sol panel ─────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background-color: {Colors.BG_BASE};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(
            Metrics.SPACING_LG, Metrics.SPACING_MD,
            Metrics.SPACING_MD, Metrics.SPACING_MD,
        )
        layout.setSpacing(Metrics.SPACING_SM)

        # ── Kaynak klasör ──
        layout.addWidget(self._section_label("Kaynak Klasör"))
        self._source_drop = DropZone(
            "Bölüm klasörlerini içeren ana klasörü buraya sürükleyin veya tıklayıp seçin"
        )
        self._source_drop.folder_selected.connect(self._on_source_selected)
        layout.addWidget(self._source_drop)

        # ── Çıktı klasörü ──
        layout.addWidget(self._section_label("Çıktı Klasörü"))
        out_row = QHBoxLayout()
        out_row.setSpacing(Metrics.SPACING_SM)

        self._output_path_lbl = QLabel("Seçilmedi")
        self._output_path_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )
        self._output_path_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._output_path_lbl.setWordWrap(False)

        change_out_btn = QPushButton("Değiştir")
        change_out_btn.setProperty("class", "secondary")
        change_out_btn.setFixedHeight(30)
        change_out_btn.setFixedWidth(96)
        change_out_btn.setIconSize(QSize(14, 14))
        change_out_btn.setStyleSheet(
            f"font-size: {Metrics.FONT_SIZE_SM}pt; padding: 2px 8px;"
        )
        change_out_btn.clicked.connect(self._browse_output)

        out_row.addWidget(self._output_path_lbl, stretch=1)
        out_row.addWidget(change_out_btn)
        layout.addLayout(out_row)

        # ── Bölüm listesi başlığı ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {Colors.BORDER};")
        layout.addWidget(sep)

        ch_header = QHBoxLayout()
        ch_header.setSpacing(Metrics.SPACING_SM)

        self._chapter_count_lbl = QLabel("0 bölüm")
        self._chapter_count_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "font-weight: 600; background: transparent;"
        )

        select_all_btn = QPushButton("Tümünü Seç")
        select_all_btn.setProperty("class", "secondary")
        select_all_btn.setIcon(make_icon("fa5s.check-square"))
        select_all_btn.setFixedHeight(28)
        select_all_btn.setMinimumWidth(96)
        select_all_btn.setIconSize(QSize(13, 13))
        select_all_btn.setStyleSheet(
            f"font-size: {Metrics.FONT_SIZE_SM}pt; padding: 1px 8px;"
        )
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))

        deselect_all_btn = QPushButton("Tümünü Kaldır")
        deselect_all_btn.setProperty("class", "secondary")
        deselect_all_btn.setIcon(make_icon("fa5s.square"))
        deselect_all_btn.setFixedHeight(28)
        deselect_all_btn.setMinimumWidth(112)
        deselect_all_btn.setIconSize(QSize(13, 13))
        deselect_all_btn.setStyleSheet(select_all_btn.styleSheet())
        deselect_all_btn.clicked.connect(lambda: self._set_all_checked(False))

        ch_header.addWidget(self._chapter_count_lbl)
        ch_header.addStretch()
        ch_header.addWidget(select_all_btn)
        ch_header.addWidget(deselect_all_btn)
        layout.addLayout(ch_header)

        # ── Bölüm listesi (kaydırılabilir) ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {Colors.BG_BASE}; border: none; }}"
        )

        self._chapter_list_widget = QWidget()
        self._chapter_list_widget.setStyleSheet(
            f"background-color: {Colors.BG_BASE};"
        )
        self._chapter_list_layout = QVBoxLayout(self._chapter_list_widget)
        self._chapter_list_layout.setContentsMargins(0, 0, Metrics.SPACING_XS, 0)
        self._chapter_list_layout.setSpacing(Metrics.SPACING_XS)
        self._chapter_list_layout.addStretch()
        scroll.setWidget(self._chapter_list_widget)

        layout.addWidget(scroll, stretch=1)

        # ── Taramayı Yenile ──
        rescan_btn = QPushButton("Taramayı Yenile")
        rescan_btn.setIcon(make_icon("fa5s.sync-alt"))
        rescan_btn.setProperty("class", "secondary")
        rescan_btn.setFixedHeight(34)
        rescan_btn.setIconSize(QSize(15, 15))
        rescan_btn.clicked.connect(self._on_rescan)
        layout.addWidget(rescan_btn)

        return panel

    # ── Sağ panel ─────────────────────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(
            f"background-color: {Colors.BG_SURFACE};"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(
            Metrics.SPACING_LG, Metrics.SPACING_LG,
            Metrics.SPACING_LG, Metrics.SPACING_LG,
        )
        layout.setSpacing(Metrics.SPACING_MD)

        # ── İstatistikler ──
        stats_row = QHBoxLayout()
        stats_row.setSpacing(Metrics.SPACING_MD)
        self._stat_total  = StatCard("Toplam Sayfa", "0", Colors.TEXT_PRIMARY)
        self._stat_done   = StatCard("Tamamlandı",   "0", Colors.SUCCESS)
        self._stat_failed = StatCard("Hatalı",        "0", Colors.ERROR)
        for card in (self._stat_total, self._stat_done, self._stat_failed):
            stats_row.addWidget(card, stretch=1)
        layout.addLayout(stats_row)

        # ── Genel ilerleme ──
        self._progress_lbl = QLabel("Genel İlerleme: —")
        self._progress_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )
        self._main_progress = QProgressBar()
        self._main_progress.setRange(0, 1)
        self._main_progress.setValue(0)
        self._main_progress.setFixedHeight(12)
        self._main_progress.setTextVisible(False)

        layout.addWidget(self._progress_lbl)
        layout.addWidget(self._main_progress)

        # ── Log paneli ──
        layout.addWidget(self._section_label("Canlı Log"), stretch=0)
        self._log_view = LogView()
        layout.addWidget(self._log_view, stretch=1)

        # ── Hızlı ayarlar özet çubuğu ──
        layout.addWidget(self._build_settings_summary_bar())

        return panel

    def _build_settings_summary_bar(self) -> QWidget:
        """Alt kısımda mevcut ayarları özetleyen, tıklanınca ayarları açan şerit."""
        bar = QWidget()
        bar.setObjectName("SettingsSummaryBar")
        bar.setFixedHeight(40)
        bar.setStyleSheet(
            f"QWidget#SettingsSummaryBar {{ "
            f"background-color: {Colors.BG_ELEVATED}; "
            f"border-radius: {Metrics.RADIUS_MD}px; "
            f"border: 1px solid {Colors.BORDER}; }}"
            f"QWidget#SettingsSummaryBar:hover {{ "
            f"border-color: {Colors.ACCENT}; "
            f"background-color: {Colors.BG_ELEVATED}; }}"
        )
        bar.setCursor(Qt.CursorShape.PointingHandCursor)
        bar.mousePressEvent = lambda e: self._open_settings()  # type: ignore[assignment]

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Metrics.SPACING_MD, 0, Metrics.SPACING_MD, 0)
        layout.setSpacing(0)

        self._sum_lang       = self._mini_stat("Dil", "—")
        self._sum_translator = self._mini_stat("Model", "—")
        self._sum_font       = self._mini_stat("Font", "—")

        items = [self._sum_lang, self._sum_translator, self._sum_font]
        for i, w in enumerate(items):
            layout.addWidget(w)
            if i < len(items) - 1:
                # İnce dikey ayırıcı (border değil, sadece renk)
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setFixedWidth(1)
                sep.setStyleSheet(f"background-color: {Colors.BORDER}; border: none;")
                layout.addWidget(sep)
            layout.addSpacing(Metrics.SPACING_MD)

        layout.addStretch()

        hint_lbl = QLabel("Ayarları düzenle")
        hint_lbl.setStyleSheet(
            f"color: {Colors.TEXT_DISABLED}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )
        layout.addWidget(hint_lbl)

        return bar

    # ── Footer (butonlar) ─────────────────────────────────────────────

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(74)
        footer.setStyleSheet(
            f"background-color: {Colors.BG_SURFACE}; "
            f"border-top: 1px solid {Colors.BORDER};"
        )

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(
            Metrics.SPACING_LG, Metrics.SPACING_SM,
            Metrics.SPACING_LG, Metrics.SPACING_SM,
        )
        layout.setSpacing(Metrics.SPACING_MD)

        font_btn = self.font()
        font_btn.setPointSize(Metrics.FONT_SIZE_MD)
        font_btn.setBold(True)

        # ---- Çeviriyi Başlat ----
        self._start_btn = QPushButton("  Çeviriyi Başlat")
        self._start_btn.setIcon(make_icon("fa5s.play", Colors.TEXT_ON_ACCENT))
        self._start_btn.setFixedHeight(46)
        self._start_btn.setIconSize(QSize(16, 16))
        self._start_btn.setMinimumWidth(200)
        self._start_btn.setFont(font_btn)
        self._start_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {Colors.ACCENT};"
            f"  color: {Colors.TEXT_ON_ACCENT};"
            f"  border: none;"
            f"  border-radius: {Metrics.RADIUS_MD}px;"
            f"  padding: 0px 24px;"
            f"  font-weight: 700;"
            f"  font-size: {Metrics.FONT_SIZE_MD}pt;"
            f"  text-align: left;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {Colors.ACCENT_HOVER};"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: {Colors.ACCENT_PRESSED};"
            f"}}"
            f"QPushButton:disabled {{"
            f"  background-color: {Colors.BG_ELEVATED};"
            f"  color: {Colors.TEXT_DISABLED};"
            f"}}"
        )
        self._start_btn.clicked.connect(self._on_start)

        # ---- Duraklat ----
        self._pause_btn = QPushButton("  Duraklat")
        self._pause_btn.setIcon(make_icon("fa5s.pause", Colors.TEXT_PRIMARY))
        self._pause_btn.setFixedHeight(46)
        self._pause_btn.setIconSize(QSize(15, 15))
        self._pause_btn.setMinimumWidth(140)
        self._pause_btn.setFont(font_btn)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {Colors.BG_ELEVATED};"
            f"  color: {Colors.TEXT_PRIMARY};"
            f"  border: 1px solid {Colors.BORDER};"
            f"  border-radius: {Metrics.RADIUS_MD}px;"
            f"  padding: 0px 20px;"
            f"  font-weight: 600;"
            f"  font-size: {Metrics.FONT_SIZE_MD}pt;"
            f"  text-align: left;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {Colors.BG_INPUT};"
            f"  border-color: {Colors.ACCENT_HOVER};"
            f"  color: {Colors.ACCENT_HOVER};"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: {Colors.BG_BASE};"
            f"  border-color: {Colors.ACCENT};"
            f"}}"
            f"QPushButton:disabled {{"
            f"  background-color: {Colors.BG_ELEVATED};"
            f"  color: {Colors.TEXT_DISABLED};"
            f"  border-color: {Colors.BORDER};"
            f"}}"
        )
        self._pause_btn.clicked.connect(self._on_pause_resume)
        self._paused = False

        # ---- İptal Et ----
        self._cancel_btn = QPushButton("  İptal Et")
        self._cancel_btn.setIcon(make_icon("fa5s.stop", Colors.ERROR))
        self._cancel_btn.setFixedHeight(46)
        self._cancel_btn.setIconSize(QSize(15, 15))
        self._cancel_btn.setMinimumWidth(140)
        self._cancel_btn.setFont(font_btn)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {Colors.BG_ELEVATED};"
            f"  color: {Colors.ERROR};"
            f"  border: 1px solid {Colors.BORDER};"
            f"  border-radius: {Metrics.RADIUS_MD}px;"
            f"  padding: 0px 20px;"
            f"  font-weight: 600;"
            f"  font-size: {Metrics.FONT_SIZE_MD}pt;"
            f"  text-align: left;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: rgba(239, 68, 68, 0.12);"
            f"  border-color: {Colors.ERROR};"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: rgba(239, 68, 68, 0.22);"
            f"}}"
            f"QPushButton:disabled {{"
            f"  background-color: {Colors.BG_ELEVATED};"
            f"  color: {Colors.TEXT_DISABLED};"
            f"  border-color: {Colors.BORDER};"
            f"}}"
        )
        self._cancel_btn.clicked.connect(self._on_cancel)

        layout.addStretch()
        layout.addWidget(self._start_btn)
        layout.addWidget(self._pause_btn)
        layout.addWidget(self._cancel_btn)

        return footer

    # ── Yardımcı widget oluşturucular ─────────────────────────────────

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "font-weight: 600; background: transparent;"
        )
        return lbl

    @staticmethod
    def _mini_stat(label: str, value: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(Metrics.SPACING_XS)
        lbl = QLabel(label + ":")
        lbl.setStyleSheet(
            f"color: {Colors.TEXT_DISABLED}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )
        val = QLabel(value)
        val.setObjectName(f"_mini_{label}")
        val.setStyleSheet(
            f"color: {Colors.ACCENT}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "font-weight: 600; background: transparent;"
        )
        hl.addWidget(lbl)
        hl.addWidget(val)
        # value label'a erişim için widget üzerinde sakla
        w._val_lbl = val  # type: ignore[attr-defined]
        return w

    # ------------------------------------------------------------------
    # Menü
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = self.menuBar()

        fm = mb.addMenu("Dosya")
        a = QAction("Kaynak Klasör Aç…", self)
        a.setShortcut("Ctrl+O")
        a.triggered.connect(self._browse_source)
        fm.addAction(a)

        a2 = QAction("Çıktı Klasörü Seç…", self)
        a2.triggered.connect(self._browse_output)
        fm.addAction(a2)

        fm.addSeparator()

        aq = QAction("Çıkış", self)
        aq.setShortcut("Ctrl+Q")
        aq.triggered.connect(self.close)
        fm.addAction(aq)

        tm = mb.addMenu("Araçlar")
        asett = QAction("Ayarlar…", self)
        asett.setShortcut("Ctrl+,")
        asett.triggered.connect(self._open_settings)
        tm.addAction(asett)

        acreds = QAction(make_icon("fa5s.sync-alt"), "Kredi Bakiyesini Yenile", self)
        acreds.triggered.connect(self._refresh_credits)

        tm.addSeparator()
        ahist = QAction(make_icon("fa5s.history"), "Geçmiş…", self)
        ahist.triggered.connect(self._open_history)
        tm.addAction(ahist)
        tm.addAction(acreds)

    # ------------------------------------------------------------------
    # Motor sinyalleri
    # ------------------------------------------------------------------

    def _connect_engine_signals(self) -> None:
        e = self._engine
        e.chapter_started.connect(self._on_chapter_started)
        e.chapter_progress.connect(self._on_chapter_progress)
        e.chapter_finished.connect(self._on_chapter_finished)
        e.image_translated.connect(self._on_image_translated)
        e.log_message.connect(self._on_log_message)
        e.credits_updated.connect(self._on_credits_updated)
        e.error_occurred.connect(self._on_error_occurred)
        e.all_finished.connect(self._on_all_finished)

    # ------------------------------------------------------------------
    # Slotlar — UI etkileşimi
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def _on_source_selected(self, path: str) -> None:
        self._sm.set("source_folder", path)
        # Varsayılan çıktı: kaynak_klasör_translated
        default_out = str(Path(path).parent / (Path(path).name + "_translated"))
        if not self._sm.get("output_folder"):
            self._sm.set("output_folder", default_out)
            self._update_output_label(default_out)
        self._scan_chapters(path)

    @pyqtSlot(str)
    def _on_output_selected(self, path: str) -> None:
        self._sm.set("output_folder", path)
        self._update_output_label(path)

    def _browse_source(self) -> None:
        start = self._sm.get("source_folder") or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Kaynak Klasör Seç", start)
        if path:
            self._source_drop.set_path(path)

    def _browse_output(self) -> None:
        start = self._sm.get("output_folder") or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Çıktı Klasörü Seç", start)
        if path:
            self._on_output_selected(path)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._sm, self)
        if dlg.exec():
            self._refresh_settings_summary()

    def _on_rescan(self) -> None:
        src = self._sm.get("source_folder", "")
        if src:
            self._scan_chapters(src)
        else:
            self._log_view.append_log("warning", "Önce bir kaynak klasör seçin.")

    @pyqtSlot()
    def _on_start(self) -> None:
        """Seçili bölümlerin çevirisini başlatır."""
        selected = self._selected_chapters()
        if not selected:
            QMessageBox.warning(self, "Uyarı", "Çevrilecek bölüm seçilmedi.")
            return

        output = self._sm.get("output_folder", "")
        if not output:
            QMessageBox.warning(self, "Uyarı", "Çıktı klasörü seçilmedi.")
            return

        if not self._sm.get_api_key():
            QMessageBox.warning(
                self, "API Anahtarı Eksik",
                "Ayarlar > API & Kimlik bölümünden API anahtarınızı girin.",
            )
            self._open_settings()
            return

        # Çıktı klasörlerini oluştur
        source_root = self._sm.get("source_folder", "")
        for ch in selected:
            out_dir = Path(build_output_path(ch, source_root, output))
            out_dir.mkdir(parents=True, exist_ok=True)

        # "Kaldığı yerden devam" seçeneği aktifse zaten çevrilmiş sayfaları filtrele
        if self._sm.get("resume_mode", False):
            filtered: list[ChapterInfo] = []
            for ch in selected:
                fch = filter_already_translated(ch, output, source_root)
                if fch.status == "skipped":
                    self._log_view.append_log(
                        "info", f"[{ch.name}] Tüm sayfalar zaten çevrilmiş, atlanıyor."
                    )
                elif fch.page_count > 0:
                    filtered.append(fch)
            if not filtered:
                QMessageBox.information(
                    self, "Bilgi",
                    "Seçili tüm bölümler zaten çevrilmiş. "
                    "Yeniden çevirmek için 'Kaldığı yerden devam' seçeneğini kaldırın."
                )
                return
            selected = filtered

        # Sayaçları sıfırla
        self._done_pages         = 0
        self._failed_pages       = 0
        self._done_chapters      = 0
        self._total_chapters     = len(selected)
        self._failed_page_paths  = {}   # önceki hatalar temizlenir
        total_pages = sum(c.page_count for c in selected)

        # Kredi tahmini — mevcut bakiye biliniyorsa yeterlilik kontrolü
        current_credits = self._credits_badge.credits
        if current_credits is not None and current_credits > 0:
            # Sayfa başına tahmini maliyet (_cost_per_page öğrenilmişse kullan,
            # yoksa varsayılan 1.0 kredi/sayfa ile tahmin et)
            cost_per = getattr(self, "_avg_cost_per_page", 1.0)
            estimated_cost = total_pages * cost_per
            if estimated_cost > current_credits:
                pages_possible = int(current_credits / cost_per)
                reply = QMessageBox.warning(
                    self,
                    "Yetersiz Kredi",
                    f"Tahmini maliyet: {estimated_cost:.1f} kredi\n"
                    f"Mevcut bakiye: {current_credits:.2f} kredi\n\n"
                    f"Kredi yaklaşık {pages_possible} sayfa için yeterli.\n"
                    "Yine de başlamak istiyor musunuz?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        self._stat_total.set_value(str(total_pages))
        self._stat_done.set_value("0")
        self._stat_failed.set_value("0")
        self._main_progress.setRange(0, total_pages)
        self._main_progress.setValue(0)
        self._progress_lbl.setText(
            f"Genel İlerleme: 0/{total_pages} sayfa — Bölüm 0/{self._total_chapters}"
        )

        for name, (card, _) in self._chapter_rows.items():
            card.reset()

        self._set_running_state(True)
        settings = dict(self._sm.all()) if hasattr(self._sm, "all") else {}
        settings["source_folder"] = source_root
        # Oturum başlangıcını kaydet
        import time as _time
        self._session_started_at = _time.time()
        self._session_start_credits = self._credits_badge.credits
        self._engine.start_batch(selected, settings, output)

    @pyqtSlot()
    def _on_pause_resume(self) -> None:
        if not self._paused:
            self._engine.pause()
            self._paused = True
            self._pause_btn.setIcon(make_icon("fa5s.play"))
            self._pause_btn.setText("Devam Et")
            self._status_lbl.setText("Duraklatıldı")
        else:
            self._engine.resume()
            self._paused = False
            self._pause_btn.setIcon(make_icon("fa5s.pause"))
            self._pause_btn.setText("Duraklat")
            self._status_lbl.setText("Çevriliyor…")

    @pyqtSlot()
    def _on_cancel(self) -> None:
        reply = QMessageBox.question(
            self, "İptal Et",
            "Çeviri iptal edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._engine.cancel()
            self._set_running_state(False)
            self._status_lbl.setText("İptal edildi")

    # ------------------------------------------------------------------
    # Slotlar — motor sinyalleri
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def _on_chapter_started(self, chapter_name: str) -> None:
        row = self._chapter_rows.get(chapter_name)
        if row:
            row[0].set_status("in_progress")
        self._status_lbl.setText(f"İşleniyor: {chapter_name}")

    @pyqtSlot(str, int, int)
    def _on_chapter_progress(self, chapter_name: str, done: int, total: int) -> None:
        row = self._chapter_rows.get(chapter_name)
        if row:
            row[0].set_progress(done)

        # done = bu bölümde şimdiye kadar işlenen sayfa sayısı (kümülatif).
        # Her sinyal 1 sayfa ilerlemesini temsil eder (başarılı veya hatalı).
        total_pages = self._main_progress.maximum()
        if self._done_pages < total_pages:
            self._done_pages += 1
        self._main_progress.setValue(self._done_pages)
        # Başarılı = toplam işlenen - hatalı (negatife düşmemesi için max(0,...))
        successful = max(0, self._done_pages - self._failed_pages)
        self._stat_done.set_value(str(successful))
        self._progress_lbl.setText(
            f"Genel İlerleme: {self._done_pages}/{total_pages} sayfa"
            f" — Bölüm {self._done_chapters}/{self._total_chapters}"
        )

    @pyqtSlot(str, bool)
    def _on_chapter_finished(self, chapter_name: str, success: bool) -> None:
        row = self._chapter_rows.get(chapter_name)
        if row:
            row[0].set_status("done" if success else "error")
        self._done_chapters += 1
        self._progress_lbl.setText(
            f"Genel İlerleme: {self._done_pages}/{self._main_progress.maximum()} sayfa"
            f" — Bölüm {self._done_chapters}/{self._total_chapters}"
        )

    @pyqtSlot(str, str, str)
    def _on_image_translated(self, chapter_name: str, src: str, dst: str) -> None:
        # Sessiz — log zaten engine tarafından yazılıyor
        pass

    @pyqtSlot(str, str)
    def _on_log_message(self, level: str, message: str) -> None:
        self._log_view.append_log(level, message)
        if level == "error":
            self._status_lbl.setText(f"Hata: {message[:80]}")

    @pyqtSlot(float)
    def _on_credits_updated(self, credits: float) -> None:
        # Sayfa başına ortalama maliyet öğren.
        # API her sayfa sonunda bir güncelleme gönderir; harcanan miktar
        # (prev - credits) o sayfanın maliyetidir.
        prev = self._credits_badge.credits
        if prev is not None and credits < prev:
            spent = prev - credits
            if 0 < spent < 100:  # mantıksız büyük değerleri filtrele
                old_avg = getattr(self, "_avg_cost_per_page", None)
                if old_avg is None:
                    self._avg_cost_per_page = spent
                else:
                    alpha = 0.3
                    self._avg_cost_per_page = alpha * spent + (1 - alpha) * old_avg
        self._credits_badge.update_credits(credits)

    @pyqtSlot(str, str, str)
    def _on_error_occurred(self, chapter_name: str, error: str, source_path: str) -> None:
        self._failed_pages += 1
        self._stat_failed.set_value(str(self._failed_pages))
        logger.warning("[%s] Hata: %s", chapter_name, error)
        # Yeniden deneme için hatalı sayfa yolunu kaydet
        if source_path:
            if chapter_name not in self._failed_page_paths:
                self._failed_page_paths[chapter_name] = []
            self._failed_page_paths[chapter_name].append(source_path)

    @pyqtSlot()
    def _on_all_finished(self) -> None:
        success = max(0, self._done_pages - self._failed_pages)
        self._set_running_state(False)
        self._stat_done.set_value(str(success))
        self._stat_failed.set_value(str(self._failed_pages))
        self._main_progress.setValue(self._main_progress.maximum())
        self._progress_lbl.setText(
            f"Tamamlandı — {success} başarılı, {self._failed_pages} hatalı"
        )
        self._status_lbl.setText("Tamamlandı")

        output_folder = self._sm.get("output_folder", "")

        def _open_output() -> None:
            if output_folder and Path(output_folder).exists():
                try:
                    os.startfile(output_folder)  # Windows
                except AttributeError:
                    import subprocess
                    subprocess.Popen(["xdg-open", output_folder])

        total_pages = self._done_pages
        if self._failed_pages == 0:
            self._toast.show_message(
                f"Tüm bölümler çevrildi! {total_pages} sayfa işlendi.",
                icon="success",
                action_label="Klasörü Aç",
                action_callback=_open_output,
                duration_ms=10000,
            )
        else:
            self._toast.show_message(
                f"{max(0, success)} başarılı, {self._failed_pages} hatalı. "
                "Hataları yeniden denemek için butona tıklayın.",
                icon="warning",
                action_label="Yeniden Dene",
                action_callback=self._retry_failed_pages,
                duration_ms=15000,
            )


        # --- Oturumu geçmişe kaydet ---
        import time as _time_import
        ended_at = _time_import.time()
        started_at = getattr(self, "_session_started_at", ended_at)
        start_credits = getattr(self, "_session_start_credits", None)
        current_credits = self._credits_badge.credits
        credits_spent = 0.0
        if start_credits is not None and current_credits is not None:
            credits_spent = max(0.0, start_credits - current_credits)
        self._history.add_session(
            started_at=started_at,
            ended_at=ended_at,
            source_folder=self._sm.get("source_folder", ""),
            output_folder=self._sm.get("output_folder", ""),
            total_pages=self._done_pages,
            successful_pages=max(0, self._done_pages - self._failed_pages),
            failed_pages=self._failed_pages,
            total_chapters=self._total_chapters,
            credits_spent=credits_spent,
            translator=self._sm.get("translator", ""),
        )

        # --- Sistem tepsisi bildirimi ---
        if hasattr(self, "_tray") and self._tray is not None and not self.isVisible():
            msg = f"Çeviri tamamlandı! {max(0, self._done_pages - self._failed_pages)} başarılı"
            if self._failed_pages > 0:
                msg += f", {self._failed_pages} hatalı"
            self._tray.showMessage(
                "ToriiBatch",
                msg,
                __import__("PyQt6.QtWidgets", fromlist=["QSystemTrayIcon"]).QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    @pyqtSlot()
    def _retry_failed_pages(self) -> None:
        """
        Hatalı sayfaları yeniden dene.

        `_failed_page_paths` (chapter_name → [source_path, ...]) sözlüğünden
        orijinal ChapterInfo nesnelerini klonlayıp yalnızca hatalı sayfaları
        içerecek şekilde image_paths listesini kısaltır ve engine'e gönderir.
        """
        if not self._failed_page_paths:
            return

        output = self._sm.get("output_folder", "")
        source_root = self._sm.get("source_folder", "")
        if not output:
            QMessageBox.warning(self, "Uyarı", "Çıktı klasörü belirtilmemiş.")
            return

        # Hatalı sayfaları içeren sahte ChapterInfo listesi oluştur
        retry_chapters: list[ChapterInfo] = []
        chapter_map = {ch.name: ch for ch in self._chapters}

        for chapter_name, failed_paths in self._failed_page_paths.items():
            original = chapter_map.get(chapter_name)
            if original is None:
                continue
            # Sadece hatalı sayfaları içeren yeni bir ChapterInfo oluştur
            retry_ch = ChapterInfo(
                name=original.name,
                path=original.path,
                image_paths=list(dict.fromkeys(failed_paths)),  # tekrar sırasızı kaldır
                page_count=len(dict.fromkeys(failed_paths)),
                status="pending",
            )
            retry_chapters.append(retry_ch)

        if not retry_chapters:
            return

        total_retry = sum(c.page_count for c in retry_chapters)
        reply = QMessageBox.question(
            self,
            "Yeniden Dene",
            f"{self._failed_pages} hatalı sayfa ({total_retry} benzersiz) yeniden çevrilecek.\nDevam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Sayaçları yeniden dene moduna al
        self._done_pages        = 0
        self._failed_pages      = 0
        self._done_chapters     = 0
        self._total_chapters    = len(retry_chapters)
        self._failed_page_paths = {}

        self._main_progress.setRange(0, total_retry)
        self._main_progress.setValue(0)
        self._stat_done.set_value("0")
        self._stat_failed.set_value("0")
        self._stat_total.set_value(str(total_retry))
        self._progress_lbl.setText(
            f"Genel İlerleme: 0/{total_retry} sayfa — Bölüm 0/{self._total_chapters}"
        )
        self._log_view.append_log(
            "info",
            f"Yeniden deneme başlatılıyor — {total_retry} hatalı sayfa.",
        )

        self._set_running_state(True)
        settings = dict(self._sm.all()) if hasattr(self._sm, "all") else {}
        settings["source_folder"] = source_root
        self._engine.start_batch(retry_chapters, settings, output)

    # ------------------------------------------------------------------
    # Yardımcı metodlar
    # ------------------------------------------------------------------

    def _scan_chapters(self, source_path: str) -> None:
        """Kaynak klasörü tarar ve bölüm listesini günceller."""
        try:
            self._chapters = scan_root_folder(source_path)
        except Exception as exc:
            QMessageBox.critical(self, "Tarama Hatası", str(exc))
            return

        self._rebuild_chapter_list()

        total_pages = sum(c.page_count for c in self._chapters)
        self._chapter_count_lbl.setText(f"{len(self._chapters)} bölüm")
        self._stat_total.set_value(str(total_pages))
        self._stat_done.set_value("0")
        self._stat_failed.set_value("0")
        self._main_progress.setRange(0, total_pages)
        self._main_progress.setValue(0)
        self._progress_lbl.setText(
            f"Genel İlerleme: 0/{total_pages} sayfa — Bölüm 0/{len(self._chapters)}"
        )
        self._log_view.append_log(
            "info",
            f"Tarama tamamlandı: {len(self._chapters)} bölüm, {total_pages} sayfa.",
        )

    def _rebuild_chapter_list(self) -> None:
        """Bölüm listesindeki widget satırlarını yeniden oluşturur."""
        # Eski satırları temizle
        while self._chapter_list_layout.count() > 1:
            item = self._chapter_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chapter_rows.clear()

        for chapter in self._chapters:
            row_widget = QWidget()
            row_widget.setStyleSheet(f"background-color: {Colors.BG_BASE};")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(Metrics.SPACING_XS)

            cb = QCheckBox()
            cb.setChecked(True)
            cb.setFixedWidth(24)

            card = ChapterCard(chapter.name, chapter.page_count)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            row_layout.addWidget(cb)
            row_layout.addWidget(card, stretch=1)

            self._chapter_rows[chapter.name] = (card, cb)
            self._chapter_list_layout.insertWidget(
                self._chapter_list_layout.count() - 1, row_widget
            )

    def _selected_chapters(self) -> list[ChapterInfo]:
        """İşaretli checkbox'a sahip bölümleri döndürür."""
        selected: list[ChapterInfo] = []
        for chapter in self._chapters:
            row = self._chapter_rows.get(chapter.name)
            if row and row[1].isChecked():
                selected.append(chapter)
        return selected

    def _set_all_checked(self, checked: bool) -> None:
        """Tüm bölüm checkbox'larını toplu olarak ayarlar."""
        for _, (_, cb) in self._chapter_rows.items():
            cb.setChecked(checked)

    def _set_running_state(self, running: bool) -> None:
        """Çalışma durumuna göre butonları aktif/pasif yapar."""
        self._start_btn.setEnabled(not running)
        self._pause_btn.setEnabled(running)
        self._cancel_btn.setEnabled(running)
        self._settings_btn.setEnabled(not running)
        self._source_drop.setEnabled(not running)

        if running:
            self._paused = False
            self._pause_btn.setIcon(make_icon("fa5s.pause"))
            self._pause_btn.setText("Duraklat")
            self._status_lbl.setText("Çevriliyor…")
        else:
            if not self._engine.is_running():
                self._status_lbl.setText("Hazır")

    def _update_output_label(self, path: str) -> None:
        """Çıktı klasörü etiketini günceller (kısaltılmış yol, tam yol tooltip'te)."""
        display = path
        max_len = 48
        if len(path) > max_len:
            parts = Path(path).parts
            if len(parts) > 3:
                display = str(Path(*parts[:2]) / "…" / parts[-1])
        self._output_path_lbl.setText(display)
        self._output_path_lbl.setToolTip(path)
        self._output_path_lbl.setStyleSheet(
            f"color: {Colors.ACCENT}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )

    def _refresh_settings_summary(self) -> None:
        """Ayarlar özet çubuğunu ve sağ paneli günceller."""
        sm = self._sm
        self._sum_lang._val_lbl.setText(sm.get("target_lang", "—"))       # type: ignore[attr-defined]
        self._sum_translator._val_lbl.setText(sm.get("translator", "—"))   # type: ignore[attr-defined]
        self._sum_font._val_lbl.setText(sm.get("font", "—"))               # type: ignore[attr-defined]

    def _refresh_credits(self) -> None:
        """Kredi bakiyesini API'den asenkron olarak günceller."""
        sm = self._sm
        api_key = sm.get_api_key()
        if not api_key:
            return

        def _fetch() -> None:
            async def _run() -> float | None:
                url = "https://api.toriitranslate.com/api/credits"
                headers = {"Authorization": f"Bearer {api_key}"}
                timeout = aiohttp.ClientTimeout(total=15, connect=8)
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 200:
                                body = await resp.json(content_type=None)
                                if isinstance(body, dict):
                                    return float(body.get("credits", 0))
                    return None
                except Exception as exc:
                    logger.warning("Kredi yenileme hatası: %s", exc)
                    return None

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            credits = None
            try:
                credits = loop.run_until_complete(_run())
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as exc:
                logger.warning("Kredi yenileme loop hatası: %s", exc)
            finally:
                loop.close()

            # UI güncellemesi ana thread'de yapılmalı
            if credits is not None:
                QTimer.singleShot(0, lambda: self._credits_badge.update_credits(credits))

        threading.Thread(target=_fetch, daemon=True).start()

    def _restore_state(self) -> None:
        """
        Uygulama açılışında ayarlardan son durumu geri yükler.
        API anahtarı yoksa ayarlar penceresini açmaya yönlendirir.
        """
        sm = self._sm

        # Önceki çıktı klasörünü geri yükle
        saved_out = sm.get("output_folder", "")
        if saved_out:
            self._update_output_label(saved_out)

        # Önceki kaynak klasörü geri yükle ve tarama yap
        saved_src = sm.get("source_folder", "")
        if saved_src and Path(saved_src).is_dir():
            self._source_drop.set_path(saved_src)
            self._scan_chapters(saved_src)

        self._refresh_settings_summary()

        # API anahtarı yoksa uyarı
        if not sm.get_api_key():
            QTimer.singleShot(300, self._warn_missing_api_key)

    def _warn_missing_api_key(self) -> None:
        reply = QMessageBox.warning(
            self,
            "API Anahtarı Gerekli",
            "toriitranslate.com API anahtarı girilmemiş.\n"
            "Uygulamayı kullanmak için Ayarlar'dan API anahtarınızı ekleyin.",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Open,
        )
        if reply == QMessageBox.StandardButton.Open:
            self._open_settings()

    # ------------------------------------------------------------------
    # Kapatma olayı
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def _on_save_failed(self, error: str) -> None:
        """Ayarlar diske yazılamadığında kullanıcıya bildir."""
        QMessageBox.critical(
            self,
            "Kaydetme Hatası",
            f"Ayarlar diske kaydedilemedi:\n{error}\n\n"
            "Uygulama çalışmaya devam eder ancak ayarlar kalıcı olmayabilir.",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._engine.is_running():
            reply = QMessageBox.question(
                self,
                "Çıkış",
                "Çeviri devam ediyor. Yine de çıkmak istiyor musunuz?\n"
                "(İptal işlemi mevcut sayfa bitince durur.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            self._engine.cancel()
            # Thread'in bitmesini bekle — mevcut HTTP isteği bitene kadar
            # maksimum 8 saniye bekle; süre dolarsa force-quit
            thread = self._engine._thread  # type: ignore[attr-defined]
            if thread is not None and thread.isRunning():
                if not thread.wait(8000):
                    logger.warning("Engine thread 8 sn içinde bitmedi, zorla sonlandırılıyor.")
                    thread.terminate()
                    thread.wait(2000)

        self._sm.save()
        event.accept()

    # ------------------------------------------------------------------
    # Sistem Tepsisi
    # ------------------------------------------------------------------

    def _setup_tray(self) -> None:
        """Sistem tepsisi ikonunu ve menüsünü oluşturur."""
        from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        self._tray = QSystemTrayIcon(self)
        if _ICON_PATH.exists():
            self._tray.setIcon(QIcon(str(_ICON_PATH)))
        else:
            self._tray.setIcon(self.style().standardIcon(
                __import__("PyQt6.QtWidgets", fromlist=["QStyle"]).QStyle.StandardPixmap.SP_ComputerIcon
            ))
        self._tray.setToolTip("ToriiBatch")

        tray_menu = QMenu()
        tray_menu.setStyleSheet(
            f"QMenu {{ background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #313244; }}"
            f"QMenu::item:selected {{ background-color: #7c3aed; }}"
        )
        show_action = tray_menu.addAction("Göster")
        show_action.triggered.connect(self._tray_show)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Çıkış")
        quit_action.triggered.connect(self._tray_quit)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _tray_show(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self) -> None:
        self._tray_force_quit = True
        self.close()

    def _on_tray_activated(self, reason) -> None:
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._tray_show()

    def closeEvent(self, event: "QCloseEvent") -> None:
        """Motor çalışırken onay iste; tray varsa minimize et, yoksa kapat."""
        force_quit = getattr(self, "_tray_force_quit", False)
        tray_available = hasattr(self, "_tray") and self._tray is not None

        # Motor çalışıyorsa onay iste
        if self._engine.is_running() and not force_quit:
            reply = QMessageBox.question(
                self, "Çıkış",
                "Çeviri devam ediyor. Çıkmak istiyor musunuz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._engine.cancel()

        # Tray varsa minimize et (force_quit değilse)
        if tray_available and not force_quit:
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "ToriiBatch",
                "Uygulama arka planda çalışmaya devam ediyor.",
                __import__("PyQt6.QtWidgets", fromlist=["QSystemTrayIcon"]).QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            return

        # Geçmiş DB'yi kapat
        if hasattr(self, "_history"):
            self._history.close()

        event.accept()

    # ------------------------------------------------------------------
    # Geçmiş penceresi
    # ------------------------------------------------------------------

    def _open_history(self) -> None:
        dlg = HistoryDialog(self._history, self)
        dlg.exec()

    # ------------------------------------------------------------------
    # Güncelleme kontrolü
    # ------------------------------------------------------------------

    def _check_for_updates(self) -> None:
        """Arka planda GitHub releases API'sini kontrol eder."""
        import threading
        threading.Thread(target=self._fetch_latest_release, daemon=True).start()

    def _fetch_latest_release(self) -> None:
        import urllib.request
        import json as _json
        import logging as _log
        _logger = _log.getLogger(__name__)
        try:
            url = "https://api.github.com/repos/souldret/ToriiBatch/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "ToriiBatch/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode())
            tag = data.get("tag_name", "")
            name = data.get("name", tag)
            html_url = data.get("html_url", "")
            current = "v1.0.0"
            if tag and tag != current:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._show_update_toast(name, html_url))
        except Exception as exc:
            _logger.debug("Güncelleme kontrolü başarısız: %s", exc)

    def _show_update_toast(self, release_name: str, url: str) -> None:
        import webbrowser
        self._toast.show_message(
            f"Yeni sürüm mevcut: {release_name}  — İndirmek için tıklayın.",
            icon="info",
            action_label="GitHub'a Git",
            action_callback=lambda: webbrowser.open(url),
            duration_ms=15000,
        )

