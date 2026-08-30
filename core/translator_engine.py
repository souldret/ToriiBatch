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
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

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
    error_occurred    = pyqtSignal(str, str, str)     # (bölüm, hata, kaynak_yol)
    eta_updated       = pyqtSignal(float)             # kalan saniye
    fatal_error       = pyqtSignal(str)

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
        self._page_times: list[float] = []
        self._pages_left = 0
        self._auth_failed = False

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
            self.error_occurred.emit("", str(exc), "")
        finally:
            # Session'ı kapat (TCP bağlantılarını temizle)
            try:
                if not loop.is_closed():
                    try:
                        loop.run_until_complete(
                            asyncio.wait_for(self._client.close(), timeout=5.0)
                        )
                    except Exception:
                        pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
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

        concurrent = max(1, int(settings.get("max_concurrent_requests") or 1))
        self._page_times: list[float] = []
        self._pages_left = sum(c.page_count for c in self._chapters)
        self._auth_failed = False

        if concurrent <= 1:
            for chapter in self._chapters:
                if self._cancel_event.is_set() or self._auth_failed:
                    self.log_message.emit("warning", "İptal edildi, işlem durduruluyor.")
                    break
                await self._process_chapter(chapter, source_root)
        else:
            sem = asyncio.Semaphore(concurrent)

            async def _run_one(ch: ChapterInfo) -> None:
                async with sem:
                    if self._cancel_event.is_set() or self._auth_failed:
                        return
                    await self._process_chapter(ch, source_root)

            await asyncio.gather(*[_run_one(ch) for ch in self._chapters])

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
        keep_backup: bool = bool(settings.get("keep_original_backup", False))
        target_lang: str = settings.get("target_lang", "tr")

        # Çıktı klasörü
        output_dir = Path(build_output_path(chapter, source_root, self._output_root))
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error_occurred.emit(chapter.name, f"Çıktı klasörü oluşturulamadı: {exc}", "")
            self.chapter_finished.emit(chapter.name, False)
            return

        total = chapter.page_count
        completed = 0
        failed = 0
        consecutive_errors = 0
        context_file = output_dir / ".torii_context.json"
        context = "None"
        if use_context:
            context = _load_context(context_file)

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
                for leftover in output_dir.glob("*.tmp"):
                    try:
                        leftover.unlink()
                    except OSError:
                        pass
                break

            # --- Duraklat kontrolü (asyncio ile thread güvenli bekleme) ---
            await self._wait_if_paused()

            if self._cancel_event.is_set():
                for leftover in output_dir.glob("*.tmp"):
                    try:
                        leftover.unlink()
                    except OSError:
                        pass
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
                keep_backup=keep_backup,
            )

            if result.status == PageStatus.DONE:
                completed += 1
                consecutive_errors = 0
                if use_context:
                    context = result.next_context
                    _save_context(context_file, context)
                self.image_translated.emit(
                    chapter.name,
                    str(result.source_path),
                    str(result.output_path),
                )
                if result.credits_remaining is not None:
                    self.credits_updated.emit(result.credits_remaining)
                self._update_eta(result.elapsed_seconds)
            else:
                failed += 1
                consecutive_errors += 1
                context = "None"
                err = result.error or "Bilinmeyen hata"
                self.error_occurred.emit(chapter.name, err, str(result.source_path))
                if "401" in err or "yetkisiz" in err.lower():
                    self._auth_failed = True
                    self._cancel_event.set()
                    self.fatal_error.emit(err)
                    break
                if "kaydedilemedi" in err.lower() or "diske" in err.lower():
                    self.log_message.emit("error", f"[{chapter.name}] Disk yazma hatası: {err}")

                self._update_eta(result.elapsed_seconds)
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
        keep_backup: bool = False,
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

        if keep_backup:
            backup_path = output_path.with_stem(output_path.stem + "_original")
            backup_path = backup_path.with_suffix(source_path.suffix)
            try:
                await asyncio.to_thread(_copy_file, source_path, backup_path)
            except Exception as exc:
                logger.warning("Orijinal yedek kopyalanamadı (%s): %s", source_path, exc)

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

    def _update_eta(self, elapsed: float) -> None:
        if elapsed > 0:
            self._page_times.append(elapsed)
        self._pages_left = max(0, self._pages_left - 1)
        if self._page_times:
            avg = sum(self._page_times[-20:]) / len(self._page_times[-20:])
            self.eta_updated.emit(avg * self._pages_left)

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

def _load_context(path: Path) -> str:
    import json
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            ctx = data.get("context")
            if isinstance(ctx, str) and ctx:
                return ctx
    except Exception:
        pass
    return "None"


