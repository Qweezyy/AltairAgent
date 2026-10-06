---
name: dev_server
description: Watching a live dev server (npm run dev, uvicorn --reload, vite, cargo watch) — starting it in the background, reading its logs, catching build and runtime errors, and the "edit → check the rebuild" loop. For web development and debugging with hot reload.
---

# Skill: watching a dev server

A plain `execute_command` starts a command and waits for it to end — a dead end for a dev server,
which never ends by itself. Live servers have their own tools: `start_dev_server`,
`read_dev_server`, `stop_dev_server`, `list_dev_servers`.

## When to use them

* Starting a development server with hot reload: `npm run dev`, `pnpm dev`, `vite`,
  `uvicorn app:app --reload`, `cargo watch -x run`, `next dev`.
* Debugging where you need to see the stream of logs and catch build and runtime errors as they
  appear.

Do NOT use them for ordinary commands (tests, builds, git) — those are `execute_command`.

## The loop (Observe → Fix → Verify)

1. **Start.** `start_dev_server(command="npm run dev", name="web")`. A short, meaningful name —
   you refer to the server by it. The first logs come back; if the server died at once, you see
   the exit code and the reason.
2. **Read.** `read_dev_server(name="web", wait_sec=3)` returns ONLY the lines new since the last
   read and marks the ones that look like errors. `wait_sec` gives the server time to react — use
   2–5 s after edits.
3. **Fix.** Found an error — locate the file (`find_symbol`, `grep_search`) and fix it
   (`edit_file` / `apply_patch`).
4. **Check the rebuild.** `read_dev_server(name="web", wait_sec=3)` again. Success is the line
   saying the server built successfully (ready/compiled/HMR). An error — repeat step 3. Do not
   show the result to the user until the logs are clean.
5. **Stop.** When done — `stop_dev_server(name="web")`. Do not leave forgotten servers behind:
   `list_dev_servers` shows the running ones.

## Details

* Logs are read incrementally: if the server printed more between two reads than the buffer
  holds, the tool marks honestly how many lines were skipped.
* The server runs in the working folder (`cwd` relative to the workspace). Make sure there is a
  `package.json` or an entry point there.
* When the app exits, every dev server is stopped automatically (with its tree of node/python
  child processes); no ports stay taken.
* One server per name. To restart — `stop_dev_server` first, then `start_dev_server` again.
