"""
api_client.py - Torii Translate API istemcisi.

Sorumluluğu:
- POST /api/v2/upload ile görsel çevirmek.
- POST /api/v2/ocr ile OCR almak.
- POST /api/inpaint ile maskelenmiş bölgeyi temizlemek.
- POST /api/typeset ile metin kutularını basmak.
- POST /api/colorize ile siyah-beyaz görseli renklendirmek.
- GET /api/credits ve GET /api/usage ile bakiyeyi ve kullanımı okumak.
- GET /api/keys ile hesaptaki API anahtarlarını listelemek.
- POST /api/credits/share ile kredi paylaşmak.
- 429/503 hatalarında exponential backoff ile otomatik yeniden deneme.
- BYOK alanlarını (x-byok-*, x-byok-local-url, x-byok-fallback) multipart
    form gövdesinde göndermek.
- İstemci genelinde saniyede 1 istek sınırına uymak.

Referans: https://toriitranslate.com/api
"""

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.toriitranslate.com"

# Retry ayarları
_MAX_RETRY_RATE = 5        # 429/503 için max deneme
_MAX_RETRY_NETWORK = 3     # Network hatası için max deneme
_BACKOFF_BASE = 1.0        # Başlangıç bekleme süresi (saniye)
_BACKOFF_MULTIPLIER = 2.0
_JITTER_MAX = 0.5          # Rastgele ek bekleme üst sınırı (saniye)
_MAX_RETRY_AFTER = 60.0    # Retry-After başlığına uyulacak üst sınır (saniye)

# Rate limit: saniyede 1 istek
_MIN_REQUEST_INTERVAL = 1.0  # saniye

# API limitleri (toriitranslate.com/api)
MAX_FILE_BYTES = 50 * 1024 * 1024   # 50 MB
MAX_CONTEXT_CHARS = 10_000          # API yalnızca son 10.000 karakteri tutar
MAX_CUSTOM_PROMPT_CHARS = 1_000
MAX_COLORIZE_PROMPT_CHARS = 2_100
MIN_FONT_SIZE_RANGE = (6, 100)
DEFAULT_TRANSLATOR = "gemini-3.1-flash-lite"
DEFAULT_COLORIZE_MODEL = "muse"
COLORIZE_MODELS: tuple[str, ...] = ("local", "muse", "gpt", "grok", "gemini")
# Colorize BYOK, çeviri BYOK'undan ayrı: model → form alanı.
COLORIZE_BYOK_FIELDS: dict[str, str] = {
    "muse": "x-byok-openrouter",
    "gpt": "x-byok-openrouter",
    "grok": "x-byok-xai",
    "gemini": "x-byok-google",
}
REASONING_EFFORTS: tuple[str, ...] = ("none", "low", "medium", "high")
TEXT_ALIGNS: tuple[str, ...] = ("auto", "left", "center", "right")
BYOK_PROVIDERS: tuple[str, ...] = (
    "openai", "openrouter", "google", "anthropic", "deepseek", "xai", "local",
)
USAGE_SCOPES: tuple[str, ...] = ("key", "api", "account")

# UI'da gösterilen font adlarını API'nin beklediği kısa koda çevirir.
# (toriitranslate.com/api referans alınmıştır; API "NotoSans" gibi görünen
# adları değil "noto" gibi kısa kodları kabul eder.)
_FONT_NAME_TO_CODE: dict[str, str] = {
    "notosans": "noto",
    "wildwords": "wildwords",
    "badcomic": "badcomic",
    "mashanzheng": "mashanzheng",
    "komikajam": "komika",
    "bangers": "bangers",
    "edo": "edo",
    "ridibatang": "ridi",
    "bushidoo": "bushidoo",
    "hayah": "hayah",
    "itim": "itim",
    "mogulirina": "mogul",
    "mogul irina": "mogul",
    "heroika": "heroika",
    "shonen": "shonen",
    "kalam": "kalam",
    "hindsiliguri": "hindsiliguri",
    "shantellsans": "shantellsans",
    "shantellsansinformal": "shantellsansinformal",
    "vazirmatn": "vazirmatn",
    "pretendard": "pretendard",
    "zenmarugothic": "zenmarugothic",
    "chillround": "chillround",
    "cairo": "cairo",
    "pangolin": "pangolin",
    "mansalva": "mansalva",
    "patrickhand": "patrickhand",
    "poetsenone": "poetsenone",
    "balsamiqsans": "balsamiqsans",
    "lilitaone": "lilitaone",
    "estedad": "estedad",
    "marhey": "marhey",
    "gveretlevin": "gveretlevin",
    "kodchasan": "kodchasan",
    "poorstory": "poorstory",
    "nanumpenscript": "nanumpenscript",
    "zcoolkuaile": "zcoolkuaile",
    "chilanka": "chilanka",
    "agbalumo": "agbalumo",
    "atma": "atma",
    "koulen": "koulen",
    "yatraone": "yatraone",
    "akayakanadaka": "akayakanadaka",
    "mogra": "mogra",
    "anekodia": "anekodia",
    "langar": "langar",
    "kavivanar": "kavivanar",
    "akayatelivigala": "akayatelivigala",
    "yaldevi": "yaldevi",
    "anekmalayalam": "anekmalayalam",
    "notoserifmyanmar": "notoserifmyanmar",
    "phetsarath": "phetsarath",
    "kantumruypro": "kantumruypro",
    "eeyek": "eeyek",
    "gurugomke": "gurugomke",
    "montserratarmenian": "montserratarmenian",
    "firago": "firago",
    "fredoka": "fredoka",
    "abyssinicasil": "abyssinicasil",
    "scheherazadenew": "scheherazadenew",
    "iansui": "iansui",
}