def _save_context(path: Path, context: str) -> None:
    import json
    try:
        path.write_text(json.dumps({"context": context}, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("Context kaydedilemedi: %s", exc)


def _copy_file(src: Path, dst: Path) -> None:
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


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
        tmp = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp.write_bytes(image_bytes)
        import os
        os.replace(tmp, output_path)
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
    error_occurred   = pyqtSignal(str, str, str)     # (bölüm, hata, kaynak_yol)
    eta_updated      = pyqtSignal(float)
    fatal_error      = pyqtSignal(str)

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

        # Her start_batch çağrısı yeni bir nesil üretir; eski thread sinyalleri yok sayılır
        self._generation: int = 0
        self._active_generation: int = 0

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

        self._generation += 1
        generation = self._generation
        self._active_generation = generation

        self._total_pages = sum(c.page_count for c in chapters)
        self._done_pages = 0
        self._failed_pages = 0

        client = self._build_client()

        thread = _EngineThread(
            chapters=chapters,
            settings=settings,
            output_root=output_root,
            client=client,
            parent=self,
        )
        self._thread = thread

        # Sinyal yönlendirme — generation kapanışı ile eski thread sızıntısı engellenir
        thread.chapter_started.connect(self.chapter_started)
        thread.chapter_progress.connect(self._on_chapter_progress)
        thread.chapter_finished.connect(self._on_chapter_finished)
        thread.image_translated.connect(self.image_translated)
        thread.log_message.connect(self.log_message)
        thread.credits_updated.connect(self.credits_updated)
        thread.error_occurred.connect(self._on_error_occurred)
        thread.eta_updated.connect(self.eta_updated)
        thread.fatal_error.connect(self.fatal_error)
        thread.all_finished.connect(
            lambda g=generation: self._on_all_finished(g)
        )

        thread.start()

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
        İşlemi iptal eder, sinyalleri keser ve thread'in bitmesini bekler
        (en fazla 8 sn).
        """
        thread = self._thread
        if thread is None:
            return

        # Bu iptal sonrası eski sinyaller UI'ya sızmasın
        self._active_generation = -1
        self._disconnect_thread(thread)

        if thread.isRunning():
            thread.request_cancel()
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            deadline = time.monotonic() + 8.0
            while thread.isRunning() and time.monotonic() < deadline:
                if app is not None:
                    app.processEvents()
                thread.wait(50)
            if thread.isRunning():
                logger.warning(
                    "Engine thread 8 sn içinde bitmedi; sinyaller kesildi, "
                    "thread arka planda kapanacak."
                )
        if self._thread is thread:
            self._thread = None

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

    def _is_active_sender(self) -> bool:
        """Sinyal aktif thread'den geliyorsa True."""
        if self._thread is None or self._active_generation < 0:
            return False
        sender = self.sender()
        return sender is None or sender is self._thread

    @staticmethod
    def _disconnect_thread(thread: _EngineThread) -> None:
        """Thread sinyallerini güvenli şekilde keser."""
        for signal in (
            thread.chapter_started,
            thread.chapter_progress,
            thread.chapter_finished,
            thread.image_translated,
            thread.log_message,
            thread.credits_updated,
            thread.error_occurred,
            thread.eta_updated,
            thread.fatal_error,
            thread.all_finished,
        ):
            try:
                signal.disconnect()
            except TypeError:
                pass

    @pyqtSlot(str, int, int)
    def _on_chapter_progress(self, chapter_name: str, done: int, total: int) -> None:
        """chapter_progress sinyalini iletir; genel ilerlemeyi de günceller."""
        if not self._is_active_sender():
            return
        self.chapter_progress.emit(chapter_name, done, total)
        # Her sinyal 1 sayfa işlendiğini temsil eder (başarılı veya hatalı)
        if self._done_pages < self._total_pages:
            self._done_pages += 1
        self.progress_updated.emit(self._done_pages, self._total_pages)

    @pyqtSlot(str, bool)
    def _on_chapter_finished(self, chapter_name: str, success: bool) -> None:
        """chapter_finished ve geriye dönük chapter_done sinyallerini yayınlar."""
        if not self._is_active_sender():
            return
        self.chapter_finished.emit(chapter_name, success)
        self.chapter_done.emit(chapter_name, success)

    @pyqtSlot(str, str, str)
    def _on_error_occurred(self, chapter_name: str, error: str, source_path: str) -> None:
        """Hata sayacını günceller ve error_occurred sinyalini dışarı iletir."""
        if not self._is_active_sender():
            return
        self._failed_pages += 1
        self.error_occurred.emit(chapter_name, error, source_path)

    def _on_all_finished(self, generation: int) -> None:
        """all_finished ve geriye dönük all_done sinyallerini yayınlar."""
        # Eski/iptal edilmiş batch'in gecikmiş bitiş sinyali
        if generation != self._active_generation:
            return

        finished_thread = self._thread
        if finished_thread is not None:
            try:
                finished_thread.deleteLater()
            except RuntimeError:
                pass
        self.all_finished.emit()
        failed = self._failed_pages
        success = max(0, self._done_pages - failed)
        self.all_done.emit(success, failed)
        self.log_message.emit(
            "info",
            "Tüm işler tamamlandı.",
        )
        if self._thread is finished_thread:
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