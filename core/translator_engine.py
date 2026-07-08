"""
translator_engine.py - Toplu çeviri iş motoru.

Sorumluluğu:
- Tek bir QThread içinde asyncio event loop çalıştırarak UI'yı bloklamadan
  toplu çeviriyi yönetmek.
- Bölüm içi context chain'i otomatik takip etmek (her bölüm için ayrı zincir).
- İlerleme, hata ve tamamlanma sinyallerini thread-safe biçimde UI'a iletmek.
- Rate limit'e (saniyede 1 istek) uymak.
- Duraklat / Devam Et / İptal Et kontrolünü desteklemek.
"""

import asyncio
import base64
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QObject,
    QThread,
    pyqtSignal,
    pyqtSlot,
)

from core.api_client import ToriiAPIClient
from core.file_scanner import ChapterInfo, build_output_path

logger = logging.getLogger(__name__)

# Üst üste bu kadar sayfa hata alırsa bölüm atlanır
_MAX_CONSECUTIVE_ERRORS: int = 3


# ---------------------------------------------------------------------------
# Durum sabitleri
# ---------------------------------------------------------------------------

class PageStatus(Enum):
    """Tek bir sayfanın çeviri durumu."""
    PENDING  = auto()
    RUNNING  = auto()
    DONE     = auto()
    FAILED   = auto()
    SKIPPED  = auto()


class ChapterStatus(Enum):
    """Bir bölümün genel durumu."""
    PENDING   = auto()
    RUNNING   = auto()
    DONE      = auto()
    FAILED    = auto()
    CANCELLED = auto()


# ---------------------------------------------------------------------------
# Veri sınıfları
# ---------------------------------------------------------------------------

@dataclass
class PageResult:
    """Tek bir sayfa çevirisinin sonucunu tutar."""

    chapter_name: str
    page_index: int
    source_path: Path
    output_path: Path
    status: PageStatus = PageStatus.PENDING
    error: str | None = None
    credits_remaining: float | None = None
    elapsed_seconds: float = 0.0
    next_context: str = "None"


