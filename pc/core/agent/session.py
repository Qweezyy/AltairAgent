"""The conversation of one chat.

Two rules:

* Nothing of the conversation is ever lost. `messages` keeps every message as it was
  written, across versions, restarts and model switches. What the model gets is a *view*
  of it (`view()` / `snapshot()`): old tool outputs masked, stale page states replaced,
  the oldest part folded into a summary. Those are marks next to the original message
  (`_view`, `_hidden`, `_drop_calls`), never an edit of it, so the full text stays in the
  chat file for the user, for search and for the agent's own recall.
* The view stays valid for the provider: every role="tool" message follows the assistant
  message with its tool_call_id, or the API answers 400.
"""

from __future__ import annotations

import json
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
#: Marks on a message that shape the model's view of it; the message itself is kept whole.
VIEW = "_view"            # the content the model gets instead of the original
HIDDEN = "_hidden"        # not sent at all (folded into a summary)
DROP_CALLS = "_drop_calls"  # sent without its tool calls (their results were dropped)
#: A user-role message that is a reminder waking the agent, not something the user wrote.
WAKE = "_wake"
#: A user-role message the app adds for the model (a context note), not something the user wrote.
NOTE = "_note"
#: The tool calls the model gets instead of the original ones (old writes without their text).
CALLS_VIEW = "_calls_view"
#: Arguments of old writes that are folded away: the file holds that text now, and the
#: model reads the file if it needs it. The key is dropped, not replaced with a note, so the
#: model has no placeholder text it could copy into a new write.
FOLDABLE_ARGS = {"write_file": ("content",), "edit_file": ("old_text", "new_text"), "apply_patch": ("patch",)}
#: The text that opens a message carrying images from tools (browser/vision screenshots),
#: so they can be told apart from the user's own attachments and dropped once stale.
TOOL_MEDIA_MARK = "[Tool screenshot]"
#: The two shapes of a browser result (core/browser_session.py writes them; kept in sync by a
#: test so the session layer does not import the browser).
FULL_PAGE_MARK = "Page (accessibility tree"
PAGE_CHANGES_MARK = "Page changes since your previous look"
#: Keep this many of the newest page snapshots / tool screenshots in full.
KEEP_PAGE_STATES = 2

#: Tools whose outputs are never cleared: user answers and skill recipes are not
#: reproducible by re-running a tool.
CLEARING_PROTECTED_TOOLS = frozenset({"ask", "read_skill", "phone_ask_user", "request_secret", "tool_search",
                                      "tool_output"})


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


