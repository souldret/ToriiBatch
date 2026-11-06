import asyncio
import base64
from pathlib import Path

from core.api_client import (
    ToriiAPIClient,
    _content_type_for,
    _normalize_context,
    _normalize_lang_list,
    _resolve_font_code,
)
from core.translator_engine import _sniff_format, _write_image


def test_font_codes() -> None:
    assert _resolve_font_code("NotoSans") == "noto"
    assert _resolve_font_code("KomikaJam") == "komika"
    assert _resolve_font_code("Mogul Irina") == "mogul"
    assert _resolve_font_code("RIDIBatang") == "ridi"
    assert _resolve_font_code("noto") == "noto"


def test_reconcile_replaces_removed_models() -> None:
    from core.settings_manager import SettingsManager

    defaults = {
        "translator": "gemini-3.1-flash-lite",
        "translator_options": [
            "gemini-3.1-flash-lite",
            "gpt-6-luna",
            "deepseek-v4-flash",
            "claude-sonnet-5.5",
            "gpt-6.1-sol",
            "claude-opus-5.5",
        ],
        "language_options": ["tr", "en"],
        "font_options": ["NotoSans"],
        "font": "NotoSans",
        "target_lang": "tr",
    }
    user = {
        "translator": "gpt-5.4",
        "translator_options": [
            "gemini-3.1-flash-lite",
            "gpt-5.6-luna",
            "gpt-5.4",
            "gpt-6-sol",
            "claude-sonnet-4.6",
            "claude-sonnet-5",
            "deepseek",
            "gemini-3.6-flash",
            "my-custom-model",
        ],
        "language_options": ["tr", "en", "xx-custom"],
        "font_options": ["NotoSans"],
        "font": "GoneFont",
        "target_lang": "tr",
        "min_font_size": 3,
        "reasoning_effort": "invalid",
    }
    merged = dict(defaults)
    merged.update(user)
    result = SettingsManager._reconcile_catalog(merged, defaults, user)
    assert result["translator"] == "gpt-6.1-sol"
    assert "gpt-5.4" not in result["translator_options"]
    assert "gpt-6-sol" not in result["translator_options"]
    assert "gpt-6.1-sol" in result["translator_options"]
    assert "gpt-6-luna" in result["translator_options"]
    assert "claude-sonnet-5.5" in result["translator_options"]
    assert "claude-sonnet-5" not in result["translator_options"]
    assert "claude-sonnet-4.6" not in result["translator_options"]
    assert "deepseek" not in result["translator_options"]
    assert "deepseek-v4-flash" in result["translator_options"]
    assert "my-custom-model" in result["translator_options"]
    assert "claude-opus-5.5" in result["translator_options"]
    assert "xx-custom" in result["language_options"]
    assert result["font"] == "NotoSans"
    assert result["min_font_size"] is None
    assert result["reasoning_effort"] == "none"


def test_reconcile_preserves_custom_selected_translator() -> None:
    from core.settings_manager import SettingsManager

    defaults = {
        "translator": "gemini-3.1-flash-lite",
        "translator_options": ["gemini-3.1-flash-lite", "gpt-6-luna"],
        "language_options": ["tr"],
        "font_options": ["NotoSans"],
        "font": "NotoSans",
        "target_lang": "tr",
    }
    user = {"translator": "my-openrouter-model"}
    merged = {**defaults, **user}
    result = SettingsManager._reconcile_catalog(merged, defaults, user)
    assert result["translator"] == "my-openrouter-model"
    assert "my-openrouter-model" in result["translator_options"]


def test_content_type_avif() -> None:
    assert _content_type_for(Path("page.AVIF")) == "image/avif"
    assert _content_type_for(Path("page.png")) == "image/png"


def test_write_image_strips_data_uri(tmp_path: Path) -> None:
    raw = b"hello-image"
    b64 = "data:image/png;base64," + base64.b64encode(raw).decode()
    dest = tmp_path / "out.png"
    assert _write_image(b64, dest)
    assert dest.read_bytes() == raw
    assert not dest.with_suffix(".png.tmp").exists()


