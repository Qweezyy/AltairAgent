"""Независимый верификатор шага и тиры риска.

Идея: перед выполнением каждого инструмента ядро само, НЕ доверяя ни решению
модели, ни её словам, детерминированно оценивает риск конкретного вызова по его
реальным аргументам. Это «независимый верификатор» — правила, а не вторая модель:
без задержек, без трат, предсказуемо и воспроизводимо в тестах.

Результат — тир риска и причины:

  * ``safe``     — только чтение, ничего не меняет;
  * ``low``      — правка файлов внутри рабочей папки;
  * ``moderate`` — запуск команд/кода или обращение к сети;
  * ``high``     — необратимое или потенциально разрушительное действие
                   (rm -r, git push --force, sudo, DROP TABLE, системные пути);
  * ``critical`` — катастрофическое: форматирование диска, rm -rf, fork-бомба.

Как тир влияет на выполнение (см. Tool.invoke):
  * ``critical`` из списка BLOCK — блокируется ВСЕГДА, даже с подтверждением
    (это защита от катастрофической опечатки модели, а не песочница);
  * ``high``/``critical`` — требуют подтверждения пользователя, даже если режим
    разрешений выполнил бы их сам (кроме явного «Без подтверждений» для high).

ВАЖНО: разрушительные паттерны сверяются ТОЛЬКО с аргументами команд и сетевых
вызовов (execute/network). Содержимое файлов не сканируется — иначе запись
легального деплой-скрипта с `rm -rf` внутри блокировалась бы по ошибке.

Модуль намеренно не импортирует ничего из core.tools — иначе возник бы цикл
(tools.base -> security.risk -> tools...). Паттерны здесь каноничны; shell.py
берёт список блокировок отсюда.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Порядок тиров от безопасного к катастрофическому (для сравнения по рангу).
TIER_ORDER: tuple[str, ...] = ("safe", "low", "moderate", "high", "critical")

#: Базовый тир по категории инструмента.
_CATEGORY_BASE: dict[str, str] = {
    "read": "safe",
    "edit": "low",
    "execute": "moderate",
    "network": "moderate",
}

#: Команды, которые не выполняются НИКОГДА — даже с подтверждением. Защита от
#: катастрофической опечатки модели. Список каноничен: shell.py импортирует его.
BLOCK_PATTERNS: list[tuple[str, str]] = [
    (r"\bformat\s+[a-z]:", "форматирование диска"),
    (r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf]", "рекурсивное удаление (rm -rf)"),
    (r"\bremove-item\b.*-recurse.*\b[a-z]:\\?\s*$", "рекурсивное удаление диска"),
    (r"\bdel\s+/[sfq]", "массовое удаление (del /s /q)"),
    (r"\brd\s+/s\b", "рекурсивное удаление директории"),
    (r"\bdiskpart\b", "управление разделами диска"),
    (r"\bmkfs\b", "форматирование файловой системы"),
    (r"\bdd\b[^\n]*\bof=/dev/", "перезапись устройства (dd of=/dev/…)"),
    (r"\b(shutdown|restart-computer)\b", "выключение/перезагрузка компьютера"),
    (r"\bcipher\s+/w", "затирание свободного места"),
    (r"\breg\s+delete\s+hk(lm|ey_local_machine)", "удаление веток реестра HKLM"),
    (r":\(\)\s*\{.*\}\s*;\s*:", "fork-бомба"),
]

#: Паттерны повышенного риска: не блокируются, но требуют подтверждения.
HIGH_PATTERNS: list[tuple[str, str]] = [
    (r"\bgit\s+push\b[^\n]*(--force|\s-f\b)", "принудительный git push (перезапись истории)"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard (потеря незакоммиченных изменений)"),
    (r"\bgit\s+clean\s+-[a-z]*f", "git clean -f (удаление неотслеживаемых файлов)"),
    (r"\bsudo\b", "выполнение с правами суперпользователя (sudo)"),
    (r"\bchmod\s+(-[a-z]*\s+)*7{3}\b", "снятие всех ограничений доступа (chmod 777)"),
    (r"\b(curl|wget)\b[^|\n]*\|\s*(sh|bash|zsh)", "запуск скрипта прямо из сети (curl|wget | sh)"),
    (r"\b(iwr|invoke-webrequest)\b[^\n]*\|\s*iex", "запуск кода из сети (iwr | iex)"),
    (r"\bdrop\s+(table|database)\b", "удаление таблицы/базы данных (DROP)"),
    (r"\btruncate\s+table\b", "очистка таблицы (TRUNCATE TABLE)"),
    (r"\bnpm\s+publish\b", "публикация пакета в реестр (npm publish)"),
    (r"\bremove-item\b[^\n]*-recurse", "рекурсивное удаление (Remove-Item -Recurse)"),
    (r"[A-Za-z]:\\Windows\\", "обращение к системной папке Windows"),
    (r"(^|[\s'\"])/(etc|usr|bin|boot|sys|dev)/", "обращение к системным путям"),
]

# Компилируем один раз: verifier зовётся на каждый вызов инструмента.
_BLOCK = [(re.compile(p, re.IGNORECASE), why) for p, why in BLOCK_PATTERNS]
_HIGH = [(re.compile(p, re.IGNORECASE), why) for p, why in HIGH_PATTERNS]


def rank(tier: str) -> int:
    """Числовой ранг тира (для сравнений). Неизвестный тир = ``low``."""
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return TIER_ORDER.index("low")


def _max_tier(a: str, b: str) -> str:
    return a if rank(a) >= rank(b) else b


@dataclass(slots=True)
class RiskAssessment:
    """Вердикт независимого верификатора для одного вызова инструмента."""

    tier: str = "low"
    reasons: list[str] = field(default_factory=list)
    #: True -> действие запрещено всегда, даже с подтверждением.
    blocked: bool = False


def collect_text(value: Any) -> str:
    """Собирает все строковые значения аргументов в один текст для сканирования."""
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(value)
    return "\n".join(out)


def assess(
    *,
    category: str,
    irreversible: bool = False,
    scan_text: str = "",
) -> RiskAssessment:
    """Оценивает риск вызова по метаданным инструмента и тексту его аргументов.

    ``scan_text`` вызывающий заполняет ТОЛЬКО для команд/сетевых вызовов
    (execute/network); содержимое файлов сюда попадать не должно.
    """
    tier = _CATEGORY_BASE.get(category, "low")
    reasons: list[str] = []
    blocked = False

    if irreversible:
        tier = _max_tier(tier, "high")

    if scan_text:
        for rx, why in _BLOCK:
            if rx.search(scan_text):
                tier = "critical"
                blocked = True
                reasons.append(why)
        for rx, why in _HIGH:
            if rx.search(scan_text):
                tier = _max_tier(tier, "high")
                reasons.append(why)

    return RiskAssessment(tier=tier, reasons=reasons, blocked=blocked)
