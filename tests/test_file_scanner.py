from pathlib import Path

from core.file_scanner import ChapterInfo, filter_already_translated, scan_root_folder


def test_scan_root_as_single_chapter(tmp_path: Path) -> None:
    (tmp_path / "01.png").write_bytes(b"x")
    (tmp_path / "02.jpg").write_bytes(b"x")
    chapters = scan_root_folder(str(tmp_path))
    assert len(chapters) == 1
    assert chapters[0].page_count == 2


def test_scan_subfolders(tmp_path: Path) -> None:
    ch = tmp_path / "Bolum 1"
    ch.mkdir()
    (ch / "page.png").write_bytes(b"x")
    empty = tmp_path / "empty"
    empty.mkdir()
    chapters = scan_root_folder(str(tmp_path))
    assert len(chapters) == 1
    assert chapters[0].name == "Bolum 1"


def test_resume_ignores_original_and_inpainted(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "01.png").write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "01_original.png").write_bytes(b"x")
    (out / "01_inpainted.png").write_bytes(b"x")
    chapter = ChapterInfo(name="src", path=str(src), image_paths=[str(src / "01.png")], page_count=1)
    filtered = filter_already_translated(chapter, str(out), str(tmp_path))
    assert filtered.page_count == 1
