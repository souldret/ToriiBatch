"""
widgets.py - Yeniden kullanılabilir custom Qt widget'ları.

Sorumluluğu:
- ChapterListItemWidget: bölüm listesi satır widget'ı
- LogPanel: renk kodlu kaydırılabilir log görüntüleyici
- CreditsBadge: kredi bakiyesi rozeti
- DropZoneFrame: sürükle-bırak + tıkla klasör seçimi alanı

Tüm widget'lar theme.py renk paleti ile uyumludur.
"""

import datetime
import logging
from pathlib import Path

from PyQt6.QtCore import (
    Qt,
    QSize,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.icons import icon
from ui.theme import Colors, Metrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Yardımcı: renkli nokta (durum göstergesi)
# ---------------------------------------------------------------------------

class _StatusDot(QLabel):
    """
    Küçük dolu daire şeklinde renk kodlu durum göstergesi.

    ``set_status(status)`` çağrısıyla rengi güncellenir.
    """

    _STATUS_COLORS: dict[str, str] = {
        "pending":     Colors.TEXT_DISABLED,
        "in_progress": Colors.ACCENT,
        "done":        Colors.SUCCESS,
        "error":       Colors.ERROR,
        "skipped":     Colors.WARNING,
        "cancelled":   Colors.WARNING,
    }

    _SIZE = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.set_status("pending")

    def set_status(self, status: str) -> None:
        """Durum adına göre nokta rengini günceller."""
        color = self._STATUS_COLORS.get(status, Colors.TEXT_DISABLED)
        self.setStyleSheet(
            f"background-color: {color};"
            f"border-radius: {self._SIZE // 2}px;"
            f"border: none;"
        )
        self.setToolTip(status.replace("_", " ").capitalize())


# ---------------------------------------------------------------------------
# 1. ChapterListItemWidget
# ---------------------------------------------------------------------------

class ChapterListItemWidget(QWidget):
    """
    Bölüm listesindeki tek bir satırı temsil eden widget.

    Düzen (soldan sağa):
    [durum noktası] [bölüm adı]  [sayfa sayısı]  [mini progress bar]

    Mini progress bar yalnızca bölüm işlenirken görünür;
    diğer durumlarda gizlenir.
    """

    def __init__(
        self,
        chapter_name: str,
        page_count: int,
        parent: QWidget | None = None,
    ) -> None:
        """
        Parametreler
        ------------
        chapter_name : str
            Görüntülenecek bölüm adı.
        page_count : int
            Toplam sayfa sayısı.
        parent : QWidget | None
        """
        super().__init__(parent)
        self.setObjectName("ChapterListItemWidget")
        self._chapter_name = chapter_name
        self._page_count = page_count

        self._build_ui()
        self.set_status("pending")

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Metrics.SPACING_SM, Metrics.SPACING_SM,
            Metrics.SPACING_SM, Metrics.SPACING_SM,
        )
        layout.setSpacing(Metrics.SPACING_SM)

        self._dot = _StatusDot()

        self._name_label = QLabel(self._chapter_name)
        self._name_label.setMinimumWidth(0)
        self._name_label.setToolTip(self._chapter_name)
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._name_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; "
            f"font-size: {Metrics.FONT_SIZE_BASE}pt; "
            "background: transparent;"
        )

        self._page_label = QLabel(f"{self._page_count} sayfa")
        self._page_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "background: transparent;"
        )
        self._page_label.setFixedWidth(72)
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, self._page_count if self._page_count > 0 else 1)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedWidth(72)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()

        layout.addWidget(self._dot)
        layout.addWidget(self._name_label)
        layout.addWidget(self._page_label)
        layout.addWidget(self._progress_bar)

        self.setStyleSheet(
            f"QWidget#ChapterListItemWidget {{ background-color: {Colors.BG_ELEVATED}; "
            f"border: 1px solid transparent; "
            f"border-radius: {Metrics.RADIUS_MD}px; }}"
            f"QWidget#ChapterListItemWidget:hover {{ "
            f"border-color: {Colors.BORDER_FOCUS}; "
            f"background-color: {Colors.ACCENT_DIM}; }}"
        )
        self.setMinimumHeight(50)
        self.setMaximumHeight(58)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_status(self, status: str) -> None:
        """
        Bölüm durumunu günceller; durum noktası ve progress bar buna göre ayarlanır.

        Parametreler
        ------------
        status : str
            "pending", "in_progress", "done", "error", "skipped", "cancelled"
        """
        self._dot.set_status(status)

        if status == "in_progress":
            self._progress_bar.show()
        else:
            self._progress_bar.hide()

        # Bölüm adı rengi
        color_map = {
            "done":        Colors.SUCCESS,
            "error":       Colors.ERROR,
            "skipped":     Colors.WARNING,
            "cancelled":   Colors.WARNING,
            "in_progress": Colors.ACCENT,
            "pending":     Colors.TEXT_PRIMARY,
        }
        self._name_label.setStyleSheet(
            f"color: {color_map.get(status, Colors.TEXT_PRIMARY)}; "
            f"font-size: {Metrics.FONT_SIZE_BASE}pt; background: transparent;"
        )

    def set_progress(self, completed: int, failed: int = 0) -> None:
        """
        Progress bar'ı günceller.

        Parametreler
        ------------
        completed : int
            Başarıyla tamamlanan sayfa sayısı.
        failed : int
            Hatalı sayfa sayısı.
        """
        total_done = completed + failed
        self._progress_bar.setValue(min(total_done, self._page_count))
        self._page_label.setText(f"{total_done}/{self._page_count}")

    def reset(self) -> None:
        """Widget'ı başlangıç durumuna döndürür."""
        self._progress_bar.setValue(0)
        self._page_label.setText(f"{self._page_count} sayfa")
        self.set_status("pending")

    @property
    def chapter_name(self) -> str:
        """Bölüm adı."""
        return self._chapter_name


