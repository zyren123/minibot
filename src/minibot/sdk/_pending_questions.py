"""Persistence and continuation of questions awaiting a user answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING
from uuid import uuid4

from ..events import StreamEvent
from ..session.manager import SessionManager
from ._active_streams import is_active_stream

if TYPE_CHECKING:
    from .minibot import Minibot


_AWAITING_ANSWER = "awaiting_user_answer"


@dataclass(frozen=True)
class PendingQuestionResume:
    question_id: str
    answer_text: str
    selected_option_value: str | None
    trace_run_id: str | None
    durable_run_id: str | None

    @property
    def refresh_cached_bot(self) -> bool:
        return self.durable_run_id is None

    def stream(self, bot: Minibot, *, reasoning_effort: str | None) -> AsyncIterator[StreamEvent]:
        if hasattr(bot, "resume_pending_question_stream"):
            return bot.resume_pending_question_stream(
                question_id=self.question_id,
                answer_text=self.answer_text,
                selected_option_value=self.selected_option_value,
                reasoning_effort=reasoning_effort,
                **({"parent_run_id": self.trace_run_id} if self.trace_run_id else {}),
                **({"durable_run_id": self.durable_run_id} if self.durable_run_id is not None else {}),
            )
        if not hasattr(bot, "stream_existing_messages"):
            raise ValueError("Bot does not support persisted pending question resume")
        return bot.stream_existing_messages(reasoning_effort=reasoning_effort)


class PendingQuestions:
    def __init__(self, sessions: SessionManager, session_id: str) -> None:
        self._sessions = sessions
        self._session_id = session_id

    def save_legacy(self, question: dict[str, Any], *, trace_run_id: str | None) -> None:
        run_ids = {"trace_run_id": trace_run_id} if trace_run_id is not None else {}
        self._save(question, question["message_id"], run_ids)

    def save_durable(self, event: StreamEvent) -> None:
        question = {
            key: event[key] for key in (
                "message_id", "question_id", "prompt", "options", "allow_free_text", "required",
            )
        }
        self._save(question, event.get("message_id"), {"durable_run_id": (event.get("data") or {}).get("durable_run_id")})

    def _save(self, question: dict[str, Any], message_id: str | None, run_ids: dict[str, Any]) -> None:
        self._sessions.save_runtime_state(self._session_id, {
            "version": 1,
            "state": _AWAITING_ANSWER,
            **run_ids,
            "assistant_message_id": message_id,
            "pending_question": question,
        })

    def _load_reconciled(self) -> dict[str, Any] | None:
        saved = self._sessions.load_runtime_state(self._session_id)
        if saved is None or saved.get("state") != "streaming":
            return saved
        if is_active_stream(self._sessions, self._session_id, saved.get("run_id")):
            return saved

        message_id = saved.get("assistant_message_id")
        if isinstance(message_id, str) and message_id.strip():
            self._sessions.update_message(
                self._session_id,
                message_id,
                lambda current: (
                    dict(current)
                    if current.get("completion_state") == "complete"
                    else {**current, "completion_state": "interrupted"}
                ),
            )
        self._sessions.delete_runtime_state(self._session_id, expected=saved)
        return None

    async def snapshot(self, get_bot: Callable[[], Awaitable[Minibot]]) -> dict[str, Any] | None:
        saved = self._load_reconciled()
        if saved is not None and saved.get("state") == "streaming":
            return None
        question = _pending_question(saved)
        if question is not None:
            return dict(question)

        bot = await get_bot()
        if not hasattr(bot, "pending_question"):
            raise ValueError("Bot does not support askuserquestion state")
        question = bot.pending_question()
        return dict(question) if isinstance(question, dict) and question else None

    def prepare_resume(self, payload: dict[str, Any]) -> PendingQuestionResume:
        saved = self._load_reconciled()
        if saved is None or saved.get("state") != _AWAITING_ANSWER:
            raise ValueError("No persisted pending question is waiting for an answer")
        question = _pending_question(saved)
        if question is None:
            raise ValueError("Persisted pending question state is invalid")

        question_id = str(payload.get("question_id") or "").strip()
        if question_id != str(question.get("question_id") or "").strip():
            raise ValueError("Pending question id does not match")
        answer_text, selected_option_value = _normalize_answer(question, payload)
        message_id = str(saved.get("assistant_message_id") or question.get("message_id") or "").strip()
        if not message_id:
            raise ValueError("Persisted pending question is missing its assistant message id")

        resume = PendingQuestionResume(
            question_id, answer_text, selected_option_value, saved.get("trace_run_id"), saved.get("durable_run_id"),
        )
        if resume.refresh_cached_bot:
            self._mark_answered(message_id)
            self._sessions.append_message(self._session_id, {
                "role": "user",
                "content": answer_text,
                "message_id": f"msg-{uuid4().hex[:12]}",
                "answer_to_question_id": question_id,
                "selected_option_value": selected_option_value,
            })
            self._sessions.delete_runtime_state(self._session_id)
        return resume

    def trace_run_id(self) -> str | None:
        saved = self._load_awaiting_answer()
        return saved.get("trace_run_id") if saved is not None else None

    def clear_live_answer(self, message_id: str | None) -> None:
        saved = self._load_awaiting_answer()
        if saved is not None and saved.get("assistant_message_id") == message_id:
            self._sessions.delete_runtime_state(self._session_id, expected=saved)

    def clear_durable_answer(self, messages: list[dict[str, Any]]) -> None:
        saved = self._load_awaiting_answer()
        if saved is None:
            return
        question = saved.get("pending_question") or {}
        if any(message.get("answer_to_question_id") == question.get("question_id") for message in messages
               if message.get("answer_to_question_id")):
            self._sessions.delete_runtime_state(self._session_id, expected=saved)

    def abandon_durable_run(self, run_id: str) -> None:
        saved = self._sessions.load_runtime_state(self._session_id)
        if saved and saved.get("durable_run_id") == run_id:
            message_id = saved.get("assistant_message_id")
            if message_id:
                self._mark_answered(message_id)
            self._sessions.delete_runtime_state(self._session_id, expected=saved)

    def _mark_answered(self, message_id: str) -> None:
        self._sessions.update_message(
            self._session_id, message_id, lambda message: {**message, "question_pending": False},
        )

    def _load_awaiting_answer(self) -> dict[str, Any] | None:
        saved = self._sessions.load_runtime_state(self._session_id)
        return saved if saved and saved.get("state") == _AWAITING_ANSWER else None


def _pending_question(saved: dict[str, Any] | None) -> dict[str, Any] | None:
    if saved is not None and saved.get("state") == _AWAITING_ANSWER:
        question = saved.get("pending_question")
        if isinstance(question, dict) and question:
            return question
    return None


def _normalize_answer(question: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str | None]:
    answer_text = str(payload.get("answer_text") or "").strip()
    selected_value = payload.get("selected_option_value")
    selected = (str(selected_value).strip() or None) if selected_value is not None else None
    options = question.get("options") or []
    option_values = {str(item.get("value")) for item in options}

    if selected is not None and selected not in option_values:
        raise ValueError("Pending question selected option is invalid")
    if not question.get("allow_free_text", True) and selected is None:
        raise ValueError("Pending question requires choosing one of the provided options")
    if question.get("required", True) and not answer_text and selected is None:
        raise ValueError("Pending question requires a non-empty answer")

    if selected is not None and not answer_text:
        for item in options:
            if item.get("value") == selected:
                answer_text = str(item.get("label") or selected)
                break
    return answer_text, selected
