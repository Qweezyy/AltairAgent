---
name: windows_platform
description: The hard-won pitfalls of this app's platform and stack — asyncio subprocesses on Windows, the ConPTY vs WinPTY pseudo-terminal, the language server, the headless browser, console encodings. Read it when working with processes, PTYs, LSP, websockets and builds on Windows, so as not to spend hours on what is already solved.
---

# Skill: platform pitfalls (Windows + this stack)

Collected here is what already cost hours of debugging. If the task touches subprocesses, the
terminal, the language server, websockets or the build — check here FIRST, so as not to walk the
same path again.

## asyncio subprocesses stall outside the main thread (Windows)

The app's server runs in a daemon thread (uvicorn is not started in the main one). In that mode on
Windows `asyncio.create_subprocess_exec` and the pipes to the child process **hang**: the process
starts, but no input or output flows.

**Fix:** start long-running processes with `subprocess.Popen` plus a separate reader thread, and
hand the results to the event loop with `loop.call_soon_threadsafe`. Short commands —
`subprocess.run` inside `asyncio.to_thread`. The terminal, dev servers, git, pyright and LSP are
done this way.

## Pseudo-terminal: WinPTY, not ConPTY

By default pywinpty takes the **ConPTY** backend, which uses IOCP and clashes with the asyncio
loop's IOCP when the server is in a separate thread: the terminal starts, prints a couple of bytes
and goes "silent" on input.

**Fix:** force `backend=Backend.WinPTY` (winpty-agent.exe + named pipes, no IOCP). See
`core/terminal/session.py`.

## Websocket + background output: two tasks, not a race on one input stream

Concurrent `send_text` and `receive_text` from one `asyncio.wait` can behave oddly with ConPTY on
Windows. Reliable: separate tasks (an output pump + a receive loop) and a queue to keep the order.
Concurrent send+receive on a websocket by itself works — checked.

## Language server (LSP): `workspaceFolders` is needed

pyright-langserver will NOT load `pyproject.toml` nor resolve imports of the project's own
packages and re-exports when `initialize` carries only the old `rootUri`. Definitions and hover
come back empty, while CLI pyright resolves everything.

**Fix:** pass `workspaceFolders` in `initialize`. See `core/lsp/client.py`. Also: wait for
`publishDiagnostics` before a request — the sign that the file has been analysed.

## Console encoding (cp1251)

The standard Windows console is cp1251, and printing Cyrillic or emoji from scripts fails with
`UnicodeEncodeError`, while output in the build terminal turns into mojibake. In diagnostic
scripts set `sys.stdout.reconfigure(encoding='utf-8')` or `PYTHONIOENCODING=utf-8`. Inside the
app, read and write files with `encoding='utf-8'`.

## Paths in the sandbox

The tools work strictly inside the WORKSPACE. `/tmp` and paths on another drive are refused by the
sandbox — that is correct. For temporary files in tests use the working folder / `tmp_path`, not
the system temp.

## The build (PyInstaller)

* The app must be CLOSED — Windows does not let a running exe be overwritten.
* What is imported dynamically (`__import__`, deferred imports inside functions) is invisible to
  PyInstaller — add `--collect-all <package>` in `build_app.py` (that is how winpty, pyright and
  the tree-sitter grammars are bundled).
* After the build, check that the needed binaries really are in `dist/`.