@dataclass
class ChapterResult:
    """Bir bölümün toplam çeviri sonucunu tutar."""

    chapter_name: str
    total_pages: int
    completed: int = 0
    failed: int = 0
    status: ChapterStatus = ChapterStatus.PENDING
    page_results: list[PageResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Çeviri işini koşturan QThread alt sınıfı
# ---------------------------------------------------------------------------

class _EngineThread(QThread):
    """
    Asyncio event loop'unu ayrı bir QThread içinde çalıştıran yardımcı sınıf.

    TranslatorEngine tarafından oluşturulur; doğrudan kullanılmaz.
    Tüm sinyaller bu thread'den Qt sinyal/slot mekanizması (queued connection)
    aracılığıyla GUI thread'ine iletilir — thread güvenliği Qt tarafından
    sağlanır.
    """

    # Sinyaller — GUI thread'inde bağlantı kurulur
    chapter_started   = pyqtSignal(str)
    chapter_progress  = pyqtSignal(str, int, int)   # (ad, tamamlanan, toplam)
    chapter_finished  = pyqtSignal(str, bool)        # (ad, başarılı_mı)
    image_translated  = pyqtSignal(str, str, str)    # (bölüm, kaynak, hedef)
    log_message       = pyqtSignal(str, str)          # (seviye, mesaj)
    credits_updated   = pyqtSignal(float)
    all_finished      = pyqtSignal()
    error_occurred    = pyqtSignal(str, str)          # (bölüm, hata)

    def __init__(
        self,
        chapters: list[ChapterInfo],
        settings: dict,
        output_root: str,
        client: ToriiAPIClient,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._chapters    = chapters
        self._settings    = settings
        self._output_root = output_root
        self._client      = client

        # Kontrol olayları
        self._cancel_event = threading.Event()
        self._pause_event  = threading.Event()   # set = devam et, clear = durakla
        self._pause_event.set()  # başlangıçta duraklamamış

    # ------------------------------------------------------------------
    # Kontrol metodları (GUI thread'inden çağrılır)
    # ------------------------------------------------------------------

    def request_cancel(self) -> None:
        """İptal isteği gönderir."""
        self._cancel_event.set()
        self._pause_event.set()  # duraklıyorsa bloku aç

    def request_pause(self) -> None:
        """Mevcut sayfadan sonra duraklatır."""
        self._pause_event.clear()

    def request_resume(self) -> None:
        """Duraklatılmış işlemi devam ettirir."""
        self._pause_event.set()

    # ------------------------------------------------------------------
    # QThread giriş noktası
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Yeni bir asyncio event loop oluşturur ve toplu çeviriyi çalıştırır."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_batch())
        except Exception as exc:
            logger.exception("EngineThread beklenmedik hata: %s", exc)
            self.error_occurred.emit("", str(exc))
        finally:
            # Session'ı kapat (TCP bağlantılarını temizle)
            if not loop.is_closed():
                try:
                    loop.run_until_complete(self._client.close())
                except Exception:
                    pass
            loop.close()
            self.all_finished.emit()

    # ------------------------------------------------------------------
    # Toplu çeviri asenkron giriş noktası
    # ------------------------------------------------------------------

    async def _run_batch(self) -> None:
        """
        Bölüm listesini sırayla işler.

        Bölümler arası paralellik yoktur — API rate limit ve context chain
        tutarlılığı bölüm içi sıralamayı zorunlu kılar.
        """
        settings  = self._settings
        source_root = settings.get("source_folder", "")

        self.log_message.emit(
            "info",
            f"Çeviri başlıyor: {len(self._chapters)} bölüm.",
        )

        for chapter in self._chapters:
            if self._cancel_event.is_set():
                self.log_message.emit("warning", "İptal edildi, işlem durduruluyor.")
                break

            await self._process_chapter(chapter, source_root)

    # ------------------------------------------------------------------
    # Bölüm işleme
    # ------------------------------------------------------------------

    async def _process_chapter(
        self,
        chapter: ChapterInfo,
        source_root: str,
    ) -> None:
        """
        Tek bir bölümün tüm sayfalarını sırayla çevirir.

        Parametreler
        ------------
        chapter : ChapterInfo
            Çevrilecek bölüm.
        source_root : str
            Kaynak kök klasör (çıktı yolu hesabı için).
        """
        settings = self._settings
        use_context: bool = bool(settings.get("use_context_chain", True))
        output_format: str = settings.get("output_image_format", "png")
        save_inpainted: bool = bool(settings.get("keep_inpainted_copy", False))
        target_lang: str = settings.get("target_lang", "tr")

        # Çıktı klasörü
        output_dir = Path(build_output_path(chapter, source_root, self._output_root))
        output_dir.mkdir(parents=True, exist_ok=True)

        total = chapter.page_count
        completed = 0
        failed = 0
        consecutive_errors = 0
        context = "None"

        self.chapter_started.emit(chapter.name)
        self.log_message.emit(
            "info",
            f"[{chapter.name}] Başladı — {total} sayfa.",
        )

        for idx, image_path in enumerate(chapter.image_paths):
            # --- İptal kontrolü ---
            if self._cancel_event.is_set():
                self.log_message.emit(
                    "warning",
                    f"[{chapter.name}] İptal: kalan sayfalar atlandı.",
                )
                break

            # --- Duraklat kontrolü (asyncio ile thread güvenli bekleme) ---
            await self._wait_if_paused()

            if self._cancel_event.is_set():
                break

            source_path = Path(image_path)
            output_path = output_dir / f"{source_path.stem}.{output_format}"

            # --- API isteği ---
            result = await self._translate_one_page(
                chapter_name=chapter.name,
                page_index=idx,
                total_pages=total,
                source_path=source_path,
                output_path=output_path,
                target_lang=target_lang,
                context=context if use_context else "None",
                output_format=output_format,
                save_inpainted=save_inpainted,
            )

            if result.status == PageStatus.DONE:
                completed += 1
                consecutive_errors = 0
                # Sonraki sayfa için context'i güncelle
                if use_context:
                    context = result.next_context
                # Sinyal: görsel çevrildi
                self.image_translated.emit(
                    chapter.name,
                    str(result.source_path),
                    str(result.output_path),
                )
                if result.credits_remaining is not None:
                    self.credits_updated.emit(result.credits_remaining)
            else:
                failed += 1
                consecutive_errors += 1
                context = "None"  # hata sonrası context sıfırla
                self.error_occurred.emit(
                    chapter.name,
                    result.error or "Bilinmeyen hata",
                )

                # Üst üste 3 hata → bölümü atla
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    self.log_message.emit(
                        "error",
                        f"[{chapter.name}] {_MAX_CONSECUTIVE_ERRORS} ardışık hata, "
                        f"bölüm atlanıyor.",
                    )
                    break

            # İlerleme sinyali
            self.chapter_progress.emit(chapter.name, completed + failed, total)

        # Bölüm sonucu
        success = failed == 0 or (completed > 0 and consecutive_errors < _MAX_CONSECUTIVE_ERRORS)
        self.chapter_finished.emit(chapter.name, success)
        level = "info" if success else "warning"
        self.log_message.emit(
            level,
            f"[{chapter.name}] Bitti — {completed} başarılı, {failed} hatalı.",
        )

    # ------------------------------------------------------------------
    # Tek sayfa çevirisi
    # ------------------------------------------------------------------

    async def _translate_one_page(
        self,
        chapter_name: str,
        page_index: int,
        total_pages: int,
        source_path: Path,
        output_path: Path,
        target_lang: str,
        context: str,
        output_format: str,
        save_inpainted: bool,
    ) -> PageResult:
        """
        Tek bir sayfayı API üzerinden çevirir ve diske kaydeder.

        Diske yazma `asyncio.to_thread` ile ayrı bir thread'de yapılarak
        event loop bloklanmaz.

        Parametreler
        ------------
        chapter_name : str
            Bölüm adı (log ve sinyal için).
        page_index : int
            0-tabanlı sayfa indeksi.
        total_pages : int
            Bölümdeki toplam sayfa sayısı.
        source_path : Path
            Kaynak görsel dosyası.
        output_path : Path
            Çevrilmiş görselin hedef yolu.
        target_lang : str
            Hedef dil kodu.
        context : str
            Önceki sayfadan gelen context zinciri.
        output_format : str
            Çıktı uzantısı ("png", "jpg", "webp").
        save_inpainted : bool
            True ise inpainted versiyonu da kaydet.

        Dönüş
        -----
        PageResult
        """
        import time

        result = PageResult(
            chapter_name=chapter_name,
            page_index=page_index,
            source_path=source_path,
            output_path=output_path,
        )

        self.log_message.emit(
            "info",
            f"[{chapter_name}] Sayfa {page_index + 1}/{total_pages}: "
            f"{source_path.name} çevriliyor…",
        )

        t_start = time.monotonic()

        # Çeviri parametrelerini settings'den derle
        settings = self._settings

        # translator zorunlu parametre — varsayılan olarak ilk seçenek
        translator: str = (
            settings.get("translator")
            or "gemini-3.1-flash-lite"
        )
        font: str = settings.get("font") or "NotoSans"
        text_align: str = settings.get("text_align") or "auto"
        stroke_disabled: bool = bool(settings.get("stroke_disabled", False))
        min_font_size: int | None = settings.get("min_font_size") or None
        bubbles_only: bool = bool(settings.get("bubbles_only", False))
        custom_prompt: str = settings.get("custom_prompt") or ""

        response = await self._client.translate_image(
            image_path=str(source_path),
            target_lang=target_lang,
            translator=translator,
            font=font,
            text_align=text_align,
            stroke_disabled=stroke_disabled,
            min_font_size=min_font_size,
            bubbles_only=bubbles_only,
            custom_prompt=custom_prompt,
            context=context,
        )

        result.elapsed_seconds = time.monotonic() - t_start

        if not response.get("success"):
            result.status = PageStatus.FAILED
            result.error = response.get("error", "API isteği başarısız")
            self.log_message.emit(
                "error",
                f"[{chapter_name}] Sayfa {page_index + 1} başarısız: {result.error}",
            )
            return result

        # Görsel decode + diske yaz (thread'de — event loop bloklanmasın)
        image_b64: str | None = response.get("image_b64")
        save_ok = await asyncio.to_thread(
            _write_image, image_b64, output_path
        )

        if not save_ok:
            result.status = PageStatus.FAILED
            result.error = "Görsel diske kaydedilemedi."
            self.log_message.emit(
                "error",
                f"[{chapter_name}] Sayfa {page_index + 1} kaydedilemedi.",
            )
            return result

        # İnpainted versiyonu kaydet (isteğe bağlı)
        if save_inpainted:
            inpainted_b64: str | None = response.get("inpainted_b64")
            if inpainted_b64:
                inpainted_path = output_path.with_stem(
                    output_path.stem + "_inpainted"
                )
                await asyncio.to_thread(_write_image, inpainted_b64, inpainted_path)

        # Başarı
        result.status = PageStatus.DONE
        result.credits_remaining = response.get("credits_remaining")
        result.next_context = response.get("context", "None")

        self.log_message.emit(
            "info",
            f"[{chapter_name}] Sayfa {page_index + 1} tamamlandı "
            f"({result.elapsed_seconds:.1f}s).",
        )
        return result

    # ------------------------------------------------------------------
    # Duraklat yardımcısı
    # ------------------------------------------------------------------

    async def _wait_if_paused(self) -> None:
        """
        Duraklat isteği varsa (pause_event clear ise) devam et sinyali
        gelene kadar asyncio sleep döngüsüyle bekler.

        Event loop meşgul edilmez; 100 ms aralıklarla kontrol edilir.
        """
        while not self._pause_event.is_set():
            await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Dosya yazma yardımcısı (asyncio.to_thread ile çağrılır)
# ---------------------------------------------------------------------------

def _write_image(b64_data: str | None, output_path: Path) -> bool:
    """
    Base64 kodlu görsel verisini diske kaydeder.

    ``data:image/...;base64,`` ön ekini otomatik kaldırır.

    Parametreler
    ------------
    b64_data : str | None
        Base64 verisi veya data URI.
    output_path : Path
        Hedef dosya yolu.

    Dönüş
    -----
    bool
        Başarılıysa True.
    """
    if not b64_data:
        return False
    try:
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        image_bytes = base64.b64decode(b64_data)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return True
    except Exception as exc:
        logger.error("Görsel kaydedilemedi (%s): %s", output_path, exc)
        return False


# ---------------------------------------------------------------------------
# Ana çeviri motoru (UI tarafından kullanılan sınıf)
# ---------------------------------------------------------------------------

class TranslatorEngine(QObject):
    """
    Toplu çeviri işini yöneten ve UI ile sinyaller üzerinden iletişim kuran motor.

    Dahili olarak bir ``_EngineThread`` oluşturur ve tüm sinyallerini
    dışarıya yönlendirir. UI katmanı yalnızca bu sınıfla muhatap olur.

    Kullanım
    --------
    engine = TranslatorEngine(settings_manager, parent=window)
    engine.chapter_started.connect(my_slot)
    engine.start_batch(chapters, settings, output_root)
    # ...
    engine.pause()
    engine.resume()
    engine.cancel()

    Geriye dönük uyumluluk için eski sinyal adları da korunmuştur:
    page_done, chapter_done, all_done, progress_updated.
    """

    # --- Birincil sinyaller (istek tarafından belirtilen) ---
    chapter_started  = pyqtSignal(str)
    chapter_progress = pyqtSignal(str, int, int)   # (ad, tamamlanan, toplam)
    chapter_finished = pyqtSignal(str, bool)        # (ad, başarılı_mı)
    image_translated = pyqtSignal(str, str, str)    # (bölüm, kaynak, hedef)
    log_message      = pyqtSignal(str, str)          # (seviye, mesaj)
    credits_updated  = pyqtSignal(float)
    all_finished     = pyqtSignal()
    error_occurred   = pyqtSignal(str, str)          # (bölüm, hata)

    # --- Geriye dönük uyumluluk sinyalleri (main_window.py bağlantıları için) ---
    chapter_done     = pyqtSignal(str, bool)         # chapter_finished ile aynı
    all_done         = pyqtSignal(int, int)           # all_finished tetiklenince emit edilir
    progress_updated = pyqtSignal(int, int)           # chapter_progress'ten türetilir

    def __init__(
        self,
        settings_manager,
        parent: QObject | None = None,
    ) -> None:
        """
        Parametreler
        ------------
        settings_manager : SettingsManager
            Ayarları okumak için kullanılan örnek.
        parent : QObject | None
            Opsiyonel Qt ebeveyn nesnesi.
        """
        super().__init__(parent)
        self._sm = settings_manager
        self._thread: _EngineThread | None = None

        # İlerleme sayaçları (geriye dönük uyumluluk için)
        self._total_pages: int = 0
        self._done_pages: int = 0
        self._failed_pages: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_batch(
        self,
        chapters: list[ChapterInfo],
        settings: dict,
        output_root: str,
    ) -> None:
        """
        Bölüm listesini kuyruğa alarak çeviriyi başlatır.

        Eğer önceki bir çeviri devam ediyorsa önce iptal edilir.

        Parametreler
        ------------
        chapters : list[ChapterInfo]
            scan_root_folder() ile elde edilmiş bölüm listesi.
        settings : dict
            Çeviri parametrelerini içeren ayar sözlüğü
            (settings_manager.all() veya as_translate_kwargs() çıktısı gibi).
        output_root : str
            Çevrilmiş görsellerin yazılacağı kök klasör.
        """
        if not chapters:
            self.log_message.emit("warning", "Çevrilecek bölüm bulunamadı.")
            self.all_finished.emit()
            self.all_done.emit(0, 0)
            return

        self.cancel()  # varsa önceki işi temizle

        self._total_pages = sum(c.page_count for c in chapters)
        self._done_pages = 0
        self._failed_pages = 0

        client = self._build_client()

        self._thread = _EngineThread(
            chapters=chapters,
            settings=settings,
            output_root=output_root,
            client=client,
            parent=self,
        )

        # Sinyal yönlendirme
        self._thread.chapter_started.connect(self.chapter_started)
        self._thread.chapter_progress.connect(self._on_chapter_progress)
        self._thread.chapter_finished.connect(self._on_chapter_finished)
        self._thread.image_translated.connect(self.image_translated)
        self._thread.log_message.connect(self.log_message)
        self._thread.credits_updated.connect(self.credits_updated)
        self._thread.error_occurred.connect(self.error_occurred)
        self._thread.all_finished.connect(self._on_all_finished)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def start(self, chapters: list[ChapterInfo]) -> None:
        """
        Geriye dönük uyumluluk için settings_manager'dan ayarları okuyarak
        start_batch() çağırır.

        Parametreler
        ------------
        chapters : list[ChapterInfo]
        """
        settings = dict(self._sm.all()) if hasattr(self._sm, "all") else {}
        settings["source_folder"] = self._sm.get("source_folder", "")
        output_root: str = self._sm.get("output_folder", "")
        self.start_batch(chapters, settings, output_root)

    def pause(self) -> None:
        """
        Mevcut sayfadan sonra işlemi duraklatır.

        Duraklama: mevcut HTTP isteği tamamlanır, ardından devam sinyali
        gelene kadar beklenir.
        """
        if self._thread and self._thread.isRunning():
            self._thread.request_pause()
            self.log_message.emit("info", "Duraklatıldı.")

    def resume(self) -> None:
        """Duraklatılmış işlemi devam ettirir."""
        if self._thread and self._thread.isRunning():
            self._thread.request_resume()
            self.log_message.emit("info", "Devam ediliyor…")

    def cancel(self) -> None:
        """
        İşlemi iptal eder ve thread'in bitmesini bekler (en fazla 5 sn).
        """
        if self._thread and self._thread.isRunning():
            self._thread.request_cancel()
            self._thread.wait(5000)

    def stop(self) -> None:
        """Geriye dönük uyumluluk: cancel() ile aynı işlevi görür."""
        self.cancel()

    def is_running(self) -> bool:
        """
        Çeviri devam ediyorsa True döndürür.

        Dönüş
        -----
        bool
        """
        return self._thread is not None and self._thread.isRunning()

    # ------------------------------------------------------------------
    # Dahili slotlar
    # ------------------------------------------------------------------

    @pyqtSlot(str, int, int)
    def _on_chapter_progress(self, chapter_name: str, done: int, total: int) -> None:
        """chapter_progress sinyalini iletir; genel ilerlemeyi de günceller."""
        self.chapter_progress.emit(chapter_name, done, total)
        # Her sinyal 1 sayfa işlendiğini temsil eder (başarılı veya hatalı)
        if self._done_pages < self._total_pages:
            self._done_pages += 1
        self.progress_updated.emit(self._done_pages, self._total_pages)

    @pyqtSlot(str, bool)
    def _on_chapter_finished(self, chapter_name: str, success: bool) -> None:
        """chapter_finished ve geriye dönük chapter_done sinyallerini yayınlar."""
        self.chapter_finished.emit(chapter_name, success)
        self.chapter_done.emit(chapter_name, success)

    @pyqtSlot()
    def _on_all_finished(self) -> None:
        """all_finished ve geriye dönük all_done sinyallerini yayınlar."""
        self.all_finished.emit()
        # _failed_pages UI katmanından error_occurred sinyali üzerinden izlenir;
        # burada sıfır olabilir — all_done için mevcut sayaçları kullan
        failed = self._failed_pages
        success = max(0, self._done_pages - failed)
        self.all_done.emit(success, failed)
        self.log_message.emit(
            "info",
            "Tüm işler tamamlandı.",
        )
        self._thread = None

    # ------------------------------------------------------------------
    # Yardımcılar
    # ------------------------------------------------------------------

    def _build_client(self) -> ToriiAPIClient:
        """
        Mevcut ayarlardan ToriiAPIClient örneği oluşturur.

        Dönüş
        -----
        ToriiAPIClient
        """
        api_key = self._sm.get_api_key()
        provider = self._sm.get("byok_provider", "none")

        byok_provider: str | None = None
        byok_key: str | None = None
        byok_local_url: str | None = None

        if provider and provider != "none":
            byok_provider = provider
            byok_key = self._sm.get("byok_key") or None
            byok_local_url = self._sm.get("byok_local_url") or None

        return ToriiAPIClient(
            api_key=api_key,
            byok_provider=byok_provider,
            byok_key=byok_key,
            byok_local_url=byok_local_url,
        )