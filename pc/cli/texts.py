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
    "steering": {"en": "passed to the agent", "ru": "передано агенту"},
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
    "handoff": {"en": "The agent needs you in the browser: {reason}", "ru": "Агенту нужна ваша помощь в браузере: {reason}"},
    "handoff_enter": {"en": "Press Enter when done.", "ru": "Нажмите Enter, когда закончите."},
    "secret_hint": {"en": "The agent asks for the secret {name}: enter it in the app window or with /secret {name}.",
                    "ru": "Агент просит секрет {name}: введите его в окне приложения или командой /secret {name}."},
    # --- tool previews ---
    "tool.more": {"en": "… +{n} more lines (/out shows the output in full)",
                  "ru": "… ещё строк: {n} (/out покажет вывод целиком)"},
    "tool.earlier": {"en": "… {n} earlier lines", "ru": "… выше ещё строк: {n}"},
    "tool.no_output": {"en": "(no output)", "ru": "(нет вывода)"},
    "tool.failed": {"en": "failed", "ru": "ошибка"},
    "tool.read": {"en": "Read {n} lines", "ru": "Прочитано строк: {n}"},
    "tool.wrote": {"en": "Wrote {n} lines", "ru": "Записано строк: {n}"},
    "tool.changed": {"en": "+{added} −{removed}", "ru": "+{added} −{removed}"},
    "plan": {"en": "Plan", "ru": "План"},
    "retrying": {"en": "the model did not answer, retrying {n}", "ru": "модель не ответила, повтор {n}"},
    "widget": {"en": "an interactive widget: open this chat in the app window to see it",
               "ru": "интерактивный виджет: откройте этот чат в окне приложения, чтобы его увидеть"},
    "undone": {"en": "Undone: files restored — {n}", "ru": "Откачено: восстановлено файлов — {n}"},
    # --- the status line ---
    "st.thinking": {"en": "Thinking · {detail}", "ru": "Думаю · {detail}"},
    "st.thinking.plain": {"en": "Thinking", "ru": "Думаю"},
    "st.reasoning": {"en": "Reasoning · {detail}", "ru": "Рассуждаю · {detail}"},
    "st.reasoning.plain": {"en": "Reasoning", "ru": "Рассуждаю"},
    "st.writing": {"en": "Writing · {detail}", "ru": "Пишу · {detail}"},
    "st.writing.plain": {"en": "Writing", "ru": "Пишу"},
    "st.tool": {"en": "{detail}", "ru": "{detail}"},
    "st.tool.plain": {"en": "Working", "ru": "Работаю"},
    "st.pending": {"en": "Preparing {detail}", "ru": "Готовлю {detail}"},
    "st.pending.plain": {"en": "Preparing a tool call", "ru": "Готовлю вызов инструмента"},
    "st.retry": {"en": "Reconnecting {detail}", "ru": "Переподключаюсь {detail}"},
    "st.retry.plain": {"en": "Reconnecting", "ru": "Переподключаюсь"},
    "st.compacting": {"en": "Compacting the conversation", "ru": "Сжимаю переписку"},
    "st.compacting.plain": {"en": "Compacting the conversation", "ru": "Сжимаю переписку"},
    "compacted": {"en": "Compacted: {n} messages folded into a summary · {before} → {after} tokens",
                  "ru": "Сжато: {n} сообщений свёрнуты в резюме · {before} → {after} токенов"},
    "compact_busy": {"en": "The agent is working: compact when the task ends.",
                     "ru": "Агент работает: сожмите, когда задача закончится."},
    "detach_hint": {"en": "(/detach removes)", "ru": "(/detach — убрать)"},
    "clipboard_empty": {"en": "No image or files in the clipboard (copy a picture or files, then Alt+V).",
                        "ru": "В буфере нет картинки или файлов (скопируйте картинку или файлы, затем Alt+V)."},
    "attach_later": {"en": "The attachments wait for the next task (a hint carries text only).",
                     "ru": "Вложения уйдут со следующей задачей (уточнение передаёт только текст)."},
    "not_a_file": {"en": "No such file: {path}", "ru": "Нет такого файла: {path}"},
    "nothing_to_open": {"en": "Nothing to open yet: the agent has not shown a file or a widget.",
                        "ru": "Пока нечего открыть: агент не показывал файл или виджет."},
    "opened": {"en": "Opened {path}", "ru": "Открыт {path}"},
    "open_failed": {"en": "Could not open {path}", "ru": "Не удалось открыть {path}"},
    "widget_saved": {"en": "Interactive widget", "ru": "Интерактивный виджет"},
    "open_hint": {"en": "(ctrl+click or /open)", "ru": "(ctrl+клик или /open)"},
    "ck.passed": {"en": "checks passed", "ru": "проверки прошли"},
    "ck.failed": {"en": "checks failed", "ru": "проверки не прошли"},
    "ck.not_run": {"en": "not verified (the check could not start)",
                   "ru": "не проверено (проверка не запустилась)"},
    "st.done": {"en": "done", "ru": "готово"},
    "q_asks": {"en": "The agent has a question", "ru": "У агента вопрос"},
    "bye": {"en": "See you — the chat is saved.", "ru": "До встречи — чат сохранён."},
    "bye_resume": {"en": "altair -r {id} picks it up again", "ru": "altair -r {id} — продолжить его"},
    "esc_stop": {"en": "esc to stop", "ru": "esc — стоп"},
    "mode.manual": {"en": "ask before changes", "ru": "спрашивать перед изменениями"},
    "mode.manual.desc": {"en": "approve each risky action", "ru": "подтверждать каждое рискованное действие"},
    "mode.auto": {"en": "⏵ auto: changes in the folder allowed", "ru": "⏵ авто: изменения в папке разрешены"},
    "mode.auto.desc": {"en": "edits and safe commands in the folder go without asking",
                       "ru": "правки и безопасные команды в папке — без вопросов"},
    "mode.bypass": {"en": "⏵⏵ bypass: no approvals", "ru": "⏵⏵ без подтверждений"},
    "mode.bypass.desc": {"en": "nothing is asked — only where you trust the agent fully",
                         "ru": "ничего не спрашивается — только там, где агенту полностью доверяете"},
    "cycle_mode": {"en": "(shift+tab to change)", "ru": "(shift+tab — сменить)"},
    "shortcuts_hint": {"en": "? shortcuts · / commands · @ files", "ru": "? клавиши · / команды · @ файлы"},
    "ctx": {"en": "{pct}% context", "ru": "контекст {pct}%"},
    "thinking_on": {"en": "Showing the model's reasoning (ctrl+t to hide).",
                    "ru": "Показываю рассуждения модели (ctrl+t — скрыть)."},
    "thinking_off": {"en": "The model's reasoning is hidden (ctrl+t to show).",
                     "ru": "Рассуждения модели скрыты (ctrl+t — показать)."},
    # --- pickers ---
    "type_own": {"en": "Type your own answer", "ru": "Свой ответ"},
    "select_keys": {"en": "↑↓ move · enter pick · esc cancel", "ru": "↑↓ выбор · enter — выбрать · esc — отмена"},
    "select_keys_multi": {"en": "↑↓ move · space tick · enter confirm · esc cancel",
                          "ru": "↑↓ выбор · пробел — отметить · enter — готово · esc — отмена"},
    "ap.title": {"en": "Allow {name}?", "ru": "Разрешить {name}?"},
    "ap.once": {"en": "Yes", "ru": "Да"},
    "ap.project": {"en": "Yes, and don't ask again in this folder", "ru": "Да, и больше не спрашивать в этой папке"},
    "ap.project_desc": {"en": "", "ru": ""},
    "ap.global": {"en": "Yes, and don't ask again anywhere", "ru": "Да, и больше не спрашивать нигде"},
    "ap.global_desc": {"en": "", "ru": ""},
    "ap.deny": {"en": "No", "ru": "Нет"},
    "ap.deny_desc": {"en": "then type what to do instead", "ru": "затем напишите, что сделать вместо этого"},
    "ap.done.once": {"en": "Allowed", "ru": "Разрешено"},
    "ap.done.project": {"en": "Allowed in this folder", "ru": "Разрешено в этой папке"},
    "ap.done.global": {"en": "Allowed everywhere", "ru": "Разрешено везде"},
    "ap.done.deny": {"en": "Denied", "ru": "Отклонено"},
    "no_answer": {"en": "(no answer)", "ru": "(без ответа)"},
    "handoff_title": {"en": "Finish it in the browser, then confirm", "ru": "Сделайте это в браузере и подтвердите"},
    "handoff_done": {"en": "Done", "ru": "Готово"},
    "secret_asked": {"en": "The agent asks for the secret {name}", "ru": "Агент просит секрет {name}"},
    "secret_prompt": {"en": "{name} (hidden, Enter to skip): ", "ru": "{name} (скрыто, Enter — пропустить): "},
    "secret_skipped": {"en": "Skipped: /secret {name} sets it later", "ru": "Пропущено: /secret {name} задаст позже"},
    "secret_saved": {"en": "{name} saved to the folder's .env (the model does not see it)",
                     "ru": "{name} сохранён в .env папки (модель его не видит)"},
    "secret_name": {"en": "Secret name: ", "ru": "Имя секрета: "},
    # --- the conversation ---
    "welcome": {"en": "Welcome to Altair", "ru": "Добро пожаловать в Altair"},
    "welcome_name": {"en": "Welcome back, {name}", "ru": "С возвращением, {name}"},
    "w.model": {"en": "model", "ru": "модель"},
    "w.folder": {"en": "folder", "ru": "папка"},
    "w.chat": {"en": "chat", "ru": "чат"},
    "tips": {"en": "/help commands · @ attach a file · alt+v paste an image · shift+tab mode · esc stops",
             "ru": "/help команды · @ приложить файл · alt+v вставить картинку · shift+tab режим · esc — стоп"},
    "earlier": {"en": "… {n} earlier messages (/history shows them all)",
                "ru": "… ранее сообщений: {n} (/history покажет все)"},
    "closed": {"en": "The connection to the backend closed.", "ru": "Связь с бэкендом прервалась."},
    "new_chat": {"en": "New chat", "ru": "Новый чат"},
    "pick_chat": {"en": "Resume a chat", "ru": "Продолжить чат"},
    "rename_prompt": {"en": "New title: ", "ru": "Новое название: "},
    "renamed": {"en": "Renamed: {title}", "ru": "Переименован: {title}"},
    "current": {"en": "current", "ru": "текущая"},
    "pick_model": {"en": "Model for the next tasks", "ru": "Модель для следующих задач"},
    "other_model": {"en": "Another model…", "ru": "Другая модель…"},
    "model_set": {"en": "Model: {model}", "ru": "Модель: {model}"},
    "pick_mode": {"en": "Approval mode", "ru": "Режим подтверждений"},
    "pick_reasoning": {"en": "Reasoning level (for every chat)", "ru": "Уровень рассуждений (для всех чатов)"},
    "r.adaptive": {"en": "Adaptive", "ru": "Адаптивный"},
    "r.adaptive.desc": {"en": "low, and high right after a failure — the best value",
                        "ru": "низкий, и высокий сразу после сбоя — лучшее соотношение"},
    "r.low": {"en": "Low", "ru": "Низкий"},
    "r.low.desc": {"en": "fast and cheap", "ru": "быстро и дёшево"},
    "r.medium": {"en": "Medium", "ru": "Средний"},
    "r.medium.desc": {"en": "", "ru": ""},
    "r.high": {"en": "High", "ru": "Высокий"},
    "r.high.desc": {"en": "slower and pricier, for hard problems", "ru": "медленнее и дороже, для трудных задач"},
    "r.default": {"en": "Provider's default", "ru": "Как у провайдера"},
    "r.default.desc": {"en": "nothing is sent; some models then think for minutes",
                       "ru": "ничего не передаётся; некоторые модели тогда думают минутами"},
    "reasoning_set": {"en": "Reasoning: {level}", "ru": "Рассуждения: {level}"},
    "context_title": {"en": "Context", "ru": "Контекст"},
    "context_note": {"en": "Old tool outputs are cleared and the history is summarized automatically as it fills.",
                     "ru": "По мере заполнения старые выводы инструментов очищаются, а история сжимается автоматически."},
    "cost_title": {"en": "Cost", "ru": "Расходы"},
    "cost_chat": {"en": "this chat: {usd} over {runs} tasks · {tokens} tokens",
                  "ru": "этот чат: {usd} за задач: {runs} · токенов: {tokens}"},
    "cost_here": {"en": "since this terminal opened it: {usd}", "ru": "с открытия в этом терминале: {usd}"},
    "no_git": {"en": "Not a git repository.", "ru": "Это не git-репозиторий."},
    "clean_tree": {"en": "No uncommitted changes.", "ru": "Незакоммиченных изменений нет."},
    "diff_title": {"en": "Uncommitted changes · {branch}", "ru": "Незакоммиченные изменения · {branch}"},
    "nothing_to_undo": {"en": "No task in this terminal to undo yet.", "ru": "В этом терминале ещё нет задачи для отката."},
    "undo_title": {"en": "Undo every file change of the last task?", "ru": "Откатить все изменения файлов последней задачи?"},
    "undo_yes": {"en": "Yes, undo", "ru": "Да, откатить"},
    "undo_no": {"en": "No", "ru": "Нет"},
    "memory_title": {"en": "Memory · {n} notes", "ru": "Память · заметок: {n}"},
    "skills_title": {"en": "Skills · {n}", "ru": "Навыки · {n}"},
    "mcp_title": {"en": "MCP servers · {n}", "ru": "MCP-серверы · {n}"},
    "mcp_tools": {"en": "{n} tools", "ru": "инструментов: {n}"},
    "no_tool_yet": {"en": "No tool has run yet.", "ru": "Инструменты ещё не запускались."},
    "unknown_cmd": {"en": "Unknown command /{name} — /help lists them.", "ru": "Нет команды /{name} — список в /help."},
    "commands": {"en": "Commands", "ru": "Команды"},
    "shortcuts_title": {"en": "Keys", "ru": "Клавиши"},
    "c.help": {"en": "commands and keys", "ru": "команды и клавиши"},
    "c.new": {"en": "start a new chat", "ru": "начать новый чат"},
    "c.resume": {"en": "resume a chat (also one from the window)", "ru": "продолжить чат (и начатый в окне)"},
    "c.rename": {"en": "rename this chat", "ru": "переименовать чат"},
    "c.model": {"en": "the model for the next tasks", "ru": "модель для следующих задач"},
    "c.mode": {"en": "the approval mode", "ru": "режим подтверждений"},
    "c.reasoning": {"en": "the reasoning level", "ru": "уровень рассуждений"},
    "c.thinking": {"en": "show or hide the model's reasoning", "ru": "показать или скрыть рассуждения модели"},
    "c.context": {"en": "how full the context is", "ru": "заполнение контекста"},
    "c.cost": {"en": "what this chat cost", "ru": "сколько стоил этот чат"},
    "c.diff": {"en": "uncommitted changes in the folder", "ru": "незакоммиченные изменения в папке"},
    "c.undo": {"en": "undo the file changes of the last task", "ru": "откатить изменения файлов последней задачи"},
    "c.init": {"en": "write AGENTS.md for this project", "ru": "написать AGENTS.md для проекта"},
    "c.memory": {"en": "what the agent remembers", "ru": "что агент помнит"},
    "c.skills": {"en": "the skills", "ru": "навыки"},
    "c.mcp": {"en": "the MCP servers", "ru": "MCP-серверы"},
    "c.secret": {"en": "set a secret (an API key) for the folder", "ru": "задать секрет (API-ключ) для папки"},
    "c.compact": {"en": "fold the conversation into a summary now (optionally: what to keep)",
                  "ru": "свернуть переписку в резюме сейчас (можно указать, что сохранить)"},
    "c.attach": {"en": "attach a file (no path: from the clipboard)", "ru": "приложить файл (без пути — из буфера)"},
    "c.detach": {"en": "remove the attachments", "ru": "убрать вложения"},
    "c.open": {"en": "open the last picture, file or widget", "ru": "открыть последнюю картинку, файл или виджет"},
    "c.out": {"en": "the last tool's output in full", "ru": "вывод последнего инструмента целиком"},
    "c.history": {"en": "the whole conversation", "ru": "вся переписка"},
    "c.stop": {"en": "stop the task", "ru": "остановить задачу"},
    "c.exit": {"en": "leave (the chat stays)", "ru": "выйти (чат сохранится)"},
}

