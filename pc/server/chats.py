"""Chats that live on their own, apart from the windows showing them.

A chat (ChatState) owns its session, its run and everything the run waits on: approvals,
questions, steering. A window's socket (server/ws.py Connection) only views one chat at a
time. So a run keeps going when the user opens another chat, reloads the page or the window
goes to the tray; the chat's history is written into that chat, never into the one on
screen; and a chat nobody has open can be woken by a reminder (the ChatHub).

Before this, the socket was the chat: switching chats mid-run swapped the session under
the running agent, and its steps were written into whatever chat was open.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

import core.settings as settings_module
from core.agent.run_options import RunOptions
from core.agent.run_state import RunStateStore
from core.agent.runner import AgentRunner
from core.agent.session import WAKE, Session
from core.agent.storage import SessionStore
from core.errors import AgentError, ConfigError
from core.events import (
    ArtifactCreated,
    BrowserHandoff,
    Event,
    PlanUpdate,
    QuestionAsked,
    RunFailed,
    RunFinished,
    RunStarted,
    ShowFile,
    ShowHtml,
    ShowImage,
    StepStarted,
    TextDelta,
    ToolFinished,
    ToolStarted,
)
from core.i18n import tr
from core.llm import build_llm_client
from core.logging_setup import get_logger
from core.reminders import Reminder, ReminderStore
from core.security.permissions import MODES, PermissionStore
from core.settings import Settings
from core.tools.base import ApprovalRequest

if TYPE_CHECKING:
    from core.tools.registry import ToolRegistry

logger = get_logger("server.chats")

#: Session's default title (core/agent/session.py); the UI shows it localised as "New chat".
DEFAULT_TITLE = "Новый диалог"

#: During a run the chat is stored at most this often (and at its end): a crash or a kill
#: loses at most a few seconds, and a long chat is not rewritten on every step.
SAVE_EVERY = 5.0

#: How many chats nobody has open may run at once after being woken: after a long absence
#: many reminders can be due together; the rest wait for the next scheduler pass.
MAX_HEADLESS_RUNS = 3

WAKE_HEAD = (
    "[Automatic wake-up — this is not a message from the user. Something you scheduled "
    "is due:]"
)
WAKE_TAIL = (
    "Continue the work it was set for. If it only asks to tell the user something, tell "
    "them in one or two lines. If there is nothing to do now, say so in one line."
)


def _jsonable(value: Any) -> Any:
    """Tool arguments may hold a Path and the like: make them JSON."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def settings_for(session: Session, base: Settings) -> tuple[Settings, str]:
    """The chat's settings: its folder and its own approval mode. Returns a warning text
    when the chat's folder is gone (the chat then opens in the default folder)."""
    warning = ""
    if session.workspace:
        try:
            chat_settings = base.for_workspace(session.workspace)
        except ConfigError as exc:
            # The folder may have been deleted or its drive unplugged: the chat must open.
            logger.warning("chat folder unavailable: %s", exc)
            warning = tr("ws.chat_folder_missing", path=session.workspace)
            chat_settings = base
            session.workspace = str(base.workspace)
    else:
        session.workspace = str(base.workspace)
        chat_settings = base
    if session.approval_mode in MODES:
        chat_settings = chat_settings.model_copy(update={"approval_mode": session.approval_mode})
    return chat_settings, warning


def user_text(reminder: Reminder) -> str:
    """What the user sees of a fired reminder (the model gets Reminder.fired_text)."""
    note = reminder.note.strip()
    if reminder.kind == "wait":
        return tr("rem.wait_over", note=note or tr("rem.continue"))
    if reminder.kind == "job":
        ok = reminder.outcome.startswith("finished successfully")
        head = tr("rem.job_ok" if ok else "rem.job_failed", job=reminder.job)
        return f"{head}: {note}" if note else head
    return note or tr("rem.default")


