"""
main.py - ToriiBatch uygulaması giriş noktası.

Sorumluluğu:
- Python sürümünü doğrulamak.
- Rotating file handler + konsol logging altyapısını kurmak.
- QApplication oluşturmak, koyu temayı uygulamak, DPI ölçeklemeyi etkinleştirmek.
- Global exception handler ile beklenmeyen hataları yakalamak.
- MainWindow'u başlatmak ve olay döngüsünü çalıştırmak.
"""

import logging
import logging.handlers
import os
import platform
import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Python sürüm kontrolü — Qt import'larından önce yapılmalı
# ---------------------------------------------------------------------------

if sys.version_info < (3, 11):
    print(
        f"HATA: ToriiBatch Python 3.11 veya üstü gerektirir.\n"
        f"Mevcut sürüm: {sys.version}",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Log dizini yardımcısı
# ---------------------------------------------------------------------------

def _get_log_dir() -> Path:
    """
    İşletim sistemine göre log klasörü yolunu döndürür ve oluşturur.

    Windows : %APPDATA%\\ToriiBatch\\logs
    macOS   : ~/Library/Application Support/ToriiBatch/logs
    Linux   : ~/.config/ToriiBatch/logs
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))

    log_dir = base / "ToriiBatch" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


# ---------------------------------------------------------------------------
# Logging altyapısı
# ---------------------------------------------------------------------------

def _setup_logging() -> Path:
    """
    Konsol + RotatingFileHandler ile logging altyapısını kurar.

    - Konsol : INFO ve üstü, renkli değil sade format
    - Dosya  : DEBUG ve üstü, rotating (max 5 MB × 3 dosya)
    - Log dosyası: <log_dir>/app.log

    Dönüş
    -----
    Path
        Log dosyasının tam yolu.
    """
    log_dir  = _get_log_dir()
    log_file = log_dir / "app.log"

    fmt      = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, date_fmt)

    # Rotating file handler — max 5 MB × 3 yedek
    file_handler = None
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            delay=True,  # Dosyayı ilk log yazımına kadar açma (Bad fd önlemi)
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
    except OSError:
        file_handler = None  # Log dosyası yazılamazsa devam et

    # Konsol handler — stdout kapalıysa (sandbox/pythonw) güvenli atla
    console_handler = None
    try:
        if sys.stdout and sys.stdout.fileno() >= 0:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(formatter)
    except (OSError, AttributeError):
        console_handler = None

    handlers: list[logging.Handler] = []
    if console_handler:
        handlers.append(console_handler)
    if file_handler:
        handlers.append(file_handler)
    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(level=logging.DEBUG, handlers=handlers)

    # Gürültülü üçüncü taraf kütüphaneleri kıs
    for noisy in ("aiohttp", "urllib3", "PIL", "cryptography"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # asyncio'nun "Unclosed client session" uyarıları ERROR seviyesinde gelir;
    # bunlar aiohttp'ın GC sırasında ürettiği teknik uyarılar — kritik değil
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    logger = logging.getLogger(__name__)
    logger.info("Logging başlatıldı. Log dosyası: %s", log_file)
    return log_file


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

def _make_exception_hook(log_file: Path):
    """
    sys.excepthook için bir fonksiyon döndürür.

    Beklenmeyen istisnalar:
    - Log dosyasına tam traceback olarak yazılır.
    - Kullanıcıya QMessageBox ile gösterilir (Qt hazırsa).
    - Uygulama çökmez; kullanıcı mesajı kapatınca çıkış olur.
    """
    logger = logging.getLogger("excepthook")

    def _hook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt'ı normal davranışla bırak
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.critical("Beklenmeyen hata:\n%s", tb_str)

        # Qt arayüzü hazırsa kullanıcıya göster
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance()
            if app is not None:
                msg = QMessageBox()
                msg.setWindowTitle("Beklenmeyen Hata — ToriiBatch")
                msg.setIcon(QMessageBox.Icon.Critical)
                msg.setText(
                    "Beklenmeyen bir hata oluştu. Uygulama kapatılacak.\n\n"
                    f"Hata: {exc_type.__name__}: {exc_value}"
                )
                msg.setDetailedText(tb_str)
                msg.setInformativeText(
                    f"Detaylı hata günlüğü:\n{log_file}"
                )
                msg.exec()
        except Exception:
            pass

    return _hook


# ---------------------------------------------------------------------------
# Uygulama kapanış işleyicisi
# ---------------------------------------------------------------------------

def _on_about_to_quit(window) -> None:
    """
    QApplication.aboutToQuit sinyaline bağlanan slot.

    Çeviri motoru hâlâ çalışıyorsa ayarları kaydet.
    (Kullanıcı onayı closeEvent'te yönetilir; burada sadece temizlik.)
    """
    logger = logging.getLogger(__name__)
    try:
        if window._engine.is_running():
            window._engine.cancel()
            logger.info("aboutToQuit: çalışan motor iptal edildi.")
        window._sm.save()
        logger.info("Ayarlar kaydedildi.")
    except Exception as exc:
        logger.warning("Kapanış temizliği sırasında hata: %s", exc)


# ---------------------------------------------------------------------------
# Ana giriş noktası
# ---------------------------------------------------------------------------

def main() -> None:
    """ToriiBatch uygulamasını başlatır."""
    log_file = _setup_logging()
    logger   = logging.getLogger(__name__)
    logger.info("ToriiBatch başlatılıyor… (Python %s)", sys.version.split()[0])

    # Global exception hook — logging hazır olduktan hemen sonra kurulmalı
    sys.excepthook = _make_exception_hook(log_file)

    # ── Yüksek DPI desteği (QApplication oluşturulmadan önce ayarlanmalı) ──
    # PyQt6'da AA_EnableHighDpiScaling kaldırıldı; Qt6 varsayılan olarak
    # etkinleştirir. Ölçek faktörü yuvarlama politikasını ayarlıyoruz.
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except AttributeError:
        pass  # Eski PyQt6 sürümlerinde bu metod olmayabilir

    # ── QApplication ──
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("ToriiBatch")
    app.setApplicationDisplayName("ToriiBatch")
    app.setOrganizationName("ToriiBatch")
    app.setApplicationVersion("1.0.0")

    # ── Uygulama ikonu ──
    icon_path = Path(__file__).parent / "assets" / "icon.ico"
    if icon_path.is_file():
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))
        logger.debug("Uygulama ikonu yüklendi: %s", icon_path)
    else:
        logger.debug("icon.ico bulunamadı, varsayılan ikon kullanılıyor.")

    # ── Koyu tema ──
    try:
        from ui.theme import apply_theme
        apply_theme(app)
        logger.debug("Koyu tema uygulandı.")
    except Exception as exc:
        logger.warning("Tema uygulanamadı: %s", exc)

    # ── Ana pencere ──
    try:
        from ui.main_window import MainWindow
        window = MainWindow()
    except Exception as exc:
        logger.critical("MainWindow oluşturulamadı: %s", exc, exc_info=True)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None,
            "Başlatma Hatası",
            f"Uygulama penceresi açılamadı:\n\n{exc}\n\n"
            f"Log dosyası: {log_file}",
        )
        sys.exit(1)

    # ── aboutToQuit — son temizlik ──
    app.aboutToQuit.connect(lambda: _on_about_to_quit(window))

    window.show()
    logger.info("Ana pencere gösterildi.")

    exit_code = app.exec()
    logger.info("Uygulama kapandı (çıkış kodu: %d).", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()