# ---------------------------------------------------------------------------
# 2. LogPanel
# ---------------------------------------------------------------------------

class LogPanel(QWidget):
    """
    Renk kodlu, kaydırılabilir log görüntüleyici.

    Seviyeler:
    - info    → gri (#9a9a9a)
    - warning → turuncu (#F0A500)
    - error   → kırmızı (#E05050)
    - success → turkuaz (#2EE6B6)

    Yeni mesaj eklendiğinde otomatik olarak en alta kaydırılır.
    Üst kısımda "Logları Temizle" butonu bulunur.

    Bellek yönetimi
    ---------------
    Sınırsız widget birikimini önlemek için ``_MAX_ROWS`` sınırı uygulanır.
    Bu sınır aşıldığında en eski satır silinir.
    """

    _MAX_ROWS: int = 500

    _LEVEL_COLORS: dict[str, str] = {
        "info":    Colors.TEXT_SECONDARY,
        "warning": Colors.WARNING,
        "error":   Colors.ERROR,
        "success": Colors.SUCCESS,
        "debug":   Colors.TEXT_DISABLED,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setObjectName("LogPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Başlık çubuğu
        header = QWidget()
        header.setObjectName("LogPanelHeader")
        header.setFixedHeight(34)
        header.setStyleSheet(
            f"QWidget#LogPanelHeader {{ background-color: {Colors.BG_ELEVATED}; "
            f"border-bottom: 1px solid {Colors.BORDER}; "
            f"border-top-left-radius: {Metrics.RADIUS_MD}px; "
            f"border-top-right-radius: {Metrics.RADIUS_MD}px; }}"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(
            Metrics.SPACING_SM, 0, Metrics.SPACING_SM, 0
        )

        title_lbl = QLabel("Log")
        title_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "font-weight: 600; background: transparent;"
        )

        clear_btn = QPushButton("Temizle")
        clear_btn.setIcon(icon("fa5s.trash-alt"))
        clear_btn.setProperty("class", "secondary")
        clear_btn.setFixedHeight(24)
        clear_btn.setFixedWidth(88)
        clear_btn.setStyleSheet(
            f"font-size: {Metrics.FONT_SIZE_SM}pt; padding: 2px 8px;"
        )
        clear_btn.clicked.connect(self.clear)

        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        header_layout.addWidget(clear_btn)

        # Kaydırılabilir log alanı
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {Colors.BG_INPUT}; border: none; "
            f"border-bottom-left-radius: {Metrics.RADIUS_MD}px; "
            f"border-bottom-right-radius: {Metrics.RADIUS_MD}px; }}"
        )

        self._log_container = QWidget()
        self._log_container.setStyleSheet(
            f"background-color: {Colors.BG_INPUT};"
        )
        self._log_layout = QVBoxLayout(self._log_container)
        self._log_layout.setContentsMargins(
            Metrics.SPACING_SM, Metrics.SPACING_SM,
            Metrics.SPACING_SM, Metrics.SPACING_SM,
        )
        self._log_layout.setSpacing(2)
        self._log_layout.addStretch()

        scroll.setWidget(self._log_container)
        self._scroll = scroll

        root.addWidget(header)
        root.addWidget(scroll, stretch=1)

        self.setStyleSheet(
            f"QWidget#LogPanel {{ border: 1px solid {Colors.BORDER}; "
            f"border-radius: {Metrics.RADIUS_MD}px; }}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_log(self, level: str, message: str) -> None:
        """
        Log mesajı ekler.

        Parametreler
        ------------
        level : str
            "info", "warning", "error", "success" veya "debug".
        message : str
            Görüntülenecek mesaj.
        """
        color = self._LEVEL_COLORS.get(level.lower(), Colors.TEXT_SECONDARY)
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 1, 0, 1)
        row_layout.setSpacing(Metrics.SPACING_SM)

        time_lbl = QLabel(timestamp)
        time_lbl.setFixedWidth(52)
        time_lbl.setStyleSheet(
            f"color: {Colors.TEXT_DISABLED}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "font-family: 'Consolas', monospace; background: transparent;"
        )

        level_lbl = QLabel(level.upper()[:4])
        level_lbl.setFixedWidth(36)
        level_lbl.setStyleSheet(
            f"color: {color}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "font-weight: 600; background: transparent;"
        )

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        msg_lbl.setStyleSheet(
            f"color: {color}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "background: transparent;"
        )

        row_layout.addWidget(time_lbl)
        row_layout.addWidget(level_lbl)
        row_layout.addWidget(msg_lbl)

        # addStretch() layout'un sonuna eklenir. insertWidget(count()-1) her seferinde
        # stretch'in hemen önüne ekler → layout: [row0, row1, ..., rowN, stretch]
        self._log_layout.insertWidget(self._log_layout.count() - 1, row)

        # Maksimum satır sınırı — en eski satırı sil
        # Layout: [row0(0), row1(1), ..., rowN(N), stretch(N+1)]
        # Satır sayısı = count() - 1 (stretch hariç)
        while self._log_layout.count() - 1 > self._MAX_ROWS:
            # index 0 = en eski satır
            item = self._log_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # En alta kaydır
        QTimer.singleShot(
            10,
            lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            ),
        )

    def clear(self) -> None:
        """Tüm log satırlarını siler."""
        while self._log_layout.count() > 1:
            item = self._log_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


