# The phone as a body of a server — contract for the Android app (0.3.0 stage 6)

The PC side is done and tested (`pc/server/phone.py`, `remote_access.py`, `notices.py`,
`bodies.py`, `tests/test_phone_server.py`). This document is everything the Android app needs to
do. The existing PC bridge (`BRIDGE_PC_SPEC.md`, `altair://pair?…`) does not change.

## 0. What the phone gets

A server (a VPS the owner installed Altair on from the PC) becomes reachable from the phone, also
when the PC is off:

- the server's chats — open, write, stop, answer approvals and questions — with the **same `/ws`
  protocol** the phone already speaks with the PC;
- the server's status (what it is, its load), its Journal, a **stop-everything** button;
- **notifications** of what the server did: a task done or failed, the agent waiting for a
  permission, an update or a system change rolled back, the agent restarted.

There are two ways to the server; the phone keeps both and uses whichever works:

| Route | When | Auth | Needs |
|---|---|---|---|
| **relay** — through the PC | the PC is on and the phone reaches it (LAN/Tailscale), as today | the PC's bridge token | nothing new on the server |
| **direct** — the server's own TLS door | anywhere, the PC may be off | the phone's own Ed25519 key | the owner turned the door on (PC: Settings → Servers → Phone → "Direct door"), port open |

A Tailscale/WireGuard address of the server is just another host for the **direct** route (same
certificate pin, same key).

## 1. The pairing link (QR on the PC)

PC: Settings → Servers → a server's card → **Phone**. The QR / link:

```
altair://body?n=<name>&s=<server id>&b=<server body id>&c=<one-time code>
             [&u=<PC bridge base URL>&t=<PC bridge token>]
             [&d=<https://host:port>&fp=<certificate SHA-256, hex>]
```

All values URL-encoded. Host `body` (the existing `altair://pair` stays the PC pairing link).

| Param | Meaning |
|---|---|
| `n` | the server's name, for the UI |
| `s` | the server's id **on the PC** — the relay path is `/b/<s>/…` |
| `b` | the server's **body id** — must equal the `verifier` its challenges return (§3.3) |
| `c` | one-time pairing code, 8 chars `[A-HJ-NP-Z2-9]`, valid **once, 10 minutes** |
| `u`, `t` | relay: the PC bridge base URL (`http://ip:port`) and its token; absent if the PC bridge is off |
| `d`, `fp` | direct: the door's URL and the **SHA-256 of its certificate (DER), lowercase hex**; absent if the door is off |

Parse like `Pairing.kt::parsePairLink`. Store per server: `n, s, b, u, t, d, fp` and, after pairing,
the server's card. Several servers → several entries.

## 2. The phone's own identity (once per phone, shared by all servers)

- **Ed25519** key pair. Android < 13 has no Ed25519 in the platform JCA (minSdk is 26): use
  **BouncyCastle** (`org.bouncycastle:bcprov-jdk18on`, MIT) — `Ed25519PrivateKeyParameters`,
  `Ed25519Signer`.
- Keep the 32-byte private key **encrypted with `security/KeyVault.kt`** (Keystore AES), never in
  plain prefs, never in logs.
