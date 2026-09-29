"""The terminal's own texts, in English and Russian (the server's texts follow `ui_lang`)."""

from __future__ import annotations

import locale
import os

TEXTS: dict[str, dict[str, str]] = {
    "starting": {"en": "Starting the Altair backend…", "ru": "Запускаю бэкенд Altair…"},
    "joined": {"en": "Connected to the running Altair (the same chats as in the window).",
               "ru": "Подключился к запущенному Altair (те же чаты, что в окне)."},
    "no_backend": {"en": "Could not start the backend: {error}", "ru": "Не удалось запустить бэкенд: {error}"},
    "chat": {"en": "Chat: {title}  ·  {id}", "ru": "Чат: {title}  ·  {id}"},
    "folder": {"en": "Folder: {path}", "ru": "Папка: {path}"},
    "mode": {"en": "Approval mode: {mode}", "ru": "Режим подтверждений: {mode}"},
    "help": {"en": ("Type a task and press Enter. While the agent works, a new line goes to it as a hint. "
                    "Ctrl+C stops the task, twice exits. Commands: /new, /chats, /resume <id or title>, "
                    "/mode manual|auto|bypass, /model <name>, /stop, /exit."),
             "ru": ("Напишите задачу и нажмите Enter. Пока агент работает, новая строка уходит ему как "
                    "уточнение. Ctrl+C останавливает задачу, дважды — выход. Команды: /new, /chats, "
                    "/resume <id или название>, /mode manual|auto|bypass, /model <имя>, /stop, /exit.")},
    "prompt": {"en": "you", "ru": "вы"},
    "stopping": {"en": "Stopping…", "ru": "Останавливаю…"},
    "again_to_exit": {"en": "Press Ctrl+C again to exit.", "ru": "Нажмите Ctrl+C ещё раз, чтобы выйти."},
    "steering": {"en": "↳ passed to the agent", "ru": "↳ передано агенту"},
    "approval": {"en": "Allow {name}? {reason}", "ru": "Разрешить {name}? {reason}"},
    "approval_keys": {"en": "[y] once  [p] always in this folder  [g] always  [n] no",
                      "ru": "[y] один раз  [p] всегда в этой папке  [g] всегда  [n] нет"},
    "denied_no_tty": {"en": "Denied {name}: nobody to ask (not a terminal). Use --mode or run interactively.",
                      "ru": "Отклонено {name}: некого спросить (не терминал). Задайте --mode или запустите интерактивно."},
    "question_pick": {"en": "Pick (number, several with commas, or your own text): ",
                      "ru": "Выберите (номер, несколько через запятую или свой текст): "},
    "done": {"en": "done in {sec} s · {steps} steps{cost}", "ru": "готово за {sec} с · шагов: {steps}{cost}"},
    "failed": {"en": "Failed: {message}", "ru": "Ошибка: {message}"},
    "cancelled": {"en": "Stopped.", "ru": "Остановлено."},
    "no_chats": {"en": "No chats yet.", "ru": "Чатов пока нет."},
    "not_found": {"en": "No chat matches '{query}'.", "ru": "Нет чата по запросу «{query}»."},
    "reminder": {"en": "⏰ {title}: {text}", "ru": "⏰ {title}: {text}"},
    "running_bg": {"en": "(working in the background)", "ru": "(работает в фоне)"},
    "empty_task": {"en": "No task: give it as an argument or on stdin.", "ru": "Нет задачи: передайте её аргументом или через stdin."},
}


def detect_lang() -> str:
    """ALTAIR_LANG, else the system's language: Russian for ru/uk/be locales, else English."""
    forced = os.environ.get("ALTAIR_LANG", "").strip().lower()
    if forced in ("ru", "en"):
        return forced
    candidates = [os.environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    if os.name == "nt":
        try:
            import ctypes

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            candidates.append({0x19: "ru", 0x22: "uk", 0x23: "be"}.get(lang_id & 0x3FF, ""))
        except (AttributeError, OSError):
            pass
    joined = " ".join(c.lower() for c in candidates)
    return "ru" if any(code in joined for code in ("ru", "russian", "uk", "be_by")) else "en"


class Texts:
    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang or detect_lang()

    def __call__(self, key: str, **values: object) -> str:
        entry = TEXTS.get(key, {})
        text = entry.get(self.lang) or entry.get("en") or key
        return text.format(**values) if values else text
