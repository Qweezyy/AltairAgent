# PC side of the bridge: what to implement in `server/ws.py`

The phone side is ready. This document is the exact contract for the **PC**: which messages
arrive from the phone over the same WS `/ws`, how to respond to them, and which callback
requests the PC sends to the phone. Everything uses the same JSON-over-WS already in use
(`_handle_message` in `Connection`). Bytes are base64, the limit is **25 MB**.

Architecture: two agents, **coordinator ⇄ executor**. Usually the phone is the coordinator and
the PC is the executor (`run`). The coordinator's file commands are served by the server
directly (without the PC's LLM); the executor's callback requests are emitted by the PC agent's
tools.

---

## 0. General requirements

### 0.1. Resolving a path inside the workspace sandbox

All `path` values are **relative to the working folder** and must not escape it. Add a helper
and use it everywhere:

```python
from pathlib import Path

def _resolve_in_workspace(workspace: str | Path, rel: str) -> Path:
    base = Path(workspace).resolve()
    target = (base / rel).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"path outside the working folder: {rel}")
    return target
```

### 0.2. Which folder (important!)

The phone opens **a separate WS socket for each file operation** (`list_files`, `get_file`,
`put_file`, `stat_file`) — a new `Connection` with a new empty auto-folder. So in these messages
the phone **sends a `workspace` field** (from the bridge settings). Resolve paths against it:

```python
def _bridge_ws(self, message: dict) -> Path:
    ws = str(message.get("workspace") or "").strip()
    if ws:
        try:
            return Path(self.settings.for_workspace(ws).workspace)
        except ConfigError:
            pass
    # fallback — the shared exchange folder
    return self.settings.app_dir / "storage" / "bridge"
```

On **callback requests** (`need_file` → `put_file` in reply) the message arrives on the **same
socket where `run` is happening**, and has no `workspace` — use the current run's working folder
(`self.session_settings.workspace`).

### 0.3. Size limit

```python
MAX_FILE_BYTES = 25 * 1024 * 1024
```
For `get_file`/`put_file` above the limit — respond with an error, do not read/write.

### 0.4. File kind by extension

```python
def _kind(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in {"png","jpg","jpeg","gif","webp","bmp","heic"}: return "image"
    if ext in {"txt","md","json","csv","xml","yaml","yml","py","kt","java","js",
               "ts","html","css","log","sh","toml","ini","gradle"}: return "text"
    return "file"
```

---

## 1. Layer 1 — Discovery (minimum: change nothing)

On connect the phone reads the existing **`ready`** message (it already carries `tools` and
`workspace`) — discovery of the PC's capabilities works as is.

**Optional:** a reply `hello`. The phone sends on connect:
```json
{"type":"hello","platform":"android","capabilities":["camera","photo_library","files",
 "location","notify_user","ask_user","sensors","share","clipboard","draw"]}
```
Handle `hello` (right now it falls into "Unknown command") — just accept it and, if you like,
reply:
```json
{"type":"hello","platform":"pc","capabilities":["shell","python","git","files","web",
 "heavy_compute"],"workspace":"<path>","version":"<...>"}
```
This removes the warning log and gives the phone an explicit list of the PC's capabilities.

---

## 2. Layer 2 — File exchange (the server responds directly)

Add four branches and handlers to `_handle_message`. **They do not require starting the agent.**

### 2.1. `list_files` — what is on the PC

Request (phone → PC):
```json
{"type":"list_files","workspace":"<path>","glob":"out/**"}   // glob is optional
```
Response:
```json
{"type":"files","items":[{"path":"out/report.pdf","bytes":12345,"mtime":"2026-08-30T12:00:00","kind":"file"}]}
```
- `path` — relative to workspace, `/` as the separator.
- `mtime` — an ISO string (or anything as a string; the phone shows it as is).
- Without `glob` — list the whole folder recursively (cap it sensibly, e.g. 500 files).

```python
async def _bridge_list_files(self, message: dict) -> None:
    base = self._bridge_ws(message)
    glob = str(message.get("glob") or "**/*")
    items = []
    for p in sorted(base.glob(glob)):
        if p.is_file():
            st = p.stat()
            items.append({
                "path": p.relative_to(base).as_posix(),
                "bytes": st.st_size,
                "mtime": _iso(st.st_mtime),
                "kind": _kind(p.name),
            })
            if len(items) >= 500:
                break
    await self.send({"type": "files", "items": items})
```

### 2.2. `stat_file` — metadata (inspect before fetching)

Request:
```json
{"type":"stat_file","workspace":"<path>","path":"out/report.pdf"}
```
Response:
```json
{"type":"file.stat","path":"out/report.pdf","exists":true,"bytes":12345,
 "mtime":"2026-08-30T12:00:00","kind":"file"}
```
If the file is missing — `{"type":"file.stat","path":...,"exists":false}`.

### 2.3. `get_file` — hand a file to the phone

Request:
```json
{"type":"get_file","workspace":"<path>","path":"out/report.pdf"}
```
Response (success):
```json
{"type":"file","path":"out/report.pdf","bytes":12345,"b64":"<base64>"}
```
Missing / too large:
```json
{"type":"file.missing","path":"out/report.pdf"}
```

```python
import base64
async def _bridge_get_file(self, message: dict) -> None:
    base = self._bridge_ws(message)
    rel = str(message.get("path") or "")
    try:
        target = _resolve_in_workspace(base, rel)
    except ValueError:
        await self.send({"type": "file.missing", "path": rel}); return
    if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
        await self.send({"type": "file.missing", "path": rel}); return
    data = await asyncio.to_thread(target.read_bytes)
    await self.send({
        "type": "file", "path": rel, "bytes": len(data),
        "b64": base64.b64encode(data).decode("ascii"),
    })
```

### 2.4. `put_file` — accept a file from the phone

Request (phone → PC; both for `pc_send_file` and as a reply to `need_file`):
```json
{"type":"put_file","workspace":"<path>","path":"inbox/photo.jpg","b64":"<base64>"}
```
Response:
```json
{"type":"put_file.ok","path":"inbox/photo.jpg","bytes":12345}
```
or `{"type":"put_file.error","path":...,"message":"..."}`.

- Create missing folders (`target.parent.mkdir(parents=True, exist_ok=True)`).
- **Important:** if `put_file` arrived during a `run` (a callback reply to `need_file`), it may
  have no `workspace`; then use `self.session_settings.workspace`.

```python
async def _bridge_put_file(self, message: dict) -> None:
    rel = str(message.get("path") or "")
    ws = message.get("workspace")
    base = self._bridge_ws(message) if ws else Path(self.session_settings.workspace)
    try:
        target = _resolve_in_workspace(base, rel)
        raw = base64.b64decode(str(message.get("b64") or ""))
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("file is larger than 25 MB")
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, raw)
    except Exception as exc:  # noqa: BLE001
        await self.send({"type": "put_file.error", "path": rel, "message": str(exc)}); return
    await self.send({"type": "put_file.ok", "path": rel, "bytes": len(raw)})
```

### 2.5. Dispatcher

In `_handle_message` add the branches:
```python
elif kind == "hello":       await self._bridge_hello(message)      # see layer 1 (optional)
elif kind == "list_files":  await self._bridge_list_files(message)
elif kind == "stat_file":   await self._bridge_stat_file(message)
elif kind == "get_file":    await self._bridge_get_file(message)
elif kind == "put_file":    await self._bridge_put_file(message)
# phone replies to callback requests (layer 3):
elif kind == "need_file.done":   self._bridge_resolve(message)
elif kind == "need_file.cancel": self._bridge_resolve(message)
elif kind == "capability.result":self._bridge_resolve(message)
# "answer" is already handled (same format as ask) — see 3.3
```

---

## 3. Layer 3 — Callback requests (PC executor → phone coordinator)

Here the PC **agent**, mid-run, asks the phone for something it does not have. Model the
mechanism on the existing `ask`/`approval`: a future keyed by `req_id`, resolved when the phone
sends a reply. You need (a) a send+await helper, (b) PC agent tools that call it, (c) resolution
of replies in `_handle_message`.

### 3.1. The request→reply mechanism

```python
# in Connection.__init__:
self._bridge_waiters: dict[str, asyncio.Future] = {}

async def request_from_phone(self, msg: dict, timeout: float = 180) -> dict:
    req_id = uuid.uuid4().hex[:8]
    msg = {**msg, "req_id": req_id}
    fut = asyncio.get_running_loop().create_future()
    self._bridge_waiters[req_id] = fut
    await self.send(msg)
    try:
        return await asyncio.wait_for(fut, timeout)
    finally:
        self._bridge_waiters.pop(req_id, None)

def _bridge_resolve(self, message: dict) -> None:
    fut = self._bridge_waiters.get(str(message.get("req_id") or ""))
    if fut and not fut.done():
        fut.set_result(message)
```

Do not forget to resolve/cancel waiters in `shutdown`/`_stop_run` (like `pending_approvals`),
otherwise the run hangs if the phone disconnects.

### 3.2. `need_file` / `need_photo` — ask the phone for a file/photo

The PC agent's tool sends:
```json
{"type":"need_file","req_id":"<auto>","hint":"need the logo"}
// or {"type":"need_photo","req_id":"<auto>","hint":"take a photo of the receipt"}
```
The phone shows the user a picker, drops the file into your `inbox/<name>` via `put_file`
(arriving on this same socket, handled by `_bridge_put_file` with the run's workspace), then
sends:
```json
{"type":"need_file.done","req_id":"<...>","path":"inbox/<name>"}   // success
{"type":"need_file.cancel","req_id":"<...>"}                       // the user declined
```
Tool: `await request_from_phone({"type":"need_file","hint":...})`; on a `need_file.done` reply
the file is already in `inbox/`, return the path to the model; on `need_file.cancel` tell the
model there will be no file.

### 3.3. `ask_user` — ask the phone's user

```json
{"type":"ask_user","req_id":"<auto>","question":"Really delete?","options":["Yes","No"]}
```
The phone shows a form and replies **in the existing `answer` format**:
```json
{"type":"answer","request_id":"<req_id>","answers":{"0":{"selected":["Yes"]}}}
```
⚠️ Note: the phone sends the key **`request_id`** (not `req_id`) and an `answers` object in your
`ask` tool's format. Resolve by `request_id`. The simplest path — reuse the existing `answer`
branch if it puts the result into a future; or map `request_id`→`_bridge_waiters` and pull out
`answers`.

### 3.4. `need_capability` — ask the phone to do what the PC cannot

```json
{"type":"need_capability","req_id":"<auto>","capability":"location","task":"give me my coordinates"}
```
The phone (MVP) asks the user and replies:
```json
{"type":"capability.result","req_id":"<...>","ok":true,"answers":{...}}
```
PC agent tool: `await request_from_phone({"type":"need_capability","capability":...,
"task":...})` → return `answers`/`ok` to the model.

### 3.4-bis. About approval of phone_* tools (verified live)

`phone_*` tools have the `network` category → the PC runner emits `approval.requested` even in
bypass mode. The phone now **auto-approves** such approvals during a delegated run (the user
already approved the task by delegating it), so everything works as is. Cleaner still — mark the
`phone_*` tools as safe (not dangerous), so the PC does not run an extra approval round-trip
inside a run.

The phone's reply format for `ask_user` (confirmed): the phone sends
`{"type":"answer","request_id":"<req_id>","answers":{"0":["Blue"]}}` — values are already parsed
(the label of the chosen option or custom text). Your `_flatten_answers` understands this.

### 3.5. PC agent tools (the PC registry)

So the PC model can call all of this, add PC tools to the registry (counterparts of the phone's
`pc_*`) that drive `connection.request_from_phone(...)`:
- `phone_request_file(hint)` → `need_file`
- `phone_request_photo(hint)` → `need_photo`
- `phone_ask_user(question, options?)` → `ask_user`
- `phone_capability(capability, task)` → `need_capability`
- `phone_list_files(glob?)` / `phone_get_file(path)` / `phone_put_file(path)` — if you want
  symmetry (the PC coordinator asking the phone for files): the same `list_files`/`get_file`/
  `put_file`, but **toward the phone**; the phone side does not yet serve these as a server —
  that is the next stage (phone-as-worker). For "phone-as-coordinator" scenarios `need_*` is
  enough.

The tool needs access to `Connection` (like `ask` gets `scratch`) — pass `connection` (or
`request_from_phone`) into `tool_context`.

---

## 3-bis. Shared-memory sync (`sync_memory`)

The memory formats differ (the phone uses `.md` with bullets, the PC uses `memory.json` of
`Fact`), so we exchange **fact texts**, not a file. The phone sends its facts, the PC adds the
missing ones to itself and returns the merged list; the phone appends what it lacks. The result
is a shared set on both sides.

Request (phone → PC):
```json
{"type":"sync_memory","scope":"global","facts":["The user likes Kotlin","Timezone MSK"]}
```
Response (PC → phone) — ALL of the PC's facts after adding the received ones:
```json
{"type":"memory_sync","facts":["The user likes Kotlin","Timezone MSK","Project — Android agent"]}
```

`MemoryStore.remember()` deduplicates by normalized text and saves to disk itself, so the
handler is simple:
```python
from core.memory import MemoryStore

async def _bridge_sync_memory(self, message: dict[str, Any]) -> None:
    store = MemoryStore(self.settings.data_dir)          # the same shared memory the agent uses
    for text in (message.get("facts") or []):
        store.remember(str(text), category="fact", session_id="bridge")  # dupes are dropped automatically
    facts = [f.text for f in store.all()]
    await self.send({"type": "memory_sync", "facts": facts})
```
Dispatcher: `elif kind == "sync_memory": await self._bridge_sync_memory(message)`.

Check: on the phone add a fact to shared memory, say "sync memory with the PC"
(`pc_sync_memory`) — the fact appears in the PC's `memory.json`, and the PC's facts appear in the
phone's `global.md`.

## 3-ter. Sync of plugins (MCP) and skills

The phone connects to MCP servers **directly over HTTP**, so only HTTP/SSE servers can be synced
with the PC. The PC's stdio servers (`command`) cannot be launched by the phone — they are not
shared (if desired, proxy them to HTTP, e.g. `mcp-proxy`, and add them as a `url` entry in
`mcp_servers.json`).

**`mcp_list`** — the list of servers (phone → PC → phone):
```json
{"type":"mcp_list"}
```
```json
{"type":"mcp","servers":[{"name":"remote","url":"https://mcp.example/x","transport":"http","headers":{"Authorization":"Bearer T"}}]}
```
The PC reads `mcp_servers.json`, takes entries with a `url` (not `disabled`), `transport` —
`http` (default) or `sse`, `headers` — from `headers` and/or `token`→`Authorization: Bearer`.

**`skills_list`** — skill metadata:
```json
{"type":"skills_list"}
```
```json
{"type":"skills","items":[{"name":"git","description":"Working with git…","version":""}]}
```

**`skill_get`** — the files of one skill (written by the phone into `filesDir/skills/<name>/`):
```json
{"type":"skill_get","name":"git"}
```
```json
{"type":"skill","name":"git","files":[{"path":"SKILL.md","b64":"…"},{"path":"ref/x.md","b64":"…"}]}
```
`path` — relative to the skill folder; total size capped at 25 MB. An unknown skill →
`{"type":"skill","name":…,"files":[],"error":"skill not found"}`.

Handlers (PC): `_bridge_mcp_list` (`MCPManager(self.settings).shareable_servers()`),
`_bridge_skills_list` / `_bridge_skill_get` (`SkillManager(self.settings)`). Dispatcher:
`elif kind == "mcp_list" | "skills_list" | "skill_get"`. Android side: a "Sync with PC" button
(full), the methods `fetchMcpServers/fetchSkills/fetchSkillFiles`, DTOs in
`android/.../bridge/PcSyncModels.kt`.

Check: on the phone (full) "Plugins" → "Sync with PC" → the HTTP servers from the PC's
`mcp_servers.json` appear in the list, skills download into `filesDir/skills/`.

## 4. Order and verification

1. **Layer 2** (files) — the most valuable and the simplest. Add 4 handlers + the dispatcher +
   `hello`. Check: in the app enable the bridge, set `workspace`, ask the phone agent to
   "show the files on the PC" (`pc_list_files`), "download out/x" (`pc_fetch_file`), "send file
   y to the PC" (`pc_send_file`).
2. **Layer 3** (callback requests) — the `request_from_phone` mechanism + resolution + PC agent
   tools. Check: give the PC a task that needs a file from the phone → the PC calls
   `phone_request_file` → a file picker pops up on the phone → the file lands in `inbox/`.
3. Later — phone-as-worker (PC coordinator), memory sync (`get_file memory/global.md`), a native
   `need_capability` without the user in the loop.

## 5. Security

- The bridge token is already checked — file commands are available only to an authorized socket.
- All paths stay strictly inside the workspace (`_resolve_in_workspace`).
- A 25 MB limit on `get_file`/`put_file`.
- Do not hand the test API key or `.env` over the bridge (do not put them in the exchange workspace).

## Appendix: message summary

| type | direction | key fields | reply |
|---|---|---|---|
| `hello` | phone→PC | platform, capabilities | `hello` (opt.) |
| `list_files` | phone→PC | workspace, glob? | `files{items}` |
| `stat_file` | phone→PC | workspace, path | `file.stat` |
| `get_file` | phone→PC | workspace, path | `file{b64}` / `file.missing` |
| `put_file` | phone→PC | workspace?, path, b64 | `put_file.ok` / `put_file.error` |
| `need_file`/`need_photo` | PC→phone | req_id, hint | `put_file` + `need_file.done` / `need_file.cancel` |
| `ask_user` | PC→phone | req_id, question, options? | `answer{request_id,answers}` |
| `need_capability` | PC→phone | req_id, capability, task | `capability.result{req_id,ok,answers}` |
