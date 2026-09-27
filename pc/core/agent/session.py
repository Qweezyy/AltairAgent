"""История диалога одной сессии.

Главная задача — держать историю валидной. Ключевое правило OpenAI-совместимых
API: у каждого сообщения role="tool" должен быть предшествующий assistant с
соответствующим tool_call_id. Обрезка истории обязана это сохранять, иначе
провайдер вернёт 400 и агент «сломается» без видимой причины.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.llm.base import AssistantTurn

#: Грубая оценка: 1 токен ≈ 3 символа для смеси русского и кода.
CHARS_PER_TOKEN = 3


#: Метка указаний, действующих один запуск (см. add_run_note).
RUN_NOTE_PREFIX = "[настройки запуска] "

#: Во что оцениваем одно фото/видео/аудио вложение. По длине base64 считать
#: нельзя: картинка на 5 МБ дала бы «2 млн токенов» и обрезка выкинула бы всю
#: историю, хотя провайдер берёт за изображение на порядки меньше.
TOKENS_PER_MEDIA_PART = 1500

#: Prefix of a cleared tool output (see Session.clear_old_tool_results).
CLEARED_MARK = "[Earlier"
#: Tools whose outputs are never cleared: user answers and skill recipes are not
#: reproducible by re-running a tool.
#: The text that opens a message carrying images from tools (browser/vision screenshots),
#: so they can be told apart from the user's own attachments and dropped once stale.
TOOL_MEDIA_MARK = "[Tool screenshot]"
#: Keep this many of the newest page snapshots / tool screenshots in full.
KEEP_PAGE_STATES = 2

CLEARING_PROTECTED_TOOLS = frozenset({"ask", "read_skill", "phone_ask_user", "request_secret", "tool_search"})


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(str(part.get("text", "")))
                else:
                    total += TOKENS_PER_MEDIA_PART * CHARS_PER_TOKEN
        else:
            total += len(str(content))
        for call in message.get("tool_calls") or []:
            total += len(str(call.get("function", {}).get("arguments", "")))
        total += 8  # служебные поля роли/разделители
    return total // CHARS_PER_TOKEN


def _first_text(content: list[Any]) -> str:
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            return str(part.get("text") or "")
    return ""


@dataclass
class Session:
    """Диалог с моделью. Один объект = одна вкладка/подключение пользователя."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "Новый диалог"
    workspace: str = ""
    model: str = ""
    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan_steps: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    #: Режим разрешений выбирается в каждом чате отдельно.
    approval_mode: str = ""
    #: Лента для интерфейса: вопросы, шаги и ответы в порядке появления.
    #: Хранится отдельно от messages, потому что messages — формат модели,
    #: и по нему нельзя восстановить, что именно агент делал.
    timeline: list[dict[str, Any]] = field(default_factory=list)
    #: What the provider reported the context to be on the latest request (input incl. cached
    #: tokens, plus the answer). 0 = not known yet; the ring falls back to an estimate.
    context_tokens: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------

    def set_system_prompt(self, prompt: str) -> None:
        """Системный промпт всегда первый и всегда один."""
        self.system_prompt = prompt
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = prompt
        else:
            self.messages.insert(0, {"role": "system", "content": prompt})
        self.updated_at = time.time()

    def add_user(self, text: str, parts: list[dict[str, Any]] | None = None) -> None:
        """Сообщение пользователя. `parts` — фото, видео и аудио вложения.

        При наличии вложений content становится массивом: это формат
        мультимодальных сообщений OpenAI/OpenRouter. Текст идёт первым, чтобы
        модель сначала прочитала задачу, а потом смотрела приложенное.
        """
        if parts:
            content: Any = [{"type": "text", "text": text}, *parts]
        else:
            content = text
        self.messages.append({"role": "user", "content": content})
        # Если заголовок по умолчанию, генерируем по первому сообщению
        if self.title == "Новый диалог" and text.strip():
            first_line = text.strip().splitlines()[0][:50]
            self.title = first_line + ("..." if len(first_line) == 50 else "")
        self.updated_at = time.time()

    def rewind_to_user_turn(self, turn: int) -> bool:
        """Откатывает диалог к состоянию перед указанным запросом пользователя.

        `turn` — порядковый номер сообщения пользователя (0 — первое). Удаляются
        это сообщение и всё, что после него, — и из истории модели, и из ленты
        интерфейса. Нужно для «повторить задачу»: перезапуск должен начаться с
        чистого места, иначе прошлый ответ мешает модели ответить заново.

        Возвращает True, если точка найдена и откат выполнен.
        """
        # Индекс turn-го сообщения пользователя в messages.
        seen = -1
        cut_at = None
        for index, message in enumerate(self.messages):
            if message.get("role") == "user":
                seen += 1
                if seen == turn:
                    cut_at = index
                    break
        if cut_at is None:
            return False

        del self.messages[cut_at:]

        # Лента: обрезаем по turn-й записи kind="user".
        seen = -1
        for index, entry in enumerate(self.timeline):
            if entry.get("kind") == "user":
                seen += 1
                if seen == turn:
                    del self.timeline[index:]
                    break
        self.updated_at = time.time()
        return True

    def contains_media(self) -> bool:
        """Есть ли в истории фото, видео или аудио.

        Важно для выбора модели: картинка остаётся в диалоге и после своего
        запроса, поэтому все следующие ответы тоже должна давать модель,
        которая умеет её читать. Иначе провайдер вернёт «no endpoints found
        that support image input» — и виноватым будет выглядеть агент.
        """
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") != "text" for part in content
            ):
                return True
        return False

    def add_assistant_turn(self, turn: AssistantTurn) -> None:
        message = turn.to_message()
        # Пустой assistant без tool_calls только мусорит историю.
        if message.get("content") is None and not message.get("tool_calls"):
            return
        self.messages.append(message)
        self.updated_at = time.time()

    def add_tool_result(self, call_id: str, name: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
        )
        self.updated_at = time.time()

    def append_timeline(self, entry: dict[str, Any], limit: int = 400) -> None:
        """Добавляет запись в ленту интерфейса (вопрос, шаг, ответ)."""
        self.timeline.append(entry)
        if len(self.timeline) > limit:
            del self.timeline[: len(self.timeline) - limit]

    def add_note(self, text: str) -> None:
        """Служебная реплика системы внутри диалога (например, лимит шагов)."""
        self.messages.append({"role": "system", "content": text})
        self.updated_at = time.time()

    def add_run_note(self, text: str) -> None:
        """Указание, действующее только на текущий запуск.

        Помечается префиксом, чтобы следующий запуск мог его снять: иначе
        выключенный однажды веб-поиск остался бы выключенным навсегда.
        """
        self.add_note(f"{RUN_NOTE_PREFIX}{text}")

    def clear_run_notes(self) -> int:
        """Убирает указания прошлого запуска. Возвращает, сколько убрано."""
        before = len(self.messages)
        self.messages = [
            message
            for message in self.messages
            if not (
                message.get("role") == "system"
                and isinstance(message.get("content"), str)
                and message["content"].startswith(RUN_NOTE_PREFIX)
            )
        ]
        return before - len(self.messages)

    def set_plan(self, steps: list[dict[str, Any]]) -> None:
        self.plan_steps = steps
        self.updated_at = time.time()

    def add_artifact(self, artifact: dict[str, Any]) -> None:
        # Избегаем дублей по path
        path = artifact.get("path")
        self.artifacts = [a for a in self.artifacts if a.get("path") != path]
        self.artifacts.append(artifact)
        self.updated_at = time.time()

    def reset(self) -> None:
        self.context_tokens = 0
        self.messages = []
        self.plan_steps = []
        self.artifacts = []
        self.timeline = []
        if self.system_prompt:
            self.set_system_prompt(self.system_prompt)
        self.updated_at = time.time()

    # ------------------------------------------------------------------

    def token_estimate(self) -> int:
        return estimate_tokens(self.messages)

    def _start_index(self) -> int:
        """Индекс первого сообщения после системного промпта."""
        return 1 if self.messages and self.messages[0].get("role") == "system" else 0

    def overflow_count(self, budget_tokens: int, keep_recent: int = 6) -> int:
        """Сколько самых старых сообщений нужно убрать, чтобы влезть в бюджет.

        Считает целыми группами assistant→tool (обрывать пару нельзя — провайдер
        вернёт 400). Не мутирует историю.
        """
        start = self._start_index()
        tail = self.messages[start:]
        if len(tail) <= keep_recent:
            return 0
        total = estimate_tokens(self.messages)
        if total <= budget_tokens:
            return 0

        removed = 0
        index = 0
        while total > budget_tokens and (len(tail) - removed) > keep_recent and index < len(tail):
            group = 1
            if tail[index].get("role") == "assistant" and tail[index].get("tool_calls"):
                while index + group < len(tail) and tail[index + group].get("role") == "tool":
                    group += 1
            total -= estimate_tokens(tail[index : index + group])
            removed += group
            index += group
        return removed

    def peek_prefix(self, count: int) -> list[dict[str, Any]]:
        """Копии первых `count` сообщений после системного промпта."""
        start = self._start_index()
        return [dict(m) for m in self.messages[start : start + count]]

    def replace_prefix(self, count: int, note: str) -> int:
        """Убирает первые `count` сообщений после системного промпта и вставляет
        вместо них одну служебную реплику `note`. Возвращает, сколько убрано."""
        if count <= 0:
            return 0
        start = self._start_index()
        del self.messages[start : start + count]
        self.messages.insert(start, {"role": "system", "content": note})
        self.updated_at = time.time()
        return count

    def clear_old_tool_results(
        self,
        keep_recent: int = 8,
        min_chars: int = 1_000,
        min_free_chars: int = 0,
        protected: frozenset[str] = CLEARING_PROTECTED_TOOLS,
    ) -> tuple[int, int]:
        """Replaces old, large tool outputs with a short note ("observation masking").

        The call itself (tool name and arguments) stays, so the model still knows what it
        did and can repeat it. The newest `keep_recent` outputs, small ones and outputs of
        `protected` tools (user answers, skills) are kept. Nothing happens unless at least
        `min_free_chars` would be freed: each clearing invalidates the prompt cache from
        that point, so it must be worth it. Returns (outputs cleared, chars freed).
        """
        tool_indexes = [i for i, m in enumerate(self.messages) if m.get("role") == "tool"]
        candidates = []
        for index in tool_indexes[: max(0, len(tool_indexes) - keep_recent)]:
            message = self.messages[index]
            content = message.get("content")
            if (
                isinstance(content, str)
                and len(content) >= min_chars
                and message.get("name") not in protected
                and not content.startswith(CLEARED_MARK)
            ):
                candidates.append(index)

        freed = sum(len(self.messages[i]["content"]) for i in candidates)
        if not candidates or freed < min_free_chars:
            return 0, 0
        for index in candidates:
            message = self.messages[index]
            size = len(message["content"])
            message["content"] = (
                f"{CLEARED_MARK} output of {message.get('name') or 'tool'} ({size} chars) was cleared to "
                "save context. Run the tool again if you need it.]"
            )
            freed -= len(message["content"])
        self.updated_at = time.time()
        return len(candidates), freed

    def supersede_page_states(self, keep: int = KEEP_PAGE_STATES, min_free_chars: int = 30_000) -> tuple[int, int]:
        """Drops page snapshots and tool screenshots that later ones made stale.

        Every browser action returns the whole page, and after the next action the old
        snapshot describes a page that is gone. Left in the history, a long browsing task
        piles up hundreds of thousands of tokens that are resent on every request. The
        newest `keep` snapshots and screenshots stay; older ones become a one-line note (the
        call and its arguments remain). Batched by `min_free_chars`, because each change
        invalidates the prompt cache from that point. Returns (items dropped, chars freed).
        """
        snapshots = [i for i, m in enumerate(self.messages)
                     if m.get("role") == "tool" and str(m.get("name") or "").startswith("browser_")
                     and isinstance(m.get("content"), str) and len(m["content"]) >= 1_500
                     and not m["content"].startswith(CLEARED_MARK)]
        shots = [i for i, m in enumerate(self.messages)
                 if m.get("role") == "user" and isinstance(m.get("content"), list)
                 and _first_text(m["content"]).startswith(TOOL_MEDIA_MARK)
                 and any(isinstance(p, dict) and p.get("type") != "text" for p in m["content"])]
        stale_snapshots = snapshots[: max(0, len(snapshots) - keep)]
        stale_shots = shots[: max(0, len(shots) - keep)]
        media_chars = TOKENS_PER_MEDIA_PART * CHARS_PER_TOKEN
        freed = sum(len(self.messages[i]["content"]) for i in stale_snapshots)
        freed += sum(media_chars * sum(1 for p in self.messages[i]["content"]
                                       if isinstance(p, dict) and p.get("type") != "text") for i in stale_shots)
        if not (stale_snapshots or stale_shots) or freed < min_free_chars:
            return 0, 0
        for index in stale_snapshots:
            message = self.messages[index]
            first_line = message["content"].strip().splitlines()[0][:160]
            message["content"] = (
                f"{CLEARED_MARK} page state from {message.get('name')}: {first_line} ... "
                f"({len(message['content'])} chars) is superseded by a later snapshot.]"
            )
        for index in stale_shots:
            text = _first_text(self.messages[index]["content"])
            self.messages[index]["content"] = f"{text}\n{CLEARED_MARK} screenshot removed: a newer one supersedes it.]"
        self.updated_at = time.time()
        return len(stale_snapshots) + len(stale_shots), freed

    def trim(self, budget_tokens: int, keep_recent: int = 6) -> int:
        """Убирает самые старые сообщения, пока история не влезет в бюджет.

        Грубый вариант (просто выброс с пометкой) — запасной для компакции.
        Возвращает количество удалённых сообщений.
        """
        count = self.overflow_count(budget_tokens, keep_recent)
        if not count:
            return 0
        return self.replace_prefix(
            count,
            f"[Начало диалога свёрнуто: удалено {count} старых сообщений. "
            "Если нужны детали — перечитай файлы инструментами.]",
        )

    def snapshot(self) -> list[dict[str, Any]]:
        """Копия истории для передачи в LLM (защита от мутаций во время запроса)."""
        return [dict(message) for message in self.messages]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "workspace": self.workspace,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "plan_steps": self.plan_steps,
            "artifacts": self.artifacts,
            "approval_mode": self.approval_mode,
            "timeline": self.timeline,
            "context_tokens": self.context_tokens,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:12],
            title=data.get("title") or "Новый диалог",
            workspace=data.get("workspace") or "",
            model=data.get("model") or "",
            system_prompt=data.get("system_prompt") or "",
            messages=data.get("messages") or [],
            plan_steps=data.get("plan_steps") or [],
            artifacts=data.get("artifacts") or [],
            approval_mode=data.get("approval_mode") or "",
            timeline=data.get("timeline") or [],
            context_tokens=int(data.get("context_tokens") or 0),
            created_at=data.get("created_at") or time.time(),
            updated_at=data.get("updated_at") or time.time(),
        )
