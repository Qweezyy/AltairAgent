"""Единая типизированная конфигурация приложения.

Единственный источник правды для настроек. Читается из переменных окружения
и файла `.env`. Не добавляйте `os.getenv(...)` в других модулях — добавляйте
поле сюда, тогда настройка автоматически валидируется и документируется.
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _is_frozen() -> bool:
    """Запущены ли мы как собранное приложение."""
    return bool(getattr(sys, "frozen", False))


def default_app_dir() -> Path:
    """Где приложению хранить свои данные.

    В собранном виде — в профиле пользователя: рядом с exe писать нельзя
    (Program Files доступен только на чтение), а внутри _internal данные
    потерялись бы при первом же обновлении.
    """
    if _is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "LocalAIAgent"
    return PROJECT_ROOT


def default_workspace() -> Path:
    """Рабочая папка по умолчанию: у собранного приложения — Документы."""
    if _is_frozen():
        documents = Path.home() / "Documents"
        return documents if documents.is_dir() else Path.home()
    return PROJECT_ROOT


def _env_files() -> list[str]:
    """Где искать .env: рядом с исходниками, в данных приложения и у exe."""
    candidates = [PROJECT_ROOT / ".env", default_app_dir() / ".env"]
    if _is_frozen():
        candidates.append(Path(sys.executable).parent / ".env")
    return [str(path) for path in candidates]

ApprovalMode = Literal["manual", "accept_edits", "plan", "allowlist", "bypass"]


_TOKEN_SUFFIX = {"k": 1e3, "к": 1e3, "тыс": 1e3, "m": 1e6, "м": 1e6, "млн": 1e6, "b": 1e9}
_TOKEN_RE = re.compile(r"^([\d.,]+)(k|к|тыс|m|м|млн|b)?")


def parse_token_count(value: str) -> int | str:
    """"1M" -> 1000000, "200K"/"200к" -> 200000, "1.5M"/"1,5M" -> 1500000, "1 000 000" and
    "1,000,000" -> 1000000. Anything else is returned as is for the normal validation error."""
    text = re.sub(r"[\s\u00a0\u202f_']", "", value.strip().lower())
    match = _TOKEN_RE.match(text)
    if not match:
        return value
    digits, suffix = match.group(1), match.group(2) or ""
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", digits) and not (suffix and re.fullmatch(r"\d+[.,]\d{3}", digits)):
        digits = digits.replace(",", "").replace(".", "")
    else:
        digits = digits.replace(",", ".", 1)
    try:
        return round(float(digits) * _TOKEN_SUFFIX.get(suffix, 1))
    except ValueError:
        return value


class Settings(BaseSettings):
    """Конфигурация агента. Все поля читаются из .env (регистр не важен)."""

    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM ---
    #: Универсальный ключ OpenAI-совместимого API. Приоритетнее старого
    #: OPENROUTER_API_KEY, оставленного для существующих .env.
    llm_api_key: str = ""
    openrouter_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    default_model: str = "anthropic/claude-sonnet-4.5"
    llm_temperature: float = 0.3
    llm_max_tokens: int | None = None
    llm_timeout: float = 120.0
    llm_max_retries: int = 3
    #: Предохранитель от зацикливания: reasoning-модели иногда генерируют один
    #: и тот же текст без конца, а read-timeout не срабатывает, пока идёт поток.
    #: Как только суммарная длина ответа (текст + размышления) превысит лимит,
    #: поток обрывается. Значение заведомо выше любого нормального ответа.
    llm_stream_char_limit: int = 160_000

    # --- Директории ---
    #: Папка приложения: логи, чаты, навыки, mcp_servers.json. Не меняется в чатах.
    app_path: Path = Field(default_factory=default_app_dir)
    #: Рабочая папка проекта. Своя для каждого чата, выбирается в интерфейсе.
    workspace_path: Path = Field(default_factory=default_workspace)
    allow_outside_workspace: bool = False
    extra_allowed_roots: str = ""

    # --- Поведение агента ---
    #: Потолок шагов. По умолчанию 0 — БЕЗ лимита: агент работает до завершения
    #: задачи. Задачу и так динамически останавливает детектор застревания
    #: (stall_limit) — как только агент начинает крутить одно и то же. Поставьте
    #: положительное число в .env, если нужен жёсткий предохранитель.
    max_steps: int = 0
    #: Сколько шагов подряд БЕЗ новых действий (одни повторы/пустышки) считать
    #: зацикливанием и останавливать задачу. Продуктивный шаг сбрасывает счётчик.
    stall_limit: int = 6
    #: Потолок токенов на одну задачу (сумма по всем шагам). 0 — без лимита.
    #: Когда суммарный расход превысит лимит, агент завершает задачу итоговым
    #: ответом вместо того, чтобы жечь бюджет дальше.
    max_run_tokens: int = 0
    #: Стоп-кран по времени: потолок длительности одной задачи в секундах.
    #: 0 — без лимита. Когда время вышло, агент не начинает новый шаг, а завершает
    #: задачу итоговым ответом. Страхует от долгих/зависших инструментов, которые
    #: сами по себе не жгут токены (сеть, сборка, ожидание процессов).
    max_run_seconds: float = 0.0
    max_parallel_tools: int = 5
    #: Обязательные «ворота проверки»: если агент правил код, но ни разу не запускал
    #: проверок, перед «готово» его один раз попросят проверить результат. Не
    #: зацикливает — только один толчок. Можно выключить в .env.
    verification_gate: bool = True
    #: The model names a new chat from its first message (one tiny extra request to the
    #: cheapest configured model). Off: the chat is named after that message's first line.
    chat_titles: bool = True
    #: Health-gate: перед завершением, если правился код, АВТОМАТИЧЕСКИ прогнать
    #: автоопределённые тесты проекта. Не прошли — вернуть агенту вывод и попросить
    #: починить (до health_gate_max_cycles попыток); если так и красно — по желанию
    #: откатить весь прогон (health_gate_auto_rollback) и честно доложить. Это ядро
    #: обещания «оставить одного»: агент сам держит результат зелёным.
    health_gate: bool = True
    health_gate_max_cycles: int = 2
    health_gate_timeout: float = 300.0
    #: При исчерпании попыток откатывать все изменения прогона (оставить чисто),
    #: а не бросать код красным. Выключи, если предпочитаешь «красное, но сохранено».
    health_gate_auto_rollback: bool = True
    #: Независимый верификатор шага + тиры риска: перед выполнением каждого
    #: инструмента ядро само (правилами, не моделью) оценивает риск по реальным
    #: аргументам. Катастрофическое (rm -rf, format, fork-бомба) блокируется всегда;
    #: высокорисковое (git push --force, sudo, DROP TABLE, системные пути) требует
    #: подтверждения, даже если режим разрешений выполнил бы его сам. Выключение
    #: снимает эскалацию до вопроса; жёсткий блок катастрофического остаётся.
    risk_gate: bool = True
    #: Разрешить ИИ самому запускать субагентов (инструмент spawn_subagent). Выключено
    #: по умолчанию: субагенты умножают расход токенов. Галочка в настройках.
    allow_subagents: bool = False
    #: Номер одинакового вызова инструмента, на котором агент прекращает его выполнять.
    repeat_call_block_limit: int = 20
    context_token_budget: int = 120_000
    #: Сворачивать старую часть длинного диалога в резюме (иначе просто выбрасывать).
    #: Стоит одного дешёвого вызова модели, зато агент не теряет договорённости.
    context_compaction: bool = True
    #: Clear old tool outputs (keeping the most recent ones) once the history passes half
    #: the budget — "observation masking": about half the cost at the same solve rate
    #: (JetBrains, NeurIPS 2025). Done in one batch so the prompt cache breaks rarely.
    tool_result_clearing: bool = True
    #: How many of the most recent tool outputs stay untouched when clearing.
    tool_result_keep_recent: int = 8
    #: Send only core tools up front; the rest are found with tool_search on demand.
    #: Saves ~20k tokens per request and improves tool selection with many tools.
    tool_search: bool = True
    tool_output_limit: int = 12_000
    agent_language: str = "русский"

    # --- Быстрый поиск по коду (tgrep) ---
    #: Персистентный индекс-сервер tgrep для ОГРОМНЫХ репозиториев: держит
    #: трёхграммный индекс тёплым и следит за файлами (watcher), поэтому поиск
    #: почти мгновенный даже на сотнях тысяч файлов. По умолчанию выключено —
    #: обычным папкам хватает прямого скана (--no-index, всегда свежо), а в
    #: индекс-режиме у пишущего агента есть небольшой лаг вотчера. Включать для
    #: гигантских кодовых баз. Индекс хранится в app_path/tgrep-index, не в проекте.
    tgrep_serve: bool = False
    #: Порог: сервер поднимается, только если в рабочей папке файлов не меньше этого
    #: (для маленьких папок прямой скан и так мгновенный, демон не нужен).
    tgrep_serve_min_files: int = 20_000
    #: Личные инструкции пользователя — добавляются в системный промпт (стиль,
    #: предпочтения, что учитывать). Пусто — ничего не добавляется.
    custom_instructions: str = ""

    # --- Поиск в интернете (опционально: по умолчанию бесплатный DuckDuckGo) ---
    tavily_api_key: str = ""
    brave_api_key: str = ""
    #: URL своего SearXNG (опц.): включает image-поиск для инлайн-картинок в ответах.
    searxng_url: str = ""

    # --- Маршрутизация моделей по сложности ---
    #: Оценщик распределяет задачи между дешёвой (слабой) и сильной (дорогой)
    #: моделью по сложности запроса. Все три — ID моделей у текущего провайдера
    #: (llm_base_url/llm_api_key). Пусто у fast/strong -> маршрутизация выключена
    #: даже при model_routing=true (нужны обе модели). model_router пуст -> судьёй
    #: работает дешёвая модель.
    model_routing: bool = False
    model_fast: str = ""
    model_strong: str = ""
    model_router: str = ""
    #: Полная привязка тиров к провайдерам (JSON): каждый тир хранит свою модель
    #: вместе с base_url и ключом, поэтому дешёвая/сильная/оценщик могут быть у
    #: РАЗНЫХ провайдеров. Формируется интерфейсом из вкладки «Модели и провайдеры».
    #: Формат: {"fast":{"model","base_url","api_key"},"strong":{...},"router":{...}}.
    #: Пусто -> берутся плоские model_fast/model_strong/model_router у текущего провайдера.
    model_tiers: str = ""

    # Отдельной vision-модели больше нет: фото/видео и скриншот-аудит уходят той
    # же основной модели (`default_model`). Если модель не мультимодальна —
    # ответит отказом. Поле VISION_MODEL в .env, если осталось, просто игнорируется.

    # --- Глубокое исследование ---
    #: Модель для служебных шагов исследования (планирование, конспекты).
    #: Пусто -> та же, что и основная. Дешёвая модель здесь экономит заметно:
    #: конспектов делается столько же, сколько прочитано источников.
    research_model: str = ""
    #: Сколько источников читаем максимум за одно исследование.
    research_max_sources: int = 14
    #: Сколько страниц открываем одновременно.
    research_concurrency: int = 4

    # --- Безопасность ---
    approval_mode: ApprovalMode = "manual"
    approval_timeout: float = 180.0
    shell_timeout: float = 120.0

    # --- Android UI-проверки ---
    #: Корень Android SDK. Пусто — поиск через ANDROID_HOME/ANDROID_SDK_ROOT/PATH.
    android_sdk_path: str = ""
    #: Имя AVD по умолчанию для запуска внешнего эмулятора.
    android_avd_name: str = ""
    #: Таймаут команд adb и диагностики.
    android_timeout: float = 120.0

    # --- Обновления ---
    #: Ссылка на JSON-манифест или путь к нему в общей папке. Пусто — не проверять.
    update_url: str = ""
    #: Проверять обновления при запуске (тихо, без окон).
    update_check_on_start: bool = True

    # --- Сервер ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Мост к телефону (Android, Фаза 4) ---
    #: Общий секрет для УДАЛЁННОГО подключения к /ws (инструмент pc_agent на
    #: телефоне). Пусто — удалённые подключения запрещены, разрешён только
    #: localhost (веб-интерфейс). Никогда не логируется. Чтобы телефон достучался,
    #: сервер должен слушать не только 127.0.0.1 (host=0.0.0.0 или адрес Tailscale).
    bridge_token: str = ""
    #: Разрешить подключение по локальной сети (сервер слушает 0.0.0.0 при
    #: следующем запуске). Нужно, чтобы телефон достучался до моста по Wi-Fi.
    #: Удалённый доступ всё равно защищён bridge_token. Меняется из UI связывания.
    bridge_lan: bool = False
    #: How the built-in browser reaches sites when a VPN is on: "auto" (direct first, the VPN
    #: when a site is unreachable directly), "direct" (always bypass the VPN) or "vpn".
    browser_network: str = "auto"

    # --- Логи ---
    log_level: str = "INFO"

    # ------------------------------------------------------------------

    @field_validator("app_path", "workspace_path", mode="before")
    @classmethod
    def _expand_workspace(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip().strip('"')
            if not v:
                return PROJECT_ROOT
            return Path(os.path.expandvars(v)).expanduser()
        return v

    @field_validator("context_token_budget", "max_run_tokens", mode="before")
    @classmethod
    def _token_count(cls, v: object) -> object:
        # People write "1M", "200K", "1 000 000" in .env by hand; a plain int() would refuse
        # to start the app over it.
        return parse_token_count(v) if isinstance(v, str) else v

    @field_validator("approval_mode", mode="before")
    @classmethod
    def _migrate_mode(cls, v: object) -> object:
        # Старые значения из .env: auto/dangerous/all. Молча ломать настройку
        # пользователя нельзя, поэтому переводим её в новую раскладку.
        legacy = {"auto": "bypass", "dangerous": "manual", "all": "manual"}
        value = str(v or "manual").strip()
        return legacy.get(value, value)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, v: object) -> object:
        return str(v).upper() if v else "INFO"

    @property
    def workspace(self) -> Path:
        """Абсолютный путь к рабочей директории (создаётся при первом обращении)."""
        p = Path(self.workspace_path).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def for_workspace(self, workspace: Path | str) -> Settings:
        """Копия настроек с другой рабочей папкой (app_dir остаётся прежним).

        Raises:
            ConfigError: путь пустой, недоступен или это файл.
        """
        clone = self.model_copy()
        clone.workspace_path = validate_workspace(workspace)
        return clone

    # ------------------------------------------------------------------
    # ВАЖНО: различайте две директории.
    #   app_dir   — где живёт само приложение: логи, чаты, навыки, конфиг MCP.
    #   workspace — папка проекта пользователя, меняется в каждом чате.
    # Данные приложения НИКОГДА не должны попадать в workspace: иначе при
    # переключении папки пропадают чаты и навыки, а в чужом проекте
    # появляется мусор (storage/, logs/).
    # ------------------------------------------------------------------

    @property
    def app_dir(self) -> Path:
        p = Path(self.app_path).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.app_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_dir(self) -> Path:
        """Данные приложения: память, каталог моделей, снимки. НЕ путать с
        storage_dir — там лежат сами чаты, и класть рядом посторонние .json
        нельзя (их подхватит список чатов и поиск по переписке)."""
        p = self.app_dir / "storage"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def storage_dir(self) -> Path:
        """Где хранятся сохранённые чаты (только файлы сессий)."""
        p = self.app_dir / "storage" / "sessions"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def chat_files_dir(self, session_id: str) -> Path:
        """Персональная папка файлов чата: агент пишет сюда заметки/файлы, и они
        всегда доступны в пределах этого чата (для «обычных» чатов без проекта)."""
        safe = "".join(ch for ch in (session_id or "chat") if ch.isalnum() or ch in "-_")[:40] or "chat"
        p = self.app_dir / "storage" / "chat_files" / safe
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def skills_dir(self) -> Path:
        """Глобальные навыки: доступны в любом проекте."""
        p = self.app_dir / "skills"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def project_skills_dir(self) -> Path:
        """Навыки конкретного проекта (лежат внутри рабочей папки)."""
        return self.workspace / ".agent" / "skills"

    @property
    def skills_dirs(self) -> list[Path]:
        """Все источники навыков: глобальные + проектные."""
        dirs = [self.skills_dir]
        project = self.project_skills_dir
        if os.path.normcase(str(project)) != os.path.normcase(str(self.skills_dir)):
            dirs.append(project)
        return dirs

    @property
    def mcp_config_path(self) -> Path:
        return self.app_dir / "mcp_servers.json"

    @property
    def allowed_roots(self) -> list[Path]:
        """Список корней, в которых инструментам разрешено работать."""
        roots = [self.workspace]
        for raw in self.extra_allowed_roots.replace(",", ";").split(";"):
            raw = raw.strip().strip('"')
            if raw:
                roots.append(Path(os.path.expandvars(raw)).expanduser().resolve())
        return roots

    def problems(self) -> list[str]:
        """Список проблем конфигурации (не исключения — просто предупреждения для UI)."""
        issues: list[str] = []
        if not self.llm_api_key_effective or self.llm_api_key_effective.startswith("your_"):
            issues.append(
                "LLM_API_KEY не задан. Добавьте ключ в .env "
                "(см. .env.example). Без него запросы к модели невозможны."
            )
        if not self.default_model:
            issues.append("DEFAULT_MODEL не задан.")
        if self.allow_outside_workspace:
            issues.append(
                "ALLOW_OUTSIDE_WORKSPACE=true — инструменты могут читать/писать "
                "в любом месте диска. Отключите, если это не нужно."
            )
        return issues

    @property
    def llm_api_key_effective(self) -> str:
        """Ключ модели с поддержкой прежнего имени настройки."""
        return self.llm_api_key or self.openrouter_api_key


def validate_workspace(path: Path | str) -> Path:
    """Проверяет папку проекта перед тем, как отдать её агенту.

    Создаёт папку, только если существует её родитель — иначе опечатка в пути
    порождала бы мусорные директории где попало.
    """
    raw = str(path).strip().strip('"').strip("'")
    if not raw:
        raise ConfigError("Путь к рабочей папке пуст.")

    try:
        target = Path(os.path.expandvars(raw)).expanduser().resolve()
    except OSError as exc:
        raise ConfigError(f"Некорректный путь '{raw}': {exc}") from exc

    if target.is_file():
        raise ConfigError(f"'{target}' — это файл, а нужна папка.")

    if not target.exists():
        if not target.parent.exists():
            raise ConfigError(
                f"Папка '{target}' не существует, и родительская папка тоже. "
                "Выберите существующую папку через кнопку «Обзор»."
            )
        try:
            target.mkdir(parents=False)
        except OSError as exc:
            raise ConfigError(f"Не удалось создать папку '{target}': {exc}") from exc

    if not os.access(target, os.R_OK):
        raise ConfigError(f"Нет доступа на чтение папки '{target}'.")

    return target


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам. Используйте везде вместо создания Settings()."""
    return Settings()


def reload_settings() -> Settings:
    """Сброс кэша (нужно в тестах и при смене .env на лету)."""
    get_settings.cache_clear()
    return get_settings()
