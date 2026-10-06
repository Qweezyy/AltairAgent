---
name: python_expert
description: Expert guidance for clean modern Python — type hints, asyncio, error handling, paths and project conventions.
---

# Skill: Python expert

Rules and good practice for writing Python.

## Principles

1. **Type hints everywhere**, in the modern form: built-in generics (`list[str]`, `dict[str, Any]`)
   and `X | None` instead of `Optional[X]`; `from __future__ import annotations` in new modules.
2. **Async for I/O**: network and file I/O in async code goes through `asyncio`, `httpx` or
   `asyncio.to_thread(...)` for blocking calls. Never block the event loop inside `async def`.
3. **Errors**: catch specific exceptions, log them, never a bare `except: pass`. Re-raise
   `asyncio.CancelledError` — swallowing it breaks stopping a task.
4. **Style**: PEP 8, meaningful names, small functions; match the project's existing style and
   formatter (ruff/black settings in `pyproject.toml`).
5. **Paths**: `pathlib.Path` for cross-platform paths; never glue paths with string concatenation.
6. **Check it**: `run_lint` (ruff) and `type_check` (pyright) after edits, `run_tests` before
   saying it works.
