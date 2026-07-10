"""
settings_dialog.py - Ayarlar diyaloğu.

Sorumluluğu:
- API anahtarı, BYOK, hedef dil, translator, font, prompt vb.
  tüm çeviri parametrelerini sekmeli QDialog ile düzenlemek.
- SettingsManager üzerinden okuma/yazma yapmak.
- Kaydet'te settings_changed sinyali yayınlamak.
"""

import asyncio
import logging
import threading
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.icons import icon
from ui.theme import Colors, Metrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Yardımcı widget fabrikaları
# ---------------------------------------------------------------------------

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {Colors.TEXT_SECONDARY}; "
        f"font-size: {Metrics.FONT_SIZE_SM}pt; "
        "font-weight: 600; "
        f"background: transparent; "
        f"padding-top: {Metrics.SPACING_SM}px;"
    )
    return lbl


def _page_intro(title: str, description: str) -> QWidget:
    container = QWidget()
    container.setObjectName("SettingsPageIntro")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, Metrics.SPACING_SM)
    layout.setSpacing(Metrics.SPACING_XS)

    heading = QLabel(title)
    heading.setProperty("class", "pageTitle")
    detail = QLabel(description)
    detail.setProperty("class", "pageDescription")
    detail.setWordWrap(True)

    layout.addWidget(heading)
    layout.addWidget(detail)
    return container


def _note_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        f"color: {Colors.TEXT_SECONDARY}; "
        f"font-size: {Metrics.FONT_SIZE_SM}pt; "
        "background: transparent;"
    )
    return lbl


def _separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet(
        f"color: {Colors.BORDER}; background-color: {Colors.BORDER};"
    )
    sep.setFixedHeight(1)
    return sep


def _scrollable(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet(
        f"QScrollArea {{ background: transparent; border: none; }}"
    )
    scroll.setWidget(inner)
    return scroll


# ---------------------------------------------------------------------------
# Düzenlenebilir liste (translator / dil listeleri)
# ---------------------------------------------------------------------------

class _EditableListWidget(QWidget):
    """Sürükle-sırala + Ekle/Sil destekli liste bileşeni."""

    def __init__(self, title: str, items: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.SPACING_SM)

        layout.addWidget(_section_label(title))

        self._list = QListWidget()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._list.setMinimumHeight(120)
        self._list.setMaximumHeight(180)
        for item in items:
            self._list.addItem(item)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Metrics.SPACING_SM)
        add_btn = QPushButton("Ekle")
        add_btn.setIcon(icon("fa5s.plus"))
        add_btn.setProperty("class", "secondary")
        add_btn.setFixedHeight(26)
        add_btn.clicked.connect(self._add_item)
        del_btn = QPushButton("Sil")
        del_btn.setIcon(icon("fa5s.trash-alt", Colors.ERROR))
        del_btn.setProperty("class", "danger")
        del_btn.setFixedHeight(26)
        del_btn.clicked.connect(self._delete_item)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()

        layout.addWidget(self._list)
        layout.addLayout(btn_row)

    def get_items(self) -> list[str]:
        return [
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).text().strip()
        ]

    def set_items(self, items: list[str]) -> None:
        self._list.clear()
        for item in items:
            self._list.addItem(item)

    @pyqtSlot()
    def _add_item(self) -> None:
        text, ok = QInputDialog.getText(self, "Yeni Öğe", "Değer:")
        if ok and text.strip():
            self._list.addItem(text.strip())

    @pyqtSlot()
    def _delete_item(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))