- **Body id** = first 16 chars of `base64url(SHA-256(raw 32-byte public key))` (standard base64url
  alphabet `-_`, as Python's `urlsafe_b64encode`, padding irrelevant at 16 chars).
- **Card** (what the server stores):

```json
{"id": "<body id>", "name": "<device name, e.g. Pixel 8>", "kind": "phone",
 "public_key": "<standard base64 of the raw 32-byte public key>"}
```

The server recomputes the id from the key and refuses a card whose id does not match.

## 3. Direct route

### 3.1. TLS with a pinned certificate

The certificate is self-signed (no CA), so the normal trust check fails by design. Trust it **only
if `SHA-256(cert.encoded)` equals `fp`** — compare the whole hex string, constant time. Hostname
verification is replaced by the pin (the host can be an IP or a Tailscale name). OkHttp sketch:

```kotlin
val pinned = object : X509TrustManager {
    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
        val got = MessageDigest.getInstance("SHA-256").digest(chain[0].encoded).toHex()
        if (!MessageDigest.isEqual(got.toByteArray(), fp.lowercase().toByteArray()))
            throw CertificateException("not the paired server")
    }
    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
        throw CertificateException()
    override fun getAcceptedIssuers() = emptyArray<X509Certificate>()
}
val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(pinned), null) }
val client = OkHttpClient.Builder()
    .sslSocketFactory(ssl.socketFactory, pinned)
    .hostnameVerifier { _, _ -> true }          // the pin above is the identity check
    .build()
```

Use this client **only** for that server's direct URL.

### 3.2. Pairing (once per server)

```
POST {d}/api/bodies/pair      {"code": "<c>", "card": <phone card>}
→ 200 {"ok": true, "body": <server card>}       store it; body.id must equal b
→ 401 wrong / used / expired code, or 5 wrong tries in 10 min (locked) → ask for a new QR
→ 400 bad card
```

Pairing may also go through the relay (`{u}/b/{s}/api/bodies/pair?token={t}`): same body, same
answer. Pair right after scanning, by whichever route answers first. The code works once.

### 3.3. Signing in (every session; tokens live 12 h, in the server's memory)

```
POST {d}/api/bodies/challenge  {"id": "<phone body id>"}       → {"nonce": "...", "verifier": "<server body id>"}
check verifier == b (the server you paired with), else stop
signature = base64( Ed25519.sign( UTF-8("altair-body-auth:v1:" + nonce + ":" + verifier) ) )
POST {d}/api/bodies/login      {"id": "<phone body id>", "nonce": "...", "signature": "..."}
→ 200 {"token": "...", "expires_in": 43200}
→ 401 not trusted (revoked?), bad signature or a stale nonce (60 s) → re-pair
```

Then every request: `Authorization: Bearer <token>` or `?token=<token>` (WebSocket). A `401` on any
call → sign in again once; still `401` → the server no longer trusts the phone (re-pair).

Without a token only `/api/health`, `/api/bodies/challenge`, `/api/bodies/login`,
`/api/bodies/pair` answer. Everything through the door is "from outside" — even a connection
from the server itself — so the owner-only routes stay closed (`403`).

## 4. Relay route

Exactly like the phone talks to the PC today, with the prefix `/b/<s>`:

```
HTTP  {u}/b/{s}/api/...?token={t}            (or Authorization: Bearer {t})
WS    ws://<u host:port>/b/{s}/ws?token={t}  (wss if u is https)
```

The PC strips its token before forwarding — the server never sees it; on the server the request
counts as local, so no signing in is needed on this route. `503` = the PC has no live tunnel to
that server right now (try direct); `404` = the PC does not know `s` (server removed → drop it).

`GET {u}/api/bodies?token={t}` lists the PC and every server with state and load (`state`
online/connecting/offline, `agent` ok/down, `status.load`), handy for a "bodies" screen.

## 5. What to call on the server (both routes, `<base>` = `{d}` or `{u}/b/{s}`)

| What | Call |
|---|---|
| Status | `GET <base>/api/body/status` → card, `system, arch, cpus, mem_mb, gpus, docker, workspace, load{cpu_pct, mem_used_mb, disk_free_mb, tasks}` |
| Chats | `GET <base>/api/sessions`; chat itself over `<base>/ws` — **the same protocol as with the PC**: `ready`, `load_session`, `run`, `stop`, `approval`, `answer`, events… |
| Journal | `GET <base>/api/journal?limit=100&before=<seq>&since=<seq>&kind=<k1,k2>` (newest first) |
| Stop everything | `POST <base>/api/runs/stop` → `{"ok": true, "stopped": [chat ids]}` — one button, confirm first |
| News (poll) | `GET <base>/api/notices` (first time: no `since`) → `{"notices": [], "next": N}`; then `GET <base>/api/notices?since=N` → `{"notices": [...], "next": N2}`; keep `N` per server |
| Who may reach it | `GET <base>/api/bodies/trusted` → `{"self": {id, name, kind}, "you": <phone id>, "trusted": [{id, name, kind: "pc"\|"phone"\|"server", added_at, revoked}]}` |
| Shut a body out | `POST <base>/api/bodies/{id}/revoke` → `{"ok": true, "sessions_ended": n}`; for a PC also `"key_removed": bool, "sessions_cut": n` — its SSH key leaves the server's `authorized_keys` and its open tunnel is ended, so a lost or taken-over PC cannot reach the server any more. Confirm first; `403` for anything but a phone, `400` for the server itself |

On connect send `{"type": "ui_lang", "lang": "ru"|"en"}` as with the PC: the server's texts
(notice titles too) follow it.

## 6. Notifications

A notice:

```json
{"type": "notice", "kind": "run.finished", "level": "info|warning|error",
 "title": "test-vps: задача готова", "text": "plain text, ≤ 240 chars",
 "chat": "<chat id or empty>", "body_id": "self|<server id on the PC>", "at": 1791404705.9}
```

Kinds: `run.finished`, `run.failed`, `approval.waiting` (the agent waits for the phone/PC —
open `chat`, the pending `approval.requested` is replayed on `load_session`), `guardian.update.rolled_back`,
`guardian.update.rollback_impossible`, `guardian.change.rolled_back`, `guardian.agent.restarted`,
`guardian.disk.cleaned`; from the PC also `body.offline` / `body.online`.

Where they come from:
1. **Connected to the server's `/ws`** (either route): the server pushes its own notices
   (`body_id: "self"`) about every 15 s.
2. **Connected to the PC's `/ws`**: the PC pushes notices of all its servers (`body_id` = `s`).
3. **In the background**: WorkManager periodic job (15 min minimum) → `GET /api/notices?since=N`
   on each server (direct route preferred), post an Android notification per notice (reuse
   `Notifications.kt`; channel "Servers"), tap → open that server's chat `chat`.

Dedupe by `(server, kind, chat, at)` — the same event can arrive through the PC and the server.
Don't notify for the chat the user is looking at.

## 7. Choosing the route

```
paired direct and reachable  → direct
else relay configured and the PC answers → relay
else → offline (show it; keep polling in the background)
```

Probe with `GET /api/health` (open on both routes) with a short timeout; re-probe on network change.

## 8. Security rules

- The PC token `t` goes **only** to `u`. The direct route never carries it.
- The private key: KeyVault-encrypted; the session token: memory only.
- The certificate pin is the server's identity on the direct route — never fall back to "trust all".
- The pairing code is single use; never store it after pairing.
- Removing the server in the phone: forget its entry; the owner revokes the phone on the server
  (`POST /api/bodies/{id}/revoke`, from the PC or another phone).
- Revoking a PC from the phone (§5) is the owner's emergency brake: show the PC's name, ask to
  confirm, and say that the PC has to be added to the server again to get back in.

## 9. Done means (for the Android chat)

1. Scan the QR of `test-vps` (PC: Settings → Servers → test-vps → Phone): the server appears in the
   phone's bodies; pairing used the code once.
2. With the PC on: open a server chat through the relay, send a task, see it run, stop it.
3. With the door on and the phone on mobile data (PC off): status, chats, Journal, stop-everything,
   a task, an approval answered from the phone — all over the direct route.
4. A wrong certificate (another server's `fp`) is refused; a revoked phone gets `401` and asks to re-pair.
5. Background: lock the phone, run a task on the server from the PC → a notification arrives
   (within the WorkManager period) and opens the chat.
6. Same notice through PC and server → one notification.
7. The server's screen lists who may reach it (`/api/bodies/trusted`); revoking the PC there
   ends its tunnel (`sessions_cut` ≥ 1 while the PC is on) and the PC shows the server offline.
8. Tests in `:core`/`:app` for: link parsing, card/id computation (match Python's for a fixed key —
   vector below), message to sign, pin check, notice dedupe.

**Test vectors** (pinned in `tests/test_phone_server.py`; the Kotlin tests must give the same):

| Input | Expected |
|---|---|
| body id of the raw public key `bytes(1..32)` (`0x01 0x02 … 0x20`) | `riFsLvUkejeCwTXv` |
| Ed25519 private key seed `bytes(0..31)` (`0x00 0x01 … 0x1f`) → public key (base64) | `A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=` |
| its body id | `Vkdap1RjR0wChd9d` |
| its signature of `altair-body-auth:v1:NONCE123:riFsLvUkejeCwTXv` (base64) | `HT1CqEH/n4RH36kQmXLrIuoO4vqO2IOd8EIAaJc4SaTf8VoisqjMln6NdR+LfizRB2WFEfHKWMjSW48yw+cHBg==` |
