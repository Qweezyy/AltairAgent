"""The browser panel's side of the app WebSocket.

One `BrowserChannel` per connection. The app window (Tauri) announces itself with
`browser_host_hello` and becomes the host of the embedded browser: the engine asks it
to open/select/close tabs (`browser_host_cmd` → `browser_host_ack`) and it forwards what
happens inside the tabs (`browser_host_event`, `browser_host_tabs`). A UI without the
shell (opened in an ordinary browser) gets the fallback: a real Chrome in a hidden
window, shown as a live screencast, with the user's mouse and keys forwarded to it.

Every message runs as its own task: a slow page must never hold up the chat socket —
that queueing was a big part of why the old panel felt sluggish.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path
from typing import Any

from core.browser_downloads import user_downloads_dir
from core.browser_session import get_agent_browser, normalize_target
from core.logging_setup import get_logger

logger = get_logger("server.browser")


class _Relay:
    """Engine → app window requests, answered by `browser_host_ack`."""

    def __init__(self, channel: BrowserChannel) -> None:
        self.channel = channel
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def request(self, op: str, **params: Any) -> dict[str, Any]:
        request_id = uuid.uuid4().hex[:10]
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.channel.send({"type": "browser_host_cmd", "id": request_id, "op": op, **params})
            return await asyncio.wait_for(future, timeout=20)
        except TimeoutError:
            return {"ok": False, "error": "the app window did not answer"}
        finally:
            self.pending.pop(request_id, None)

    def resolve(self, message: dict[str, Any]) -> None:
        future = self.pending.get(str(message.get("id") or ""))
        if future is not None and not future.done():
            future.set_result(message)


class BrowserChannel:
    def __init__(self, send: Any) -> None:
        self.send = send
        self.relay: _Relay | None = None
        self._dialogs: dict[str, asyncio.Future[str]] = {}
        self._streaming = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self.browser = get_agent_browser()
        self.browser.add_listener(self._on_browser_event)
        self.browser.downloads.on_change(self._on_downloads)

    # --- routing ---------------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> None:
        task = asyncio.create_task(self._dispatch(message), name=f"browser:{message.get('type')}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        try:
            if kind == "browser_host_hello":
                self.relay = _Relay(self)
                self.browser.attach_host(self.relay)
                await self._send_downloads()
            elif kind == "browser_host_ack" and self.relay:
                self.relay.resolve(message)
            elif kind == "browser_host_event":
                await self.browser.on_host_event(message)
            elif kind == "browser_host_tabs":
                await self.browser.on_host_tabs(list(message.get("tabs") or []), str(message.get("active") or ""))
            elif kind == "browser_dialog_reply":
                future = self._dialogs.pop(str(message.get("id") or ""), None)
                if future is not None and not future.done():
                    future.set_result(message.get("answer"))
            elif kind == "browser_downloads":
                await self._downloads_cmd(message)
            # --- fallback panel (no desktop shell): screencast + forwarded input ---
            elif kind == "browser_stream":
                await self._stream(bool(message.get("on")), message)
            elif kind == "browser_state":
                await self._send_state()
            elif kind == "browser_nav":
                await self._fallback_nav(str(message.get("url") or ""))
            elif kind == "browser_input":
                await self.browser.user_input(message)
            elif kind == "browser_tab_cmd":
                await self._fallback_tab(message)
        except Exception as exc:  # noqa: BLE001 — report to the panel, keep the socket alive
            logger.info("browser message %s failed: %s", kind, exc)
            await self.send({"type": "browser_state", "error": str(exc)[:300]})

    async def close(self) -> None:
        self.browser.remove_listener(self._on_browser_event)
        self.browser.downloads.off_change(self._on_downloads)
        if self.relay is not None:
            self.browser.detach_host(self.relay)
        for future in self._dialogs.values():
            if not future.done():
                future.set_result("")
        if self._streaming:
            await self.browser.stop_screencast()
        for task in list(self._tasks):
            task.cancel()

    # --- engine events → UI ----------------------------------------------------

    async def _on_browser_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "dialog":
            reply: asyncio.Future[str] = event["reply"]
            if self.relay is None and not self._streaming:
                return  # another window shows the panel
            dialog_id = uuid.uuid4().hex[:10]
            self._dialogs[dialog_id] = reply
            await self.send({"type": "browser_dialog", "id": dialog_id, "dialog": event.get("dialog"),
                             "message": event.get("message"), "default": event.get("default") or ""})
        elif kind == "agent_active":
            await self.send({"type": "browser_agent_active"})
        elif kind == "tabs" and self._streaming:
            await self._send_state()

    async def _on_downloads(self, items: list[dict[str, Any]]) -> None:
        await self.send({"type": "browser_downloads", "items": items})

    async def _send_downloads(self) -> None:
        await self._on_downloads(self.browser.downloads.list())

    async def _downloads_cmd(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        store = self.browser.downloads
        item_id = str(message.get("id") or "")
        if action == "move":
            if message.get("destination") == "workspace" and message.get("workspace"):
                target = Path(str(message["workspace"]))
            else:
                target = user_downloads_dir()
            # The UI asks the user before sending confirmed=true for a risky file.
            await store.move(item_id, target, confirmed=bool(message.get("confirmed")))
        elif action == "delete":
            await store.delete(item_id)
        else:
            await self._send_downloads()

    # --- fallback panel (Chrome in a hidden window) ------------------------------

    async def _send_state(self) -> None:
        b = self.browser
        tabs = await b.tabs()
        active = next((t for t in tabs if t["active"]), None)
        await self.send({
            "type": "browser_state", "mode": b.mode,
            "url": active["url"] if active else "", "title": active["title"] if active else "",
            "tabs": tabs, "width": b.viewport[0], "height": b.viewport[1],
        })

    def _frame_sender(self) -> Any:
        async def frame(data: bytes) -> None:
            w, h = self.browser.viewport
            await self.send({"type": "browser_frame", "data": base64.b64encode(data).decode("ascii"),
                             "width": w, "height": h})
        return frame

    async def _stream(self, on: bool, message: dict[str, Any]) -> None:
        if not on:
            self._streaming = False
            await self.browser.stop_screencast()
            return
        self._streaming = True
        if message.get("w") and message.get("h"):
            await self.browser.set_viewport(int(message["w"]), int(message["h"]))
        await self.browser.stop_screencast()  # a new size needs a new cast
        await self.browser.start_screencast(self._frame_sender())
        await self._send_state()

    async def _restream(self) -> None:
        if self._streaming:
            await self.browser.start_screencast(self._frame_sender())
        await self._send_state()

    async def _fallback_nav(self, url: str) -> None:
        if not url:
            return
        page = await self.browser.page()
        await page.goto(normalize_target(url), wait_until="commit", timeout=45000)
        await self._restream()

    async def _fallback_tab(self, message: dict[str, Any]) -> None:
        op, tab = message.get("op"), str(message.get("tab") or "")
        if op == "new":
            await self.browser.new_tab(message.get("url") or None, by_agent=False)
        elif op == "select":
            await self.browser.select_tab(tab)
        elif op == "close":
            await self.browser.close_tab(tab)
        elif op in ("back", "forward", "reload"):
            page = await self.browser.page()
            action = {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[op]
            await action(wait_until="commit", timeout=20000)
        await self._restream()
