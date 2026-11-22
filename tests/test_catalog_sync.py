"""Torii API kataloğu senkronizasyonu testleri."""

from core import catalog_sync
from core.catalog_sync import _merge_catalog, parse_catalog, sync_catalog


SAMPLE_HTML = """
<html><body>
<select id="target_lang_select"><option value="tr">Turkish</option></select>
<select id="translator_select" class="hidden">
  <option value="gpt-6-luna">GPT-6 Luna</option>
  <option value="claude-haiku-5.5">Claude Haiku 5.5</option>
  <option selected value="gemini-3.1-flash-lite">Gemini 3.1 Flash Lite</option>
  <option value="gpt-6.1-terra">GPT-6.1 Terra</option>
  <option value="gemini-4-carbon">Gemini 4 Carbon</option>
</select>
<select id="font_select" class="hidden">
  <option value="noto">NotoSans (150 languages)</option>
  <option value="alklife">ALKLife (24 languages)</option>
  <option value="baron">Baron (106 languages)</option>
</select>
</body></html>
"""


class FakeSettings:
    """sync_catalog için hafif ayar yöneticisi."""

    def __init__(self, data: dict) -> None:
        self.data = dict(data)
        self.saved = 0

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.saved += 1

    def _load_defaults(self) -> dict:
        return {
            "translator_options": ["gpt-6-luna", "gemini-3.1-flash-lite", "deepseek-v4-flash"],
            "font_options": ["NotoSans", "Iansui"],
        }


def test_parse_catalog_extracts_models_and_fonts() -> None:
    models, fonts = parse_catalog(SAMPLE_HTML)
    assert models == [
        "gpt-6-luna", "claude-haiku-5.5", "gemini-3.1-flash-lite",
        "gpt-6.1-terra", "gemini-4-carbon",
    ]
    assert fonts == ["NotoSans", "ALKLife", "Baron"]


def test_parse_catalog_empty_on_unexpected_html() -> None:
    assert parse_catalog("<html><body>no selects</body></html>") == ([], [])


def test_merge_keeps_custom_and_drops_stale_defaults() -> None:
    merged, added, removed = _merge_catalog(
        current=["gemini-3.1-flash-lite", "deepseek-v4-flash", "my-custom-model"],
        remote=["gpt-6-luna", "gemini-3.1-flash-lite", "gpt-6.1-terra"],
        defaults=["gemini-3.1-flash-lite", "deepseek-v4-flash"],
    )
    assert merged == ["gpt-6-luna", "gemini-3.1-flash-lite", "gpt-6.1-terra", "my-custom-model"]
    assert added == ["gpt-6-luna", "gpt-6.1-terra"]
    assert removed == ["deepseek-v4-flash"]


def test_sync_catalog_updates_settings(monkeypatch) -> None:
    sm = FakeSettings({
        "translator": "deepseek-v4-flash",
        "translator_options": ["gemini-3.1-flash-lite", "deepseek-v4-flash"],
        "font_options": ["NotoSans", "Iansui"],
    })
    monkeypatch.setattr(catalog_sync, "fetch_catalog", lambda *a, **kw: (
        ["gpt-6-luna", "claude-haiku-5.5", "gemini-3.1-flash-lite", "gpt-6.1-terra"],
        ["NotoSans", "ALKLife", "Baron"],
    ))

    result = sync_catalog(sm)
    assert result.success is True
    assert "claude-haiku-5.5" in sm.data["translator_options"]
    assert "gpt-6.1-terra" in sm.data["translator_options"]
    assert "deepseek-v4-flash" not in sm.data["translator_options"]
    assert sm.data["translator"] == "gpt-6-luna"  # eski seçili model değiştirildi
    assert "ALKLife" in sm.data["font_options"]
    assert "Baron" in sm.data["font_options"]
    assert sm.data["catalog_last_sync"]
    assert sm.saved == 1


def test_sync_catalog_keeps_custom_selected_model(monkeypatch) -> None:
    sm = FakeSettings({
        "translator": "my-openrouter-model",
        "translator_options": ["gemini-3.1-flash-lite", "my-openrouter-model"],
        "font_options": ["NotoSans"],
    })
    monkeypatch.setattr(catalog_sync, "fetch_catalog", lambda *a, **kw: (
        ["gpt-6-luna", "gemini-3.1-flash-lite", "gpt-6.1-terra"], ["NotoSans"],
    ))
    result = sync_catalog(sm)
    assert result.success is True
    assert sm.data["translator"] == "my-openrouter-model"
    assert "my-openrouter-model" in sm.data["translator_options"]


def test_sync_catalog_network_error_keeps_catalog(monkeypatch) -> None:
    sm = FakeSettings({
        "translator": "gemini-3.1-flash-lite",
        "translator_options": ["gemini-3.1-flash-lite"],
        "font_options": ["NotoSans"],
    })

    def _boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(catalog_sync, "fetch_catalog", _boom)
    result = sync_catalog(sm)
    assert result.success is False
    assert "network down" in result.error
    assert sm.data["translator_options"] == ["gemini-3.1-flash-lite"]
    assert sm.saved == 0