# ---------------------------------------------------------------------------
# 3. CreditsBadge
# ---------------------------------------------------------------------------

class CreditsBadge(QWidget):
    """
    Kredi bakiyesini gösteren küçük rozet widget'ı.

    Bakiye 5'in altına düştüğünde kırmızı uyarı rengine geçer.
    ``update_credits(value)`` ile bakiye güncellenir.
    """

    _LOW_THRESHOLD: float = 5.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._credits: float | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.setObjectName("CreditsBadge")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Metrics.SPACING_SM, 2, Metrics.SPACING_SM, 2
        )
        layout.setSpacing(Metrics.SPACING_XS)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(14, 14)
        self._icon_lbl.setStyleSheet("background: transparent;")
        self._set_badge_icon("ok")

        self._label = QLabel("\u2014")
        self._label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "background: transparent;"
        )

        layout.addWidget(self._icon_lbl)
        layout.addWidget(self._label)

        self.setStyleSheet(
            f"QWidget#CreditsBadge {{ background-color: {Colors.BG_ELEVATED}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: {Metrics.RADIUS_SM}px; }}"
        )
        self.setFixedHeight(26)

    def _set_badge_icon(self, state: str) -> None:
        if state == "warning":
            icon_name, color = "fa5s.exclamation-triangle", Colors.ERROR
        else:
            icon_name, color = "fa5s.wallet", Colors.ACCENT
        self._icon_lbl.setPixmap(icon(icon_name, color).pixmap(14, 14))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_credits(self, value: float) -> None:
        """
        Kredi bakiyesini günceller ve görsel durumu ayarlar.

        Parametreler
        ------------
        value : float
            Güncel kredi bakiyesi.
        """
        self._credits = value
        self._label.setText(f"Kredi: {value:.2f}")

        if value < self._LOW_THRESHOLD:
            color = Colors.ERROR
            self._set_badge_icon("warning")
            self.setStyleSheet(
                f"QWidget#CreditsBadge {{ background-color: {Colors.BG_ELEVATED}; "
                f"border: 1px solid {Colors.ERROR}; "
                f"border-radius: {Metrics.RADIUS_SM}px; }}"
            )
            self.setToolTip("Kredi bakiyeniz düşük!")
        else:
            color = Colors.TEXT_SECONDARY
            self._set_badge_icon("ok")
            self.setStyleSheet(
                f"QWidget#CreditsBadge {{ background-color: {Colors.BG_ELEVATED}; "
                f"border: 1px solid {Colors.BORDER}; "
                f"border-radius: {Metrics.RADIUS_SM}px; }}"
            )
            self.setToolTip("")

        self._label.setStyleSheet(
            f"color: {color}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "background: transparent;"
        )

    @property
    def credits(self) -> float | None:
        """Mevcut kredi bakiyesi; henüz güncellenmemişse None."""
        return self._credits