def test_write_image_converts_png_to_jpg_and_webp(tmp_path: Path) -> None:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage

    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor(200, 100, 50, 128))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    png_b64 = "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()

    jpg_path = tmp_path / "page.jpg"
    webp_path = tmp_path / "page.webp"
    assert _write_image(png_b64, jpg_path)
    assert _write_image(png_b64, webp_path)
    assert _sniff_format(jpg_path.read_bytes()) == "jpeg"
    assert _sniff_format(webp_path.read_bytes()) == "webp"


def test_finish_request_reads_validation_detail() -> None:
    client = ToriiAPIClient("tt_test")
    body = {"detail": [{"msg": "translator is required"}]}
    result = client._finish_request(422, body, {})
    assert result["success"] is False
    assert "translator is required" in result["error"]


def test_finish_request_respects_success_header_false() -> None:
    client = ToriiAPIClient("tt_test")
    result = client._finish_request(
        200, {"message": "Insufficient credits"}, {"success": "false", "credits": "0"}
    )
    assert result["success"] is False
    assert "Insufficient credits" in result["error"]
    assert result["credits_remaining"] == 0.0


def test_finish_request_image_payload() -> None:
    client = ToriiAPIClient("tt_test")
    body = {
        "image": "data:image/png;base64,abc",
        "inpainted": "data:image/png;base64,def",
        "context": "next",
        "text": [{"text": "Merhaba"}],
        "metadata": {"credits_used": 1},
    }
    result = client._finish_request(200, body, {"credits": "12.5"})
    assert result["success"] is True
    assert result["image_b64"].endswith("abc")
    assert result["credits_remaining"] == 12.5
    assert result["context"] == "next"
    assert result["metadata"]["credits_used"] == 1


def test_context_truncates_keep_tail_and_normalizes_empty() -> None:
    assert _normalize_context(None) == "None"
    assert _normalize_context("") == "None"
    long_ctx = "a" * 5000 + "b" * 10000
    assert _normalize_context(long_ctx) == "b" * 10000


def test_lang_whitelist_normalization() -> None:
    assert _normalize_lang_list("JA, ko; ja, zh-cn ") == ["ja", "ko", "zh-cn"]
    assert _normalize_lang_list([]) == []


def test_share_credits_rejects_bad_input() -> None:
    client = ToriiAPIClient("tt_test")
    bad_email = asyncio.run(client.share_credits("not-an-email", 1))
    bad_amount = asyncio.run(client.share_credits("a@b.com", 0))
    fractional = asyncio.run(client.share_credits("a@b.com", 1.5))
    assert bad_email["success"] is False
    assert bad_amount["success"] is False
    assert fractional["success"] is False


def test_byok_validation_and_form_fields() -> None:
    import aiohttp

    client = ToriiAPIClient(
        "tt_test",
        byok_provider="openai",
        byok_key="sk-test",
        byok_fallback=True,
    )
    assert client._build_headers() == {"Authorization": "Bearer tt_test"}
    assert "x-byok-openai" not in client._build_headers()
    assert client.byok_error() is None

    form = aiohttp.FormData()
    client._apply_byok(form)
    fields = {f[0]["name"]: f[2] for f in form._fields}
    assert fields["x-byok-openai"] == "sk-test"
    assert fields["x-byok-fallback"] == "true"

    missing_key = ToriiAPIClient("tt_test", byok_provider="openai", byok_key="")
    assert missing_key.byok_error() is not None

    local_ok = ToriiAPIClient(
        "tt_test", byok_provider="local", byok_local_url="https://example.com/v1"
    )
    assert local_ok.byok_error() is None
    local_missing = ToriiAPIClient("tt_test", byok_provider="local")
    assert local_missing.byok_error() is not None


def test_typeset_limits_validation(tmp_path: Path) -> None:
    img = tmp_path / "clean.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    client = ToriiAPIClient("tt_test")
    too_many = [{"x": 1, "y": 1, "width": 10, "height": 10, "text": "a"}] * 501
    res = asyncio.run(client.typeset_image(str(img), too_many))
    assert res["success"] is False
    assert "500" in res["error"]
