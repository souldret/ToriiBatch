"""
file_scanner.py - Klasör ve görsel dosyası tarayıcı.

Sorumluluğu:
- Seçilen ana klasördeki alt klasörleri (bölümleri) listelemek.
- Her bölümdeki görsel dosyalarını natsort ile doğru sırayla döndürmek.
- Desteklenen görsel uzantılarını filtrelemek.
- Çıktı klasörü yollarını orijinal yapıyla eşleşecek şekilde üretmek.
- Zaten çevrilmiş sayfaları tespit edip atlama (resume) desteği sunmak.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from natsort import natsorted

logger = logging.getLogger(__name__)

# Desteklenen görsel uzantıları (küçük harf)
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
)


# ---------------------------------------------------------------------------
# Veri yapısı
# ---------------------------------------------------------------------------

@dataclass
class ChapterInfo:
    """
    Tek bir bölümü (alt klasör veya kök klasör) temsil eder.

    Alanlar
    -------
    name : str
        Bölüm klasörünün adı (görüntüleme ve çıktı yolu hesaplama için).
    path : str
        Bölüm klasörünün tam yolu.
    image_paths : list[str]
        Bölümdeki görsel dosyalarının tam yolları — natsort ile sıralanmış.
    page_count : int
        Toplam görsel sayısı (``len(image_paths)`` ile eşdeğer).
    status : str
        Bölümün çeviri durumu.
        Geçerli değerler: ``"pending"``, ``"in_progress"``, ``"done"``,
        ``"error"``, ``"skipped"``.
    progress : int
        0–100 aralığında ilerleme yüzdesi.
    error_message : str | None
        Son hata mesajı; henüz hata yoksa ``None``.
    """

    name: str
    path: str
    image_paths: list[str] = field(default_factory=list)
    page_count: int = 0
    status: str = "pending"
    progress: int = 0
    error_message: str | None = None

    def __post_init__(self) -> None:
        # page_count her zaman image_paths uzunluğuyla senkronize olsun
        if self.page_count == 0 and self.image_paths:
            self.page_count = len(self.image_paths)


# ---------------------------------------------------------------------------
# Dahili yardımcılar
# ---------------------------------------------------------------------------

def _collect_images(directory: Path) -> list[str]:
    """
    Verilen klasördeki desteklenen görsel dosyalarını natsort ile sıralar.

    Alt klasörlere inilmez; yalnızca doğrudan alt dosyalar taranır.

    Parametreler
    ------------
    directory : Path
        Taranacak klasör.

    Dönüş
    -----
    list[str]
        Tam dosya yollarından oluşan, natsort ile sıralanmış liste.
        Görsel bulunamazsa boş liste döner.
    """
    try:
        files = [
            p for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
    except OSError as exc:
        logger.warning("Klasör okunamadı (%s): %s", directory, exc)
        return []
    return [str(p) for p in natsorted(files, key=lambda p: p.name)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_root_folder(root_path: str) -> list[ChapterInfo]:
    """
    Belirtilen kök klasördeki bölümleri ve sayfaları tarar.

    Davranış
    --------
    - Kök klasörün altında alt klasör **varsa**: her alt klasör bir bölüm.
    - Kök klasörün altında alt klasör **yoksa** (doğrudan görseller varsa):
      kök klasörün kendisi tek bölüm olarak değerlendirilir.
    - Görsel içermeyen alt klasörler atlanır ve uyarı loglanır.
    - Bölümler natsort ile sıralanır (örn. "Bölüm 2" < "Bölüm 10").

    Parametreler
    ------------
    root_path : str
        Bölüm alt klasörlerini içeren kök klasörün tam yolu.

    Dönüş
    -----
    list[ChapterInfo]
        Natsort ile sıralanmış bölüm listesi.

    Yükseltir
    ---------
    FileNotFoundError
        ``root_path`` mevcut değilse.
    NotADirectoryError
        ``root_path`` bir klasör değilse.
    """
    root = Path(root_path)

    if not root.exists():
        raise FileNotFoundError(f"Kök klasör bulunamadı: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Belirtilen yol bir klasör değil: {root}")

    # Alt klasörleri listele
    try:
        subdirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError as exc:
        raise FileNotFoundError(f"Kök klasör okunamadı: {root}") from exc

    chapters: list[ChapterInfo] = []

    if subdirs:
        # Alt klasör var → her biri bir bölüm
        subdirs_sorted = natsorted(subdirs, key=lambda p: p.name)
        for subdir in subdirs_sorted:
            images = _collect_images(subdir)
            if not images:
                logger.warning(
                    "Görsel bulunamadı, bölüm atlanıyor: %s", subdir.name
                )
                continue
            chapters.append(
                ChapterInfo(
                    name=subdir.name,
                    path=str(subdir),
                    image_paths=images,
                    page_count=len(images),
                )
            )
            logger.debug(
                "Bölüm tarandı: %s (%d sayfa)", subdir.name, len(images)
            )
    else:
        # Alt klasör yok → kök klasörün kendisi tek bölüm
        images = _collect_images(root)
        if images:
            chapters.append(
                ChapterInfo(
                    name=root.name,
                    path=str(root),
                    image_paths=images,
                    page_count=len(images),
                )
            )
            logger.debug(
                "Kök klasör tek bölüm olarak tarandı: %s (%d sayfa)",
                root.name, len(images),
            )
        else:
            logger.warning("Kök klasörde hiç görsel bulunamadı: %s", root)

    logger.info(
        "Tarama tamamlandı: %d bölüm, toplam %d sayfa.",
        len(chapters),
        sum(c.page_count for c in chapters),
    )
    return chapters


def estimate_total_images(chapters: list[ChapterInfo]) -> int:
    """
    Bölüm listesindeki toplam görsel (sayfa) sayısını döndürür.

    İlerleme çubuğunun maksimum değerini belirlemek için kullanılır.

    Parametreler
    ------------
    chapters : list[ChapterInfo]
        ``scan_root_folder`` tarafından döndürülen bölüm listesi.

    Dönüş
    -----
    int
        Tüm bölümlerdeki toplam sayfa sayısı.
    """
    return sum(c.page_count for c in chapters)


def build_output_path(
    chapter: ChapterInfo,
    root_path: str,
    output_root: str,
) -> str:
    """
    Bölümün çıktı klasör yolunu üretir; orijinal klasör yapısını korur.

    Örnek
    -----
    Kök: ``/manga/seri``
    Bölüm yolu: ``/manga/seri/Bolum_01``
    Çıktı kökü: ``/cikti/seri``
    → Sonuç: ``/cikti/seri/Bolum_01``

    Parametreler
    ------------
    chapter : ChapterInfo
        Çıktı yolu hesaplanacak bölüm.
    root_path : str
        Kaynak kök klasörün tam yolu.
    output_root : str
        Çevrilmiş görsellerin yazılacağı hedef kök klasörün tam yolu.

    Dönüş
    -----
    str
        Bölüme ait çıktı klasörünün tam yolu.
    """
    chapter_path = Path(chapter.path)
    root = Path(root_path)

    try:
        # Göreli yolu hesapla (kök ile bölüm aynıysa relative_path = ".")
        relative = chapter_path.relative_to(root)
    except ValueError:
        # chapter.path, root_path altında değilse bölüm adını kullan
        relative = Path(chapter.name)

    output_dir = Path(output_root) / relative
    return str(output_dir)


def filter_already_translated(
    chapter: ChapterInfo,
    output_root: str,
    source_root: str = "",
) -> ChapterInfo:
    """
    Çıktı klasöründe zaten mevcut olan (tamamlanmış) sayfaları kaldırır.

    "Kaldığı yerden devam et" özelliği için kullanılır.
    Bir sayfanın tamamlandığı kabul edilir ancak ve ancak çıktı klasöründe
    aynı dosya adıyla (uzantısından bağımsız olarak **base name**) bir görsel
    dosyası mevcutsa.

    Filtre sonrası kalan sayfalar yoksa ``status`` alanı ``"skipped"`` olarak
    ayarlanır; aksi takdirde ``"pending"`` olarak kalır.

    Parametreler
    ------------
    chapter : ChapterInfo
        Filtrelenecek bölüm (bu nesne **değiştirilmez**, yeni bir kopya döner).
    output_root : str
        Çevrilmiş görsellerin bulunduğu kök klasör.
    source_root : str
        Kaynak kök klasör; verilirse ``build_output_path`` ile tutarlı
        çıktı yolu hesaplanır.  Boşsa bölüm adı üzerinden tahmin yapılır.

    Dönüş
    -----
    ChapterInfo
        Çevrilmemiş sayfaları içeren yeni ``ChapterInfo`` nesnesi.
        Tüm sayfalar zaten çevrilmişse ``image_paths`` boş, ``status``
        ``"skipped"`` olur.
    """
    if source_root:
        output_dir = Path(build_output_path(chapter, source_root, output_root))
    else:
        output_dir = Path(output_root) / Path(chapter.path).name

    if not output_dir.is_dir():
        # Çıktı klasörü henüz yok — tüm sayfalar işlenecek
        return ChapterInfo(
            name=chapter.name,
            path=chapter.path,
            image_paths=list(chapter.image_paths),
            page_count=chapter.page_count,
            status=chapter.status,
            progress=chapter.progress,
            error_message=chapter.error_message,
        )

    # Çıktı klasöründeki mevcut base name'leri (uzantısız) topla
    existing_bases: set[str] = {
        p.stem.lower()
        for p in output_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    }

    remaining: list[str] = [
        img for img in chapter.image_paths
        if Path(img).stem.lower() not in existing_bases
    ]

    skipped_count = chapter.page_count - len(remaining)
    if skipped_count > 0:
        logger.info(
            "[%s] %d sayfa zaten çevrilmiş, atlanıyor. Kalan: %d sayfa.",
            chapter.name, skipped_count, len(remaining),
        )

    new_status = "skipped" if not remaining else chapter.status
    return ChapterInfo(
        name=chapter.name,
        path=chapter.path,
        image_paths=remaining,
        page_count=len(remaining),
        status=new_status,
        progress=chapter.progress,
        error_message=chapter.error_message,
    )