# ---------------------------------------------------------------------------
# 4. DropZoneFrame
# ---------------------------------------------------------------------------

class DropZoneFrame(QWidget):
    """
    Sürükle-bırak ve tıklama ile klasör seçimini destekleyen alan.

    Sinyaller
    ---------
    folder_selected : str
        Seçilen klasörün tam yolu.
    """

    folder_selected = pyqtSignal(str)

    _IDLE_STYLE = (
        f"QWidget#DropZoneFrame {{"
        f"  background-color: {Colors.BG_SURFACE};"
        f"  border: 2px dashed {Colors.BORDER};"
        f"  border-radius: {Metrics.RADIUS_LG}px;"
        f"}}"
    )
    _HOVER_STYLE = (
        f"QWidget#DropZoneFrame {{"
        f"  background-color: {Colors.ACCENT_DIM};"
        f"  border: 2px dashed {Colors.ACCENT};"
        f"  border-radius: {Metrics.RADIUS_LG}px;"
        f"}}"
    )
    _SELECTED_STYLE = (
        f"QWidget#DropZoneFrame {{"
        f"  background-color: {Colors.BG_ELEVATED};"
        f"  border: 2px solid {Colors.ACCENT};"
        f"  border-radius: {Metrics.RADIUS_LG}px;"
        f"}}"
    )

    def __init__(
        self,
        placeholder: str = "Klasörü buraya sürükleyin veya tıklayın",
        parent: QWidget | None = None,
    ) -> None:
        """
        Parametreler
        ------------
        placeholder : str
            Klasör seçilmeden önceki yönlendirici metin.
        parent : QWidget | None
        """
        super().__init__(parent)
        self.setObjectName("DropZoneFrame")
        self._placeholder = placeholder
        self._selected_path: str = ""

        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_ui()
        self._apply_style("idle")

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(
            Metrics.SPACING_MD, Metrics.SPACING_SM,
            Metrics.SPACING_MD, Metrics.SPACING_SM,
        )
        layout.setSpacing(Metrics.SPACING_SM)

        # İkon — QStyle standart ikonu
        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("background: transparent;")
        self._icon_lbl = icon_lbl
        self._set_icon_pixmap("folder")

        # Ana metin
        self._main_lbl = QLabel(self._placeholder)
        self._main_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._main_lbl.setWordWrap(True)
        self._main_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_BASE}pt; "
            "background: transparent;"
        )

        # Seçilen yol etiketi
        self._path_lbl = QLabel("")
        self._path_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setStyleSheet(
            f"color: {Colors.ACCENT}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "background: transparent;"
        )
        self._path_lbl.hide()

        layout.addWidget(icon_lbl)
        layout.addWidget(self._main_lbl)
        layout.addWidget(self._path_lbl)

        self.setMinimumHeight(112)
        self.setMaximumHeight(148)

    # ------------------------------------------------------------------
    # İkon yardımcısı
    # ------------------------------------------------------------------

    def _set_icon_pixmap(self, kind: str) -> None:
        icon_name = "fa5s.check-circle" if kind == "ok" else "fa5s.folder-open"
        color = Colors.SUCCESS if kind == "ok" else Colors.ACCENT
        self._icon_lbl.setPixmap(icon(icon_name, color).pixmap(32, 32))

    # ------------------------------------------------------------------
    # Stil
    # ------------------------------------------------------------------

    def _apply_style(self, state: str) -> None:
        styles = {
            "idle":     self._IDLE_STYLE,
            "hover":    self._HOVER_STYLE,
            "selected": self._SELECTED_STYLE,
        }
        self.setStyleSheet(styles.get(state, self._IDLE_STYLE))

    # ------------------------------------------------------------------
    # Drag & Drop olayları
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and Path(urls[0].toLocalFile()).is_dir():
                event.acceptProposedAction()
                self._apply_style("hover")
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._apply_style("selected" if self._selected_path else "idle")

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            folder = urls[0].toLocalFile()
            if Path(folder).is_dir():
                self.set_path(folder)
                event.acceptProposedAction()
                return
        event.ignore()
        self._apply_style("selected" if self._selected_path else "idle")

    # ------------------------------------------------------------------
    # Fare olayları (tıkla → QFileDialog)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_dialog()

    def _open_dialog(self) -> None:
        start_dir = self._selected_path or str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self,
            "Klasör Seç",
            start_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self.set_path(folder)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_path(self, path: str) -> None:
        """
        Seçili klasörü programatik olarak ayarlar ve sinyali yayınlar.

        Parametreler
        ------------
        path : str
            Seçilen klasörün tam yolu.
        """
        self._selected_path = path
        self._apply_style("selected")
        self._set_icon_pixmap("ok")

        # Uzun yolları kısalt
        display = path
        max_len = 55
        if len(display) > max_len:
            parts = Path(path).parts
            if len(parts) > 3:
                display = str(Path(*parts[:2]) / "…" / parts[-1])

        self._main_lbl.setText(Path(path).name)
        self._main_lbl.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; "
            f"font-size: {Metrics.FONT_SIZE_BASE}pt; "
            "font-weight: 600; background: transparent;"
        )
        self._path_lbl.setText(display)
        self._path_lbl.show()

        self.folder_selected.emit(path)
        logger.debug("DropZoneFrame: klasör seçildi → %s", path)

    def get_path(self) -> str:
        """
        Seçilen klasörün tam yolunu döndürür.

        Dönüş
        -----
        str
            Seçilen yol; henüz seçilmemişse boş string.
        """
        return self._selected_path

    def clear_path(self) -> None:
        """Seçili klasörü temizler ve widget'ı başlangıç durumuna döndürür."""
        self._selected_path = ""
        self._apply_style("idle")
        self._set_icon_pixmap("folder")
        self._main_lbl.setText(self._placeholder)
        self._main_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_BASE}pt; "
            "background: transparent;"
        )
        self._path_lbl.hide()


