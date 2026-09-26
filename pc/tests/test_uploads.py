"""Приём перетащенных в чат файлов."""

from __future__ import annotations

import io

import pytest

from server.uploads import _safe_name, save_uploads


class FakeUpload:
    """Мини-замена fastapi.UploadFile для тестов."""

    def __init__(self, filename: str, data: bytes) -> None:
        self.filename = filename
        self._buf = io.BytesIO(data)
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    async def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------- имена


@pytest.mark.parametrize(
    ("raw", "check"),
    [
        ("../../etc/passwd", lambda n: "/" not in n and ".." not in n),
        ("отчёт за март.pdf", lambda n: n.endswith(".pdf")),
        ("", lambda n: bool(n)),
        ("C:\\Windows\\evil.exe", lambda n: "\\" not in n),
    ],
)
def test_safe_name_strips_paths(raw, check):
    assert check(_safe_name(raw))


def test_safe_name_recovers_utf8_mojibake():
    # Multipart отдаёт UTF-8 имя как latin-1 → «Ð¿Ð¾ÑÑ…». Должны восстановить.
    mojibake = "портал тест.docx".encode().decode("latin-1")
    assert _safe_name(mojibake) == "портал тест.docx"


def test_safe_name_keeps_already_correct_cyrillic():
    # Корректную кириллицу не трогаем (в latin-1 не кодируется — остаётся как есть).
    assert _safe_name("отчёт.pdf") == "отчёт.pdf"


def test_safe_name_keeps_plain_ascii():
    assert _safe_name("report_v2.txt") == "report_v2.txt"


# --------------------------------------------------------------- сохранение


async def test_saves_files_and_returns_paths(settings):
    uploads = [FakeUpload("фото.png", b"\x89PNG data"), FakeUpload("data.csv", b"a,b\n1,2")]
    paths, errors = await save_uploads(uploads, settings.data_dir)

    assert not errors
    assert len(paths) == 2
    from pathlib import Path

    assert Path(paths[0]).read_bytes() == b"\x89PNG data"  # noqa: ASYNC240
    # Имя файла сохранено в названии (с префиксом от коллизий).
    assert "фото.png" in Path(paths[0]).name
    assert all(u.closed for u in uploads)


async def test_same_name_does_not_overwrite(settings):
    a, b = FakeUpload("файл.txt", b"AAA"), FakeUpload("файл.txt", b"BBB")
    paths, _ = await save_uploads([a, b], settings.data_dir)
    assert len(paths) == 2
    assert paths[0] != paths[1]  # префиксы разные


async def test_empty_file_is_rejected(settings):
    paths, errors = await save_uploads([FakeUpload("пусто.txt", b"")], settings.data_dir)
    assert paths == []
    assert any("пуст" in e for e in errors)


async def test_oversized_file_is_rejected(settings, monkeypatch):
    import server.uploads as up

    monkeypatch.setattr(up, "MAX_UPLOAD_BYTES", 10)
    paths, errors = await save_uploads([FakeUpload("big.bin", b"x" * 100)], settings.data_dir)
    assert paths == []
    assert any("лимит" in e for e in errors)
    # Частично записанный файл убран.
    folder = settings.data_dir / "attachments"
    assert not any(folder.glob("*big.bin"))


async def test_uploaded_file_becomes_a_usable_attachment(settings):
    """Путь из загрузки должен читаться системой вложений."""
    from core.attachments import collect

    paths, _ = await save_uploads([FakeUpload("заметка.txt", "секрет: 42".encode())], settings.data_dir)
    result = collect(paths)
    assert result.items
    assert "секрет: 42" in result.context()
