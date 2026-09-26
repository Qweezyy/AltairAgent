"""Меню композера: вложения, режим веб-поиска, исследование, навыки."""

from __future__ import annotations

import zipfile

import pytest

from core import attachments
from core.agent.run_options import WEB_TOOLS, RunOptions
from core.agent.session import RUN_NOTE_PREFIX, Session
from core.tools import build_default_registry

# ------------------------------------------------------------- вложения


def test_image_becomes_a_message_part(tmp_path):
    image = tmp_path / "снимок.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)

    result = attachments.collect([str(image)])
    part = result.parts()[0]

    assert result.has_media
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_video_uses_its_own_part_type(tmp_path):
    video = tmp_path / "ролик.mp4"
    video.write_bytes(b"\x00" * 64)

    part = attachments.collect([str(video)]).parts()[0]
    assert part["type"] == "video_url"
    assert part["video_url"]["url"].startswith("data:video/mp4;base64,")


def test_huge_media_is_refused_with_an_explanation(tmp_path, monkeypatch):
    """Молча выкинуть файл нельзя: пользователь решит, что агент его увидел."""
    monkeypatch.setattr(attachments, "MAX_IMAGE_MB", 0.0001)
    image = tmp_path / "большая.png"
    image.write_bytes(b"0" * 5000)

    result = attachments.collect([str(image)])
    assert result.parts() == []
    assert "лимит" in result.items[0].note


def test_text_file_content_goes_into_context(tmp_path):
    source = tmp_path / "код.py"
    source.write_text("print('привет')\n", encoding="utf-8")

    context = attachments.collect([str(source)]).context()
    assert "print('привет')" in context
    assert str(source) in context, "агент должен знать путь, чтобы дочитать файл"


def test_archive_is_listed_not_unpacked(tmp_path):
    archive = tmp_path / "проект.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.md", "текст")
        bundle.writestr("src/main.py", "код")

    context = attachments.collect([str(archive)]).context()
    assert "src/main.py" in context
    assert "readme.md" in context


def test_folder_is_listed_with_its_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_bytes(b"0")

    context = attachments.collect([str(tmp_path)]).context()
    assert "a.txt" in context
    assert "junk.pyc" not in context, "служебные папки только зашумляют контекст"


def test_missing_file_is_reported_not_swallowed(tmp_path):
    result = attachments.collect([str(tmp_path / "нет.txt")])
    assert result.items == []
    assert "не найден" in result.errors[0]


def test_one_bad_attachment_does_not_lose_the_others(tmp_path):
    good = tmp_path / "есть.txt"
    good.write_text("данные", encoding="utf-8")

    result = attachments.collect([str(tmp_path / "нет.txt"), str(good)])
    assert len(result.items) == 1
    assert len(result.errors) == 1


# ------------------------------------------------------ режимы и навыки


def test_web_off_removes_the_tools_not_just_asks_nicely():
    """Запрет в промпте модель может обойти, отсутствие инструмента — нет."""
    registry = build_default_registry()
    limited = RunOptions(web_mode="off").filter_registry(registry)

    assert not set(WEB_TOOLS) & set(limited.names())
    assert set(WEB_TOOLS) <= set(registry.names()), "исходный реестр трогать нельзя"


def test_auto_mode_keeps_the_registry_untouched():
    registry = build_default_registry()
    assert RunOptions().filter_registry(registry) is registry


def test_forced_search_tells_the_agent_to_search():
    notes = RunOptions(web_mode="force").notes()
    assert any("web_search" in note for note in notes)


def test_deep_research_wins_over_disabled_web():
    """Исследование без интернета невозможно — противоречие снимается сразу."""
    options = RunOptions.from_message({"deep_research": True, "web_mode": "off"})
    assert options.web_mode == "auto"


def test_unknown_web_mode_falls_back_to_auto():
    assert RunOptions.from_message({"web_mode": "чепуха"}).web_mode == "auto"


def test_chosen_skill_is_inlined_whole(settings, tmp_path):
    from core.skills.manager import SkillManager

    manager = SkillManager(settings)
    manager.create("reporting", "как писать отчёты", "Пиши коротко и по делу.")

    notes = RunOptions(skills=["reporting"]).notes(manager)
    assert any("Пиши коротко и по делу." in note for note in notes)


def test_missing_skill_is_reported_not_ignored(settings):
    from core.skills.manager import SkillManager

    notes = RunOptions(skills=["несуществующий"]).notes(SkillManager(settings))
    assert any("несуществующий" in note for note in notes)


# ------------------------------------------------------- заметки запуска


def test_run_notes_do_not_leak_into_the_next_task():
    """Выключенный однажды поиск не должен остаться выключенным навсегда."""
    session = Session()
    session.add_user("первая задача")
    session.add_run_note("Поиск отключён.")

    removed = session.clear_run_notes()

    assert removed == 1
    assert all(RUN_NOTE_PREFIX not in str(m.get("content")) for m in session.messages)
    assert any(m["role"] == "user" for m in session.messages), "обычные сообщения не трогаем"


def test_media_message_keeps_text_first():
    session = Session()
    session.add_user("опиши фото", parts=[{"type": "image_url", "image_url": {"url": "data:..."}}])

    content = session.messages[-1]["content"]
    assert content[0] == {"type": "text", "text": "опиши фото"}
    assert content[1]["type"] == "image_url"


def test_session_remembers_that_it_holds_media():
    """Картинка остаётся в диалоге — значит и следующий ответ нужен от vision-модели."""
    session = Session()
    assert not session.contains_media()

    session.add_user("фото", parts=[{"type": "image_url", "image_url": {"url": "data:..."}}])
    session.add_user("а теперь просто вопрос")

    assert session.contains_media()


def test_media_does_not_blow_up_the_token_estimate():
    """По длине base64 вложение «весило» бы миллионы токенов и сносило историю."""
    session = Session()
    huge = "x" * 4_000_000
    session.add_user("вопрос", parts=[{"type": "image_url", "image_url": {"url": huge}}])

    assert session.token_estimate() < 10_000


@pytest.mark.parametrize("mode", ["auto", "force", "off"])
def test_every_mode_survives_a_round_trip(mode):
    assert RunOptions.from_message({"web_mode": mode}).web_mode in ("auto", mode)
