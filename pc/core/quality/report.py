"""Сжатие вывода тестов и линтеров до того, что реально нужно модели.

Полный вывод pytest на большом проекте — это десятки тысяч символов, из
которых полезны 20 строк. Отдавать их целиком значит забить контекст и
заплатить за токены, которые ничего не решают.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Строки итогов pytest: "5 failed, 120 passed in 3.21s"
_PYTEST_SUMMARY = re.compile(r"^=+\s*(.*\b(?:passed|failed|error|no tests ran).*?)\s*=+$", re.IGNORECASE)
_PYTEST_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$")
_COUNTS = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")

#: ruff/eslint/mypy: "path:line:col: CODE message"
_LINT_LINE = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<msg>.+)$")


@dataclass(slots=True)
class CheckReport:
    tool: str
    ok: bool
    summary: str = ""
    failures: list[str] = field(default_factory=list)
    details: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    timed_out: bool = False

    def render(self, limit: int = 4000) -> str:
        lines = [f"[{self.tool}] {'успех' if self.ok else 'ЕСТЬ ПРОБЛЕМЫ'}"]
        if self.summary:
            lines.append(self.summary)
        if self.failures:
            lines.append("")
            lines.append(f"Проблемы ({len(self.failures)}):")
            lines.extend(f"  {item}" for item in self.failures[:25])
            if len(self.failures) > 25:
                lines.append(f"  … ещё {len(self.failures) - 25}")
        if self.details:
            lines.append("")
            lines.append("Подробности:")
            lines.append(self.details[:limit])
        return "\n".join(lines)


def _tail(text: str, lines: int) -> str:
    rows = [row for row in text.splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def parse_pytest(stdout: str, stderr: str, returncode: int, timed_out: bool) -> CheckReport:
    report = CheckReport(tool="pytest", ok=returncode == 0 and not timed_out, timed_out=timed_out)
    text = f"{stdout}\n{stderr}"

    for line in text.splitlines():
        summary_match = _PYTEST_SUMMARY.match(line.strip())
        if summary_match:
            report.summary = summary_match.group(1)

        failed_match = _PYTEST_FAILED.match(line.strip())
        if failed_match:
            test = failed_match.group(1)
            reason = (failed_match.group(2) or "").strip()
            report.failures.append(f"{test}{' — ' + reason if reason else ''}")

    if not report.summary:
        # В режиме -q итог печатается без рамки из '=': "1 failed, 1 passed in 0.29s".
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if _COUNTS.search(stripped) and " in " in stripped:
                report.summary = stripped
                break

    if report.summary:
        report.counts = {name: int(count) for count, name in _COUNTS.findall(report.summary)}

    if not report.ok:
        # Traceback последнего падения информативнее всего остального вывода.
        marker = text.rfind("\n____")
        if marker == -1:
            marker = text.rfind("\nE   ")
        report.details = _tail(text[marker:] if marker != -1 else text, 40)

    if timed_out:
        report.summary = "прогон прерван по таймауту"
    elif not report.summary:
        report.summary = "не удалось разобрать итог pytest"
    return report


def parse_lint(tool: str, stdout: str, stderr: str, returncode: int, timed_out: bool) -> CheckReport:
    report = CheckReport(tool=tool, ok=returncode == 0 and not timed_out, timed_out=timed_out)
    text = f"{stdout}\n{stderr}"

    for line in text.splitlines():
        stripped = line.strip()
        match = _LINT_LINE.match(stripped)
        if match and not stripped.startswith(("Found", "warning:", "note:")):
            report.failures.append(stripped[:220])

    if report.ok:
        report.summary = "замечаний нет"
    else:
        report.summary = f"замечаний: {len(report.failures)}" if report.failures else "проверка не прошла"
        if not report.failures:
            report.details = _tail(text, 30)
    if timed_out:
        report.summary = "проверка прервана по таймауту"
    return report


def parse_generic(tool: str, stdout: str, stderr: str, returncode: int, timed_out: bool) -> CheckReport:
    """Для команд, формат вывода которых нам неизвестен."""
    report = CheckReport(tool=tool, ok=returncode == 0 and not timed_out, timed_out=timed_out)
    report.summary = "успех" if report.ok else f"код возврата {returncode}"
    if not report.ok:
        report.details = _tail(f"{stdout}\n{stderr}", 40)
    if timed_out:
        report.summary = "прервано по таймауту"
    return report


def build_report(tool: str, stdout: str, stderr: str, returncode: int, timed_out: bool) -> CheckReport:
    if tool == "pytest":
        return parse_pytest(stdout, stderr, returncode, timed_out)
    if tool in ("ruff", "eslint", "mypy", "tsc"):
        return parse_lint(tool, stdout, stderr, returncode, timed_out)
    return parse_generic(tool, stdout, stderr, returncode, timed_out)
