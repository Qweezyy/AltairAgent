"""Инструменты перекрёстной проверки кода (рычаг «много способов проверить»).

`differential_check` запускает ДВЕ команды и сравнивает их вывод. Классический
приём повышения надёжности слабой модели: решить задачу двумя независимыми
способами (наивный эталон против оптимизации, старая версия против новой,
референс-реализация против своей) и убедиться, что результаты совпадают на общих
входах. Расхождение — почти наверняка баг.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.i18n import tr
from core.security.paths import resolve_path
from core.tools.base import EmptyArgs, Tool, ToolContext, ToolResult
from core.tools.builtin.shell import check_command, is_read_only_command
from core.utils.proc import run_process, shell_argv

#: Потолок размера диффа, уходящего критику: слабую модель нельзя топить в тексте.
_DIFF_LIMIT = 24_000

_CRITIC_SYSTEM = (
    "Ты — придирчивый, но конструктивный ревьюер кода. Тебе дают git-дифф изменений. "
    "Найди РЕАЛЬНЫЕ проблемы, не хвали. Проверяй по чек-листу: 1) баги и краевые случаи "
    "(пустой ввод, None/ноль, границы, гонки); 2) обработка ошибок; 3) безопасность "
    "(инъекции, секреты в коде, пути); 4) отладочный мусор (print, закомментированный код, "
    "TODO); 5) не сломан ли существующий контракт/типы; 6) есть ли тесты на новую логику; "
    "7) читаемость и дублирование. Отвечай кратко на русском."
)

_CRITIC_INSTRUCTION = (
    "Отревьюь этот дифф. Верни:\n"
    "1) СПИСОК ЗАМЕЧАНИЙ — каждое строкой: файл, суть проблемы, как исправить;\n"
    "2) чего не хватает в проверках/тестах;\n"
    "3) в конце вердикт: «Можно завершать» или «Нужны правки».\n"
    "Если всё чисто — так и скажи, не выдумывай проблем.\n\nДИФФ:\n"
)


def _first_diff(a: str, b: str) -> str:
    """Первая расходящаяся строка (1-индексная) с обеими версиями."""
    a_lines = a.splitlines()
    b_lines = b.splitlines()
    for i in range(max(len(a_lines), len(b_lines))):
        la = a_lines[i] if i < len(a_lines) else "<нет строки>"
        lb = b_lines[i] if i < len(b_lines) else "<нет строки>"
        if la != lb:
            return f"строка {i + 1}:\n  A: {la[:200]}\n  B: {lb[:200]}"
    return ""


class DifferentialCheckArgs(BaseModel):
    command_a: str = Field(description="Первая команда (например эталонная/наивная реализация)")
    command_b: str = Field(description="Вторая команда (например оптимизированная/своя реализация)")
    cwd: str = Field(default=".", description="Рабочая папка относительно workspace")
    ignore_trailing_whitespace: bool = Field(
        default=True, description="Игнорировать хвостовые пробелы и пустые строки в конце"
    )
    compare_exit_code: bool = Field(default=True, description="Сверять и коды возврата")
    timeout: float = Field(default=120.0, ge=1, le=900)


class DifferentialCheckTool(Tool):
    name = "differential_check"
    description = (
        "Запускает ДВЕ команды и сравнивает их вывод (stdout и, опционально, код возврата). "
        "Для перекрёстной проверки: реши задачу двумя независимыми способами и убедись, что "
        "результаты совпадают — расхождение указывает на баг. Сильно повышает надёжность."
    )
    Args = DifferentialCheckArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: DifferentialCheckArgs) -> str:  # type: ignore[override]
        return tr("appr.diff", a=args.command_a, b=args.command_b)

    def auto_verdict(self, args: DifferentialCheckArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Разрешаем без вопроса, только если ОБЕ команды ничего не меняют.
        both_read_only = is_read_only_command(args.command_a) and is_read_only_command(args.command_b)
        return "allow" if both_read_only else "ask"

    async def run(self, args: DifferentialCheckArgs, ctx: ToolContext) -> ToolResult:
        check_command(args.command_a)
        check_command(args.command_b)
        cwd = resolve_path(args.cwd, settings=ctx.settings, must_exist=True, must_be_dir=True)

        from core.secrets_store import load_env

        env = load_env(ctx.settings.workspace)
        res_a = await run_process(shell_argv(args.command_a), cwd=cwd, timeout=args.timeout, env=env)
        res_b = await run_process(shell_argv(args.command_b), cwd=cwd, timeout=args.timeout, env=env)

        out_a, out_b = res_a.stdout, res_b.stdout
        if args.ignore_trailing_whitespace:
            out_a = "\n".join(line.rstrip() for line in out_a.splitlines()).rstrip("\n")
            out_b = "\n".join(line.rstrip() for line in out_b.splitlines()).rstrip("\n")

        same_output = out_a == out_b
        same_code = (res_a.returncode == res_b.returncode) or not args.compare_exit_code

        if same_output and same_code:
            return ToolResult(
                content=(
                    "✅ Совпадает: обе команды дали одинаковый вывод"
                    + ("" if not args.compare_exit_code else f" и код возврата ({res_a.returncode})")
                    + ".\nПерекрёстная проверка пройдена."
                )
            )

        parts = ["❌ Расхождение — это сигнал о баге в одной из реализаций."]
        if not same_code:
            parts.append(f"Коды возврата различаются: A={res_a.returncode}, B={res_b.returncode}")
        if not same_output:
            diff = _first_diff(out_a, out_b)
            parts.append("Вывод различается — " + (diff or "разная длина/содержимое."))
            parts.append(f"A ({len(out_a.splitlines())} строк):\n{out_a[:1200]}")
            parts.append(f"B ({len(out_b.splitlines())} строк):\n{out_b[:1200]}")
        for label, res in (("A", res_a), ("B", res_b)):
            if res.stderr.strip():
                parts.append(f"stderr {label}:\n{res.stderr.strip()[:600]}")
        return ToolResult(content="\n\n".join(parts), ok=False)


class ReviewChangesTool(Tool):
    name = "review_changes"
    description = (
        "Критик-проход по твоему диффу: собирает git-изменения рабочей папки и отдаёт их "
        "отдельному ревьюеру, который по чек-листу ищет баги, краевые случаи, утечки, "
        "отладочный мусор и нехватку тестов. Вызывай ПЕРЕД тем, как заявить «готово», после "
        "правок кода. Самопроверка одной модели ненадёжна — этот взгляд со стороны ловит больше."
    )
    Args = EmptyArgs
    category = "read"
    timeout = None

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        workspace = ctx.settings.workspace

        inside = await run_process(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace, timeout=15
        )
        if inside.returncode != 0 or "true" not in inside.stdout.lower():
            return ToolResult(
                content=(
                    "Папка не под git — собрать дифф автоматически нельзя. Проверь изменения "
                    "вручную: перечитай свой код и запусти run_tests/run_lint/type_check."
                )
            )

        unstaged = await run_process(["git", "diff"], cwd=workspace, timeout=30)
        staged = await run_process(["git", "diff", "--staged"], cwd=workspace, timeout=30)
        untracked = await run_process(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=workspace, timeout=15
        )

        diff = (staged.stdout + "\n" + unstaged.stdout).strip()
        if not diff:
            extra = untracked.stdout.strip()
            if extra:
                return ToolResult(
                    content=(
                        "Отслеживаемых изменений нет, но есть новые файлы:\n"
                        f"{extra[:1500]}\nДобавь их (git add) или проверь вручную; тесты запусти."
                    )
                )
            return ToolResult(content="Изменений в рабочей папке нет — ревьюить нечего.")

        truncated = diff[:_DIFF_LIMIT]
        if len(diff) > _DIFF_LIMIT:
            truncated += f"\n… [дифф обрезан, ещё {len(diff) - _DIFF_LIMIT} символов]"

        from core.llm.openai_client import build_llm_client

        messages = [
            {"role": "system", "content": _CRITIC_SYSTEM},
            {"role": "user", "content": _CRITIC_INSTRUCTION + truncated},
        ]
        client = build_llm_client(ctx.settings.default_model, ctx.settings)
        client.reasoning = "low"
        try:
            turn = await client.complete(messages, max_tokens=1400)
        finally:
            await client.aclose()

        review = (turn.content or "").strip() or "Ревьюер не вернул замечаний."
        note = "" if untracked.stdout.strip() == "" else (
            f"\n\nНовые неотслеживаемые файлы (не в диффе): {untracked.stdout.strip()[:800]}"
        )
        return ToolResult(content=f"Ревью изменений (по git-диффу):\n\n{review}{note}")
