# Chat sync between the phone and the PC (`chat_sync` v1)

Goal: **a chat started on one device can be continued on the other.** Start on the phone,
open the same chat on the PC and keep going; next time the phone connects it sees the PC's
new messages, and the other way round.

The contract rides on the bridge that already exists (see `BRIDGE_PC_SPEC.md`): the same
JSON-over-WS `/ws`, the same `bridge_token`, a separate short-lived socket per operation like
`sync_memory`. Nothing here is sent anywhere except between the user's own paired devices.

Status: **spec only.** The PC side is planned right after 0.1.0 (§6), the Android side follows
§7.

---

## 1. What is synced (and what is not, in v1)

Synced: the visible conversation — the user's messages and the assistant's replies, in order,
with the chat title and the device each message came from.

Not synced in v1 (on purpose — each needs its own design):
- tool steps and tool results (the other device has other tools; it gets a short `steps` note);
- the model-side history (each device rebuilds its model context from the visible messages);
- attachment bytes (only metadata travels; bytes can be fetched with `get_file` later — v2);
- deletions (deleting a chat is local; a synced chat can come back — v2 adds tombstones);
- regenerated answer versions (only the currently selected version travels);
- interactive widgets (`html`): only their text fallback travels.

## 2. The portable chat

```json
{
  "id": "3b1ac3bfd7c0",
  "title": "Trip to Kazan",
  "origin": "android",
  "created_at": 1790180000.0,
  "updated_at": 1790188636.5,
  "messages": [
    {"id": "m-7f3a…", "role": "user", "text": "Plan a weekend in Kazan", "ts": 1790180000.0,
     "device": "android"},
    {"id": "m-91c0…", "role": "assistant", "text": "Here is a plan…", "ts": 1790180042.1,
     "device": "android", "steps": ["web_search", "find_images"],
     "attachments": [{"name": "map.png", "kind": "image", "size": 48213}]}
  ]
}
```

Rules:
- `role` is only `user` or `assistant`. Times are Unix seconds (float).
- `origin` is the device that created the chat: `pc` or `android`.
- **Message ids are stable forever** and unique within the chat. The phone already has one
  per message (`ChatMessage.id`). The PC derives it from the timeline entry:
  `"pc-" + sha1(f"{kind}|{ts}|{text[:64]}").hexdigest()[:12]`. The timeline entry never
  changes once written, so the id does not either.
- Messages are **append-only**. Nobody edits or removes a synced message; a new answer
  version on the phone becomes a new assistant message the next time it is sent.
- `text` is Markdown. Size limit per message: 256 KB (longer text is cut with a note).

### 2.1. PC ⇄ portable

PC → portable, from `Session.timeline` (the UI transcript — it survives context compaction,
unlike `Session.messages`):
- `kind="user"` → `role=user` (its `attachments` → metadata only);
- one agent turn → one `role=assistant` message, id taken from the turn's `kind="answer"`
  entry. Text = the interim `kind="text"` segments of the turn, then the answer's `full`
  text (if a segment is already contained in `full`, it is not repeated);
- `kind="step"` entries of the turn → their `name`s go to that message's `steps`;
- a turn that ended without an `answer` (error, stop) → its text segments only, id from the
  first segment; errors, notes and inline widgets are skipped.

Portable → PC (import or append):
- each message → a timeline entry (`user` / `answer` with `full` = text) with the same `ts`
  and the portable id stored in the entry (`"sid"`), so the id survives the round trip
  (`message_id()` returns `sid` when present);
- and a model message: `{"role": "user"|"assistant", "content": text}` in
  `Session.messages`, so the PC agent continues with the full conversation as context.

## 3. Messages

All go phone → PC; the PC replies on the same socket.

### 3.1. `chats_list` — what the PC has

```json
{"type": "chats_list", "since": 1790180000.0}
```
`since` (optional) — only chats updated after it. Reply:
```json
{"type": "chats_list", "chats": [
  {"id": "3b1ac3bfd7c0", "title": "Trip to Kazan", "origin": "android",
   "updated_at": 1790188636.5, "message_count": 14, "last_id": "pc-5e1d…"}
]}
```
Trashed chats are not listed.

### 3.2. `chat_get` — fetch one chat (or only its new part)

```json
{"type": "chat_get", "id": "3b1ac3bfd7c0", "after": "m-91c0…"}
```
`after` (optional) — return only messages after that id. If the id is unknown, the whole chat
comes back with `"partial": false`. Reply:
```json
{"type": "chat_data", "partial": true, "chat": {…portable, messages after "after"…}}
```
Unknown chat: `{"type": "chat_data", "error": "not_found", "id": "…"}`.

### 3.3. `chat_put` — give the PC a chat (new or continued)

```json
{"type": "chat_put", "chat": {…portable…}}
```
The phone sends the whole chat or only a tail (at least one message the PC already has, so the
PC can find where it continues). Reply:
```json
{"type": "chat_put_result", "id": "3b1ac3bfd7c0", "result": "appended", "last_id": "m-…"}
```
`result` is one of:
- `created` — the PC did not have this chat;
- `appended` — the PC added the new messages to its copy;
- `unchanged` — the PC already had all of them (the PC may be ahead: pull with `chat_get`);
- `forked` — both sides added different messages after the same point. The PC keeps its
  chat and saves the phone's branch as a new chat (`fork_id`, title + " (phone)"). Nothing is
  lost and nothing is interleaved;
- `busy` — the PC is running the agent in this chat right now; retry after the run ends.

### 3.4. Merge rule (the same on both sides)