# ---------------------------------------------------------------------------
# Geriye dönük uyumluluk — main_window.py'de kullanılan eski isimler
# ---------------------------------------------------------------------------

# DropZone → DropZoneFrame
DropZone = DropZoneFrame

# LogView → LogPanel
LogView = LogPanel


# ---------------------------------------------------------------------------
# StatCard — istatistik kartı (main_window.py'de kullanılır)
# ---------------------------------------------------------------------------

class StatCard(QWidget):
    """
    Büyük sayı + etiket gösteren istatistik kartı.

    main_window.py'deki "Toplam / Tamamlandı / Hatalı" kartları için kullanılır.
    """

    def __init__(
        self,
        label: str,
        value: str = "0",
        value_color: str = Colors.TEXT_PRIMARY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._build_ui(label, value, value_color)

    def _build_ui(self, label: str, value: str, color: str) -> None:
        self.setObjectName("StatCard")
        self.setStyleSheet(
            f"QWidget#StatCard {{ "
            f"background-color: {Colors.BG_ELEVATED}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-top: 2px solid {color}; "
            f"border-radius: {Metrics.RADIUS_MD}px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Metrics.SPACING_MD, Metrics.SPACING_MD,
            Metrics.SPACING_MD, Metrics.SPACING_MD,
        )
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._value_lbl = QLabel(value)
        font = QFont()
        font.setPointSize(Metrics.FONT_SIZE_TITLE + 4)
        font.setBold(True)
        self._value_lbl.setFont(font)
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value_lbl.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )

        self._label_lbl = QLabel(label)
        self._label_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label_lbl.setWordWrap(True)
        self._label_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; "
            "background: transparent; border: none;"
        )

        layout.addWidget(self._value_lbl)
        layout.addWidget(self._label_lbl)

        self.setMinimumWidth(90)
        self.setMinimumHeight(80)

    def set_value(self, value: str) -> None:
        """
        Görüntülenen sayıyı günceller.

        Parametreler
        ------------
        value : str
            Yeni değer metni.
        """
        self._value_lbl.setText(value)


# ---------------------------------------------------------------------------
# ChapterCard — main_window.py ile uyumluluk için alias
# ---------------------------------------------------------------------------

ChapterCard = ChapterListItemWidget