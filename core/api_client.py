"""
api_client.py - Torii Translate API istemcisi.

Sorumluluğu:
- POST /api/v2/upload ile görsel çevirmek.
- POST /api/v2/ocr ile OCR almak.
- POST /api/inpaint ile maskelenmiş bölgeyi temizlemek.
- POST /api/typeset ile metin kutularını basmak.
- GET /api/credits ve GET /api/usage ile bakiyeyi ve kullanımı okumak.
- POST /api/credits/share ile kredi paylaşmak.
- 429/503 hatalarında exponential backoff ile otomatik yeniden deneme.
- BYOK alanlarını multipart form gövdesinde göndermek.
- İstemci genelinde saniyede 1 istek sınırına uymak.
"""

import asyncio
import json
import logging
import random
import time
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.toriitranslate.com"

# Retry ayarları
_MAX_RETRY_RATE = 5        # 429/503 için max deneme
_MAX_RETRY_NETWORK = 3     # Network hatası için max deneme
_BACKOFF_BASE = 1.0        # Başlangıç bekleme süresi (saniye)
_BACKOFF_MULTIPLIER = 2.0
_JITTER_MAX = 0.5          # Rastgele ek bekleme üst sınırı (saniye)

# Rate limit: saniyede 1 istek
_MIN_REQUEST_INTERVAL = 1.0  # saniye

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
    "heroika": "heroika",
    "shonen": "shonen",
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
    return _FONT_NAME_TO_CODE.get(key, font)


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
            BYOK sağlayıcısına ait API anahtarı.
        byok_local_url : str | None
            Sadece byok_provider == "local" ise geçerli yerel endpoint URL'si.
        """
        self._api_key = api_key
        self._byok_provider = byok_provider
        self._byok_key = byok_key
        self._byok_local_url = byok_local_url

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
                timeout = aiohttp.ClientTimeout(total=120, connect=15)
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

    def _apply_byok(self, form: aiohttp.FormData) -> None:
        """
        BYOK alanlarını form gövdesine ekler.

        API, x-byok-<sağlayıcı> değerini multipart form alanı olarak bekler.
        Yerel sağlayıcıda x-byok-local-url da zorunludur.
        """
        if not self._byok_provider or not self._byok_key:
            return
        form.add_field(f"x-byok-{self._byok_provider}", self._byok_key)
        if self._byok_provider == "local" and self._byok_local_url:
            form.add_field("x-byok-local-url", self._byok_local_url)

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
    def _error_result(message: str) -> dict:
        """Standart hata sözlüğü döndürür."""
        return {
            "success": False,
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
    def _credits_from_headers(resp_headers: dict) -> float | None:
        raw = resp_headers.get("credits") or resp_headers.get("x-credits")
        if not raw:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _detail_from_body(body) -> str:
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("message") or body
            if isinstance(detail, list):
                parts = []
                for item in detail:
                    if isinstance(item, dict):
                        parts.append(str(item.get("msg") or item))
                    else:
                        parts.append(str(item))
                return "; ".join(parts) or str(body)
            return str(detail)
        if body is None:
            return ""
        return str(body)

    def _finish_request(self, status: int, body, resp_headers: dict) -> dict:
        """HTTP sonucunu standart sonuç sözlüğüne çevirir."""
        credits_remaining = self._credits_from_headers(resp_headers)
        if status == 200 and body is not None:
            if isinstance(body, dict) and body.get("success") is False:
                return self._error_result(
                    self._detail_from_body(body) or "API isteği başarısız."
                )
            image = body.get("image") if isinstance(body, dict) else None
            inpainted = body.get("inpainted") if isinstance(body, dict) else None
            context = body.get("context", "None") if isinstance(body, dict) else "None"
            text = body.get("text", []) if isinstance(body, dict) else body
            metadata = body.get("metadata") if isinstance(body, dict) else None
            if credits_remaining is None and isinstance(body, dict):
                raw = body.get("credits")
                if raw is None:
                    raw = body.get("balance")
                if raw is not None:
                    try:
                        credits_remaining = float(raw)
                    except (TypeError, ValueError):
                        credits_remaining = None
            return {
                "success": True,
                "image_b64": image,
                "inpainted_b64": inpainted,
                "context": context,
                "text": text if text is not None else [],
                "credits_remaining": credits_remaining,
                "metadata": metadata,
                "data": body,
                "error": None,
            }
        if status == 401:
            return self._error_result("API anahtarı geçersiz veya yetkisiz (HTTP 401).")
        if status in (400, 422):
            detail = self._detail_from_body(body)
            return self._error_result(f"Doğrulama hatası (HTTP {status}): {detail}")
        if status in (429, 503):
            return self._error_result(
                f"Rate limit veya servis geçici kapalı (HTTP {status}), max deneme aşıldı."
            )
        if status == -1:
            return self._error_result("Ağ bağlantı hatası: sunucuya ulaşılamadı.")
        return self._error_result(f"Beklenmeyen hata (HTTP {status}).")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        data=None,
        data_factory=None,
        is_rate_sensitive: bool = True,
    ) -> tuple[int, dict | None, dict]:
        """
        HTTP isteğini retry mantığıyla gerçekleştirir.

        ``data_factory`` verilirse her denemede yeniden çağrılır. Bu, aiohttp
        ``FormData`` gibi yalnızca bir kez tüketilebilen gövdelerin 429/network
        retry sırasında boş gitmesini engeller.

        Dönüş: (status_code, response_json_or_none, response_headers)
        Kurtarılamaz hatada (200 dışı ve retry bitmişse) status_code < 0 döner.
        """
        max_retries_rate = _MAX_RETRY_RATE if is_rate_sensitive else 1
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

                    # Başarılı
                    if status == 200:
                        try:
                            body = await resp.json(content_type=None)
                        except Exception:
                            body = None
                        return status, body, resp_headers

                    # Rate limit / servis geçici kapalı → retry
                    if status in (429, 503):
                        rate_attempt += 1
                        if rate_attempt >= max_retries_rate:
                            body_text = await resp.text()
                            logger.warning(
                                "Rate limit/503: max deneme aşıldı. Son yanıt: %s",
                                body_text[:200],
                            )
                            return status, None, resp_headers

                        wait = (
                            _BACKOFF_BASE
                            * (_BACKOFF_MULTIPLIER ** (rate_attempt - 1))
                            + self._jitter()
                        )
                        logger.info(
                            "HTTP %d alındı, %d/%d deneme, %.2f sn bekleniyor.",
                            status,
                            rate_attempt,
                            max_retries_rate,
                            wait,
                        )
                        await asyncio.sleep(wait)
                        continue

                    # Kimlik doğrulama hatası → hiç retry yapma
                    if status == 401:
                        return status, None, resp_headers

                    # Doğrulama hatası → body'yi ilet, retry yapma
                    if status in (400, 422):
                        try:
                            body = await resp.json(content_type=None)
                        except Exception:
                            body = {"detail": await resp.text()}
                        return status, body, resp_headers

                    # Diğer HTTP hataları
                    body_text = await resp.text()
                    logger.warning("Beklenmeyen HTTP %d: %s", status, body_text[:200])
                    return status, None, resp_headers

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
        context: str = "None",
        use_context: bool = True,
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
            Kullanılacak model adı (örn. "gemini-3.1-flash-lite").
        font : str
            Yazı tipi adı.
        text_align : str
            Metin hizalaması ("auto", "left", "center", "right").
        stroke_disabled : bool
            True ise metin stroke'u devre dışı bırakılır.
        min_font_size : int | None
            Minimum yazı boyutu; None ise gönderilmez.
        bubbles_only : bool
            True ise sadece konuşma balonları çevrilir.
        custom_prompt : str
            Ek çeviri talimatı (maks 1000 karakter); boşsa gönderilmez.
        context : str
            Bölüm içi tutarlılık için önceki sayfadan gelen context;
            ilk sayfa için "None" gönderilmeli.

        Dönüş
        -----
        dict
            {
              "success": bool,
              "image_b64": str | None,    # data URI dahil tam base64 string
              "inpainted_b64": str | None,
              "context": str,
              "text": list,
              "credits_remaining": float | None,
              "error": str | None
            }
        """
        path = Path(image_path)
        if not path.is_file():
            return self._error_result(f"Dosya bulunamadı: {image_path}")

        headers = self._build_headers()
        url = f"{BASE_URL}/api/v2/upload"

        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            return self._error_result(f"Dosya okunamadı: {exc}")

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
            form.add_field("font", _resolve_font_code(font))
            form.add_field("text_align", text_align)
            form.add_field("stroke_disabled", "true" if stroke_disabled else "false")
            if use_context:
                form.add_field("context", context[:10000])
            if min_font_size is not None:
                form.add_field("min_font_size", str(min_font_size))
            if bubbles_only:
                form.add_field("bubbles_only", "true")
            if custom_prompt:
                form.add_field("custom_prompt", custom_prompt[:1000])
            self._apply_byok(form)
            return form

        logger.debug("translate_image: %s → %s (%s)", path.name, target_lang, translator)

        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=headers, data_factory=_make_form, is_rate_sensitive=True
        )
        result = self._finish_request(status, body, resp_headers)
        if result["success"]:
            logger.debug("translate_image başarılı: %s", path.name)
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
        credits = result.get("credits_remaining")
        if credits is None and isinstance(body, dict):
            for key in ("credits", "balance", "remaining"):
                if key in body:
                    try:
                        credits = float(body[key])
                    except (TypeError, ValueError):
                        credits = None
                    break
        if credits is None:
            logger.warning("Kredi yanıtı ayrıştırılamadı: %s", body)
            return None
        logger.debug("Kredi bakiyesi: %.4f", credits)
        return credits

    async def ocr_image(self, image_path: str) -> dict:
        """
        Bir görseldeki metni OCR ile tanır.

        Not: Bu metod ileride opsiyonel bir özellik için hazırlanmıştır,
        şu an temel implementasyon içerir.

        Parametreler
        ------------
        image_path : str
            OCR uygulanacak görsel dosyasının tam yolu.

        Dönüş
        -----
        dict
            {"success": bool, "data": dict | None, "error": str | None}
        """
        path = Path(image_path)
        if not path.is_file():
            return {"success": False, "data": None, "error": f"Dosya bulunamadı: {image_path}"}

        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            return {"success": False, "data": None, "error": f"Dosya okunamadı: {exc}"}

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
        if result["success"] and isinstance(body, list):
            result["data"] = body
            result["text"] = body
        return result

    async def inpaint_image(self, image_path: str, mask_path: str) -> dict:
        """
        Maskenin beyaz bölgelerini temizler.

        POST /api/inpaint — image ve mask dosyaları zorunludur.
        """
        image = Path(image_path)
        mask = Path(mask_path)
        if not image.is_file():
            return self._error_result(f"Dosya bulunamadı: {image_path}")
        if not mask.is_file():
            return self._error_result(f"Maske bulunamadı: {mask_path}")
        try:
            image_bytes = image.read_bytes()
            mask_bytes = mask.read_bytes()
        except OSError as exc:
            return self._error_result(f"Dosya okunamadı: {exc}")

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

        POST /api/typeset — file ve text_boxes zorunludur.
        """
        path = Path(image_path)
        if not path.is_file():
            return self._error_result(f"Dosya bulunamadı: {image_path}")
        if not text_boxes:
            return self._error_result("Typeset için en az bir metin kutusu gerekli.")
        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            return self._error_result(f"Dosya okunamadı: {exc}")

        url = f"{BASE_URL}/api/typeset"
        payload = json.dumps(text_boxes, ensure_ascii=False)

        def _make_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                bytes(image_bytes),
                filename=path.name,
                content_type=_content_type_for(path),
            )
            form.add_field("text_boxes", payload)
            form.add_field("font", _resolve_font_code(font))
            if min_font_size is not None:
                form.add_field("min_font_size", str(min_font_size))
            if stroke_disabled:
                form.add_field("stroke_disabled", "true")
            return form

        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=self._build_headers(), data_factory=_make_form
        )
        return self._finish_request(status, body, resp_headers)

    async def get_usage(self, days: int = 30) -> dict:
        """
        Günlük kullanım sayılarını döndürür.

        GET /api/usage?days= — days 1 ile 730 arasındadır, varsayılan 30.
        """
        days = max(1, min(730, int(days)))
        url = f"{BASE_URL}/api/usage?days={days}"
        status, body, resp_headers = await self._request_with_retry(
            "GET", url, headers=self._build_headers(), is_rate_sensitive=False
        )
        return self._finish_request(status, body, resp_headers)

    async def share_credits(self, email: str, amount: float) -> dict:
        """
        Krediyi başka bir Torii hesabına aktarır.

        POST /api/credits/share — JSON gövde: target_email, shared_credits.
        """
        email = email.strip()
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