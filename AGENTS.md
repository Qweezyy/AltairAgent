# Working with this project

This file is read by AI agents that edit this repository (including the local agent —
its contents go straight into the system prompt).

## What this project is

A local AI agent: FastAPI + WebSocket + a web interface, the core in `core/`, transport in
`server/`. Details are in `docs/ARCHITECTURE.md`; extension recipes in `docs/EXTENDING.md`.
**Read `docs/EXTENDING.md` before adding anything.**

## Guiding principle

**Doing the best, most current thing for the user matters more than ease of implementation.**
Choose modern, up-to-date approaches and the fastest/highest-quality option for the user, not
the one that is easiest to write. **Bundling external dependencies and features is allowed** as
long as they are (a) open-source and (b) shippable with the app (bundled into the build).
Preferably with a graceful fallback if a dependency turns out to be unavailable. This overrides
the earlier "pure Python only" self-restriction.

## Mandatory rules

1. Before submitting work: `python -m pytest -q` — all tests green, and `python main.py --check`.
2. New functionality comes with a test in `tests/`.
3. Settings only through `core/settings.py` + `.env.example`. No `os.getenv` scattered in the code.
4. File paths only through `resolve_path()` from `core/security/paths.py`.
5. No blocking calls inside `async def`: use `asyncio.to_thread` or `run_process()` from
   `core/utils/proc.py`.
6. `except: pass` is forbidden. A tool error is a `ToolResult.fail("reason")`.
7. `asyncio.CancelledError` is always re-raised, otherwise stopping a task breaks.
8. Do not rewrite `core/agent/runner.py`, `core/tools/base.py`, `core/agent/session.py`
   without an explicit request — this is the foundation the tests rely on.
9. Keep comments and error messages consistent with the existing style; identifiers in the code
   are in English.
10. Do not commit `.env` or the contents of `logs/`.

## Style

- Python 3.10+, type annotations are mandatory.
- Lines up to 110 characters, formatting in the spirit of the existing code.
- A comment explains "why", not "what" — do not comment the obvious.
