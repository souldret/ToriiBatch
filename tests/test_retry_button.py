"""Kalıcı 'Yeniden Dene' butonu ve hatalı sayfa yeniden deneme testleri."""

import pytest

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_retry_button_persists_and_retries_only_failed_pages(app, monkeypatch):
    from ui.main_window import MainWindow

    window = MainWindow()
    try:
        # Başlangıçta buton gizli
        assert window._retry_btn.isHidden()

        # İki hata kaydet
        window._on_error_occurred("Bölüm 1", "API hatası", "C:/manga/Bölüm 1/001.png")
        window._on_error_occurred("Bölüm 1", "API hatası", "C:/manga/Bölüm 1/003.png")
        window._on_error_occurred("Bölüm 2", "API hatası", "C:/manga/Bölüm 2/005.png")

        assert window._retry_btn.text() == "Yeniden Dene (3)"

        # İş bitince buton görünür kalmalı
        window._set_running_state(False)
        assert not window._retry_btn.isHidden()

        # Çalışırken gizlenmeli
        window._set_running_state(True)
        assert window._retry_btn.isHidden()
        window._set_running_state(False)
        assert not window._retry_btn.isHidden()

        # Tıklayınca yalnızca hatalı sayfalar engine'e gönderilmeli
        captured = {}

        def _fake_start_batch(chapters, settings, output):
            captured["chapters"] = chapters
            captured["output"] = output

        monkeypatch.setattr(window._engine, "start_batch", _fake_start_batch)
        window._sm.set("output_folder", "C:/manga/out")
        window._retry_failed_pages()

        chapters = captured["chapters"]
        assert len(chapters) == 2
        paths = [p for ch in chapters for p in ch.image_paths]
        assert paths == [
            "C:/manga/Bölüm 1/001.png",
            "C:/manga/Bölüm 1/003.png",
            "C:/manga/Bölüm 2/005.png",
        ]
        assert all(ch.status == "pending" for ch in chapters)
        assert window._failed_page_paths == {}
        assert window._retry_btn.isHidden()  # yeniden deneme başladı
    finally:
        window.close()
