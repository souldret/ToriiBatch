import base64
from pathlib import Path

from core.api_client import _resolve_font_code
from core.translator_engine import _write_image


def test_font_codes() -> None:
    assert _resolve_font_code("NotoSans") == "noto"
    assert _resolve_font_code("KomikaJam") == "komika"
    assert _resolve_font_code("noto") == "noto"


def test_write_image_strips_data_uri(tmp_path: Path) -> None:
    raw = b"hello-image"
    b64 = "data:image/png;base64," + base64.b64encode(raw).decode()
    dest = tmp_path / "out.png"
    assert _write_image(b64, dest)
    assert dest.read_bytes() == raw
    assert not dest.with_suffix(".png.tmp").exists()
