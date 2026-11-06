"""
settings_manager.py - Uygulama ayarlarını yönetir.

Sorumluluğu:
- config/default_config.json'ı yüklemek ve kullanıcı ayarlarını
  işletim sistemine uygun dizine (Windows: %APPDATA%/ToriiBatch/config.json) kaydetmek.
- API anahtarı ve BYOK anahtarını Fernet (machine-specific türetilmiş anahtar) ile şifrelemek.
- Ayarlar değiştiğinde settings_changed sinyali yayınlamak.
- Translator listesi ve dil listesi gibi düzenlenebilir listeleri yönetmek.
"""

import hashlib
import json
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from PyQt6.QtCore import QObject, pyqtSignal

from core.secrets_store import load_secret, store_secret

logger = logging.getLogger(__name__)

# Proje içindeki varsayılan config
_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "default_config.json"

# Şifrelenecek hassas alanlar
_SENSITIVE_KEYS = {"api_key", "byok_key"}

# toriitranslate.com/api üzerinde kaldırılmış model kodları → güncel karşılık.
# Kullanıcının elle eklediği (eşleşmeyen) modeller korunur.
_REMOVED_TRANSLATORS: dict[str, str] = {
    "gpt-5.6-luna": "gpt-6-luna",
    "gpt-5.4": "gpt-6.1-sol",
    "gpt-6-sol": "gpt-6.1-sol",
    "claude-sonnet-4.6": "claude-sonnet-5.5",
    "claude-sonnet-5": "claude-sonnet-5.5",
    "gemini-3.6-flash": "gemini-3.8-flash",
    "deepseek": "deepseek-v4-flash",
}

_CATALOG_KEYS = ("translator_options", "language_options", "font_options")


