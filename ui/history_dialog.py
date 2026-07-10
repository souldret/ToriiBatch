"""
history_dialog.py - Çeviri geçmişi penceresi.

Sorumluluğu:
- HistoryManager'dan oturumları okuyup tablo halinde göstermek.
- Toplam istatistikleri özet kartlarda göstermek.
- Geçmişi temizleme butonu sunmak.
"""

import time
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.history_manager import HistoryManager
from ui.theme import Colors, Metrics


def _fmt_duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec} sn"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} dk {s} sn"
    h, m = divmod(m, 60)
    return f"{h} sa {m} dk"


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "—"


class HistoryDialog(QDialog):
    """Geçmiş oturumları listeleyen dialog penceresi."""

    def __init__(self, history: HistoryManager, parent=None) -> None:
        super().__init__(parent)
        self._history = history
        self.setWindowTitle("Çeviri Geçmişi")
        self.setMinimumSize(860, 520)
        self.resize(1000, 600)
        self.setStyleSheet(f"background-color: {Colors.BG_BASE}; color: {Colors.TEXT_PRIMARY};")
        self._build_ui()
        self._load_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Metrics.SPACING_LG, Metrics.SPACING_LG,
                                  Metrics.SPACING_LG, Metrics.SPACING_LG)
        layout.setSpacing(Metrics.SPACING_MD)

        # Başlık
        title = QLabel("Çeviri Geçmişi")
        f = title.font()
        f.setPointSize(Metrics.FONT_SIZE_TITLE)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        layout.addWidget(title)

        # Özet kartlar satırı
        self._summary_row = QHBoxLayout()
        self._summary_row.setSpacing(Metrics.SPACING_MD)
        layout.addLayout(self._summary_row)

        # Tablo
        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "Tarih", "Süre", "Bölüm", "Toplam Sayfa",
            "Başarılı", "Hatalı", "Kredi", "Model", "Kaynak",
        ])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {Colors.BG_SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                gridline-color: {Colors.BORDER};
                color: {Colors.TEXT_PRIMARY};
            }}
            QTableWidget::item:selected {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_ON_ACCENT};
            }}
            QHeaderView::section {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_SECONDARY};
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {Colors.BORDER};
                font-weight: 600;
            }}
            QTableWidget::item:alternate {{
                background-color: {Colors.BG_ELEVATED};
            }}
        """)
        layout.addWidget(self._table, stretch=1)

        # Alt butonlar
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        clear_btn = QPushButton("Geçmişi Temizle")
        clear_btn.setFixedHeight(36)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.ERROR};
                border: 1px solid {Colors.BORDER};
                border-radius: {Metrics.RADIUS_MD}px;
                padding: 0 16px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: rgba(239,68,68,0.12);
                border-color: {Colors.ERROR};
            }}
        """)
        clear_btn.clicked.connect(self._on_clear)
        close_btn = QPushButton("Kapat")
        close_btn.setFixedHeight(36)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_ON_ACCENT};
                border: none;
                border-radius: {Metrics.RADIUS_MD}px;
                padding: 0 20px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background-color: {Colors.ACCENT_HOVER}; }}
        """)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _make_summary_card(self, title: str, value: str) -> QWidget:
        card = QWidget()
        card.setStyleSheet(f"""
            QWidget {{
                background-color: {Colors.BG_SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        vl = QVBoxLayout(card)
        vl.setContentsMargins(16, 10, 16, 10)
        vl.setSpacing(2)
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.FONT_SIZE_SM}pt; border: none; background: transparent;")
        lbl_val = QLabel(value)
        f = lbl_val.font()
        f.setPointSize(Metrics.FONT_SIZE_MD + 2)
        f.setBold(True)
        lbl_val.setFont(f)
        lbl_val.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; border: none; background: transparent;")
        vl.addWidget(lbl_title)
        vl.addWidget(lbl_val)
        return card

    def _load_data(self) -> None:
        # Özet kartları temizle
        while self._summary_row.count():
            item = self._summary_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        totals = self._history.get_totals()
        sessions = self._history.get_sessions(limit=200)

        session_count = totals.get("session_count") or 0
        total_pages   = totals.get("total_pages") or 0
        credits_spent = totals.get("credits_spent") or 0.0
        total_dur     = totals.get("total_duration_sec") or 0.0

        self._summary_row.addWidget(self._make_summary_card("Toplam Oturum", str(session_count)))
        self._summary_row.addWidget(self._make_summary_card("Toplam Sayfa", str(total_pages)))
        self._summary_row.addWidget(self._make_summary_card("Toplam Kredi", f"{credits_spent:.2f}"))
        self._summary_row.addWidget(self._make_summary_card("Toplam Süre", _fmt_duration(total_dur)))
        self._summary_row.addStretch()

        # Tablo doldur
        self._table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            src_name = s.source_folder.split("\\")[-1].split("/")[-1] or s.source_folder

            cells = [
                _fmt_ts(s.started_at),
                _fmt_duration(s.duration_sec),
                str(s.total_chapters),
                str(s.total_pages),
                str(s.successful_pages),
                str(s.failed_pages),
                f"{s.credits_spent:.2f}",
                s.translator,
                src_name,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Hatalı satırları kırmızımsı yap
                if s.failed_pages > 0 and col == 5:
                    item.setForeground(
                        QColor(Colors.ERROR)
                    )
                self._table.setItem(row, col, item)

    def _on_clear(self) -> None:
        reply = QMessageBox.question(
            self, "Geçmişi Temizle",
            "Tüm geçmiş silinecek. Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._history.clear()
            self._load_data()