#: Two-column lists: (keys, what they do).
PAIRS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "shortcuts": {
        "en": [("enter", "send"), ("alt+enter · ctrl+j · \\ enter", "new line"),
               ("esc", "stop the agent"), ("ctrl+c", "clear the input · stop · twice to exit"),
               ("shift+tab", "approval mode"), ("ctrl+t", "show the model's reasoning"),
               ("alt+v", "attach an image or files from the clipboard"),
               ("↑ ↓", "earlier messages"), ("→", "accept the grey suggestion"),
               ("ctrl+x ctrl+e", "edit in $EDITOR"), ("ctrl+l", "redraw"), ("ctrl+d", "exit"),
               ("/", "commands"), ("@", "mention a file")],
        "ru": [("enter", "отправить"), ("alt+enter · ctrl+j · \\ enter", "новая строка"),
               ("esc", "остановить агента"), ("ctrl+c", "очистить ввод · стоп · дважды — выход"),
               ("shift+tab", "режим подтверждений"), ("ctrl+t", "показать рассуждения модели"),
               ("alt+v", "приложить картинку или файлы из буфера"),
               ("↑ ↓", "прошлые сообщения"), ("→", "принять серую подсказку"),
               ("ctrl+x ctrl+e", "править в $EDITOR"), ("ctrl+l", "перерисовать"), ("ctrl+d", "выход"),
               ("/", "команды"), ("@", "упомянуть файл")],
    },
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
        text = entry.get(self.lang) or entry.get("en") or ("" if key in TEXTS else key)
        return text.format(**values) if values else text

    def pairs(self, key: str) -> list[tuple[str, str]]:
        entry = PAIRS.get(key, {})
        return entry.get(self.lang) or entry.get("en") or []