def _get_config_path() -> Path:
    """
    İşletim sistemine göre kullanıcı config dosyası yolunu döndürür.

    - Windows : %APPDATA%\\ToriiBatch\\config.json
    - macOS   : ~/Library/Application Support/ToriiBatch/config.json
    - Linux   : ~/.config/ToriiBatch/config.json
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "ToriiBatch" / "config.json"


def _derive_machine_key() -> bytes:
    """
    Bu makineye özgü, deterministik bir Fernet anahtarı türetir.

    Makine kimliği kaynakları (öncelik sırasıyla):
    1. Windows: MachineGuid kayıt defteri değeri
    2. Linux/macOS: /etc/machine-id veya /var/lib/dbus/machine-id
    3. Fallback: platform.node() + os.getlogin() karışımı

    PBKDF2-HMAC-SHA256 ile 32 byte'a indirgenir, base64url ile kodlanır.
    Bu bir güvenlik sertifikası değil; düz metin API anahtarı saklamayı engeller.
    """
    machine_id = ""

    try:
        if platform.system() == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            machine_id, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
        else:
            for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                p = Path(candidate)
                if p.is_file():
                    machine_id = p.read_text(encoding="utf-8").strip()
                    break
    except Exception:
        pass

    if not machine_id:
        # Fallback: tekrarlanabilir ama makineye özel değil
        machine_id = platform.node() + str(uuid.getnode())
        logger.debug("Machine-ID fallback kullanılıyor.")

    salt = b"ToriiBatch_salt_v1"
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        machine_id.encode("utf-8"),
        salt,
        iterations=200_000,
        dklen=32,
    )
    # Fernet base64url-encoded 32-byte key bekler
    import base64
    return base64.urlsafe_b64encode(dk)


def _get_fernet() -> Fernet:
    """Makine anahtarıyla Fernet örneği oluşturur."""
    return Fernet(_derive_machine_key())


def _encrypt(value: str) -> str:
    """
    Değeri Fernet ile şifreler, base64 string döndürür.
    Boş string şifrelenmez (boş döner).
    """
    if not value:
        return ""
    try:
        return _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    except Exception as exc:
        logger.error("Şifreleme hatası: %s", exc)
        return ""


def _decrypt(value: str) -> str:
    """
    Fernet ile şifrelenmiş değeri çözer.
    Çözme başarısız olursa (farklı makine, bozuk veri) boş string döner.
    """
    if not value:
        return ""
    try:
        return _get_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception) as exc:
        logger.warning("Şifre çözme başarısız (farklı makine veya bozuk veri): %s", exc)
        return ""


class SettingsManager(QObject):
    """
    Uygulama ayarlarını yükler, günceller ve kaydeder.

    Varsayılan ayarlar proje içindeki default_config.json'dan okunur.
    Kullanıcı ayarları işletim sistemine uygun %APPDATA%/ToriiBatch/config.json'a yazılır.
    API anahtarı ve BYOK anahtarı Fernet ile makineye özgü anahtarla şifrelenir.

    Kullanım
    --------
    sm = SettingsManager()
    sm.load()
    key = sm.get("api_key")           # şifresi çözülmüş değer
    sm.set("target_lang", "en")       # settings_changed sinyali yayınlar
    sm.save()
    """

    settings_changed = pyqtSignal(dict)
    save_failed      = pyqtSignal(str)   # Disk yazma hatası — hata mesajı
    """Herhangi bir ayar değiştiğinde tüm ayar sözlüğü ile yayınlanır."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config: dict[str, Any] = {}
        self._config_path: Path = _get_config_path()

    # ------------------------------------------------------------------
    # Yükleme / Kaydetme
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """
        Ayarları diskten yükler.

        Kullanıcı config dosyası yoksa default_config.json'dan kopyalar.
        Hassas alanlar (api_key, byok_key) otomatik olarak şifresi çözülür.

        Dönüş
        -----
        dict
            Yüklenen ayarlar sözlüğü (hassas alanlar plaintext).
        """
        # 1) Varsayılanları yükle
        defaults = self._load_defaults()

        # 2) Kullanıcı config yoksa oluştur
        if not self._config_path.is_file():
            logger.info("Kullanıcı config bulunamadı, varsayılanlardan oluşturuluyor.")
            self._config = dict(defaults)
            self._ensure_config_dir()
            self._write_to_disk(self._config, encrypt_sensitive=False)
        else:
            try:
                with self._config_path.open("r", encoding="utf-8") as f:
                    user_cfg: dict = json.load(f)
                # Eksik anahtarları varsayılanlarla tamamla
                merged = dict(defaults)
                merged.update(user_cfg)
                self._config = self._reconcile_catalog(merged, defaults, user_cfg)
                logger.debug("Kullanıcı config yüklendi: %s", self._config_path)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Config okunamadı, varsayılanlar kullanılıyor: %s", exc)
                self._config = dict(defaults)

        # 3) Hassas alanları çöz (şifreli değerleri plaintext'e dönüştür)
        for key in _SENSITIVE_KEYS:
            cred = load_secret(key)
            if cred:
                self._config[key] = cred
                continue
            raw = self._config.get(key, "")
            if raw:
                decrypted = _decrypt(raw)
                self._config[key] = decrypted if decrypted else raw

        logger.info("Ayarlar yüklendi.")
        return dict(self._config)

    def save(self, settings: dict | None = None) -> None:
        """
        Mevcut ayarları (veya verilen sözlüğü) diske kaydeder.

        Hassas alanlar şifrelenerek yazılır.
        settings_changed sinyali yayınlanır.

        Parametreler
        ------------
        settings : dict | None
            Kaydedilecek ayarlar. None ise mevcut iç durum kullanılır.
        """
        if settings is not None:
            self._config.update(settings)

        self._ensure_config_dir()
        try:
            self._write_to_disk(self._config, encrypt_sensitive=True)
        except OSError as exc:
            # Disk yazma hatası — settings_changed emit edilmez, hata bildirilir
            self.save_failed.emit(str(exc))
            return
        self.settings_changed.emit(dict(self._config))
        logger.info("Ayarlar kaydedildi: %s", self._config_path)

    def reset_to_defaults(self) -> None:
        """
        Tüm ayarları default_config.json'daki değerlere sıfırlar.

        Değişikliği diske yazar ve settings_changed sinyali yayınlar.
        """
        self._config = self._load_defaults()
        self._ensure_config_dir()
        self._write_to_disk(self._config, encrypt_sensitive=False)
        self.settings_changed.emit(dict(self._config))
        logger.info("Ayarlar varsayılanlara sıfırlandı.")

    # ------------------------------------------------------------------
    # Tekil okuma / yazma
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """
        Belirtilen anahtarın değerini döndürür.

        Hassas alanlar plaintext olarak döner (bellekte zaten çözülmüş).

        Parametreler
        ------------
        key : str
            Config anahtarı.
        default : Any
            Anahtar bulunamazsa döndürülecek değer.

        Dönüş
        -----
        Any
        """
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        Belirtilen anahtarın değerini bellekte günceller ve
        settings_changed sinyali yayınlar.

        Kalıcı hale getirmek için save() çağrılmalıdır.

        Parametreler
        ------------
        key : str
            Config anahtarı.
        value : Any
            Yeni değer.
        """
        self._config[key] = value
        self.settings_changed.emit(dict(self._config))

    def all(self) -> dict:
        """
        Tüm ayarların bir kopyasını döndürür.

        Dönüş
        -----
        dict
        """
        return dict(self._config)

    # ------------------------------------------------------------------
    # Kolaylık metodları
    # ------------------------------------------------------------------

    def get_api_key(self) -> str:
        """Plaintext API anahtarını döndürür."""
        return self._config.get("api_key", "")

    def set_api_key(self, key: str) -> None:
        """API anahtarını günceller (bellekte plaintext)."""
        self.set("api_key", key)

    def get_translator_options(self) -> list[str]:
        """Translator model listesini döndürür."""
        return list(self._config.get("translator_options", []))

    def set_translator_options(self, options: list[str]) -> None:
        """Translator model listesini günceller."""
        self.set("translator_options", options)

    def get_language_options(self) -> list[str]:
        """Desteklenen dil kodları listesini döndürür."""
        return list(self._config.get("language_options", []))

    def get_font_options(self) -> list[str]:
        """Font adları listesini döndürür."""
        return list(self._config.get("font_options", []))

    def as_translate_kwargs(self) -> dict[str, Any]:
        """
        Mevcut ayarlardan api_client.translate_image() için
        keyword argüman sözlüğü oluşturur.

        Dönüş
        -----
        dict
        """
        return {
            "translator":             self.get("translator", "gemini-3.1-flash-lite"),
            "reasoning_effort":       self.get("reasoning_effort", "none"),
            "font":                   self.get("font", "NotoSans"),
            "text_align":             self.get("text_align", "auto"),
            "stroke_disabled":        bool(self.get("stroke_disabled", False)),
            "min_font_size":          self.get("min_font_size") or None,
            "bubbles_only":           bool(self.get("bubbles_only", False)),
            "source_langs_whitelist": self.get("source_langs_whitelist") or [],
            "custom_prompt":          self.get("custom_prompt", ""),
        }

    # ------------------------------------------------------------------
    # Dahili yardımcılar
    # ------------------------------------------------------------------

    @staticmethod
    def _reconcile_catalog(merged: dict, defaults: dict, user_cfg: dict) -> dict:
        """
        Site kataloğuyla uyumsuz model/dil/font listelerini düzeltir.

        Özel eklenen öğeler korunur. Siteden kalkan modeller güncel kodlara
        taşınır; katalogda olup listede olmayanlar eklenir.
        """
        for key in _CATALOG_KEYS:
            default_items = list(defaults.get(key) or [])
            user_items = user_cfg.get(key)
            if not isinstance(user_items, list) or not default_items:
                merged[key] = default_items
                continue

            cleaned: list[str] = []
            for item in user_items:
                if not isinstance(item, str) or not item.strip():
                    continue
                item = item.strip()
                if key == "translator_options":
                    item = _REMOVED_TRANSLATORS.get(item, item)
                if item not in cleaned:
                    cleaned.append(item)

            extras = [item for item in cleaned if item not in default_items]
            if extras:
                # Özel eklenenler başta kalır, sitedeki eksik modeller eklenir.
                merged[key] = cleaned + [
                    item for item in default_items if item not in cleaned
                ]
            else:
                # Liste yalnızca eski/güncel katalogsa site sırasını kullan.
                merged[key] = default_items

        translator = str(merged.get("translator") or "")
        translator = _REMOVED_TRANSLATORS.get(translator, translator)
        options = merged.get("translator_options") or []
        byok_local = str(merged.get("byok_provider") or "").lower() == "local"
        if not translator:
            translator = str(defaults.get("translator") or "gemini-3.1-flash-lite")
        elif translator not in options and not byok_local:
            # Kullanıcının elle girdiği özel model adını koru, listeye de ekle
            merged["translator_options"] = [translator, *options]
        merged["translator"] = translator

        effort = str(merged.get("reasoning_effort") or "none").lower()
        if effort not in ("none", "low", "medium", "high"):
            effort = "none"
        merged["reasoning_effort"] = effort

        mfs = merged.get("min_font_size")
        if mfs is not None:
            try:
                mfs_int = int(mfs)
            except (TypeError, ValueError):
                mfs_int = 0
            merged["min_font_size"] = mfs_int if 6 <= mfs_int <= 100 else None

        font = str(merged.get("font") or "")
        fonts = merged.get("font_options") or []
        if fonts and font not in fonts:
            merged["font"] = str(defaults.get("font") or fonts[0])

        lang = str(merged.get("target_lang") or "")
        langs = merged.get("language_options") or []
        if langs and lang not in langs:
            merged["target_lang"] = str(defaults.get("target_lang") or langs[0])

        return merged

    @staticmethod
    def _load_defaults() -> dict:
        """default_config.json'ı okur ve döndürür."""
        if not _DEFAULT_CONFIG_PATH.is_file():
            logger.warning("default_config.json bulunamadı: %s", _DEFAULT_CONFIG_PATH)
            return {}
        try:
            with _DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("default_config.json okunamadı: %s", exc)
            return {}

    def _ensure_config_dir(self) -> None:
        """Config klasörünü oluşturur (yoksa)."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_to_disk(self, data: dict, encrypt_sensitive: bool) -> None:
        """
        Ayarları diske yazar.

        Parametreler
        ------------
        data : dict
            Yazılacak ayarlar (hassas alanlar plaintext).
        encrypt_sensitive : bool
            True ise hassas alanlar şifrelenerek yazılır.
        """
        to_write = dict(data)
        if encrypt_sensitive:
            for key in _SENSITIVE_KEYS:
                raw = to_write.get(key, "")
                if store_secret(key, raw or ""):
                    to_write[key] = ""
                elif raw:
                    to_write[key] = _encrypt(raw)
                else:
                    to_write[key] = ""
        try:
            with self._config_path.open("w", encoding="utf-8") as f:
                json.dump(to_write, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("Config yazılamadı (%s): %s", self._config_path, exc)
            raise