def model_view(message: dict[str, Any]) -> dict[str, Any] | None:
    """What the model gets of one stored message: None when it is folded away."""
    if message.get(HIDDEN):
        return None
    out = {k: v for k, v in message.items() if not k.startswith("_")}
    if VIEW in message:
        out["content"] = message[VIEW]
    if CALLS_VIEW in message and not message.get(DROP_CALLS):
        out["tool_calls"] = message[CALLS_VIEW]
    if message.get(DROP_CALLS):
        out.pop("tool_calls", None)
        if not str(out.get("content") or "").strip():
            out["content"] = "[tool calls folded]"
    return out


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
    #: Our estimate of the model's view at the moment `context_tokens` was measured: what was
    #: added or masked since is the difference to today's estimate (see context_now).
    context_mark: int = 0
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
            # A wake-up from a reminder is not the user's message: the feed does not count it.
            if message.get("role") == "user" and not message.get(WAKE):
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
        for message in self.view():
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

    def answer_open_calls(self, content: str) -> int:
        """Gives every tool call still without a result that result. A chat stored mid-step
        (the app crashed or was killed while a tool ran) has such calls, and providers
        reject a history with them (HTTP 400). Returns how many were answered."""
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        added = 0
        index = 0
        while index < len(self.messages):
            message = self.messages[index]
            index += 1
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            # The results go right after the call's existing ones, in the calls' order.
            while index < len(self.messages) and self.messages[index].get("role") == "tool":
                index += 1
            for call in message["tool_calls"]:
                if call.get("id") in answered:
                    continue
                name = (call.get("function") or {}).get("name", "")
                self.messages.insert(index, {"role": "tool", "tool_call_id": call.get("id"),
                                             "name": name, "content": content})
                answered.add(call.get("id"))
                index += 1
                added += 1
        if added:
            self.updated_at = time.time()
        return added

    def append_timeline(self, entry: dict[str, Any]) -> None:
        """Adds an entry to the UI feed (a question, a step, an answer). The whole feed is
        kept: it used to keep only the last 400 entries, and the start of long chats was lost."""
        self.timeline.append(entry)

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
        self.context_mark = 0
        self.messages = []
        self.plan_steps = []
        self.artifacts = []
        self.timeline = []
        if self.system_prompt:
            self.set_system_prompt(self.system_prompt)
        self.updated_at = time.time()

    # ------------------------------------------------------------------

    def view(self) -> list[dict[str, Any]]:
        """The conversation as the model gets it (see the module docstring)."""
        return [v for m in self.messages if (v := model_view(m)) is not None]

    def token_estimate(self) -> int:
        return estimate_tokens(self.view())

    def context_now(self) -> tuple[int, bool]:
        """How full the model's window is right now, and whether that rests on the provider's
        count. The last exact count (system prompt and tool schemas included) plus what our
        estimate says changed since: new messages add, masked or folded ones subtract. Without
        an exact count yet, the estimate alone."""
        if not self.context_tokens:
            return self.token_estimate(), False
        return max(0, self.context_tokens + self.token_estimate() - self.context_mark), True

    def _start_index(self) -> int:
        """Index of the first message after the system prompt."""
        return 1 if self.messages and self.messages[0].get("role") == "system" else 0

    def _visible_tail(self) -> list[int]:
        """Indexes of the messages after the system prompt that the model still sees."""
        return [i for i in range(self._start_index(), len(self.messages)) if not self.messages[i].get(HIDDEN)]

    def overflow_count(self, budget_tokens: int, keep_recent: int = 6) -> int:
        """How many of the oldest visible messages to fold so the view fits the budget.

        Counts whole assistant→tool groups (a pair must not be split: the provider would
        answer 400). Changes nothing.
        """
        tail = [model_view(self.messages[i]) or {} for i in self._visible_tail()]
        if len(tail) <= keep_recent:
            return 0
        total = self.token_estimate()
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
        """The model's view of the first `count` visible messages after the system prompt."""
        return [model_view(self.messages[i]) or {} for i in self._visible_tail()[:count]]

    def replace_prefix(self, count: int, note: str) -> int:
        """Folds the first `count` visible messages after the system prompt into one note.

        The messages stay in the chat (marked hidden from the model); the note goes where
        they end, so the history keeps its order. Returns how many were folded.
        """
        folded = self._visible_tail()[:count] if count > 0 else []
        if not folded:
            return 0
        for i in folded:
            self.messages[i][HIDDEN] = True
        self.messages.insert(folded[-1] + 1, {"role": "system", "content": note, "_summary": True})
        self.updated_at = time.time()
        return len(folded)

    def clear_old_tool_results(
        self,
        keep_recent: int = 8,
        min_chars: int = 1_000,
        min_free_chars: int = 0,
        protected: frozenset[str] = CLEARING_PROTECTED_TOOLS,
    ) -> tuple[int, int]:
        """Masks old, large tool outputs for the model with a short note ("observation masking").

        The call itself (tool name and arguments) stays, so the model still knows what it
        did and can repeat it; the output stays in the chat too, only the model's view of it
        changes. The newest `keep_recent` outputs, small ones and outputs of `protected`
        tools (user answers, skills) are kept. Nothing happens unless at least
        `min_free_chars` would be freed: each change invalidates the prompt cache from
        that point, so it must be worth it. Returns (outputs masked, chars freed).
        """
        tool_indexes = [i for i, m in enumerate(self.messages) if m.get("role") == "tool" and not m.get(HIDDEN)]
        # The latest whole page stays: the page diffs after it are read against it.
        base = self._latest_full_page()
        candidates = []
        for index in tool_indexes[: max(0, len(tool_indexes) - keep_recent)]:
            message = self.messages[index]
            content = message.get("content")
            if (
                isinstance(content, str)
                and len(content) >= min_chars
                and message.get("name") not in protected
                and VIEW not in message
                and index != base
            ):
                candidates.append(index)

        # The writes of the same old part: their file text goes too (in the same batch, so
        # the prompt cache breaks no more often than it does for the outputs).
        if keep_recent <= 0:
            cutoff = len(self.messages)
        else:
            cutoff = tool_indexes[len(tool_indexes) - keep_recent] if len(tool_indexes) > keep_recent else -1
        # From the call that owns the first kept output on, everything stays whole.
        while 0 < cutoff < len(self.messages) and not self.messages[cutoff].get("tool_calls"):
            cutoff -= 1
        folds = {i: f for i in range(cutoff) if (f := self._folded_calls(self.messages[i], min_chars))}
        freed = sum(len(self.messages[i]["content"]) for i in candidates)
        freed += sum(saved for _, saved in folds.values())
        if (not candidates and not folds) or freed < min_free_chars:
            return 0, 0
        for index, (calls, _) in folds.items():
            self.messages[index][CALLS_VIEW] = calls
        for index in candidates:
            message = self.messages[index]
            size = len(message["content"])
            message[VIEW] = (
                f"{CLEARED_MARK} output of {message.get('name') or 'tool'} ({size} chars) was cleared to "
                f"save context. tool_output(id=\"{message.get('tool_call_id')}\") returns it exactly.]"
            )
            freed -= len(message[VIEW])
        self.updated_at = time.time()
        return len(candidates), freed

    @staticmethod
    def _folded_calls(message: dict[str, Any], min_chars: int) -> tuple[list[dict[str, Any]], int] | None:
        """The message's tool calls with the big text of writes removed, and the chars saved;
        None when there is nothing to fold (or it is folded already)."""
        if message.get("role") != "assistant" or CALLS_VIEW in message or not message.get("tool_calls"):
            return None
        saved = 0
        calls = []
        for call in message["tool_calls"]:
            fn = call.get("function") or {}
            keys = FOLDABLE_ARGS.get(fn.get("name", ""))
            raw = fn.get("arguments") or ""
            if keys and len(raw) >= min_chars:
                try:
                    args = json.loads(raw)
                except ValueError:
                    args = None
                if isinstance(args, dict) and any(k in args for k in keys):
                    slim = json.dumps({k: v for k, v in args.items() if k not in keys}, ensure_ascii=False)
                    saved += len(raw) - len(slim)
                    calls.append({**call, "function": {**fn, "arguments": slim}})
                    continue
            calls.append(call)
        return (calls, saved) if saved > 0 else None

    def supersede_page_states(self, keep: int = KEEP_PAGE_STATES, min_free_chars: int = 30_000) -> tuple[int, int]:
        """Drops page snapshots and tool screenshots that later ones made stale.

        Every browser action returns the whole page, and after the next action the old
        snapshot describes a page that is gone. Left in the history, a long browsing task
        piles up hundreds of thousands of tokens that are resent on every request. The
        newest `keep` whole pages stay with the page diffs that follow them, and the newest
        `keep` screenshots; older ones become a one-line note (the call and its arguments
        remain). Batched by `min_free_chars`, because each change
        invalidates the prompt cache from that point. Returns (items dropped, chars freed).
        """
        pages = [i for i, m in enumerate(self.messages)
                 if m.get("role") == "tool" and str(m.get("name") or "").startswith("browser_")
                 and isinstance(m.get("content"), str) and VIEW not in m and not m.get(HIDDEN)]
        full = [i for i in pages if FULL_PAGE_MARK in self.messages[i]["content"]
                or len(self.messages[i]["content"]) >= 1_500 and PAGE_CHANGES_MARK not in self.messages[i]["content"]]
        # The newest `keep` whole pages stay, and every page diff after the oldest of them:
        # a diff means nothing without the page it was taken against.
        kept_from = full[-keep] if len(full) >= keep else (full[0] if full else len(self.messages))
        full_set = set(full)
        snapshots = [i for i in pages if i < kept_from
                     and (i in full_set or PAGE_CHANGES_MARK in self.messages[i]["content"])]
        shots = [i for i, m in enumerate(self.messages)
                 if m.get("role") == "user" and isinstance(m.get("content"), list)
                 and VIEW not in m and not m.get(HIDDEN)
                 and _first_text(m["content"]).startswith(TOOL_MEDIA_MARK)
                 and any(isinstance(p, dict) and p.get("type") != "text" for p in m["content"])]
        stale_snapshots = snapshots
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
            message[VIEW] = (
                f"{CLEARED_MARK} page state from {message.get('name')}: {first_line} ... "
                f"({len(message['content'])} chars) is superseded by a later snapshot.]"
            )
        for index in stale_shots:
            text = _first_text(self.messages[index]["content"])
            self.messages[index][VIEW] = f"{text}\n{CLEARED_MARK} screenshot removed: a newer one supersedes it.]"
        self.updated_at = time.time()
        return len(stale_snapshots) + len(stale_shots), freed

    def _latest_full_page(self) -> int:
        for index in range(len(self.messages) - 1, -1, -1):
            m = self.messages[index]
            if (m.get("role") == "tool" and str(m.get("name") or "").startswith("browser_") and not m.get(HIDDEN)
                    and isinstance(m.get("content"), str) and FULL_PAGE_MARK in m["content"]):
                return index
        return -1

    def trim(self, budget_tokens: int, keep_recent: int = 6) -> int:
        """Folds the oldest messages until the view fits the budget (the fallback when a
        summary cannot be made). Returns how many were folded."""
        count = self.overflow_count(budget_tokens, keep_recent)
        if not count:
            return 0
        return self.replace_prefix(
            count,
            f"[The start of the conversation ({count} messages) is folded to fit the context. It is "
            "kept in this chat: search it with the chat search tool if you need the details.]",
        )

    def snapshot(self) -> list[dict[str, Any]]:
        """The model's view as fresh dicts (safe from changes while a request runs)."""
        return self.view()

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
            "context_mark": self.context_mark,
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
            context_mark=int(data.get("context_mark") or 0),
            created_at=data.get("created_at") or time.time(),
            updated_at=data.get("updated_at") or time.time(),
        )