class ChatState:
    """One chat: its session, its run and whatever the run waits on."""

    def __init__(self, hub: ChatHub, registry: ToolRegistry, session: Session, session_settings: Settings) -> None:
        self.hub = hub
        self.registry = registry
        self.session = session
        self.session_settings = session_settings
        self.store = hub.store
        self.viewers: set[Any] = set()
        self.run_task: asyncio.Task | None = None
        self.pending_approvals: dict[str, asyncio.Future[str]] = {}
        # Approvals are shown one at a time: tools run in parallel and would otherwise
        # flood the screen with cards, and an answer could reach the wrong request.
        self._approval_lock = asyncio.Lock()
        self.permissions = PermissionStore(settings=settings_module.get_settings())
        #: Tool arguments of running calls — needed when the result arrives.
        self._tool_args: dict[str, dict[str, Any]] = {}
        #: The answer text between tool rounds, so the history splits rounds as seen live.
        self._text_seg = ""
        #: The answer had inline media/widgets: its text is already stored in segments.
        self._turn_had_inline = False
        #: The tools' shared memory for this chat (ask answers, steering, notifications).
        self._scratch: dict[str, Any] = {}
        #: The new chat's title, asked from the model in parallel with its first answer.
        self._title_task: asyncio.Task[None] | None = None
        self._pending_title: str | None = None
        self._title_for_new_chat = False
        self._retitle_tried: set[str] = set()
        #: Question/approval/handoff cards the run is waiting on: shown again to a window
        #: that opens this chat later.
        self._open_prompts: dict[str, dict[str, Any]] = {}
        self._last_save = 0.0
        #: The chat was deleted while open: it is never stored again.
        self.deleted = False
        #: Reminders that woke this run / wait in its notifications (text -> ids).
        self._wake_ids: list[str] = []
        self._notice_ids: dict[str, list[str]] = {}
        self._scratch["_on_notifications"] = self._notifications_taken

    # ------------------------------------------------------------ state

    @property
    def running(self) -> bool:
        return self.run_task is not None and not self.run_task.done()

    async def send(self, payload: dict[str, Any]) -> None:
        """To every window showing this chat (none: the chat runs unseen)."""
        for viewer in list(self.viewers):
            await viewer.send(payload)

    async def _save_session(self) -> None:
        self.session.workspace = str(self.session_settings.workspace)
        if self.session.messages and not self.deleted:
            self._last_save = time.monotonic()
            await self.store.async_save(self.session)

    async def _state(self, state: str) -> None:
        await self.send({"type": "state", "state": state})

    async def _send_context_usage(self) -> None:
        """How many tokens the chat's context takes now — for the ring by the input."""
        try:
            tokens, exact = self.session.context_now()
        except Exception:  # noqa: BLE001 - the ring must never break anything
            return
        await self.send({"type": "context.usage", "tokens": tokens, "exact": exact})

    async def replay_to(self, viewer: Any) -> None:
        """A window opened this chat mid-run: show it the run's state, the cards the run is
        waiting on and the part of the answer streamed so far."""
        if not self.running:
            return
        waiting = bool(self.pending_approvals)
        await viewer.send({"type": "state", "state": "waiting_approval" if waiting else "running"})
        for payload in list(self._open_prompts.values()):
            await viewer.send(payload)
        if self._text_seg:
            await viewer.send(TextDelta(text=self._text_seg).model_dump(mode="json"))

    # ------------------------------------------------------------ events

    async def emit(self, event: Event) -> None:
        if isinstance(event, RunStarted):
            await self._on_run_started()
        if isinstance(event, PlanUpdate):
            self.session.set_plan([s.model_dump() for s in event.steps])
            await self._save_session()
        elif isinstance(event, ArtifactCreated):
            self.session.add_artifact(event.model_dump())
            await self._save_session()
        else:
            self._record_timeline(event)
        payload = event.model_dump(mode="json")
        if isinstance(event, (QuestionAsked, BrowserHandoff)):
            self._open_prompts[event.request_id] = payload
            if not self.viewers:
                await self.hub.announce(self, [tr("ws.question_waiting")], attention=True)
        if isinstance(event, (StepStarted, ToolFinished)) and time.monotonic() - self._last_save > SAVE_EVERY:
            await self._save_session()
        await self.send(payload)

    def _record_timeline(self, event: Event) -> None:
        """Writes the course of the work into the session, so a reopened chat looks the same.

        `messages` cannot do this: it is the model's format, it does not tell which tools
        ran and how they ended.
        """
        if isinstance(event, TextDelta):
            # The current answer segment: an intermediate "text" before the next tool round
            # or the final "answer".
            self._text_seg += event.text or ""
        elif isinstance(event, ToolFinished):
            self.session.append_timeline(
                {
                    "kind": "step",
                    "name": event.name,
                    "args": _jsonable(self._tool_args.pop(event.call_id, {})),
                    "ok": event.ok,
                    "duration_ms": event.duration_ms,
                    # The full output went to the model already; the chat file must not
                    # grow to megabytes.
                    "output": event.output[:4000],
                    "ts": event.ts,
                }
            )
        elif isinstance(event, ToolStarted):
            self._tool_args[event.call_id] = event.args
            seg = self._text_seg.strip()
            if seg:
                self.session.append_timeline({"kind": "text", "text": seg, "ts": event.ts})
            self._text_seg = ""
        elif isinstance(event, (ShowImage, ShowHtml, ShowFile)):
            # Inline media in the answer: store the text so far first, so the media keeps
            # its place in a reopened chat.
            seg = self._text_seg.strip()
            if seg:
                self.session.append_timeline({"kind": "text", "text": seg, "ts": event.ts})
            self._text_seg = ""
            self._turn_had_inline = True
            self.session.append_timeline(self._inline_entry(event))
        elif isinstance(event, RunFinished):
            # With inline media the text is already stored in segments: only the tail is
            # the final answer, otherwise the whole text would be stored twice.
            trailing = self._text_seg.strip()
            self._text_seg = ""
            answer_text = trailing if self._turn_had_inline else event.text
            self._turn_had_inline = False
            self.session.append_timeline(
                {
                    "kind": "answer",
                    "text": answer_text,
                    "full": event.text,
                    "steps": event.steps,
                    "duration_ms": event.duration_ms,
                    "usage": event.usage,
                    "cost_usd": event.cost_usd,
                    "checks": event.checks,
                    "run_id": event.run_id,  # for "roll back the run" from the history
                    "ts": event.ts,
                }
            )
        elif isinstance(event, RunFailed):
            self.session.append_timeline({"kind": "error", "text": event.message, "ts": event.ts})

    @staticmethod
    def _inline_entry(event: ShowImage | ShowHtml | ShowFile) -> dict[str, Any]:
        """The history entry of inline media — the same shape the UI gets live."""
        if isinstance(event, ShowImage):
            return {"kind": "image", "path": event.path, "name": event.name,
                    "caption": event.caption, "ts": event.ts}
        if isinstance(event, ShowHtml):
            return {"kind": "widget", "html": event.html, "caption": event.caption,
                    "widget_kind": event.kind, "ts": event.ts}
        return {"kind": "media", "path": event.path, "name": event.name,
                "caption": event.caption, "media_kind": event.kind,
                "size_bytes": event.size_bytes, "ts": event.ts}

    # ------------------------------------------------------------ runs

    def launch(self, task_text: str, model: str | None, options: RunOptions | None = None,
               allow_route: bool = False) -> None:
        self.run_task = asyncio.create_task(
            self._run(task_text, model, options, allow_route), name=f"agent-run-{self.session.id}"
        )
        self.run_task.add_done_callback(self._run_done)
        self.hub.chat_changed(self)

    def _run_done(self, _task: asyncio.Task) -> None:
        # Notifications the run ended before taking go back to the scheduler: it wakes the
        # chat with them now that it is idle.
        left = self._scratch.pop("notifications", None) or []
        for text in left:
            self.hub.inflight.difference_update(self._notice_ids.pop(text, []))
        self._notice_ids.clear()
        self.hub.chat_changed(self)

    async def _run(
        self, task_text: str, model: str | None, options: RunOptions | None = None,
        allow_route: bool = False,
    ) -> None:
        try:
            await self._run_inner(task_text, model, options, allow_route)
        finally:
            await self._after_run()

    async def _run_inner(
        self, task_text: str, model: str | None, options: RunOptions | None = None,
        allow_route: bool = False,
    ) -> None:
        new_chat = self.session.title == DEFAULT_TITLE and not self.session.messages
        self._title_for_new_chat = new_chat
        if self.session_settings.chat_titles:
            first = task_text if new_chat else self._untitled_first_message()
            if first:
                self._start_title(first, model)
        if new_chat:
            # A new chat goes into the list at once: routing may take a while before the run
            # itself starts (and stores the chat again). _save_session skips a chat without
            # messages, and the user's message reaches them only when the run starts.
            self.session.workspace = str(self.session_settings.workspace)
            await self.store.async_save(self.session)
        await self._state("running")
        # Routing by difficulty: the judge splits the request into subtasks, each with a model
        # tier. Different tiers → the subtasks go to both models in turn (the cheap one does
        # the simple part, the strong one the hard part); otherwise one model for the run.
        base_url: str | None = None
        api_key: str | None = None
        # The "Routing" toggle by the input (options.routing) wins over the setting.
        route_on = options.routing if (options and options.routing is not None) else self.session_settings.model_routing
        route_chosen: dict[str, Any] | None = None  # the chosen tier (with fallback models)
        if allow_route and route_on:
            from core.agent.router import (
                choose_model,
                is_distributed_plan,
                plan_subtasks,
                resolve_tiers,
            )

            tiers = resolve_tiers(self.session_settings)
            if tiers:
                plan = await plan_subtasks(task_text, self.session_settings, build_llm_client)
                if is_distributed_plan(plan):
                    await self._run_distributed(plan, tiers, options)
                    return
                # One model: the plan's single tier; an empty plan (the judge did not
                # answer) → the reliable one-word choose_model.
                if plan:
                    name = plan[0]["tier"] if len({s["tier"] for s in plan}) == 1 else "strong"
                    chosen: dict[str, str] | None = tiers[name]
                    note = f"{'простая' if name == 'fast' else 'сложная'} задача → {chosen.get('model')}"
                else:
                    chosen, note = await choose_model(task_text, self.session_settings, build_llm_client)
                if chosen and chosen.get("model"):
                    route_chosen = chosen
                    model = chosen["model"]
                    base_url = chosen.get("base_url") or None
                    api_key = chosen.get("api_key") or None
                    await self.send({"type": "log", "level": "info", "text": tr("ws.routing", note=note)})
                    await self.send({"type": "model.routed", "model": model, "note": note})
        # Provider overrides only when there are any (otherwise the plain
        # build_llm_client(model=...) call, which the tests monkeypatch).
        client_kwargs: dict[str, Any] = {"model": model}
        if base_url:
            client_kwargs["base_url"] = base_url
        if api_key:
            client_kwargs["api_key"] = api_key
        try:
            # The chosen tier may have fallback models: FallbackLLM switches to the next one
            # when the main one does not answer.
            if route_chosen:
                from core.agent.router import tier_candidates
                from core.llm.fallback import FallbackLLM

                cands = tier_candidates(route_chosen)
                llm = FallbackLLM(cands, build_llm_client) if len(cands) > 1 else build_llm_client(**client_kwargs)
            elif backups := fallback_models(self.session_settings, client_kwargs.get("model")):
                # Backup models from the settings: a failing or slow provider hands the step on.
                from core.llm.fallback import FallbackLLM

                llm = FallbackLLM([client_kwargs] + [{"model": m} for m in backups], build_llm_client)
            else:
                llm = build_llm_client(**client_kwargs)
        except AgentError as exc:
            await self.send({"type": "run.failed", "run_id": "", "message": str(exc)})
            await self._state("idle")
            return

        runner = AgentRunner(
            llm=llm,
            registry=self.registry,
            session=self.session,
            settings=self.session_settings,
            emitter=self.emit,
            approver=self.ask_approval,
        )
        # ask and the other waiting tools reach the chat through its shared scratch.
        runner.tool_context.scratch = self._scratch

        try:
            await runner.run(task_text, options)
            await self._save_session()
        except asyncio.CancelledError:
            logger.info("run cancelled (chat %s)", self.session.id)
            await self._save_session()
        finally:
            await llm.aclose()
            await self._state("idle")
            await self._send_context_usage()

    async def _run_distributed(
        self, plan: list[dict[str, str]], tiers: dict[str, dict[str, str]], options: RunOptions | None,
    ) -> None:
        """Subtasks go to the tiers in ONE session: the model switches between subtasks (the
        cheap one does the simple ones, the strong one the hard ones). The context is shared,
        so later subtasks see what the earlier ones did."""
        clients: dict[str, Any] = {}

        def client_for(tier_name: str) -> Any:
            if tier_name not in clients:
                from core.agent.router import tier_candidates
                from core.llm.fallback import FallbackLLM

                tier = tiers.get(tier_name) or tiers["strong"]
                cands = tier_candidates(tier)
                if len(cands) > 1:
                    clients[tier_name] = FallbackLLM(cands, build_llm_client)
                else:
                    kw: dict[str, Any] = {"model": tier.get("model")}
                    if tier.get("base_url"):
                        kw["base_url"] = tier["base_url"]
                    if tier.get("api_key"):
                        kw["api_key"] = tier["api_key"]
                    clients[tier_name] = build_llm_client(**kw)
            return clients[tier_name]

        summary = ", ".join(f"{i + 1}:{s['tier']}" for i, s in enumerate(plan))
        await self.send({
            "type": "log", "level": "info",
            "text": tr("ws.routing_split", n=len(plan), summary=summary),
        })
        runner: AgentRunner | None = None
        try:
            for i, sub in enumerate(plan):
                try:
                    llm = client_for(sub["tier"])
                except AgentError as exc:
                    await self.send({"type": "run.failed", "run_id": "", "message": str(exc)})
                    break
                model_name = (tiers.get(sub["tier"]) or {}).get("model", "")
                await self.send({
                    "type": "model.routed", "model": model_name,
                    "note": f"подзадача {i + 1}/{len(plan)} → {sub['tier']} ({model_name})",
                })
                if runner is None:
                    runner = AgentRunner(
                        llm=llm, registry=self.registry, session=self.session,
                        settings=self.session_settings, emitter=self.emit, approver=self.ask_approval,
                    )
                    runner.tool_context.scratch = self._scratch
                else:
                    runner.llm = llm
                await runner.run(sub["text"], options)
                await self._save_session()
        except asyncio.CancelledError:
            logger.info("distributed run cancelled (chat %s)", self.session.id)
            await self._save_session()
        finally:
            for client in clients.values():
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001 - closing must not mask the run's outcome
                    logger.debug("closing a model client failed", exc_info=True)
            await self._state("idle")
            await self._send_context_usage()

    async def _after_run(self) -> None:
        self._open_prompts.clear()
        # A wake that never reached the model (the model could not be built): keep it in the
        # history, so the next turn still knows, instead of retrying it every second.
        if self._wake_ids:
            self.session.add_note("[A reminder fired, but the model was unavailable to act on it.]")
            self._finish_wake()
            await self._save_session()

    async def compact(self, focus: str = "") -> None:
        """Folds the conversation so far into a summary now (/compact), keeping the last few
        messages as they are. The messages stay in the chat; the model sees the summary."""
        if self.running:
            await self.send({"type": "log", "level": "warning", "text": tr("ws.compact_busy")})
            return
        from core.agent.runner import folded_note, summarize_history

        count = self.session.overflow_count(0, keep_recent=4)
        if count <= 0:
            await self.send({"type": "compacted", "folded": 0, "message": tr("ws.compact_nothing")})
            return
        before = self.session.token_estimate()
        await self.send({"type": "state", "state": "compacting"})
        try:
            llm = build_llm_client(model=self.session.model or None)
            summary = await summarize_history(llm, self.session.peek_prefix(count), focus)
        except AgentError as exc:
            summary = ""
            logger.warning("compact: %s", exc)
        if not summary:
            await self._state("idle")
            await self.send({"type": "compacted", "folded": 0, "error": tr("ws.compact_failed")})
            return
        folded = self.session.replace_prefix(count, folded_note(count, summary))
        await self._save_session()
        await self._state("idle")
        await self.send({"type": "compacted", "folded": folded, "before": before,
                         "after": self.session.token_estimate(), "summary": summary})
        await self._send_context_usage()

    async def stop(self) -> None:
        if self.running:
            self.run_task.cancel()  # type: ignore[union-attr]
            await self.send({"type": "log", "level": "warning", "text": tr("ws.stopping")})
        for future in self.pending_approvals.values():
            if not future.done():
                future.set_result("deny")
        self.pending_approvals.clear()
        self._cancel_questions()

    # ------------------------------------------------------------ wake-ups

    async def deliver(self, items: list[Reminder]) -> None:
        """Hands fired reminders to this chat: a quiet one is only shown, the rest reach the
        model — at the next step of a running task, or as a new turn of an idle chat."""
        quiet = [r for r in items if not r.wake]
        wake = [r for r in items if r.wake]
        now = time.time()
        for r in quiet:
            text = r.fired_text()
            self.session.append_timeline({"kind": "wake", "text": user_text(r), "quiet": True, "ts": now})
            if self.running:
                # Mid-run the history may be between a call and its result: the note goes in
                # at the next step instead.
                self._scratch.setdefault("notifications", []).append(f"(shown to the user) {text}")
            else:
                self.session.add_note(f"[A reminder was shown to the user] {text}")
        if quiet:
            self._mark_delivered([r.id for r in quiet])
            await self._save_session()
        await self.hub.announce(self, [user_text(r) for r in items])
        if not wake:
            return
        if self.running:
            notices = self._scratch.setdefault("notifications", [])
            for r in wake:
                text = r.fired_text()
                notices.append(text)
                self._notice_ids.setdefault(text, []).append(r.id)
            return
        self._wake_ids = [r.id for r in wake]
        task = "\n".join([WAKE_HEAD, *(f"- {r.fired_text()}" for r in wake), WAKE_TAIL])
        self.session.append_timeline({"kind": "wake", "text": "\n".join(user_text(r) for r in wake), "ts": now})
        # A wake continues whatever was cut off: the "continue the interrupted task" offer
        # would only duplicate it.
        await asyncio.to_thread(RunStateStore(self.session_settings.data_dir).clear, self.session.id)
        # The chat may have been open for hours: the model, keys and folder as they are now.
        self.session_settings, _ = await asyncio.to_thread(settings_for, self.session, settings_module.get_settings())
        self.launch(task, self.session.model or None)

    def _notifications_taken(self, texts: list[str]) -> None:
        """The runner put these notifications into the history (see _drain_notifications)."""
        ids: list[str] = []
        for text in texts:
            ids += self._notice_ids.pop(text, [])
        self._mark_delivered(ids)

    def _finish_wake(self) -> None:
        self._mark_delivered(self._wake_ids)
        self._wake_ids = []

    def _mark_delivered(self, ids: list[str]) -> None:
        if not ids:
            return
        ReminderStore(self.session_settings.data_dir).mark_delivered(ids)
        self.hub.inflight.difference_update(ids)

    # ------------------------------------------------------------ titles

    def _untitled_first_message(self) -> str:
        """The first message of a chat still called after it (named by the old first-line rule,
        before the model wrote titles), or "" — the user's own names are left alone. Tried
        once per chat, so a model that cannot answer is not asked every time."""
        from core.agent.titler import fallback_title

        if not self.session.messages or self.session.id in self._retitle_tried:
            return ""
        candidates = [next((str(e.get("text") or "") for e in self.session.timeline if e.get("kind") == "user"), "")]
        for m in self.session.messages:
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
                candidates.append(str(content or ""))
                break
        first = next((c for c in candidates if c.strip() and self.session.title == fallback_title(c)), "")
        if not first:
            return ""
        self._retitle_tried.add(self.session.id)
        return first

    def _start_title(self, task_text: str, model: str | None) -> None:
        """Ask for the chat's title alongside the first answer (the router's cheap model when
        routing is set up, otherwise the chat's model)."""
        from core.agent.router import _build_kwargs, resolve_tiers

        tiers = resolve_tiers(self.session_settings) if self.session_settings.model_routing else None
        candidates: list[dict[str, Any]] = []
        for tier in (tiers["router"], tiers["fast"], tiers["strong"]) if tiers else ():
            candidates.append(_build_kwargs(tier))
        candidates.append({"model": model or self.session.model or self.session_settings.default_model})
        unique: list[dict[str, Any]] = []
        for kw in candidates:
            if kw.get("model") and kw not in unique:
                unique.append(kw)
        self._pending_title = None
        self._title_task = asyncio.create_task(self._make_title(task_text, unique), name="chat-title")

    async def _make_title(self, task_text: str, kwargs: list[dict[str, Any]]) -> None:
        # A background task: an error here would vanish with it, so it is logged.
        try:
            await self._apply_title(task_text, kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("chat title failed", exc_info=True)

    async def _apply_title(self, task_text: str, kwargs: list[dict[str, Any]]) -> None:
        from core.agent.titler import fallback_title, generate_title

        title = await generate_title(task_text, build_llm_client, kwargs) or fallback_title(task_text)
        self._pending_title = title
        if self.session.messages:  # the run has started: apply and store it now
            self.session.title = title
            await self._save_session()
        await self.hub.broadcast({"type": "session.title", "session_id": self.session.id, "title": title})

    def rename(self, title: str) -> None:
        """The user's own title wins over the model's: a pending title request is dropped."""
        if self._title_task is not None and not self._title_task.done():
            self._title_task.cancel()
        self._title_task, self._pending_title = None, None
        self.session.title = title

    async def _on_run_started(self) -> None:
        """The user's message is in the session now: store the chat at once, so it is in the
        list even if the app closes mid-answer. Until the model's title arrives the chat is
        called "New chat" (Session.add_user put the first line there)."""
        if self._title_task is not None:
            if self._pending_title:
                self.session.title = self._pending_title
            elif not self._title_task.done() and self._title_for_new_chat:
                self.session.title = DEFAULT_TITLE
        await self._save_session()
        # The wake-up message is in the saved history: the reminders are delivered.
        if self._wake_ids:
            last_user = next((m for m in reversed(self.session.messages) if m.get("role") == "user"), None)
            if last_user is not None:
                last_user[WAKE] = True  # not the user's: "retry"/"rewind" count only theirs
            self._finish_wake()
            await self._save_session()

    # ------------------------------------------------------------ what the run waits on

    def _cancel_questions(self) -> None:
        """Releases unanswered questions, otherwise the task would hang forever."""
        for waiter in self._scratch.get("_ask_answers", {}).values():
            if not waiter.done():
                waiter.set_result({})

    async def ask_approval(self, request: ApprovalRequest) -> bool:
        """Asks the user — strictly one request at a time. Nobody has the chat open: the card
        waits for the first window that opens it (the run waits too)."""
        workspace = str(self.session_settings.workspace)

        # A remembered "always allow" answers before any card is shown.
        if self.permissions.is_allowed(request.name, workspace):
            return True

        async with self._approval_lock:
            if self.permissions.is_allowed(request.name, workspace):
                return True

            req_id = uuid.uuid4().hex[:8]
            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self.pending_approvals[req_id] = future
            payload = {
                "type": "approval.requested",
                "request_id": req_id,
                "name": request.name,
                "reason": request.reason,
                "args": request.args,
                "category": request.category,
                "tier": request.tier,
                "reasons": request.reasons,
                "workspace": workspace,
            }
            self._open_prompts[req_id] = payload

            await self._state("waiting_approval")
            await self.send(payload)
            if not self.viewers:
                await self.hub.announce(self, [tr("ws.approval_waiting", name=request.name)], attention=True)

            try:
                answer = await future
            finally:
                self.pending_approvals.pop(req_id, None)
                self._open_prompts.pop(req_id, None)
                await self._state("running")

            if answer == "project":
                await asyncio.to_thread(self.permissions.allow_project, request.name, workspace)
            elif answer == "global":
                await asyncio.to_thread(self.permissions.allow_global, request.name)

            approved = answer in ("once", "project", "global")
            await self.send(
                {
                    "type": "approval.resolved",
                    "request_id": req_id,
                    "approved": approved,
                    "scope": answer,
                }
            )
            return approved

    def resolve_question(self, message: dict[str, Any]) -> None:
        """Answers to the agent's questions: {"0": ["option"], "1": [...]}."""
        request_id = str(message.get("request_id") or "")
        self._open_prompts.pop(request_id, None)
        waiter = self._scratch.get("_ask_answers", {}).get(request_id)
        if waiter and not waiter.done():
            waiter.set_result(message.get("answers") or {})

    def resolve_handoff(self, message: dict[str, Any]) -> None:
        """The user pressed "Done" in a browser handoff."""
        request_id = str(message.get("request_id") or "")
        self._open_prompts.pop(request_id, None)
        waiter = self._scratch.get("_browser_handoff", {}).get(request_id)
        if waiter and not waiter.done():
            waiter.set_result(message.get("result") or {})

    def resolve_approval(self, message: dict[str, Any]) -> None:
        """The user's answer: once | project | global | deny."""
        req_id = str(message.get("request_id") or "")
        scope = str(message.get("scope") or "").strip()
        if not scope:
            scope = "once" if message.get("approved") else "deny"
        if scope not in ("once", "project", "global", "deny"):
            scope = "deny"
        future = self.pending_approvals.get(req_id)
        if future and not future.done():
            future.set_result(scope)


class ChatHub:
    """Every chat that is open in a window, running, or being woken — one object per chat,
    whatever window shows it."""

    def __init__(self, registry: ToolRegistry | None = None, *, detached_runs: bool = True) -> None:
        self.registry = registry
        self.store = SessionStore(settings=settings_module.get_settings())
        self.chats: dict[str, ChatState] = {}
        #: Every window's socket: the chat list, titles and reminders go to all of them.
        self.connections: set[Any] = set()
        #: Reminder ids being delivered right now (the scheduler does not hand them out twice).
        self.inflight: set[str] = set()
        #: A run goes on when its window closes (the app's hub); a bare hub (tests without
        #: the app) stops it, as the socket used to.
        self.detached_runs = detached_runs
        self.shutting_down = False

    # ------------------------------------------------------------ chats

    def new_chat(self, registry: ToolRegistry, session: Session, session_settings: Settings) -> ChatState:
        chat = ChatState(self, registry, session, session_settings)
        self.chats[session.id] = chat
        return chat

    async def open(self, session_id: str, registry: ToolRegistry | None = None) -> tuple[ChatState | None, str]:
        """The live chat, or the stored one brought to life. Returns (chat, warning)."""
        live = self.chats.get(session_id)
        if live is not None:
            return live, ""
        loaded = await self.store.async_load(session_id)
        if loaded is None:
            return None, ""
        chat_settings, warning = await asyncio.to_thread(settings_for, loaded, settings_module.get_settings())
        # Loading takes a moment: another caller may have brought it to life meanwhile.
        live = self.chats.get(session_id)
        if live is not None:
            return live, ""
        chat = self.new_chat(registry or self.registry, loaded, chat_settings)  # type: ignore[arg-type]
        return chat, warning

    def attach(self, chat: ChatState, viewer: Any) -> None:
        chat.viewers.add(viewer)
        self.chats[chat.session.id] = chat
        # Phone tools reach the phone through the chat: keep a phone viewer over a PC one.
        current = chat._scratch.get("_bridge")
        if current is None or not getattr(current, "phone_connected", False) or getattr(viewer, "phone_connected", False):
            chat._scratch["_bridge"] = viewer

    async def detach(self, chat: ChatState, viewer: Any) -> None:
        chat.viewers.discard(viewer)
        if chat._scratch.get("_bridge") is viewer:
            chat._scratch.pop("_bridge", None)
            for other in chat.viewers:
                chat._scratch["_bridge"] = other
                break
        if chat.running and not chat.viewers and not self.detached_runs:
            await chat.stop()
            await asyncio.gather(chat.run_task, return_exceptions=True)  # type: ignore[arg-type]
        await chat._save_session()
        self._forget_if_idle(chat)

    def _forget_if_idle(self, chat: ChatState) -> None:
        if not chat.viewers and not chat.running and self.chats.get(chat.session.id) is chat:
            self.chats.pop(chat.session.id, None)

    def chat_changed(self, chat: ChatState) -> None:
        """A chat started or finished a run: every window's chat list shows it."""
        self._forget_if_idle(chat)
        payload = {"type": "chat.activity", "session_id": chat.session.id, "running": chat.running,
                   "title": chat.session.title}
        for conn in list(self.connections):
            try:
                conn.outbox.put_nowait(payload)
            except (AttributeError, asyncio.QueueFull):
                continue

    def running_ids(self) -> set[str]:
        return {sid for sid, chat in self.chats.items() if chat.running}

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for conn in list(self.connections):
            await conn.send(payload)

    async def announce(self, chat: ChatState, texts: list[str], *, attention: bool = False) -> None:
        """Fired reminders (or a card waiting for an answer): in the chat's feed for its
        windows, as a notice with the chat's name for the others."""
        payload = {"type": "reminder.fired", "session_id": chat.session.id, "title": chat.session.title,
                   "text": "\n".join(texts), "note": texts[0] if texts else "", "attention": attention}
        for conn in list(self.connections):
            await conn.send({**payload, "here": conn in chat.viewers})

    # ------------------------------------------------------------ reminders

    async def dispatch(self, fired: list[Reminder]) -> None:
        """Hands fired reminders to their chats (at most one wake per chat per pass)."""
        if self.shutting_down:
            return
        by_chat: dict[str, list[Reminder]] = {}
        for r in fired:
            if r.id not in self.inflight:
                by_chat.setdefault(r.session_id, []).append(r)
        for session_id, items in by_chat.items():
            chat, _ = await self.open(session_id) if session_id else (None, "")
            if chat is None:
                # The chat is gone (deleted): nothing can take the reminder.
                logger.info("reminder for a missing chat %s dropped", session_id)
                ReminderStore(settings_module.get_settings().data_dir).mark_delivered([r.id for r in items])
                continue
            unseen = [c for c in self.chats.values() if c.running and not c.viewers]
            if not chat.running and not chat.viewers and len(unseen) >= MAX_HEADLESS_RUNS:
                self._forget_if_idle(chat)
                continue  # too many unseen runs at once: the next pass will take it
            self.inflight.update(r.id for r in items)
            await chat.deliver(items)
            self._forget_if_idle(chat)

    # ------------------------------------------------------------ app exit

    async def shutdown(self) -> None:
        """The app is closing: stop the runs as "the app closed" (not "the user stopped"),
        so waits stay scheduled and the interrupted tasks can be continued, and store
        every chat."""
        self.shutting_down = True
        running = [c for c in self.chats.values() if c.running]
        for chat in running:
            chat._scratch["_shutdown"] = True
            chat.run_task.cancel()  # type: ignore[union-attr]
            for future in chat.pending_approvals.values():
                if not future.done():
                    future.set_result("deny")
            chat._cancel_questions()
        if running:
            await asyncio.gather(*(c.run_task for c in running), return_exceptions=True)  # type: ignore[misc]
        for chat in list(self.chats.values()):
            try:
                await chat._save_session()
            except OSError:
                logger.warning("chat %s not saved on exit", chat.session.id, exc_info=True)


def hub_of(app: Any) -> ChatHub | None:
    try:
        return app.state.chats
    except AttributeError:
        return None


def fallback_models(settings: Any, model: str | None) -> list[str]:
    """The backup models of the settings, without the one the run already uses."""
    main = (model or settings.default_model or "").strip()
    out: list[str] = []
    for name in (settings.llm_fallback_models or "").split(","):
        name = name.strip()
        if name and name != main and name not in out:
            out.append(name)
    return out
