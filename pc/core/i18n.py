"""Texts the backend shows to the user, in the user's interface language.

Model-facing text (prompts, tool descriptions and results) is English and never goes
through here. What reaches the person — approval questions, the run log, errors, the
titles of system dialogs — comes from this catalog in the language the UI reported
(`set_ui_language`, sent by the app on connect and on every language switch). This is
a single-user desktop app, so one process-wide language is exactly right.
"""

from __future__ import annotations

from typing import Any

_LANG = "en"

CATALOG: dict[str, dict[str, str]] = {
    # --- approvals -------------------------------------------------------------------
    "appr.generic": {"en": "'{name}': {args}", "ru": "«{name}»: {args}"},
    "appr.generic.edit": {"en": "'{name}' will save changes: {args}", "ru": "«{name}» сохранит изменения: {args}"},
    "appr.generic.execute": {"en": "'{name}' will run a program: {args}", "ru": "«{name}» запустит программу: {args}"},
    "appr.generic.network": {"en": "'{name}' will go online: {args}", "ru": "«{name}» обратится в интернет: {args}"},
    "appr.chart": {"en": "Create the chart file '{path}'", "ru": "Создать файл графика '{path}'"},
    "appr.plan": {"en": "Save the plan «{title}»", "ru": "Сохранить план «{title}»"},
    "appr.phone_file": {"en": "Ask the phone for a file: {hint}", "ru": "Попросить у телефона файл: {hint}"},
    "appr.phone_photo": {"en": "Ask the phone for a photo: {hint}", "ru": "Попросить у телефона фото: {hint}"},
    "appr.phone_ask": {"en": "Ask you on the phone: {question}", "ru": "Спросить вас на телефоне: {question}"},
    "appr.phone_cap": {"en": "Ask the phone to use {capability}: {task}", "ru": "Попросить телефон использовать {capability}: {task}"},
    "appr.android_stop": {"en": "Stop the Android emulator", "ru": "Остановить Android-эмулятор"},
    "appr.android_install": {"en": "Install {apk} on the Android device", "ru": "Установить {apk} на Android-устройство"},
    "appr.web_search": {"en": "Search the web: {query}", "ru": "Поиск в интернете: {query}"},
    "appr.open_url": {"en": "Open the web page {url}", "ru": "Открыть веб-страницу {url}"},
    "appr.research": {"en": "Research on the web: {question}", "ru": "Исследование в интернете: {question}"},
    "appr.images": {"en": "Search the web for images: {query}", "ru": "Поиск картинок в интернете: {query}"},
    "appr.br_open": {"en": "Open {url} in the built-in browser", "ru": "Открыть {url} во встроенном браузере"},
    "appr.br_click": {"en": "Click an element on the page ({ref})", "ru": "Нажать на элемент страницы ({ref})"},
    "appr.br_type": {"en": "Type into a field on the page ({ref}): «{text}»", "ru": "Ввести в поле на странице ({ref}): «{text}»"},
    "appr.br_type_submit": {"en": "Type into a field on the page ({ref}) and send: «{text}»", "ru": "Ввести в поле на странице ({ref}) и отправить: «{text}»"},
    "appr.br_tab_close": {"en": "Close the browser tab {tab}", "ru": "Закрыть вкладку браузера {tab}"},
    "appr.br_tab_new": {"en": "Open a new browser tab: {url}", "ru": "Открыть новую вкладку браузера: {url}"},
    "appr.br_tabs": {"en": "Browser tabs: {action}", "ru": "Вкладки браузера: {action}"},
    "appr.br_press": {"en": "Press {combo} on the page", "ru": "Нажать {combo} на странице"},
    "appr.br_select": {"en": "Choose «{values}» in a list on the page ({ref})", "ru": "Выбрать «{values}» в списке на странице ({ref})"},
    "appr.mem_remove": {"en": "Remove from memory ({scope}): {what}", "ru": "Удалить из памяти ({scope}): {what}"},
    "appr.mem_replace": {"en": "Change an entry in memory ({scope})", "ru": "Изменить запись в памяти ({scope})"},
    "appr.ctx_compress": {"en": "Compress the conversation, keeping the last {n} messages", "ru": "Сжать переписку, оставив последние {n} сообщений"},
    "appr.ctx_drop": {"en": "Drop from the conversation context: {what}", "ru": "Убрать из контекста переписки: {what}"},
    "appr.remind": {"en": "Set a reminder: {note}", "ru": "Поставить напоминание: {note}"},
    "appr.watch": {"en": "Watch for {signal} {op} {value}: {note}", "ru": "Следить за условием {signal} {op} {value}: {note}"},
    "appr.remind_cancel": {"en": "Cancel the reminder {id}", "ru": "Отменить напоминание {id}"},
    "appr.injection": {
        "en": "⚠️ Content that looked like a prompt injection was read before this action — "
              "confirm that it is really needed.",
        "ru": "⚠️ Перед этим действием читалось содержимое с признаками промпт-инъекции — "
              "подтвердите, что оно действительно нужно.",
    },
    "appr.default": {"en": "default", "ru": "по умолчанию"},
    "appr.auto": {"en": "auto", "ru": "авто"},
    "appr.android_start": {"en": "Start the Android emulator «{avd}»", "ru": "Запуск Android-эмулятора «{avd}»"},
    "appr.bg_run": {"en": "Background command «{name}»: {cmd}", "ru": "Фоновая команда «{name}»: {cmd}"},
    "appr.bg_stop": {"en": "Stop the background command «{name}»", "ru": "Остановка фоновой команды «{name}»"},
    "appr.upload": {"en": "Upload files to the web page: {paths}", "ru": "Загрузить файлы на веб-страницу: {paths}"},
    "appr.dl_move": {"en": "Move the downloaded file «{name}» to {where}", "ru": "Перенести скачанный файл «{name}» в {where}"},
    "appr.dl_move_unknown": {"en": "Move download {id}", "ru": "Перенести загрузку {id}"},
    "appr.where_downloads": {"en": "the Downloads folder", "ru": "папку «Загрузки»"},
    "appr.where_workspace": {"en": "the project folder", "ru": "рабочую папку"},
    "appr.dl_warn": {"en": " — WARNING: {status}. {detail}", "ru": " — ВНИМАНИЕ: {status}. {detail}"},
    "appr.coverage": {"en": "Measure test coverage: source={source}, tests={tests}",
                      "ru": "Измерение покрытия тестами: source={source}, tests={tests}"},
    "appr.sql": {"en": "SQL that changes {path}: {sql}", "ru": "Изменяющий SQL к {path}: {sql}"},
    "appr.dev_start": {"en": "Start the dev server «{name}»: {cmd}", "ru": "Запуск dev-сервера «{name}»: {cmd}"},
    "appr.dev_stop": {"en": "Stop the dev server «{name}»", "ru": "Остановка dev-сервера «{name}»"},
    "appr.write": {"en": "Write the file '{path}' ({n} characters)", "ru": "Запись файла '{path}' ({n} символов)"},
    "appr.edit": {"en": "Edit the file '{path}' ({a} → {b} characters)", "ru": "Правка файла '{path}' ({a} → {b} символов)"},
    "appr.delete": {"en": "Delete '{path}'{rec}", "ru": "Удаление '{path}'{rec}"},
    "appr.recursive": {"en": " (with everything inside!)", "ru": " (вместе со всем содержимым!)"},
    "appr.git_commit": {"en": "git commit ({scope}): {msg}", "ru": "git commit ({scope}): {msg}"},
    "appr.all_changes": {"en": "all changes", "ru": "все изменения"},
    "appr.git_restore": {"en": "git restore{src}: {paths}", "ru": "git restore{src}: {paths}"},
    "appr.restore_from": {"en": " from {src}", "ru": " из {src}"},
    "appr.restore_undo": {"en": " (undo edits)", "ru": " (отмена правок)"},
    "appr.git_branch": {"en": "git branch: {action} {name}", "ru": "git branch: {action} {name}"},
    "appr.video": {"en": "Process a video ({op}): {path}", "ru": "Обработка видео ({op}): {path}"},
    "appr.patch": {"en": "Edit files with a patch: {files}{more}", "ru": "Правка файлов патчем: {files}{more}"},
    "appr.patch_more": {"en": " and {n} more", "ru": " и ещё {n}"},
    "appr.patch_unparsed": {"en": "Apply a patch (the file list could not be read)",
                            "ru": "Применение патча (не удалось разобрать список файлов)"},
    "appr.python": {"en": "Run Python code:\n{code}", "ru": "Выполнение Python-кода:\n{code}"},
    "appr.tests": {"en": "Run the tests in '{path}' ({cmd})", "ru": "Запуск тестов в '{path}' ({cmd})"},
    "appr.tests_auto": {"en": "the command is detected automatically", "ru": "команда определяется автоматически"},
    "appr.lint": {"en": "Run the linters in '{path}'{fix}", "ru": "Запуск линтеров в '{path}'{fix}"},
    "appr.lint_fix": {"en": " with auto-fix", "ru": " с автоисправлением"},
    "appr.shell": {"en": "Run a console command: {cmd}", "ru": "Выполнение команды в консоли: {cmd}"},
    "appr.skill": {"en": "Create the skill '{name}': {desc}", "ru": "Создание навыка '{name}': {desc}"},
    "appr.anki": {"en": "Create the deck «{deck}» from {n} cards", "ru": "Создать колоду «{deck}» из {n} карточек"},
    "appr.subagent": {"en": "Start the subagent «{label}»: {task}", "ru": "Запуск субагента «{label}»: {task}"},
    "appr.diff": {"en": "Compare outputs: «{a}» ↔ «{b}»", "ru": "Сравнение вывода: «{a}» ↔ «{b}»"},
    "appr.screenshot": {"en": "Screenshot: {target}", "ru": "Скриншот: {target}"},
    "appr.audit": {"en": "Visual layout check: {target}", "ru": "Визуальная проверка вёрстки: {target}"},
    "appr.http": {"en": "HTTP {method} to {url}", "ru": "HTTP {method} к {url}"},
    "appr.download": {"en": "Download {url} → {dest}", "ru": "Скачивание {url} → {dest}"},
    # --- download verdicts (shown in approvals) ---------------------------------------
    "dl.risky": {"en": "it can run programs", "ru": "может запускать программы"},
    "dl.suspicious": {"en": "suspicious", "ru": "подозрительный"},
    "dl.unscanned": {"en": "not checked by the antivirus", "ru": "не проверен антивирусом"},
    # --- run log ----------------------------------------------------------------------
    "log.run_options": {"en": "Run settings — {desc}", "ru": "Параметры запуска — {desc}"},
    "log.attach_failed": {"en": "Attachment not added: {problem}", "ru": "Вложение не приложено: {problem}"},
    "log.model_time": {"en": "The model answered in {s} s", "ru": "Модель ответила за {s} с"},
    "log.gate_failed": {"en": "Health check: the checks failed (attempt {n}/{total}) — asking to fix.",
                        "ru": "Health-gate: проверки не прошли (попытка {n}/{total}) — прошу починить."},
    "log.gate_rollback": {"en": "Health check: could not get to green. The run's changes were rolled back ({n}).",
                          "ru": "Health-gate: не удалось довести до зелёного. Изменения прогона откачены ({n})."},
    "log.gate_kept": {"en": "Health check: could not get to green. The changes were kept as they are.",
                      "ru": "Health-gate: не удалось довести до зелёного. Изменения оставлены как есть."},
    "log.gate_ok": {"en": "Health check: automatic checks passed ✓", "ru": "Health-gate: автопроверки пройдены ✓"},
    "log.gate_running": {"en": "Health check: running the checks — {cmd}", "ru": "Health-gate: прогоняю проверки — {cmd}"},
    "log.gate_error": {"en": "Health check: could not run the checks ({error}).",
                       "ru": "Health-gate: не удалось прогнать проверки ({error})."},
    "log.gate_rollback_file": {"en": "Health check rollback: {path}", "ru": "Health-gate откат: {path}"},
    "log.verify_nudge": {"en": "The code changed but nothing was checked — asking to verify before finishing.",
                         "ru": "Код менялся, но проверок не было — прошу проверить перед завершением."},
    "log.background": {"en": "Background notice: {text}", "ru": "Фоновое уведомление: {text}"},
    "log.steering": {"en": "Your note was taken into the work: {text}", "ru": "Уточнение принято в работу: {text}"},
    "log.cleared": {"en": "Old tool outputs cleared: {n}, −{chars} characters",
                    "ru": "Очищены старые выводы инструментов: {n} шт., −{chars} симв."},
    "log.compacted": {"en": "Context compacted: {n} messages summarised.", "ru": "Контекст свёрнут: {n} сообщений сжаты в резюме."},
    "log.trimmed": {"en": "History trimmed: {n} old messages removed.", "ru": "История свёрнута: удалено {n} старых сообщений."},
    # --- chat socket ------------------------------------------------------------------
    "ws.history_cleared": {"en": "Chat history cleared.", "ru": "История диалога очищена."},
    "ws.unknown_command": {"en": "Unknown command: {kind}", "ru": "Неизвестная команда: {kind}"},
    "ws.session_missing": {"en": "Chat '{id}' was not found.", "ru": "Сессия '{id}' не найдена."},
    "ws.chat_folder_missing": {"en": "The chat's folder is not available ({path}). Opened in the default folder.",
                               "ru": "Папка чата недоступна ({path}). Открыт в папке по умолчанию."},
    "ws.no_checkpoint": {"en": "No point to roll back to.", "ru": "Не нашёл точку для отката."},
    "ws.default_folder": {"en": "{error} The chat was opened in the default folder.",
                          "ru": "{error} Открыт чат в папке по умолчанию."},
    "ws.folder_locked": {"en": "The project folder cannot change after the chat has started — create a new chat.",
                         "ru": "Рабочую папку нельзя сменить после начала чата — создайте новый чат."},
    "ws.folder": {"en": "Project folder: {path}", "ru": "Рабочая папка: {path}"},
    "ws.unknown_mode": {"en": "Unknown mode: {mode}", "ru": "Неизвестный режим: {mode}"},
    "ws.mode": {"en": "Permission mode: {title}", "ru": "Режим разрешений: {title}"},
    "ws.empty_task": {"en": "The request is empty.", "ru": "Пустой запрос."},
    "ws.steer_queued": {"en": "Your note was passed to the agent — it will take it into account.",
                        "ru": "Уточнение передано агенту — учту по ходу."},
    "ws.no_retry": {"en": "Nothing to repeat.", "ru": "Не нашёл запрос для повтора."},
    "ws.folder_unavailable": {"en": "The project folder is not available.", "ru": "Рабочая папка недоступна."},
    "ws.routing": {"en": "Routing: {note}", "ru": "Маршрутизация: {note}"},
    "ws.routing_split": {"en": "Routing: the task was split into {n} subtasks — {summary}",
                         "ru": "Маршрутизация: задача разбита на {n} подзадач — {summary}"},
    "ws.nothing_to_resume": {"en": "No interrupted run was found — nothing to continue.",
                             "ru": "Прерванный прогон не найден — продолжать нечего."},
    "ws.stopping": {"en": "Stopping the task…", "ru": "Останавливаю задачу..."},
    "ws.skill_missing": {"en": "skill not found", "ru": "навык не найден"},
    # --- REST ---------------------------------------------------------------------------
    "api.session_missing": {"en": "Chat not found", "ru": "Сессия не найдена"},
    "api.dialog_unavailable": {"en": "The system dialog is not available ({reason}).",
                               "ru": "Системный диалог недоступен ({reason})."},
    "api.folder_missing": {"en": "Project folder not found", "ru": "Рабочая папка не найдена"},
    "api.preset_bad": {"en": "Invalid preset", "ru": "Некорректный пресет"},
    "api.preset_name": {"en": "The preset needs a name", "ru": "У пресета должно быть имя"},
    "api.no_git": {"en": "git is not installed", "ru": "git не установлен"},
    "api.not_repo": {"en": "The folder is not a git repository", "ru": "Папка не является git-репозиторием"},
    "api.settings_write": {"en": "Could not write the settings file: {error}",
                           "ru": "Не удалось записать файл настроек: {error}"},
    "api.ff_pick": {"en": "No profile or sites were chosen.", "ru": "Не выбран профиль или сайты."},
    "api.hunk_missing_no": {"en": "No hunk number was given.", "ru": "Не указан номер ханка."},
    "api.file_missing": {"en": "No file was given.", "ru": "Не указан файл."},
    "api.git_diff_failed": {"en": "git diff did not run.", "ru": "git diff не выполнился."},
    "api.hunk_gone": {"en": "The hunk was not found (the changes may have been updated).",
                      "ru": "Ханк не найден (изменения могли обновиться)."},
    "api.hunk_revert_failed": {"en": "git apply could not roll back the hunk.", "ru": "git apply не смог откатить ханк."},
    "api.update_unavailable": {"en": "The update cannot be installed.", "ru": "Обновление недоступно для установки."},
    "api.path_missing": {"en": "Folder '{path}' was not found", "ru": "Папка '{path}' не найдена"},
    "api.no_access": {"en": "No access to '{path}': {error}", "ru": "Нет доступа к '{path}': {error}"},
    "appr.mcp": {"en": "Call the external MCP tool '{tool}' of the server '{server}'.",
                 "ru": "Вызов внешнего MCP-инструмента «{tool}» сервера «{server}»."},
    "api.local_only": {"en": "Only the app on this computer can change this.",
                       "ru": "Изменить это можно только из приложения на этом компьютере."},
    "api.skill_exists": {"en": "A skill with this name already exists: {names}",
                         "ru": "Навык с таким именем уже есть: {names}"},
    "api.skill_invalid": {"en": "Could not add the skill: {reason}", "ru": "Не удалось добавить навык: {reason}"},
    "api.mcp_invalid": {"en": "The MCP server settings are not valid: {reason}",
                        "ru": "Настройки MCP-сервера некорректны: {reason}"},
    "api.mcp_missing": {"en": "No MCP server named '{name}'", "ru": "Нет MCP-сервера с именем «{name}»"},
    # --- system dialogs ------------------------------------------------------------------
    "dlg.pick_folder": {"en": "Choose the project folder", "ru": "Выберите рабочую папку проекта"},
    "dlg.pick_files": {"en": "Choose files", "ru": "Выберите файлы"},
    "dlg.media": {"en": "Photos and videos", "ru": "Фото и видео"},
    "dlg.all_files": {"en": "All files", "ru": "Все файлы"},
}


def set_ui_language(lang: str) -> None:
    """The interface language, as the app reports it ("en", "ru", …)."""
    global _LANG
    code = (lang or "").strip().lower()[:2]
    _LANG = code if code in ("en", "ru") else "en"


def ui_language() -> str:
    return _LANG


def tr(key: str, **params: Any) -> str:
    """The text for `key` in the UI language, with {placeholders} filled in."""
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(_LANG) or entry["en"]
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text
