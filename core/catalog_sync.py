"""
catalog_sync.py - Torii API kataloğunu (çeviri modelleri + fontlar) yerel
varsayılanlarla senkronize eder.

toriitranslate.com/api sayfası statik HTML'dir; modeller ``translator_select``,
fontlar ``font_select`` elemanının ``<option value="...">`` alanlarında
listelenir. Bu modül sayfayı indirir, seçenekleri çıkarır ve kullanıcının elle
eklediği özel girdileri koruyarak kataloğu günceller.

Ağ hatası / beklenmeyen sayfa yapısı durumunda mevcut katalog değiştirilmez;
uygulama çalışmaya devam eder.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CATALOG_URL = "https://toriitranslate.com/api"
_REQUEST_TIMEOUT = 12  # saniye

# API'nin kısa font kodlarını UI'da gösterilen adlara çevirir.
# Yeni fontlar için fallback: kodun ilk harfi büyütülür (örn. "baron" -> "Baron").
_FONT_CODE_TO_NAME: dict[str, str] = {
    "noto": "NotoSans",
    "wildwords": "WildWords",
    "badcomic": "BadComic",
    "mashanzheng": "MaShanZheng",
    "komika": "KomikaJam",
    "bangers": "Bangers",
    "edo": "Edo",
    "ridi": "RIDIBatang",
    "bushidoo": "Bushidoo",
    "hayah": "Hayah",
    "itim": "Itim",
    "mogul": "MogulIrina",
    "heroika": "Heroika",
    "shonen": "Shonen",
    "kalam": "Kalam",
    "hindsiliguri": "HindSiliguri",
    "shantellsans": "ShantellSans",
    "shantellsansinformal": "ShantellSansInformal",
    "vazirmatn": "Vazirmatn",
    "pretendard": "Pretendard",
    "zenmarugothic": "ZenMaruGothic",
    "chillround": "ChillRound",
    "cairo": "Cairo",
    "pangolin": "Pangolin",
    "mansalva": "Mansalva",
    "patrickhand": "PatrickHand",
    "poetsenone": "PoetsenOne",
    "balsamiqsans": "BalsamiqSans",
    "lilitaone": "LilitaOne",
    "estedad": "Estedad",
    "marhey": "Marhey",
    "gveretlevin": "GveretLevin",
    "kodchasan": "Kodchasan",
    "poorstory": "PoorStory",
    "nanumpenscript": "NanumPenScript",
    "zcoolkuaile": "ZCOOLKuaiLe",
    "chilanka": "Chilanka",
    "agbalumo": "Agbalumo",
    "atma": "Atma",
    "koulen": "Koulen",
    "yatraone": "YatraOne",
    "akayakanadaka": "AkayaKanadaka",
    "mogra": "Mogra",
    "anekodia": "AnekOdia",
    "langar": "Langar",
    "kavivanar": "Kavivanar",
    "akayatelivigala": "AkayaTelivigala",
    "yaldevi": "Yaldevi",
    "anekmalayalam": "AnekMalayalam",
    "notoserifmyanmar": "NotoSerifMyanmar",
    "phetsarath": "Phetsarath",
    "kantumruypro": "KantumruyPro",
    "eeyek": "Eeyek",
    "gurugomke": "GuruGomke",
    "montserratarmenian": "MontserratArmenian",
    "firago": "FiraGO",
    "fredoka": "Fredoka",
    "abyssinicasil": "AbyssinicaSIL",
    "scheherazadenew": "ScheherazadeNew",
    "iansui": "Iansui",
    "alklife": "ALKLife",
    "bpgninomtavruli": "BPGNinoMtavruli",
    "baron": "Baron",
}

# Eski model kodları → yeni kodlar (kullanıcı ayarlarında eski kod kalmışsa).
_REMOVED_TRANSLATORS: dict[str, str] = {
    "gpt-5.6-luna": "gpt-6-luna",
    "gpt-5.4": "gpt-6.1-sol",
    "gpt-6-sol": "gpt-6.1-sol",
    "claude-sonnet-4.6": "claude-sonnet-5.5",
    "claude-sonnet-5": "claude-sonnet-5.5",
    "gemini-3.6-flash": "gemini-3.8-flash",
    "deepseek": "deepseek-v4.1-flash",
    "deepseek-v4-flash": "deepseek-v4.1-flash",
}

_SELECT_RE_TEMPLATE = r'<select\b[^>]*\bid=["\']{select_id}["\'][^>]*>(.*?)</select>'
_OPTION_VALUE_RE = re.compile(r'<option\b[^>]*\bvalue=["\']([^"\']+)["\']', re.IGNORECASE)


@dataclass
class CatalogResult:
    """Senkronizasyon sonucu."""

    success: bool = False
    models: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    added_models: list[str] = field(default_factory=list)
    added_fonts: list[str] = field(default_factory=list)
    removed_models: list[str] = field(default_factory=list)
    error: str = ""


def _extract_options(html: str, select_id: str) -> list[str]:
    """Belirtilen <select> elemanındaki option value'larını döndürür."""
    pattern = re.compile(
        _SELECT_RE_TEMPLATE.format(select_id=re.escape(select_id)),
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return []
    values: list[str] = []
    for raw in _OPTION_VALUE_RE.findall(match.group(1)):
        value = raw.strip()
        if value and value not in values:
            values.append(value)
    return values


def parse_catalog(html: str) -> tuple[list[str], list[str]]:
    """
    API sayfası HTML'inden (model_kodları, font_adları) çıkarır.

    Sayfa yapısı değişirse boş liste döner; çağıran mevcut kataloğu korur.
    """
    model_codes = _extract_options(html, "translator_select")
    font_codes = _extract_options(html, "font_select")

    models = [_REMOVED_TRANSLATORS.get(code, code) for code in model_codes]
    fonts: list[str] = []
    for code in font_codes:
        name = _FONT_CODE_TO_NAME.get(code, code[:1].upper() + code[1:])
        if name not in fonts:
            fonts.append(name)

    return models, fonts


def fetch_catalog(url: str = CATALOG_URL, timeout: float = _REQUEST_TIMEOUT) -> tuple[list[str], list[str]]:
    """API dokümantasyon sayfasını indirir ve kataloğu ayrıştırır."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ToriiBatch/1.0 (catalog sync)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="replace")
    return parse_catalog(html)


def _merge_catalog(
    current: list[str],
    remote: list[str],
    defaults: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Yerel listeyi uzak katalogla birleştirir.

    - Uzak listedeki sıra korunur.
    - Kullanıcının elle eklediği özel girdiler (varsayılan listede olmayanlar) korunur.
    - Varsayılan listede olup uzak listede olmayan eski kodlar kaldırılır.
    """
    if not remote:
        return list(current), [], []

    remote = [_REMOVED_TRANSLATORS.get(item, item) for item in remote]
    custom = [
        item for item in current
        if item and item not in defaults and item not in remote
    ]
    merged = list(remote) + custom

    added = [item for item in remote if item not in current]
    removed = [
        item for item in current
        if item in defaults and item not in remote
    ]
    return merged, added, removed


def sync_catalog(settings_manager, *, url: str = CATALOG_URL) -> CatalogResult:
    """
    Torii API kataloğunu indirip ayarlara yazar.

    Parametreler
    ------------
    settings_manager : SettingsManager
        Ayar yöneticisi. ``get`` / ``set`` / ``save`` metotlarını kullanır.
    url : str
        Dokümantasyon sayfası adresi (testlerde örnek HTML için değiştirilebilir).

    Dönüş
    -----
    CatalogResult
        Eklenen/kaldırılan girdiler ve hata bilgisi.
    """
    result = CatalogResult()
    try:
        remote_models, remote_fonts = fetch_catalog(url)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        result.error = str(exc)
        logger.warning("Torii kataloğu alınamadı, mevcut liste korunuyor: %s", exc)
        return result

    if len(remote_models) < 3:
        result.error = "API kataloğu beklenenden kısa"
        logger.warning("Torii kataloğu ayrıştırılamadı (%d model), mevcut liste korunuyor.", len(remote_models))
        return result

    defaults = settings_manager._load_defaults()
    default_models = list(defaults.get("translator_options") or [])
    default_fonts = list(defaults.get("font_options") or [])

    current_models = list(settings_manager.get("translator_options") or default_models)
    current_fonts = list(settings_manager.get("font_options") or default_fonts)

    models, added_models, removed_models = _merge_catalog(
        current_models, remote_models, default_models
    )
    fonts, added_fonts, _ = _merge_catalog(
        current_fonts, remote_fonts, default_fonts
    )

    original_selected = str(settings_manager.get("translator") or "").strip()
    selected = _REMOVED_TRANSLATORS.get(original_selected, original_selected)
    if selected and selected not in models and selected not in remote_models:
        # Kullanıcının özel modeli değilse ilk geçerli modele geç.
        if (
            original_selected in default_models
            or selected in default_models
            or original_selected in removed_models
            or selected in removed_models
        ):
            settings_manager.set("translator", models[0] if models else remote_models[0])

    settings_manager.set("translator_options", models)
    settings_manager.set("font_options", fonts)
    settings_manager.set("catalog_last_sync", time.strftime("%Y-%m-%d %H:%M:%S"))
    settings_manager.save()

    result.success = True
    result.models = models
    result.fonts = fonts
    result.added_models = added_models
    result.added_fonts = added_fonts
    result.removed_models = removed_models
    logger.info(
        "Torii kataloğu güncellendi: %d model, %d font (%d yeni model, %d yeni font).",
        len(models), len(fonts), len(added_models), len(added_fonts),
    )
    return result
