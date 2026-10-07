import base64
from pathlib import Path

from core.api_client import _content_type_for, _resolve_font_code
from core.translator_engine import _write_image


def test_font_codes() -> None:
    assert _resolve_font_code("NotoSans") == "noto"
    assert _resolve_font_code("KomikaJam") == "komika"
    assert _resolve_font_code("noto") == "noto"


def test_reconcile_replaces_removed_models() -> None:
    from core.settings_manager import SettingsManager

    defaults = {
        "translator": "gemini-3.1-flash-lite",
        "translator_options": [
            "gemini-3.1-flash-lite",
            "gpt-6-luna",
            "deepseek",
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
            "claude-sonnet-4.6",
            "gemini-3.6-flash",
            "my-custom-model",
        ],
        "language_options": ["tr", "en", "xx-custom"],
        "font_options": ["NotoSans"],
        "font": "GoneFont",
        "target_lang": "tr",
    }
    merged = dict(defaults)
    merged.update(user)
    result = SettingsManager._reconcile_catalog(merged, defaults, user)
    assert result["translator"] == "gpt-6-sol"
    assert "gpt-5.4" not in result["translator_options"]
    assert "gpt-6-luna" in result["translator_options"]
    assert "claude-sonnet-5" in result["translator_options"]
    assert "claude-sonnet-4.6" not in result["translator_options"]
    assert "my-custom-model" in result["translator_options"]
    assert "claude-opus-5.5" in result["translator_options"]
    assert "xx-custom" in result["language_options"]
    assert result["font"] == "NotoSans"


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


def test_finish_request_reads_validation_detail() -> None:
    from core.api_client import ToriiAPIClient

    client = ToriiAPIClient("tt_test")
    body = {"detail": [{"msg": "translator is required"}]}
    result = client._finish_request(422, body, {})
    assert result["success"] is False
    assert "translator is required" in result["error"]


def test_finish_request_image_payload() -> None:
    from core.api_client import ToriiAPIClient

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


def test_share_credits_rejects_bad_input() -> None:
    import asyncio
    from core.api_client import ToriiAPIClient

    client = ToriiAPIClient("tt_test")
    bad_email = asyncio.run(client.share_credits("not-an-email", 1))
    bad_amount = asyncio.run(client.share_credits("a@b.com", 0))
    assert bad_email["success"] is False
    assert bad_amount["success"] is False


def test_byok_fields_are_form_data() -> None:
    from core.api_client import ToriiAPIClient

    client = ToriiAPIClient(
        "tt_test",
        byok_provider="openai",
        byok_key="sk-test",
    )
    assert client._build_headers() == {"Authorization": "Bearer tt_test"}
    assert "x-byok-openai" not in client._build_headers()
