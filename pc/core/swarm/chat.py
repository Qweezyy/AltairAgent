"""Общий чат — единственный канал связи между агентами.

Устройство намеренно простое: упорядоченный журнал сообщений с монотонным
`seq`. По `seq` каждый агент понимает, какие сообщения он ещё не читал —
так же, как человек видит непрочитанные в мессенджере.

Чат не знает про модель, инструменты и оркестратор: это просто разделяемая
доска объявлений. Потокобезопасность не нужна — весь swarm живёт в одном
event loop, гонок по общей памяти нет.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Одно сообщение в общем чате."""

    seq: int
    author: str
    text: str

    def render(self) -> str:
        return f"#{self.seq} [{self.author}]: {self.text}"


@dataclass(slots=True)
class GroupChat:
    """Разделяемый журнал сообщений всех участников."""

    _messages: list[ChatMessage] = field(default_factory=list)

    def post(self, author: str, text: str) -> ChatMessage:
        """Добавляет сообщение и возвращает его (с присвоенным `seq`)."""
        text = text.strip()
        if not text:
            raise ValueError("Пустое сообщение в чат недопустимо.")
        message = ChatMessage(seq=len(self._messages) + 1, author=author, text=text)
        self._messages.append(message)
        return message

    def since(self, after_seq: int) -> list[ChatMessage]:
        """Сообщения, появившиеся строго после `after_seq`."""
        if after_seq <= 0:
            return list(self._messages)
        return [m for m in self._messages if m.seq > after_seq]

    def last_seq(self) -> int:
        """Номер последнего сообщения (0, если чат пуст)."""
        return self._messages[-1].seq if self._messages else 0

    def all(self) -> list[ChatMessage]:
        return list(self._messages)

    def transcript(self, *, author: str | None = None) -> str:
        """Весь чат текстом. `author` — от чьего лица (его реплики помечаются «вы»)."""
        if not self._messages:
            return "(чат пуст)"
        lines = []
        for m in self._messages:
            who = f"{m.author} (вы)" if author and m.author == author else m.author
            lines.append(f"#{m.seq} [{who}]: {m.text}")
        return "\n".join(lines)
