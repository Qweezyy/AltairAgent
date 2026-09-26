"""Инструмент покрытия тестами: показать, какой код НЕ проверен.

Зачем: чтобы «дописать тесты на граничные случаи», надо сначала увидеть, где
дыры. Инструмент прогоняет тесты с измерением покрытия и возвращает процент плюс
конкретные непокрытые строки — по ним агент прицельно пишет недостающие тесты,
а не гадает.

Считает через `coverage.py` (Python). Данные и JSON-отчёт кладём во временные
файлы, чтобы не сорить в рабочей папке.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from core.i18n import tr
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext, ToolResult


def _python_exe() -> str:
    """Интерпретатор для запуска coverage: из PATH (работает и в собранном exe)."""
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


def _ranges(lines: list[int]) -> str:
    """Сжимает список номеров строк в диапазоны: [1,2,3,7] -> '1-3, 7'."""
    if not lines:
        return ""
    lines = sorted(set(lines))
    parts: list[str] = []
    start = prev = lines[0]
    for n in lines[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(parts)


class CoverageArgs(BaseModel):
    source: str = Field(
        default=".", description="Что измерять: файл или папка с кодом (например core/export.py)"
    )
    tests: str = Field(default=".", description="Где брать тесты (файл/папка). По умолчанию все")
    timeout: float = Field(default=300.0, ge=5, le=1800, description="Лимит времени, секунды")


class TestCoverageTool(Tool):
    name = "test_coverage"
    description = (
        "Прогоняет тесты с измерением покрытия и показывает, какой код НЕ проверен: общий процент "
        "и конкретные непокрытые строки по файлам. Используй, чтобы прицельно дописать тесты на "
        "неохваченные ветки и граничные случаи. Только для Python (coverage.py)."
    )
    Args = CoverageArgs
    category = "execute"
    dangerous = True  # запускает тесты = выполняет код проекта
    timeout = None

    def approval_reason(self, args: CoverageArgs) -> str:  # type: ignore[override]
        return tr("appr.coverage", source=args.source, tests=args.tests)

    def auto_verdict(self, args: CoverageArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        return "allow"  # это проверка, как и run_tests

    async def run(self, args: CoverageArgs, ctx: ToolContext) -> ToolResult:
        source = resolve_path(args.source, settings=ctx.settings, must_exist=True)
        tests = resolve_path(args.tests, settings=ctx.settings, must_exist=True)
        workspace = ctx.settings.workspace
        # coverage --source принимает каталог; для файла берём его папку, а в
        # отчёте оставим только сам файл.
        source_dir = source if source.is_dir() else source.parent

        return await asyncio.to_thread(
            self._measure, source, source_dir, tests, workspace, args.timeout
        )

    def _measure(
        self, source: Path, source_dir: Path, tests: Path, workspace: Path, timeout: float
    ) -> ToolResult:
        py = _python_exe()
        tmp = Path(tempfile.mkdtemp(prefix="cov-"))
        data_file = tmp / "data"
        json_file = tmp / "cov.json"
        try:
            run = subprocess.run(
                [
                    py, "-m", "coverage", "run",
                    f"--data-file={data_file}",
                    f"--source={source_dir}",
                    "-m", "pytest", str(tests), "-q",
                ],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            if run.returncode not in (0, 1):  # 1 = тесты упали, покрытие всё равно есть
                tail = (run.stderr or run.stdout or "")[-400:]
                if "No module named coverage" in tail:
                    return ToolResult.fail(
                        "coverage.py не установлен. Поставьте: pip install coverage"
                    )
                return ToolResult.fail(f"coverage run не выполнился:\n{tail}")

            export = subprocess.run(
                [py, "-m", "coverage", "json", f"--data-file={data_file}", "-o", str(json_file)],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if not json_file.exists():
                return ToolResult.fail(
                    f"coverage json не создан:\n{(export.stderr or '')[-300:]}"
                )
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"Измерение покрытия превысило лимит {timeout:g} с.")
        except (OSError, json.JSONDecodeError) as exc:
            return ToolResult.fail(f"Не удалось получить покрытие: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        return self._format(data, source, workspace, tests_failed=run.returncode == 1)

    def _format(self, data: dict, source: Path, workspace: Path, *, tests_failed: bool) -> ToolResult:
        totals = data.get("totals", {})
        overall = totals.get("percent_covered_display", "?")
        files = data.get("files", {})

        # Если источник — конкретный файл, оставляем в отчёте только его.
        single = source.is_file()
        source_rel = None
        if single:
            try:
                source_rel = str(source.resolve().relative_to(workspace.resolve())).replace("\\", "/")
            except ValueError:
                source_rel = source.name

        rows: list[tuple[str, float, str]] = []
        for path, info in files.items():
            norm = path.replace("\\", "/")
            if single and source_rel and not norm.endswith(source_rel):
                continue
            summary = info.get("summary", {})
            pct = float(summary.get("percent_covered", 0))
            missing = info.get("missing_lines", [])
            rows.append((norm, pct, _ranges(missing)))

        if not rows:
            return ToolResult(content=f"Покрытие измерено (всего {overall}%), но нужный файл в отчёт не попал.")

        rows.sort(key=lambda r: r[1])  # сначала худшее покрытие
        # Для одиночного файла общий процент по папке-источнику бессмыслен —
        # берём покрытие самого файла.
        headline = f"{rows[0][1]:.0f}% ({rows[0][0]})" if single else f"{overall}% общий"
        lines = [f"Покрытие: {headline}." + (" ⚠️ Часть тестов упала." if tests_failed else "")]
        lines.append("")
        for path, pct, missing in rows[:25]:
            tail = f" — непокрыто: {missing}" if missing else " — покрыто полностью"
            lines.append(f"  {path}: {pct:.0f}%{tail}")
        if any(m for _, _, m in rows):
            lines.append("")
            lines.append("Дальше: напиши тесты на непокрытые строки (ветки ошибок, граничные значения).")
        return ToolResult(content="\n".join(lines))