# ---------------------------------------------------------------------------
# Ana diyalog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """
    Sekmeli ayarlar diyaloğu.

    Sekmeler
    --------
    1. API & Kimlik — API anahtarı, kredi kontrolü, BYOK
    2. Çeviri         — dil, model, font, prompt, context chain
    3. Çıktı          — klasör, format, yedek, devam et
    4. Genel          — tema, güncelleme kontrolü
    """

    def __init__(self, settings_manager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sm = settings_manager
        self.setWindowTitle("Ayarlar — ToriiBatch")
        self.setMinimumSize(780, 640)
        self.resize(900, 720)
        self.setModal(True)
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # Ana düzen
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("SettingsHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(
            Metrics.SPACING_LG, Metrics.SPACING_MD,
            Metrics.SPACING_LG, Metrics.SPACING_MD,
        )
        header_layout.setSpacing(Metrics.SPACING_XS)

        title = QLabel("Ayarlar")
        title.setProperty("class", "dialogTitle")
        subtitle = QLabel("Çeviri akışını, bağlantıları ve çıktı davranışını yönetin.")
        subtitle.setProperty("class", "dialogSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        body = QWidget()
        body.setObjectName("SettingsBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._settings_nav = QListWidget()
        self._settings_nav.setObjectName("SettingsNav")
        self._settings_nav.setFixedWidth(196)
        self._settings_nav.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._settings_stack = QStackedWidget()
        self._settings_stack.setObjectName("SettingsStack")

        pages = [
            ("API ve Kimlik", "fa5s.key", self._build_api_tab()),
            ("Çeviri", "fa5s.language", self._build_translate_tab()),
            ("Çıktı", "fa5s.file-export", self._build_output_tab()),
            ("Genel", "fa5s.sliders-h", self._build_general_tab()),
        ]
        for label, icon_name, page in pages:
            item = QListWidgetItem(icon(icon_name), label)
            item.setSizeHint(QSize(0, 48))
            self._settings_nav.addItem(item)
            self._settings_stack.addWidget(page)

        self._settings_nav.currentRowChanged.connect(
            self._settings_stack.setCurrentIndex
        )
        self._settings_nav.setCurrentRow(0)

        body_layout.addWidget(self._settings_nav)
        body_layout.addWidget(self._settings_stack, stretch=1)
        root.addWidget(body, stretch=1)

        footer = QWidget()
        footer.setObjectName("SettingsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(
            Metrics.SPACING_LG, Metrics.SPACING_SM,
            Metrics.SPACING_LG, Metrics.SPACING_SM,
        )
        footer_layout.setSpacing(Metrics.SPACING_SM)

        reset_btn = QPushButton("Varsayılanlara Dön")
        reset_btn.setIcon(icon("fa5s.undo"))
        reset_btn.setProperty("class", "secondary")
        reset_btn.clicked.connect(self._on_reset)

        cancel_btn = QPushButton("İptal")
        cancel_btn.setIcon(icon("fa5s.times"))
        cancel_btn.setProperty("class", "secondary")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Değişiklikleri Kaydet")
        save_btn.setIcon(icon("fa5s.save", Colors.TEXT_ON_ACCENT))
        save_btn.clicked.connect(self._on_save)

        footer_layout.addWidget(reset_btn)
        footer_layout.addStretch()
        footer_layout.addWidget(cancel_btn)
        footer_layout.addWidget(save_btn)
        root.addWidget(footer)

    # ------------------------------------------------------------------
    # SEKME 1 — API & Kimlik
    # ------------------------------------------------------------------

    def _build_api_tab(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(
            Metrics.SPACING_MD, Metrics.SPACING_MD,
            Metrics.SPACING_MD, Metrics.SPACING_MD,
        )
        layout.setSpacing(Metrics.SPACING_MD)

        layout.addWidget(_page_intro(
            "API ve Kimlik",
            "Torii Translate erişimini ve harici model sağlayıcılarını yapılandırın.",
        ))

        # ── API Anahtarı ──
        layout.addWidget(_section_label("Torii Translate API Anahtarı"))

        key_row = QHBoxLayout()
        key_row.setSpacing(Metrics.SPACING_SM)

        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText("tt_••••••••••••••••••••••••••••••")
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        show_btn = QPushButton()
        show_btn.setIcon(icon("fa5s.eye"))
        show_btn.setProperty("class", "secondary")
        show_btn.setFixedSize(34, 34)
        show_btn.setCheckable(True)
        show_btn.setToolTip("Anahtarı göster / gizle")
        def _toggle_api_key(checked: bool) -> None:
            self._api_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
            show_btn.setIcon(icon("fa5s.eye-slash" if checked else "fa5s.eye"))

        show_btn.toggled.connect(_toggle_api_key)

        key_row.addWidget(self._api_key_input, stretch=1)
        key_row.addWidget(show_btn)
        layout.addLayout(key_row)

        layout.addWidget(_note_label(
            "Anahtarı toriitranslate.com adresinden edinin. "
            "Yerel olarak Fernet şifrelemesiyle saklanır."
        ))

        # Kredi kontrol butonu
        credits_row = QHBoxLayout()
        credits_row.setSpacing(Metrics.SPACING_SM)
        check_credits_btn = QPushButton("Kredi Bakiyesini Kontrol Et")
        check_credits_btn.setIcon(icon("fa5s.sync-alt"))
        check_credits_btn.setProperty("class", "secondary")
        check_credits_btn.setFixedHeight(32)
        check_credits_btn.clicked.connect(self._on_check_credits)
        self._credits_result_lbl = QLabel("")
        self._credits_result_lbl.setStyleSheet(
            f"color: {Colors.ACCENT}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )
        credits_row.addWidget(check_credits_btn)
        credits_row.addWidget(self._credits_result_lbl)
        credits_row.addStretch()
        layout.addLayout(credits_row)

        layout.addWidget(_separator())

        # ── BYOK ──
        layout.addWidget(_section_label("BYOK — Kendi API Anahtarını Kullan"))

        self._byok_provider = QComboBox()
        self._byok_provider.addItems([
            "Yok", "OpenAI", "OpenRouter", "Google",
            "Anthropic", "DeepSeek", "xAI", "Yerel",
        ])
        self._byok_provider.currentIndexChanged.connect(self._on_byok_provider_changed)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(Metrics.SPACING_SM)
        form.addRow("Sağlayıcı:", self._byok_provider)

        self._byok_key_input = QLineEdit()
        self._byok_key_input.setPlaceholderText("Sağlayıcı API anahtarı")
        self._byok_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._byok_key_row_lbl = QLabel("Anahtar:")
        form.addRow(self._byok_key_row_lbl, self._byok_key_input)

        self._byok_local_url = QLineEdit()
        self._byok_local_url.setPlaceholderText("http://localhost:11434")
        self._byok_local_url_lbl = QLabel("Base URL:")
        form.addRow(self._byok_local_url_lbl, self._byok_local_url)

        layout.addLayout(form)

        layout.addWidget(_note_label(
            "BYOK kullanırsan görsel çevirisi kredi başına 1'e sabitlenir; "
            "kendi API anahtarınla ücretlendirilirsin."
        ))

        layout.addStretch()
        return _scrollable(inner)

    # ------------------------------------------------------------------
    # SEKME 2 — Çeviri Ayarları
    # ------------------------------------------------------------------

    def _build_translate_tab(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(
            Metrics.SPACING_MD, Metrics.SPACING_MD,
            Metrics.SPACING_MD, Metrics.SPACING_MD,
        )
        layout.setSpacing(Metrics.SPACING_MD)

        layout.addWidget(_page_intro(
            "Çeviri",
            "Dil, model, tipografi ve bağlam davranışını belirleyin.",
        ))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(Metrics.SPACING_SM)
        form.setContentsMargins(0, 0, 0, 0)

        # Hedef dil
        lang_widget = QWidget()
        lang_widget.setStyleSheet("background: transparent;")
        lang_vbox = QVBoxLayout(lang_widget)
        lang_vbox.setContentsMargins(0, 0, 0, 0)
        lang_vbox.setSpacing(Metrics.SPACING_XS)

        self._target_lang = QComboBox()
        self._target_lang.setEditable(False)
        for code in self._sm.get_language_options():
            self._target_lang.addItem(code)
        self._target_lang.addItem("— Özel kod gir —")
        self._target_lang.currentIndexChanged.connect(self._on_lang_changed)

        self._custom_lang_input = QLineEdit()
        self._custom_lang_input.setPlaceholderText("ISO 639-1 kodu, örn: fil, ms, uk")
        self._custom_lang_input.hide()

        lang_vbox.addWidget(self._target_lang)
        lang_vbox.addWidget(self._custom_lang_input)
        form.addRow("Hedef Dil:", lang_widget)

        # Translator
        self._translator = QComboBox()
        self._translator.setEditable(True)
        for t in self._sm.get_translator_options():
            self._translator.addItem(t)
        self._translator.setToolTip("Model adını doğrudan yazabilirsiniz")
        form.addRow("Model:", self._translator)

        # Font
        self._font_combo = QComboBox()
        for f in self._sm.get_font_options():
            self._font_combo.addItem(f)
        form.addRow("Font:", self._font_combo)

        # Metin hizalama
        self._text_align = QComboBox()
        self._text_align.addItems(["auto", "left", "center", "right"])
        form.addRow("Metin Hizalama:", self._text_align)

        layout.addLayout(form)
        layout.addWidget(_separator())

        # ── Opsiyonlar ──
        layout.addWidget(_section_label("Opsiyonlar"))

        self._stroke_disabled = QCheckBox("Anahat (stroke) efektini devre dışı bırak")
        self._bubbles_only    = QCheckBox("Sadece konuşma balonlarını çevir (bubbles_only)")
        layout.addWidget(self._stroke_disabled)
        layout.addWidget(self._bubbles_only)

        # Min font boyutu
        mf_row = QHBoxLayout()
        mf_row.setSpacing(Metrics.SPACING_SM)
        mf_lbl = QLabel("Min. Yazı Tipi Boyutu:")
        mf_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        self._min_font_size = QSpinBox()
        self._min_font_size.setRange(0, 72)
        self._min_font_size.setValue(0)
        self._min_font_size.setSpecialValueText("Opsiyonel")
        self._min_font_size.setFixedWidth(100)
        mf_row.addWidget(mf_lbl)
        mf_row.addWidget(self._min_font_size)
        mf_row.addStretch()
        layout.addLayout(mf_row)

        layout.addWidget(_separator())

        # ── Context chain ──
        layout.addWidget(_section_label("Bağlam Zinciri (Context Chain)"))
        self._use_context = QCheckBox("Bağlam zinciri kullan")
        layout.addWidget(self._use_context)
        layout.addWidget(_note_label(
            "Bölüm içindeki sayfalar arasında karakter adları ve terminoloji tutarlılığı sağlar. "
            "İlk sayfa için bağlam 'None' gönderilir; her sayfanın yanıtı bir sonrakine iletilir."
        ))

        layout.addWidget(_separator())

        # ── Özel prompt ──
        layout.addWidget(_section_label("Özel Çeviri Talimatı (custom_prompt)"))
        self._custom_prompt = QTextEdit()
        self._custom_prompt.setPlaceholderText(
            "Çevirici için ek yönergeler — örn: 'Türkçe çeviri yap, argo kullanma, "
            "karakter adlarını İngilizce bırak.'"
        )
        self._custom_prompt.setMinimumHeight(80)
        self._custom_prompt.setMaximumHeight(110)
        self._custom_prompt.textChanged.connect(self._on_prompt_changed)
        layout.addWidget(self._custom_prompt)

        counter_row = QHBoxLayout()
        self._prompt_counter = QLabel("0 / 1000")
        self._prompt_counter.setStyleSheet(
            f"color: {Colors.TEXT_DISABLED}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )
        self._prompt_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        counter_row.addStretch()
        counter_row.addWidget(self._prompt_counter)
        layout.addLayout(counter_row)

        layout.addStretch()
        return _scrollable(inner)

    # ------------------------------------------------------------------
    # SEKME 3 — Çıktı
    # ------------------------------------------------------------------

    def _build_output_tab(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(
            Metrics.SPACING_MD, Metrics.SPACING_MD,
            Metrics.SPACING_MD, Metrics.SPACING_MD,
        )
        layout.setSpacing(Metrics.SPACING_MD)

        layout.addWidget(_page_intro(
            "Çıktı",
            "Dosya konumunu, görsel formatını ve devam etme seçeneklerini yönetin.",
        ))

        # ── Çıktı klasörü ──
        layout.addWidget(_section_label("Çıktı Klasörü"))
        out_row = QHBoxLayout()
        out_row.setSpacing(Metrics.SPACING_SM)
        self._output_folder_lbl = QLabel("Seçilmedi")
        self._output_folder_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )
        self._output_folder_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        browse_btn = QPushButton("Gözat…")
        browse_btn.setIcon(icon("fa5s.folder-open"))
        browse_btn.setProperty("class", "secondary")
        browse_btn.setFixedHeight(30)
        browse_btn.clicked.connect(self._browse_output_folder)
        out_row.addWidget(self._output_folder_lbl, stretch=1)
        out_row.addWidget(browse_btn)
        layout.addLayout(out_row)

        layout.addWidget(_separator())

        # ── Görsel formatı ──
        layout.addWidget(_section_label("Çıktı Görsel Formatı"))
        fmt_row = QHBoxLayout()
        self._output_format = QComboBox()
        self._output_format.addItems(["png", "jpg", "webp"])
        self._output_format.setFixedWidth(110)
        fmt_row.addWidget(self._output_format)
        fmt_row.addStretch()
        layout.addLayout(fmt_row)

        layout.addWidget(_separator())

        # ── Seçenekler ──
        layout.addWidget(_section_label("Seçenekler"))
        self._keep_backup    = QCheckBox("Orijinal görsellerin yedeğini tut")
        self._keep_inpainted = QCheckBox(
            "Inpainted (temizlenmiş, çevirisiz) kopyayı da kaydet"
        )
        self._resume_mode = QCheckBox("Çevrilmiş sayfaları atla ve devam et")
        self._resume_mode.setToolTip(
            "Çıktı klasöründe bulunan sayfaları yeniden çevirmeden kaldığı yerden devam eder."
        )
        layout.addWidget(self._keep_backup)
        layout.addWidget(self._keep_inpainted)
        layout.addWidget(self._resume_mode)

        layout.addWidget(_note_label(
            "'Kaldığı yerden devam et' etkinleştirilirse, çıktı klasöründe "
            "zaten var olan sayfalar yeniden çevrilmez."
        ))

        layout.addWidget(_separator())

        # ── Eşzamanlılık ──
        layout.addWidget(_section_label("Eşzamanlı Bölüm İşleme"))
        conc_row = QHBoxLayout()
        self._max_concurrent = QSpinBox()
        self._max_concurrent.setRange(1, 5)
        self._max_concurrent.setValue(1)
        self._max_concurrent.setFixedWidth(80)
        conc_row.addWidget(self._max_concurrent)
        conc_row.addWidget(_note_label(
            "API rate limit: saniyede 1 istek (steady-state). "
            "Birden fazla bölüm paralel işlenmez, bu ayar ileride kullanılacak."
        ))
        conc_row.addStretch()
        layout.addLayout(conc_row)

        layout.addStretch()
        return _scrollable(inner)

    # ------------------------------------------------------------------
    # SEKME 4 — Genel
    # ------------------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(
            Metrics.SPACING_MD, Metrics.SPACING_MD,
            Metrics.SPACING_MD, Metrics.SPACING_MD,
        )
        layout.setSpacing(Metrics.SPACING_MD)

        layout.addWidget(_page_intro(
            "Genel",
            "Uygulama görünümünü ve düzenlenebilir seçenek listelerini yönetin.",
        ))

        # ── Tema ──
        layout.addWidget(_section_label("Tema"))
        theme_row = QHBoxLayout()
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["Koyu"])   # Açık tema ileride eklenecek
        self._theme_combo.setFixedWidth(140)
        self._theme_combo.setEnabled(False)    # Şimdilik sadece koyu
        theme_row.addWidget(self._theme_combo)
        theme_row.addWidget(_note_label("Açık tema ileride eklenecek."))
        theme_row.addStretch()
        layout.addLayout(theme_row)

        layout.addWidget(_separator())

        # ── Güncelleme kontrolü ──
        layout.addWidget(_section_label("Güncellemeler"))
        self._check_updates = QCheckBox("Açılışta güncellemeleri kontrol et")
        self._check_updates.setToolTip("Bu özellik yakında kullanıma sunulacak.")
        self._check_updates.setEnabled(False)   # Şimdilik işlevsiz
        layout.addWidget(self._check_updates)

        layout.addWidget(_separator())

        # ── Gelişmiş listeler ──
        layout.addWidget(_section_label("Gelişmiş — Düzenlenebilir Listeler"))
        layout.addWidget(_note_label(
            "Translator ve dil listelerini özelleştirin. "
            "Sürükleyerek yeniden sıralayabilirsiniz."
        ))

        self._translator_list = _EditableListWidget(
            "Translator Modelleri",
            self._sm.get_translator_options(),
        )
        self._language_list = _EditableListWidget(
            "Dil Kodları",
            self._sm.get_language_options(),
        )
        layout.addWidget(self._translator_list)
        layout.addWidget(self._language_list)

        layout.addStretch()
        return _scrollable(inner)

    # ------------------------------------------------------------------
    # Değer yükleme
    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        """Mevcut ayarları tüm form alanlarına yükler."""
        sm = self._sm

        # Sekme 1 — API
        self._api_key_input.setText(sm.get_api_key())

        raw_provider = sm.get("byok_provider", "none").lower()
        provider_map = {
            "none": "Yok", "openai": "OpenAI", "openrouter": "OpenRouter",
            "google": "Google", "anthropic": "Anthropic",
            "deepseek": "DeepSeek", "xai": "xAI", "local": "Yerel",
        }
        display = provider_map.get(raw_provider, "Yok")
        idx = self._byok_provider.findText(display)
        if idx >= 0:
            self._byok_provider.setCurrentIndex(idx)
        self._byok_key_input.setText(sm.get("byok_key", ""))
        self._byok_local_url.setText(sm.get("byok_local_url", ""))
        self._on_byok_provider_changed(self._byok_provider.currentIndex())

        # Sekme 2 — Çeviri
        saved_lang = sm.get("target_lang", "tr")
        idx = self._target_lang.findText(saved_lang)
        if idx >= 0:
            self._target_lang.setCurrentIndex(idx)
        else:
            # Özel kod
            self._target_lang.setCurrentIndex(self._target_lang.count() - 1)
            self._custom_lang_input.setText(saved_lang)
            self._custom_lang_input.show()

        self._set_combo(self._translator, sm.get("translator", ""))
        self._set_combo(self._font_combo, sm.get("font", "NotoSans"))
        self._set_combo(self._text_align, sm.get("text_align", "auto"))

        self._stroke_disabled.setChecked(bool(sm.get("stroke_disabled", False)))
        self._bubbles_only.setChecked(bool(sm.get("bubbles_only", False)))
        self._use_context.setChecked(bool(sm.get("use_context_chain", True)))

        mf = sm.get("min_font_size") or 0
        self._min_font_size.setValue(int(mf))

        self._custom_prompt.blockSignals(True)
        self._custom_prompt.setPlainText(sm.get("custom_prompt", ""))
        self._custom_prompt.blockSignals(False)
        self._update_prompt_counter()

        # Sekme 3 — Çıktı
        saved_out = sm.get("output_folder", "")
        if saved_out:
            self._output_folder_lbl.setText(self._short_path(saved_out))
            self._output_folder_lbl.setToolTip(saved_out)
            self._output_folder_lbl.setStyleSheet(
                f"color: {Colors.ACCENT}; "
                f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
            )

        self._set_combo(self._output_format, sm.get("output_image_format", "png"))
        self._max_concurrent.setValue(int(sm.get("max_concurrent_requests", 1)))
        self._keep_backup.setChecked(bool(sm.get("keep_original_backup", True)))
        self._keep_inpainted.setChecked(bool(sm.get("keep_inpainted_copy", False)))
        self._resume_mode.setChecked(bool(sm.get("resume_mode", True)))

        # Sekme 4 — Genel / Gelişmiş
        self._translator_list.set_items(sm.get_translator_options())
        self._language_list.set_items(sm.get_language_options())

    # ------------------------------------------------------------------
    # Kaydet / İptal / Sıfırla
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_save(self) -> None:
        """Form değerlerini SettingsManager'a yazar, sinyali yayınlar, dialog'u kapatır."""
        sm = self._sm

        # Sekme 1 — API
        sm.set_api_key(self._api_key_input.text().strip())

        provider_display = self._byok_provider.currentText()
        provider_api_map = {
            "Yok": "none", "OpenAI": "openai", "OpenRouter": "openrouter",
            "Google": "google", "Anthropic": "anthropic",
            "DeepSeek": "deepseek", "xAI": "xai", "Yerel": "local",
        }
        sm.set("byok_provider", provider_api_map.get(provider_display, "none"))
        sm.set("byok_key",       self._byok_key_input.text().strip())
        sm.set("byok_local_url", self._byok_local_url.text().strip())

        # Sekme 2 — Çeviri
        if self._target_lang.currentText() == "— Özel kod gir —":
            lang = self._custom_lang_input.text().strip() or "tr"
        else:
            lang = self._target_lang.currentText().strip()
        sm.set("target_lang",       lang)
        sm.set("translator",        self._translator.currentText().strip())
        sm.set("font",              self._font_combo.currentText())
        sm.set("text_align",        self._text_align.currentText())
        sm.set("stroke_disabled",   self._stroke_disabled.isChecked())
        sm.set("bubbles_only",      self._bubbles_only.isChecked())
        sm.set("use_context_chain", self._use_context.isChecked())
        mf = self._min_font_size.value()
        sm.set("min_font_size", mf if mf > 0 else None)
        sm.set("custom_prompt", self._custom_prompt.toPlainText()[:1000])

        # Sekme 3 — Çıktı
        sm.set("output_image_format",     self._output_format.currentText())
        sm.set("max_concurrent_requests", self._max_concurrent.value())
        sm.set("keep_original_backup",    self._keep_backup.isChecked())
        sm.set("keep_inpainted_copy",     self._keep_inpainted.isChecked())
        sm.set("resume_mode",             self._resume_mode.isChecked())

        # Sekme 4 — Gelişmiş listeler
        sm.set_translator_options(self._translator_list.get_items())
        sm.set("language_options", self._language_list.get_items())

        sm.save()
        logger.info("Ayarlar kaydedildi.")
        self.accept()

    @pyqtSlot()
    def _on_reset(self) -> None:
        reply = QMessageBox.question(
            self, "Sıfırla",
            "Tüm ayarlar varsayılan değerlere sıfırlanacak. Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._sm.reset_to_defaults()
            self._load_values()

    # ------------------------------------------------------------------
    # Slotlar — etkileşimler
    # ------------------------------------------------------------------

    @pyqtSlot(int)
    def _on_byok_provider_changed(self, index: int) -> None:
        """Seçilen BYOK sağlayıcısına göre alanları göster/gizle."""
        provider = self._byok_provider.currentText()
        is_none  = provider == "Yok"
        is_local = provider == "Yerel"

        self._byok_key_input.setEnabled(not is_none)
        self._byok_key_row_lbl.setEnabled(not is_none)
        self._byok_local_url.setVisible(is_local)
        self._byok_local_url_lbl.setVisible(is_local)

    @pyqtSlot(int)
    def _on_lang_changed(self, index: int) -> None:
        is_custom = self._target_lang.currentText() == "— Özel kod gir —"
        self._custom_lang_input.setVisible(is_custom)
        if is_custom:
            self._custom_lang_input.setFocus()

    @pyqtSlot()
    def _on_check_credits(self) -> None:
        """API anahtarı ile kredi bakiyesini asenkron kontrol eder."""
        api_key = self._api_key_input.text().strip()
        if not api_key:
            self._credits_result_lbl.setText("API anahtarı girilmedi")
            self._credits_result_lbl.setStyleSheet(
                f"color: {Colors.ERROR}; "
                f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
            )
            return

        self._credits_result_lbl.setText("Sorgulanıyor…")
        self._credits_result_lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )

        def _fetch() -> None:
            from core.api_client import ToriiAPIClient
            client = ToriiAPIClient(api_key=api_key)

            async def _run() -> float | None:
                try:
                    return await client.get_credits()
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            credits = None
            try:
                credits = loop.run_until_complete(_run())
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("Kredi kontrol hatası: %s", exc)
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

            # UI güncellemesini ana thread'de yap
            QTimer.singleShot(0, lambda: self._show_credits_result(credits))

        threading.Thread(target=_fetch, daemon=True).start()

    def _show_credits_result(self, credits: float | None) -> None:
        if credits is None:
            self._credits_result_lbl.setText("Bağlantı hatası")
            color = Colors.ERROR
        elif credits < 5:
            self._credits_result_lbl.setText(f"Kredi: {credits:.2f}  (!) Düşük")
            color = Colors.WARNING
        else:
            self._credits_result_lbl.setText(f"Kredi: {credits:.2f}")
            color = Colors.SUCCESS
        self._credits_result_lbl.setStyleSheet(
            f"color: {color}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )

    @pyqtSlot()
    def _on_prompt_changed(self) -> None:
        self._update_prompt_counter()
        text = self._custom_prompt.toPlainText()
        if len(text) > 1000:
            cursor = self._custom_prompt.textCursor()
            pos = cursor.position()
            self._custom_prompt.blockSignals(True)
            self._custom_prompt.setPlainText(text[:1000])
            self._custom_prompt.blockSignals(False)
            cursor.setPosition(min(pos, 1000))
            self._custom_prompt.setTextCursor(cursor)

    def _update_prompt_counter(self) -> None:
        count = len(self._custom_prompt.toPlainText())
        self._prompt_counter.setText(f"{min(count, 1000)} / 1000")
        color = Colors.ERROR if count > 1000 else Colors.TEXT_DISABLED
        self._prompt_counter.setStyleSheet(
            f"color: {color}; "
            f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
        )

    @pyqtSlot()
    def _browse_output_folder(self) -> None:
        start = self._sm.get("output_folder") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Çıktı Klasörü Seç", start)
        if folder:
            self._sm.set("output_folder", folder)
            self._output_folder_lbl.setText(self._short_path(folder))
            self._output_folder_lbl.setToolTip(folder)
            self._output_folder_lbl.setStyleSheet(
                f"color: {Colors.ACCENT}; "
                f"font-size: {Metrics.FONT_SIZE_SM}pt; background: transparent;"
            )

    # ------------------------------------------------------------------
    # Yardımcılar
    # ------------------------------------------------------------------

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.isEditable():
            combo.setCurrentText(value)

    @staticmethod
    def _short_path(path: str, max_len: int = 50) -> str:
        if len(path) <= max_len:
            return path
        parts = Path(path).parts
        if len(parts) > 3:
            return str(Path(*parts[:2]) / "…" / parts[-1])
        return path