Let `L` be the local message ids and `I` the incoming ones.
1. The chat does not exist locally → **created**.
2. Find `x` = the last incoming message whose id is in `L`.
   - No `x` → the chats share no message: save the incoming one as a new chat (**forked** if
     the id exists, **created** otherwise).
   - `x` is the last of `L` → append everything in `I` after `x` → **appended** (or
     **unchanged** if there is nothing after `x`).
   - `x` is not the last of `L` and `I` has nothing after `x` → **unchanged** (local is ahead).
   - Both have messages after `x` → **forked**.

Using the *last shared id* instead of a common prefix keeps the rule working when the PC's
timeline was trimmed (it keeps the last 400 entries).

## 4. When sync happens

- **On connect** and when the chat list is opened: `chats_list(since = last sync time)`, then
  `chat_get(after = last synced id)` for every chat that changed on the PC, and `chat_put` for
  every chat that changed on the phone since the last sync.
- **Before continuing a chat** on either device, if the other device is reachable: pull it
  first. This is what keeps forks rare.
- **After a turn finishes** in a synced chat: push it (phone → `chat_put`; the PC is pulled by
  the phone, it never pushes by itself).

## 5. Security

- Only over the bridge, only with `bridge_token` (the remote-auth middleware already enforces
  it for every non-loopback request).
- Chat text is never logged; the log records ids and counts only.
- A chat imported from the phone is treated like any user input. Its text is not a source of
  instructions for approvals (the injection guard sees it as the user's words, the same as if
  typed on the PC).
- Limits: 5,000 messages per `chat_put`, 256 KB per message, 25 MB per socket message. Over
  the limit → `{"error": "too_large"}`.

## 6. PC implementation plan (after 0.1.0)

1. `core/chat_sync.py` (new): `to_portable(session)`, `apply_portable(store, chat)` →
   result dict, `merge(local_ids, incoming_ids)`, `message_id(entry)`. Pure functions over
   `SessionStore`/`Session` — no network, easy to test.
2. `server/ws.py` dispatcher: `chats_list`, `chat_get`, `chat_put` → handlers that call
   `core/chat_sync.py` through `asyncio.to_thread` (file I/O). `busy` = a live `Connection`
   is running a task on that session id.
3. UI: a small phone badge on chats whose `origin` is `android`; the "(phone)" fork gets the
   same badge. Strings through i18n.
4. Tests (`tests/test_chat_sync.py`): round trip PC → portable → PC keeps ids; created /
   appended / unchanged / forked / busy; `after` returns only the tail; a trimmed timeline
   still merges; the model context after import contains the imported conversation;
   size limits; a trashed chat is not listed.
5. `docs/PC_TOOLS.md` is not affected (no new agent tool). Update `BRIDGE_PC_SPEC.md`
   summary table with the three messages.

## 7. Android implementation guide

What exists: `data/ChatStore.kt` keeps `chats/<id>/session.json` with messages that already
have a stable `id`; the bridge socket and token live in the bridge settings; `pc_sync_memory`
shows the pattern of a one-shot sync socket.

1. **Store more per chat** (`ChatStore.save/loadAll`, backward compatible — missing fields get
   defaults):
   - chat: `title`, `updatedAt`, `origin` (`"android"` for chats made on the phone),
     `syncedLastId` (last message id both sides have), `lastSyncAt`;
   - message: `ts` (epoch seconds; old messages: use the chat's `created`), `device`
     (`"android"` by default), `steps` (list, optional).
2. **Portable mapping** (`data/ChatSync.kt`, new):
   - `ChatMessage` → portable: `fromUser` → `role`; the selected version's text → `text`;
     `html` → skip (keep `text`); `attachName/attachKind` → `attachments` (no bytes);
     `replyQuote` → prepend as a Markdown quote; `reaction` → not sent.
   - portable → `ChatMessage`: `device == "pc"` messages show a small "PC" mark; `steps` shows
     as a collapsed "used N tools" line.
3. **Merge**: implement §3.4 exactly (it is ~30 lines). Never rewrite existing messages.
4. **Transport** (`bridge/ChatSyncClient.kt`, new): open a socket like `pc_sync_memory` does,
   send `chats_list` → `chat_get` / `chat_put` as in §4, close. Run it in a coroutine on
   `Dispatchers.IO`; show nothing when the PC is unreachable (sync is best-effort).
5. **Triggers**: on app start and on bridge connect; when the chat list opens; before sending
   a message in a synced chat (pull, then send); after a turn ends (push).
6. **UI**: chat list — a "PC" badge for chats with `origin == "pc"`; a chat forked by the PC
   shows " (phone)" in its title as the PC named it. Strings in `values/strings.xml` and
   `values-ru/strings.xml`.
7. **Model context on the phone**: when a chat has PC messages, the phone agent builds its
   history from the visible messages as it already does — no extra work.
8. **Tests**: unit tests for the mapping and the merge rule (created / appended / unchanged /
   forked, trimmed history); an instrumented round trip against a fake PC socket.
9. **Lite flavor**: no bridge → no sync; keep the code behind `PcBridgeFacade` like the other
   bridge features.

## 8. Order of work and checks

1. PC: `core/chat_sync.py` + tests → WS handlers → UI badge.
2. Android: storage fields → mapping + merge + tests → client → triggers → UI.
3. Live check: start a chat on the phone, continue it on the PC, answer on the PC, reopen on
   the phone — the PC's answer is there; then add messages on both while disconnected →
   one "(phone)" fork appears, nothing is lost.
