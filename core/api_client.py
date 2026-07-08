"""
api_client.py - Torii Translate API istemcisi.

Sorumluluğu:
- POST /api/v2/upload endpoint'ine görsel göndermek ve çevrilmiş görsel + context döndürmek.
- GET /api/credits endpoint'i ile kredi bakiyesini sorgulamak.
- 429/503 hatalarında exponential backoff ile otomatik yeniden deneme.
- BYOK (Bring Your Own Key) başlıklarını yönetmek.
- Client seviyesinde rate limiting (saniyede 1 istek).
"""

import asyncio
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

        # Client seviyesi rate limit mekanizması
        self._rate_lock = asyncio.Lock()
        self._last_request_time: float = 0.0

        # Paylaşımlı session — ilk istekte oluşturulur (lazy init)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

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
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
                logger.debug("aiohttp ClientSession kapatıldı.")

    # ------------------------------------------------------------------
    # Dahili yardımcılar
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """Ortak HTTP başlıklarını oluşturur."""
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
        }
        if self._byok_provider and self._byok_key:
            headers[f"x-byok-{self._byok_provider}"] = self._byok_key
            if self._byok_provider == "local" and self._byok_local_url:
                headers["x-byok-local-url"] = self._byok_local_url
        return headers

    async def _throttle(self) -> None:
        """
        İstekler arasında minimum _MIN_REQUEST_INTERVAL bekler.
        Her istek öncesinde çağrılmalıdır.

        Lock sadece zaman damgasını okuma/güncelleme sırasında tutulur;
        sleep sırasında serbest bırakılır.

        Zaman damgası sleep BİTİŞİNDE güncellenir — böylece gerçek aralık
        her zaman tam olarak _MIN_REQUEST_INTERVAL olur, daha fazla değil.
        """
        async with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            wait = _MIN_REQUEST_INTERVAL - elapsed if elapsed < _MIN_REQUEST_INTERVAL else 0.0

        if wait > 0:
            logger.debug("Rate limit throttle: %.3f sn bekleniyor.", wait)
            await asyncio.sleep(wait)

        # Zaman damgasını sleep bittikten sonra güncelle
        async with self._rate_lock:
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
            "error": message,
        }

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        data=None,
        is_rate_sensitive: bool = True,
    ) -> tuple[int, dict | None, dict]:
        """
        HTTP isteğini retry mantığıyla gerçekleştirir.

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
                async with session.request(
                    method, url, headers=headers, data=data
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

            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                net_attempt += 1
                # Bağlantı hatasında session bozulmuş olabilir — sıfırla
                await self.close()
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

        data = aiohttp.FormData()
        data.add_field(
            "file",
            image_bytes,
            filename=path.name,
            content_type="application/octet-stream",
        )
        data.add_field("target_lang", target_lang)
        data.add_field("translator", translator)
        data.add_field("font", font)
        data.add_field("text_align", text_align)
        data.add_field("stroke_disabled", "true" if stroke_disabled else "false")
        data.add_field("context", context)

        if min_font_size is not None:
            data.add_field("min_font_size", str(min_font_size))
        if bubbles_only:
            data.add_field("bubbles_only", "true")
        if custom_prompt:
            data.add_field("custom_prompt", custom_prompt[:1000])

        logger.debug("translate_image: %s → %s (%s)", path.name, target_lang, translator)

        status, body, resp_headers = await self._request_with_retry(
            "POST", url, headers=headers, data=data, is_rate_sensitive=True
        )

        credits_remaining: float | None = None
        raw_credits = resp_headers.get("credits") or resp_headers.get("x-credits")
        if raw_credits:
            try:
                credits_remaining = float(raw_credits)
            except ValueError:
                pass

        if status == 200 and body is not None:
            logger.debug("translate_image başarılı: %s", path.name)
            return {
                "success": True,
                "image_b64": body.get("image"),
                "inpainted_b64": body.get("inpainted"),
                "context": body.get("context", "None"),
                "text": body.get("text", []),
                "credits_remaining": credits_remaining,
                "error": None,
            }

        if status == 401:
            return self._error_result("API anahtarı geçersiz veya yetkisiz (HTTP 401).")

        if status in (400, 422):
            detail = ""
            if isinstance(body, dict):
                detail = body.get("detail") or body.get("message") or str(body)
            return self._error_result(f"Doğrulama hatası (HTTP {status}): {detail}")

        if status in (429, 503):
            return self._error_result(
                f"Rate limit veya servis geçici kapalı (HTTP {status}), max deneme aşıldı."
            )

        if status == -1:
            return self._error_result("Ağ bağlantı hatası: sunucuya ulaşılamadı.")

        return self._error_result(f"Beklenmeyen hata (HTTP {status}).")

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
        status, body, _ = await self._request_with_retry(
            "GET", url, headers=headers, is_rate_sensitive=False
        )

        if status == 200 and isinstance(body, dict):
            try:
                credits = float(body["credits"])
                logger.debug("Kredi bakiyesi: %.4f", credits)
                return credits
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Kredi yanıtı ayrıştırılamadı: %s — %s", body, exc)
                return None

        logger.warning("get_credits başarısız, HTTP %d.", status)
        return None

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

        data = aiohttp.FormData()
        data.add_field(
            "file",
            image_bytes,
            filename=path.name,
            content_type="application/octet-stream",
        )

        logger.debug("ocr_image: %s", path.name)
        status, body, _ = await self._request_with_retry(
            "POST", url, headers=headers, data=data, is_rate_sensitive=True
        )

        if status == 200 and body is not None:
            return {"success": True, "data": body, "error": None}

        if status == 401:
            return {"success": False, "data": None, "error": "API anahtarı geçersiz (HTTP 401)."}

        if status in (400, 422):
            detail = ""
            if isinstance(body, dict):
                detail = body.get("detail") or body.get("message") or str(body)
            return {"success": False, "data": None, "error": f"Doğrulama hatası (HTTP {status}): {detail}"}

        if status == -1:
            return {"success": False, "data": None, "error": "Ağ bağlantı hatası."}

        return {"success": False, "data": None, "error": f"Beklenmeyen hata (HTTP {status})."}