_CONTENT_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}


def _content_type_for(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _resolve_font_code(font: str) -> str:
    """
    UI'daki görünen font adını API'nin beklediği koda çevirir.

    Eşleşme bulunamazsa (örn. kullanıcı zaten kodu girmişse) değeri
    olduğu gibi (küçük harfe çevrilerek) döndürür.
    """
    key = font.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    return _FONT_NAME_TO_CODE.get(key, font.strip().lower())


def _normalize_context(context: str | None) -> str:
    """
    Context zincirini API'nin beklediği biçime getirir.

    Boş/None değer zinciri yeniden başlatmak için ``"None"`` olur. Uzun
    metinlerde API yalnızca son 10.000 karakteri tuttuğu için sondan kesilir.
    """
    if context is None:
        return "None"
    text = str(context)
    if not text.strip():
        return "None"
    return text[-MAX_CONTEXT_CHARS:]


def _normalize_lang_list(langs) -> list[str]:
    """Virgülle ayrılmış metni veya listeyi temiz bir dil kodu listesine çevirir."""
    if not langs:
        return []
    if isinstance(langs, str):
        items = langs.replace(";", ",").split(",")
    else:
        items = list(langs)
    result: list[str] = []
    for item in items:
        code = str(item).strip().lower()
        if code and code not in result:
            result.append(code)
    return result


class ToriiAPIClient:
    """
    Torii Translate REST API ile asenkron iletişim kurar.

    Tüm metodlar hata fırlatmak yerine standart bir sözlük döndürür.
    Üst katman (translator_engine) bu sözlükteki 'success' bayrağını kontrol eder.

    Session yönetimi
    ----------------
    ``aiohttp.ClientSession`` pahalı bir nesnedir; her istek için yeniden
    oluşturmak TCP handshake maliyeti ve port tükenmesi riskine yol açar.
    Bu sınıf, session'ı ilk kullanımda oluşturup ``close()`` çağrısına kadar
    yeniden kullanır (lazy-init singleton pattern).
    """

    def __init__(
        self,
        api_key: str,
        byok_provider: str | None = None,
        byok_key: str | None = None,
        byok_local_url: str | None = None,
        byok_fallback: bool = False,
    ) -> None:
        """
        Parametreler
        ------------
        api_key : str
            Torii Translate API anahtarı.
        byok_provider : str | None
            BYOK sağlayıcısı: openai, openrouter, google, anthropic,
            deepseek, xai, local — ya da None (BYOK devre dışı).
        byok_key : str | None
            BYOK sağlayıcısına ait API anahtarı (local için opsiyonel).
        byok_local_url : str | None
            Sadece byok_provider == "local" ise zorunlu olan, OpenAI uyumlu
            self-hosted endpoint'in base URL'si.
        byok_fallback : bool
            True ise kendi anahtarın başarısız olduğunda Torii'nin modelleri
            devreye girer (normal kredi ücreti alınır).
        """
        self._api_key = (api_key or "").strip()
        provider = (byok_provider or "").strip().lower() or None
        if provider == "none":
            provider = None
        self._byok_provider = provider
        self._byok_key = (byok_key or "").strip() or None
        self._byok_local_url = (byok_local_url or "").strip() or None
        self._byok_fallback = bool(byok_fallback)

        self._rate_lock: asyncio.Lock | None = None
        self._last_request_time: float = 0.0

        # Paylaşımlı session — ilk istekte oluşturulur (lazy init)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # Session yönetimi
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Mevcut session'ı döndürür; yoksa yeni bir tane oluşturur.

        TCPConnector ile connection pooling aktif edilir:
        - limit=10: aynı anda en fazla 10 bağlantı (API sıralı çalıştığından
            pratikte 1 yeterli, ancak retry/timeout senaryoları için tolerans)
        - keepalive_timeout=30: boşta bağlantıyı 30 sn açık tutar
        """
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        async with self._session_lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit=10,
                    keepalive_timeout=30,
                    enable_cleanup_closed=True,
                )
                # Ağır sayfalarda (OCR + çeviri + inpaint + render) ve
                # reasoning modellerinde yanıt 2 dakikayı aşabilir.
                timeout = aiohttp.ClientTimeout(total=300, connect=15)
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                )
                logger.debug("Yeni aiohttp ClientSession oluşturuldu.")
        return self._session

    async def close(self) -> None:
        """Session ve bağlantıları kapatır. İşlem bitişinde çağrılmalıdır."""
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
                logger.debug("aiohttp ClientSession kapatıldı.")

    # ------------------------------------------------------------------
    # Dahili yardımcılar
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """Kimlik doğrulama başlığını oluşturur. BYOK form alanıdır, başlık değil."""
        return {"Authorization": f"Bearer {self._api_key}"}

    def byok_error(self) -> str | None:
        """BYOK yapılandırması eksikse kullanıcıya gösterilecek hatayı döndürür."""
        provider = self._byok_provider
        if not provider:
            return None
        if provider not in BYOK_PROVIDERS:
            return f"Bilinmeyen BYOK sağlayıcısı: {provider}"
        if provider == "local":
            if not self._byok_local_url:
                return "Yerel (self-hosted) BYOK için Base URL (x-byok-local-url) zorunludur."
            if not self._byok_local_url.lower().startswith(("http://", "https://")):
                return "Yerel BYOK Base URL'si http:// veya https:// ile başlamalıdır."
            return None
        if not self._byok_key:
            return f"BYOK sağlayıcısı '{provider}' için API anahtarı girilmedi."
        return None

    def _byok_active(self) -> bool:
        return bool(self._byok_provider) and self.byok_error() is None

    def _apply_byok(self, form: aiohttp.FormData) -> None:
        """
        BYOK alanlarını form gövdesine ekler.

        API, x-byok-<sağlayıcı> değerini multipart form alanı olarak bekler.
        Yerel sağlayıcıda x-byok-local-url zorunlu, anahtar ise opsiyoneldir.
        x-byok-fallback=true ise anahtar başarısız olduğunda Torii modeli
        devreye girer.
        """
        if not self._byok_active():
            return
        provider = self._byok_provider
        if self._byok_key:
            form.add_field(f"x-byok-{provider}", self._byok_key)
        if provider == "local" and self._byok_local_url:
            form.add_field("x-byok-local-url", self._byok_local_url)
        form.add_field("x-byok-fallback", "true" if self._byok_fallback else "false")

    async def _throttle(self) -> None:
        """
        İstekler arasında minimum _MIN_REQUEST_INTERVAL bekler.

        Bekleme kilidin içinde yapılır. Böylece eşzamanlı görevler aynı
        anda slot alamaz ve saniyede 1 istek sınırı korunur.
        """
        if self._rate_lock is None:
            self._rate_lock = asyncio.Lock()
        async with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            wait = _MIN_REQUEST_INTERVAL - elapsed if elapsed < _MIN_REQUEST_INTERVAL else 0.0
            if wait > 0:
                logger.debug("Rate limit throttle: %.3f sn bekleniyor.", wait)
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()

    @staticmethod
    def _jitter() -> float:
        """Rastgele jitter değeri döndürür."""
        return random.uniform(0, _JITTER_MAX)

    @staticmethod
    def _retry_after_seconds(resp_headers: dict) -> float | None:
        """Retry-After başlığını (saniye cinsinden) okur."""
        raw = None
        for key, value in resp_headers.items():
            if str(key).lower() == "retry-after":
                raw = value
                break
        if raw is None:
            return None
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            return None
        if seconds < 0:
            return None
        return min(seconds, _MAX_RETRY_AFTER)

    @staticmethod
    def _error_result(message: str, status: int | None = None) -> dict:
        """Standart hata sözlüğü döndürür."""
        return {
            "success": False,
            "status": status,
            "image_b64": None,
            "inpainted_b64": None,
            "context": "None",
            "text": [],
            "credits_remaining": None,
            "metadata": None,
            "data": None,
            "error": message,
        }

    @staticmethod
    def _header(resp_headers: dict, name: str):
        """Başlığı büyük/küçük harf duyarsız okur."""
        if not resp_headers:
            return None
        if name in resp_headers:
            return resp_headers[name]
        lname = name.lower()
        for key, value in resp_headers.items():
            if str(key).lower() == lname:
                return value
        return None

    @classmethod
    def _credits_from_headers(cls, resp_headers: dict) -> float | None:
        raw = cls._header(resp_headers, "credits") or cls._header(resp_headers, "x-credits")
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _detail_from_body(body) -> str:
        if isinstance(body, dict):
            detail = (
                body.get("detail")
                or body.get("message")
                or body.get("error")
                or body
            )
            if isinstance(detail, list):
                parts = []
                for item in detail:
                    if isinstance(item, dict):
                        parts.append(str(item.get("msg") or item))
                    else:
                        parts.append(str(item))
                return "; ".join(parts) or str(body)
            if isinstance(detail, dict):
                inner = detail.get("message") or detail.get("detail") or detail.get("error")
                return str(inner) if inner else str(detail)
            return str(detail)
        if body is None:
            return ""
        return str(body)[:500]

    def _finish_request(self, status: int, body, resp_headers: dict) -> dict:
        """HTTP sonucunu standart sonuç sözlüğüne çevirir."""
        credits_remaining = self._credits_from_headers(resp_headers)
        success_header = self._header(resp_headers, "success")
        header_failed = (
            success_header is not None and str(success_header).strip().lower() == "false"
        )

        if status == 200 and body is not None and not header_failed:
            if isinstance(body, dict) and body.get("success") is False:
                return self._error_result(
                    self._detail_from_body(body) or "API isteği başarısız.", status
                )
            image = body.get("image") if isinstance(body, dict) else None
            inpainted = body.get("inpainted") if isinstance(body, dict) else None
            context = body.get("context") if isinstance(body, dict) else None
            text = body.get("text", []) if isinstance(body, dict) else body
            metadata = body.get("metadata") if isinstance(body, dict) else None
            if credits_remaining is None and isinstance(body, dict):
                raw = body.get("credits")
                if raw is None:
                    raw = body.get("balance")
                if isinstance(raw, (int, float, str)) and not isinstance(raw, bool):
                    try:
                        credits_remaining = float(raw)
                    except (TypeError, ValueError):
                        credits_remaining = None
            return {
                "success": True,
                "status": status,
                "image_b64": image,
                "inpainted_b64": inpainted,
                "context": _normalize_context(context),
                "text": text if text is not None else [],
                "credits_remaining": credits_remaining,
                "metadata": metadata if isinstance(metadata, dict) else None,
                "data": body,
                "error": None,
            }

        detail = self._detail_from_body(body)
        suffix = f": {detail}" if detail else ""
        if status == 200:
            # HTTP 200 ama success başlığı false ya da gövde okunamadı
            result = self._error_result(
                f"API isteği başarısız{suffix}" if detail else "API yanıtı okunamadı.",
                status,
            )
        elif status in (401, 403):
            result = self._error_result(
                f"API anahtarı geçersiz veya yetkisiz (HTTP {status}){suffix}", status
            )
        elif status == 402:
            result = self._error_result(f"Yetersiz kredi (HTTP 402){suffix}", status)
        elif status == 413:
            result = self._error_result(
                f"Dosya çok büyük (HTTP 413; sınır 50 MB / 100 MP){suffix}", status
            )
        elif status in (400, 422):
            result = self._error_result(f"Doğrulama hatası (HTTP {status}){suffix}", status)
        elif status in (429, 503):
            result = self._error_result(
                f"Rate limit veya servis geçici kapalı (HTTP {status}), max deneme aşıldı.",
                status,
            )
        elif status == -1:
            result = self._error_result("Ağ bağlantı hatası: sunucuya ulaşılamadı.", status)
        else:
            result = self._error_result(f"Beklenmeyen hata (HTTP {status}){suffix}", status)
        result["credits_remaining"] = credits_remaining
        return result

    @staticmethod
    async def _read_body(resp: aiohttp.ClientResponse):
        """Yanıt gövdesini JSON olarak, olmazsa düz metin olarak okur."""
        try:
            raw = await resp.read()
        except Exception:
            return None
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except ValueError:
            return {"detail": text.strip()[:500]}

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        data=None,
        data_factory=None,
        is_rate_sensitive: bool = True,
    ) -> tuple[int, dict | list | None, dict]:
        """
        HTTP isteğini retry mantığıyla gerçekleştirir.

        ``data_factory`` verilirse her denemede yeniden çağrılır. Bu, aiohttp
        ``FormData`` gibi yalnızca bir kez tüketilebilen gövdelerin 429/network
        retry sırasında boş gitmesini engeller.

        Dönüş: (status_code, response_body_or_none, response_headers)
        Ağ hatasında retry bitmişse status_code -1 döner.
        """
        max_retries_rate = _MAX_RETRY_RATE if is_rate_sensitive else 2
        max_retries_net = _MAX_RETRY_NETWORK

        rate_attempt = 0
        net_attempt = 0

        while True:
            await self._throttle()
            try:
                session = await self._get_session()
                # FormData tek kullanımlıktır; retry'da yeniden üret
                request_data = data_factory() if data_factory is not None else data
                async with session.request(
                    method, url, headers=headers, data=request_data
                ) as resp:
                    status = resp.status
                    resp_headers = dict(resp.headers)

                    # Rate limit / servis geçici kapalı → retry
                    if status in (429, 503):
                        rate_attempt += 1
                        if rate_attempt >= max_retries_rate:
                            body = await self._read_body(resp)
                            logger.warning(
                                "Rate limit/503: max deneme aşıldı. Son yanıt: %s",
                                str(body)[:200],
                            )
                            return status, body, resp_headers

                        retry_after = self._retry_after_seconds(resp_headers)
                        wait = (
                            retry_after
                            if retry_after is not None
                            else _BACKOFF_BASE * (_BACKOFF_MULTIPLIER ** (rate_attempt - 1))
                        ) + self._jitter()
                        logger.info(
                            "HTTP %d alındı, %d/%d deneme, %.2f sn bekleniyor.",
                            status,
                            rate_attempt,
                            max_retries_rate,
                            wait,
                        )
                        await asyncio.sleep(wait)
                        continue

                    body = await self._read_body(resp)
                    if status != 200:
                        logger.warning("HTTP %d: %s", status, str(body)[:300])
                    return status, body, resp_headers

            except (
                aiohttp.ClientConnectionError,
                aiohttp.ServerDisconnectedError,
                aiohttp.ClientPayloadError,
                asyncio.TimeoutError,
                aiohttp.ClientOSError,
                aiohttp.ClientResponseError,
            ) as exc:
                net_attempt += 1
                sess = self._session
                self._session = None
                if sess is not None and not sess.closed:
                    try:
                        await sess.close()
                    except Exception:
                        pass
                if net_attempt >= max_retries_net:
                    logger.error(
                        "Network hatası, max deneme aşıldı: %s", exc
                    )
                    return -1, None, {}

                wait = (
                    _BACKOFF_BASE * (_BACKOFF_MULTIPLIER ** (net_attempt - 1))
                    + self._jitter()
                )
                logger.warning(
                    "Network hatası (%s), %d/%d deneme, %.2f sn sonra yeniden denenecek.",
                    exc,
                    net_attempt,
                    max_retries_net,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

    @staticmethod
    def _read_image(path: Path) -> tuple[bytes | None, str | None]:
        """Görseli okur ve API dosya limitini kontrol eder."""
        if not path.is_file():
            return None, f"Dosya bulunamadı: {path}"
        try:
            size = path.stat().st_size
        except OSError as exc:
            return None, f"Dosya okunamadı: {exc}"
        if size == 0:
            return None, f"Dosya boş: {path.name}"
        if size > MAX_FILE_BYTES:
            return None, (
                f"Dosya çok büyük ({size / (1024 * 1024):.1f} MB); "
                "API sınırı 50 MB."
            )
        try:
            return path.read_bytes(), None
        except OSError as exc:
            return None, f"Dosya okunamadı: {exc}"

    # ------------------------------------------------------------------
    # Public metodlar
    # ------------------------------------------------------------------

    async def translate_image(
        self,
        image_path: str,
        target_lang: str,
        translator: str,
        font: str = "NotoSans",
        text_align: str = "auto",
        stroke_disabled: bool = False,
        min_font_size: int | None = None,
        bubbles_only: bool = False,
        custom_prompt: str = "",
        context: str | None = "None",
        use_context: bool = True,
        reasoning_effort: str | None = None,
        source_langs_whitelist: list[str] | str | None = None,
    ) -> dict:
        """
        Tek bir görseli çevirir.

        Parametreler
        ------------
        image_path : str
            Çevrilecek görsel dosyasının tam yolu.
        target_lang : str
            Hedef dil kodu (örn. "tr", "en", "zh-cn").
        translator : str
            Kullanılacak model adı (örn. "gemini-3.1-flash-lite"). Yerel
            BYOK'ta sunucunun servis ettiği model adı.
        font : str
            Yazı tipi adı.
        text_align : str
            Metin hizalaması ("auto", "left", "center", "right").
        stroke_disabled : bool
            True ise metin stroke'u devre dışı bırakılır.
        min_font_size : int | None
            Minimum yazı boyutu (6-100); None/aralık dışı ise gönderilmez.
        bubbles_only : bool
            True ise sadece konuşma balonları çevrilir.
        custom_prompt : str
            Ek çeviri talimatı (maks 1000 karakter); boşsa gönderilmez.
        context : str | None
            Bölüm içi tutarlılık için önceki sayfadan gelen context;
            ilk sayfa için "None" gönderilmeli.
        use_context : bool
            False ise context alanı hiç gönderilmez.
        reasoning_effort : str | None
            "none", "low", "medium", "high". None/"none" ise gönderilmez
            (API varsayılanı none).
        source_langs_whitelist : list[str] | str | None
            Yalnızca bu dillerde algılanan metinler çevrilir (örn. ["ja"]).

        Dönüş
        -----
        dict
            {
                "success": bool,
                "status": int | None,
                "image_b64": str | None,    # data URI dahil tam base64 string
                "inpainted_b64": str | None,
                "context": str,
                "text": list,
                "credits_remaining": float | None,
                "metadata": dict | None,
                "error": str | None
            }
        """
        path = Path(image_path)
        image_bytes, err = self._read_image(path)
        if err:
            return self._error_result(err)

        target_lang = (target_lang or "").strip()
        if not target_lang:
            return self._error_result("Hedef dil (target_lang) boş olamaz.")

        byok_err = self.byok_error()
        if byok_err:
            return self._error_result(byok_err)

        translator = (translator or "").strip() or DEFAULT_TRANSLATOR
        align = (text_align or "auto").strip().lower()
        if align not in TEXT_ALIGNS:
            align = "auto"
        effort = (reasoning_effort or "").strip().lower()
        if effort not in REASONING_EFFORTS:
            effort = ""
        whitelist = _normalize_lang_list(source_langs_whitelist)

        mfs: int | None = None
        if min_font_size not in (None, "", 0):
            try:
                value = int(min_font_size)
            except (TypeError, ValueError):
                value = 0
            lo, hi = MIN_FONT_SIZE_RANGE
            if lo <= value <= hi:
                mfs = value

        prompt = (custom_prompt or "").strip()[:MAX_CUSTOM_PROMPT_CHARS]
        ctx = _normalize_context(context)

        headers = self._build_headers()
        url = f"{BASE_URL}/api/v2/upload"

        def _make_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                bytes(image_bytes),
                filename=path.name,
                content_type=_content_type_for(path),
            )
            form.add_field("target_lang", target_lang)
            form.add_field("translator", translator)
            form.add_field("font", _resolve_font_code(font or "NotoSans"))
            form.add_field("text_align", align)
            form.add_field("stroke_disabled", "true" if stroke_disabled else "false")
            form.add_field("bubbles_only", "true" if bubbles_only else "false")
            if effort and effort != "none":
                form.add_field("reasoning_effort", effort)
            if whitelist:
                form.add_field("source_langs_whitelist", json.dumps(whitelist))
            if use_context:
                form.add_field("context", ctx)
            if mfs is not None:
                form.add_field("min_font_size", str(mfs))
            if prompt:
                form.add_field("custom_prompt", prompt)
            self._apply_byok(form)
            return form

        logger.debug("translate_image: %s → %s (%s)", path.name, target_lang, translator)

        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=headers, data_factory=_make_form, is_rate_sensitive=True
        )
        result = self._finish_request(status, body, resp_headers)
        if result["success"] and not result.get("image_b64"):
            result = self._error_result("API yanıtında çevrilmiş görsel yok.", status)
        if result["success"]:
            logger.debug("translate_image başarılı: %s", path.name)
            meta = result.get("metadata") or {}
            if meta.get("fallback_model"):
                logger.info(
                    "BYOK başarısız oldu, Torii modeli kullanıldı: %s (%s)",
                    meta.get("fallback_model"),
                    meta.get("fallback_reason") or "?",
                )
        return result

    async def get_credits(self) -> float | None:
        """
        Hesaptaki kalan kredi bakiyesini döndürür.

        Dönüş
        -----
        float | None
            Kredi miktarı; hata durumunda None.
        """
        headers = self._build_headers()
        url = f"{BASE_URL}/api/credits"

        logger.debug("get_credits isteği gönderiliyor.")
        status, body, resp_headers = await self._request_with_retry(
            "GET", url, headers=headers, is_rate_sensitive=False
        )
        result = self._finish_request(status, body, resp_headers)
        if not result["success"]:
            logger.warning("get_credits başarısız, HTTP %d.", status)
            return None
        credits = None
        # Gövdedeki değer başlıktan daha günceldir; önce onu dene
        if isinstance(body, dict):
            for key in ("credits", "balance", "remaining"):
                if key in body:
                    try:
                        credits = float(body[key])
                    except (TypeError, ValueError):
                        credits = None
                    break
        if credits is None:
            credits = result.get("credits_remaining")
        if credits is None:
            logger.warning("Kredi yanıtı ayrıştırılamadı: %s", body)
            return None
        logger.debug("Kredi bakiyesi: %.4f", credits)
        return credits

    async def ocr_image(self, image_path: str) -> dict:
        """
        Bir görseldeki metni OCR ile tanır.

        POST /api/v2/ocr — yanıt paragraf nesnelerinden oluşan bir listedir.

        Dönüş
        -----
        dict
            {"success": bool, "data": list | None, "error": str | None, ...}
        """
        path = Path(image_path)
        image_bytes, err = self._read_image(path)
        if err:
            return self._error_result(err)

        headers = self._build_headers()
        url = f"{BASE_URL}/api/v2/ocr"

        def _make_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                bytes(image_bytes),
                filename=path.name,
                content_type=_content_type_for(path),
            )
            return form

        logger.debug("ocr_image: %s", path.name)
        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=headers, data_factory=_make_form, is_rate_sensitive=True
        )
        result = self._finish_request(status, body, resp_headers)
        if result["success"]:
            if isinstance(body, list):
                result["data"] = body
                result["text"] = body
            elif isinstance(body, dict) and isinstance(body.get("text"), list):
                result["data"] = body["text"]
        return result

    async def inpaint_image(self, image_path: str, mask_path: str) -> dict:
        """
        Maskenin siyah olmayan bölgelerini temizler.

        POST /api/inpaint — image ve mask dosyaları zorunludur.
        """
        image = Path(image_path)
        mask = Path(mask_path)
        image_bytes, err = self._read_image(image)
        if err:
            return self._error_result(err)
        mask_bytes, err = self._read_image(mask)
        if err:
            return self._error_result(f"Maske: {err}")

        url = f"{BASE_URL}/api/inpaint"

        def _make_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "image",
                bytes(image_bytes),
                filename=image.name,
                content_type=_content_type_for(image),
            )
            form.add_field(
                "mask",
                bytes(mask_bytes),
                filename=mask.name,
                content_type=_content_type_for(mask),
            )
            return form

        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=self._build_headers(), data_factory=_make_form
        )
        return self._finish_request(status, body, resp_headers)

    async def typeset_image(
        self,
        image_path: str,
        text_boxes: list[dict],
        font: str = "NotoSans",
        min_font_size: int | None = None,
        stroke_disabled: bool = False,
    ) -> dict:
        """
        Temiz bir görsele metin kutuları basar.

        POST /api/typeset — file ve text_boxes zorunludur. En fazla 500 kutu,
        kutu başına 5.000 ve toplam 20.000 karakter.
        """
        path = Path(image_path)
        if not text_boxes:
            return self._error_result("Typeset için en az bir metin kutusu gerekli.")
        if len(text_boxes) > 500:
            return self._error_result("Typeset en fazla 500 metin kutusu kabul eder.")
        total_chars = 0
        for box in text_boxes:
            text = str(box.get("text", "")) if isinstance(box, dict) else ""
            if len(text) > 5000:
                return self._error_result("Bir metin kutusu en fazla 5.000 karakter olabilir.")
            total_chars += len(text)
        if total_chars > 20000:
            return self._error_result("Typeset toplam metni en fazla 20.000 karakter olabilir.")

        image_bytes, err = self._read_image(path)
        if err:
            return self._error_result(err)

        url = f"{BASE_URL}/api/typeset"
        payload = json.dumps(text_boxes, ensure_ascii=False)

        mfs: int | None = None
        if min_font_size:
            lo, hi = MIN_FONT_SIZE_RANGE
            if lo <= int(min_font_size) <= hi:
                mfs = int(min_font_size)

        def _make_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                bytes(image_bytes),
                filename=path.name,
                content_type=_content_type_for(path),
            )
            form.add_field("text_boxes", payload)
            form.add_field("font", _resolve_font_code(font or "NotoSans"))
            if mfs is not None:
                form.add_field("min_font_size", str(mfs))
            form.add_field("stroke_disabled", "true" if stroke_disabled else "false")
            return form

        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=self._build_headers(), data_factory=_make_form
        )
        return self._finish_request(status, body, resp_headers)

    async def colorize_image(
        self,
        image_path: str,
        model: str = DEFAULT_COLORIZE_MODEL,
        prompt: str | None = None,
        byok_key: str | None = None,
    ) -> dict:
        """
        Siyah-beyaz gorsele renk ekler.

        POST /api/colorize -- image zorunlu, colorize_model ve
        colorize_prompt opsiyonel. Model: local (1), muse (12, varsayilan),
        gpt (16), grok (25), gemini (46) kredi. local prompt ve BYOK almaz.
        BYOK alanlari modele ozeldir (openrouter / xai / google) ve duz BYOK'tan
        ayri gonderilir; verilirse istek 1 krediye duser.
        Yanit JPEG data-URL'sidir (image).
        """
        path = Path(image_path)
        image_bytes, err = self._read_image(path)
        if err:
            return self._error_result(err)

        chosen = (model or DEFAULT_COLORIZE_MODEL).strip().lower()
        if chosen not in COLORIZE_MODELS:
            return self._error_result(
                "Gecersiz colorize modeli. Gecerli: " + ", ".join(COLORIZE_MODELS) + "."
            )

        hint = ""
        if chosen != "local" and prompt:
            hint = str(prompt).strip()[:MAX_COLORIZE_PROMPT_CHARS]

        own_key = (byok_key or "").strip()
        byok_field = COLORIZE_BYOK_FIELDS.get(chosen) if own_key and chosen != "local" else None

        url = f"{BASE_URL}/api/colorize"

        def _make_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "image",
                bytes(image_bytes),
                filename=path.name,
                content_type=_content_type_for(path),
            )
            form.add_field("colorize_model", chosen)
            if hint:
                form.add_field("colorize_prompt", hint)
            if byok_field:
                form.add_field(byok_field, own_key)
            return form

        logger.debug("colorize_image: %s model=%s", path.name, chosen)
        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=self._build_headers(), data_factory=_make_form
        )
        return self._finish_request(status, body, resp_headers)

    async def get_usage(
        self,
        days: int = 30,
        scope: str = "key",
        key_id: str | None = None,
        by_key: bool = False,
    ) -> dict:
        """
        Günlük kullanım sayılarını döndürür.

        GET /api/usage — days 1 ile 730 arasındadır (varsayılan 30).
        scope: "key" (bu anahtar), "api" (tüm anahtarlar) veya "account".
        key_id yalnızca scope "key" ile, by_key yalnızca scope "api" ile geçerlidir.
        """
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(730, days))
        scope = (scope or "key").strip().lower()
        if scope not in USAGE_SCOPES:
            scope = "key"
        params: dict[str, str] = {"days": str(days), "scope": scope}
        if key_id:
            params["key_id"] = key_id.strip()
            params["scope"] = "key"
        elif by_key and scope == "api":
            params["by_key"] = "true"
        url = f"{BASE_URL}/api/usage?{urlencode(params)}"
        status, body, resp_headers = await self._request_with_retry(
            "GET", url, headers=self._build_headers(), is_rate_sensitive=False
        )
        return self._finish_request(status, body, resp_headers)

    async def get_keys(self) -> dict:
        """
        Hesaptaki API anahtarlarını listeler.

        GET /api/keys — yanıt: {"keys": [...], "limit": int}
        """
        url = f"{BASE_URL}/api/keys"
        status, body, resp_headers = await self._request_with_retry(
            "GET", url, headers=self._build_headers(), is_rate_sensitive=False
        )
        return self._finish_request(status, body, resp_headers)

    async def share_credits(self, email: str, amount: float) -> dict:
        """
        Krediyi başka bir Torii hesabına aktarır.

        POST /api/credits/share — JSON gövde: target_email, shared_credits.
        """
        email = (email or "").strip()
        if not email or "@" not in email:
            return self._error_result("Geçerli bir e-posta adresi gerekli.")
        if isinstance(amount, bool) or (
            isinstance(amount, float) and not amount.is_integer()
        ):
            return self._error_result("Paylaşılacak kredi tam sayı olmalı.")
        try:
            credits = int(amount)
        except (TypeError, ValueError):
            return self._error_result("Paylaşılacak kredi tam sayı olmalı.")
        if credits <= 0:
            return self._error_result("Paylaşılacak kredi 0'dan büyük bir tam sayı olmalı.")

        url = f"{BASE_URL}/api/credits/share"
        payload = {"target_email": email, "shared_credits": credits}
        status, body, resp_headers = await self._request_with_retry(
            "POST",
            url,
            headers={**self._build_headers(), "Content-Type": "application/json"},
            data=json.dumps(payload),
            is_rate_sensitive=False,
        )
        return self._finish_request(status, body, resp_headers)
