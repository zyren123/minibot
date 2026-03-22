"""Runtime integrations for external chat platforms."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .bots import DEFAULT_BOT_ID
from .platforms import ConversationMapStore

if TYPE_CHECKING:
    from .manager import AgentManager


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeishuPlatformRuntime:
    """Long-lived Feishu websocket runtime."""

    def __init__(self, *, manager: "AgentManager", platform: dict[str, Any]) -> None:
        self._manager = manager
        self._platform = dict(platform)
        self._conversation_store = ConversationMapStore(
            app_home=self._manager.app_home,
            platform_id=str(self._platform["platform_id"]),
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._sdk: Any = None
        self._api_client: Any = None
        self._ws_client: Any = None

    async def start(self) -> None:
        if self._runner_task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._manager.set_platform_runtime_state(
            str(self._platform["platform_id"]),
            connected=False,
            last_error=None,
        )
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name=f"feishu-worker-{self._platform['platform_id']}",
        )
        self._runner_task = asyncio.create_task(
            self._run_ws_client(),
            name=f"feishu-runtime-{self._platform['platform_id']}",
        )

    async def stop(self) -> None:
        runner_task = self._runner_task
        worker_task = self._worker_task
        self._runner_task = None
        self._worker_task = None

        ws_client = self._ws_client
        self._ws_client = None
        if ws_client is not None:
            stop = getattr(ws_client, "stop", None)
            if callable(stop):
                try:
                    await asyncio.to_thread(stop)
                except Exception:
                    pass

        await self._queue.put(None)

        if runner_task is not None:
            runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner_task
        if worker_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        self._manager.set_platform_runtime_state(
            str(self._platform["platform_id"]),
            connected=False,
        )

    async def _run_ws_client(self) -> None:
        platform_id = str(self._platform["platform_id"])
        try:
            self._sdk = importlib.import_module("lark_oapi")
            self._api_client = self._build_api_client()
            self._ws_client = self._build_ws_client()
            self._manager.set_platform_runtime_state(platform_id, connected=True, last_error=None)
            await asyncio.to_thread(self._ws_client.start)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._manager.set_platform_runtime_state(
                platform_id,
                connected=False,
                last_error=f"Feishu runtime failed: {exc}",
            )
        finally:
            self._manager.set_platform_runtime_state(platform_id, connected=False)

    def _build_api_client(self) -> Any:
        assert self._sdk is not None
        builder = self._sdk.Client.builder()
        log_level = getattr(getattr(self._sdk, "LogLevel", object()), "INFO", None)
        builder = builder.app_id(str(self._platform["app_id"])).app_secret(str(self._platform["app_secret"]))
        if log_level is not None:
            builder = builder.log_level(log_level)
        return builder.build()

    def _build_ws_client(self) -> Any:
        assert self._sdk is not None
        log_level = getattr(getattr(self._sdk, "LogLevel", object()), "INFO", None)
        builder_fn = self._sdk.EventDispatcherHandler.builder
        try:
            handler_builder = builder_fn("", "", log_level)
        except TypeError:
            handler_builder = builder_fn("", "")
        event_handler = handler_builder.register_p2_im_message_receive_v1(
            self._on_message_event,
        ).build()

        kwargs: dict[str, Any] = {"event_handler": event_handler}
        if log_level is not None:
            kwargs["log_level"] = log_level
        return self._sdk.ws.Client(
            str(self._platform["app_id"]),
            str(self._platform["app_secret"]),
            **kwargs,
        )

    def _on_message_event(self, data: Any) -> None:
        if self._loop is None:
            return
        payload = self._normalize_event(data)
        future = asyncio.run_coroutine_threadsafe(self._queue.put(payload), self._loop)
        with contextlib.suppress(Exception):
            future.result(timeout=1)

    def _normalize_event(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            return dict(data)
        if self._sdk is not None:
            try:
                raw = self._sdk.JSON.marshal(data)
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    async def _worker_loop(self) -> None:
        while True:
            payload = await self._queue.get()
            if payload is None:
                break
            try:
                await self._handle_message_payload(payload)
            except Exception as exc:
                self._manager.set_platform_runtime_state(
                    str(self._platform["platform_id"]),
                    last_error=f"Feishu event handling failed: {exc}",
                )

    async def _handle_message_payload(self, payload: dict[str, Any]) -> None:
        platform_id = str(self._platform["platform_id"])
        self._manager.set_platform_runtime_state(platform_id, last_event_at=_utc_now_iso(), last_error=None)

        event = payload.get("event")
        if not isinstance(event, dict):
            return
        message = event.get("message")
        sender = event.get("sender")
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return

        if str(sender.get("sender_type") or "").strip().lower() == "app":
            return

        chat_type = str(message.get("chat_type") or "").strip().lower()
        if chat_type != "p2p":
            return

        chat_id = str(message.get("chat_id") or "").strip()
        message_id = str(message.get("message_id") or "").strip()
        msg_type = str(message.get("message_type") or "").strip().lower()
        if not chat_id or not message_id:
            return

        existing = self._conversation_store.get(chat_id)
        if existing is not None and str(existing.get("last_message_id") or "") == message_id:
            return

        bound_bot_id = str(self._platform.get("bound_bot_id") or DEFAULT_BOT_ID).strip() or DEFAULT_BOT_ID
        if not self._manager.bot_exists(bound_bot_id):
            bound_bot_id = DEFAULT_BOT_ID

        session_id = str(existing.get("session_id") or "").strip() if existing else ""
        if not session_id:
            session_id = self._manager.sessions_for(bound_bot_id).create()
        self._conversation_store.set_mapping(
            external_chat_id=chat_id,
            session_id=session_id,
            last_message_id=message_id,
        )

        if msg_type != "text":
            await self._send_text(chat_id, "This Feishu bridge currently supports text messages only.")
            return

        content = self._extract_text(message)
        if not content:
            return

        try:
            lock = await self._manager.session_lock(bound_bot_id, session_id)
            async with lock:
                bot = await self._manager.get_bot(bound_bot_id, session_id)
                result = await bot.chat(content, session_id=session_id)
            reply = (result.assistant_text or "").strip() or "Minibot returned an empty reply."
        except Exception as exc:
            reply = f"Minibot is unavailable right now: {exc}"

        await self._send_text(chat_id, reply)

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str:
        raw = message.get("content")
        if not isinstance(raw, str) or not raw.strip():
            return ""
        try:
            parsed = json.loads(raw)
        except Exception:
            return raw.strip()
        if isinstance(parsed, dict):
            text = parsed.get("text")
            if isinstance(text, str):
                return text.strip()
        return raw.strip()

    async def _send_text(self, chat_id: str, text: str) -> None:
        if self._api_client is None:
            raise RuntimeError("Feishu API client is not ready")
        content = json.dumps({"text": text}, ensure_ascii=False)
        await asyncio.to_thread(self._send_text_sync, chat_id, content)

    def _send_text_sync(self, chat_id: str, content: str) -> None:
        im_v1 = importlib.import_module("lark_oapi.api.im.v1")
        request = im_v1.CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                im_v1.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(content)
                .build()
            ) \
            .build()
        response = self._api_client.im.v1.message.create(request)
        if hasattr(response, "success") and not response.success():
            code = getattr(response, "code", "")
            msg = getattr(response, "msg", "unknown error")
            raise RuntimeError(f"Feishu send failed ({code}): {msg}")
