from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import json
from typing import Any, Literal, overload


ReplyFallback = Literal["missing_message", "missing_choice", "never"]
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: str

    def as_message(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class ModelCharge:
    reported_usage: dict[str, int] | None
    output: tuple[str, str | None, list[Any]]


@dataclass(frozen=True)
class ModelReply:
    content: str
    reasoning: str
    finish_reason: str
    tool_calls: Sequence[ModelToolCall]
    charge: ModelCharge
    _display_usage_values: dict[str, Any] | None = None

    @property
    def usage(self) -> dict[str, int] | None:
        if self._display_usage_values is not None:
            return _coerce_usage(self._display_usage_values)
        return self.charge.reported_usage


def _field(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _nested_container_values(value: Any) -> list[Any]:
    nested: list[Any] = []
    for key in (
        "delta",
        "message",
        "content",
        "contents",
        "output",
        "outputs",
        "items",
        "parts",
        "response",
        "responses",
        "result",
        "results",
        "candidate",
        "candidates",
        "choices",
    ):
        item = _field(value, key)
        if item is not None:
            nested.append(item)
    return nested


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            item_type = str(_field(item, "type", "") or "").lower()
            if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
                continue
            if item_type in {"tool_call", "function_call", "tool_use"}:
                continue
            text = _field(item, "text")
            if text is not None:
                parts.append(_extract_text(text))
                continue
            output_text = _field(item, "output_text")
            if output_text is not None:
                parts.append(_extract_text(output_text))
                continue
            content = _field(item, "content")
            if content is not None:
                parts.append(_extract_text(content))
                continue
            for nested in _nested_container_values(item):
                parts.append(_extract_text(nested))
        return "".join(parts)
    item_type = str(_field(value, "type", "") or "").lower()
    if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
        return ""
    if item_type in {"tool_call", "function_call", "tool_use"}:
        return ""
    text = _field(value, "text")
    if text is not None:
        return _extract_text(text)
    output_text = _field(value, "output_text")
    if output_text is not None:
        return _extract_text(output_text)
    content = _field(value, "content")
    if content is not None:
        return _extract_text(content)
    parts = [_extract_text(nested) for nested in _nested_container_values(value)]
    return "".join(parts)


def _extract_reasoning_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            item_type = str(_field(item, "type", "") or "").lower()
            if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
                text = _extract_text(_field(item, "text"))
                if text:
                    parts.append(text)
                    continue
                output_text = _extract_text(_field(item, "output_text"))
                if output_text:
                    parts.append(output_text)
                    continue
                content = _extract_text(_field(item, "content"))
                if content:
                    parts.append(content)
                    continue
            for key in ("reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"):
                nested = _field(item, key)
                if nested is not None:
                    parts.append(_extract_reasoning_text(nested))
            for nested in _nested_container_values(item):
                parts.append(_extract_reasoning_text(nested))
        return "".join(parts)
    parts: list[str] = []
    for key in ("reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"):
        nested = _field(value, key)
        if nested is not None:
            parts.append(_extract_reasoning_text(nested))
    if parts:
        return "".join(parts)
    item_type = str(_field(value, "type", "") or "").lower()
    if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
        text = _extract_text(_field(value, "text"))
        if text:
            return text
        output_text = _extract_text(_field(value, "output_text"))
        if output_text:
            return output_text
        return _extract_text(_field(value, "content"))
    content = _field(value, "content")
    if isinstance(content, list):
        return _extract_reasoning_text(content)
    nested_parts = [_extract_reasoning_text(nested) for nested in _nested_container_values(value)]
    joined = "".join(nested_parts)
    if joined:
        return joined
    return ""


def _first_choice(value: Any) -> Any:
    choices = _field(value, "choices", []) or []
    if isinstance(choices, list) and choices:
        return choices[0]
    return None


def _stream_payload(chunk: Any) -> tuple[Any, str | None]:
    choice = _first_choice(chunk)
    if choice is not None:
        delta = _field(choice, "delta")
        finish_reason = _field(choice, "finish_reason")
        if delta is not None:
            return delta, str(finish_reason) if finish_reason else None
        return choice, str(finish_reason) if finish_reason else None
    finish_reason = _field(chunk, "finish_reason")
    if finish_reason is None:
        finish_reason = _field(chunk, "status")
    return chunk, str(finish_reason) if finish_reason else None


def _normalize_tool_call_entry(value: Any, *, index: int = 0) -> dict[str, Any] | None:
    function = _field(value, "function")
    item_type = str(_field(value, "type", "") or "").lower()
    if function is None and item_type not in {"function_call", "tool_call", "tool_use", "function"}:
        return None

    name = _field(function, "name") if function is not None else None
    if name is None:
        name = _field(value, "name") or _field(value, "tool_name")

    arguments = _field(function, "arguments") if function is not None else None
    if arguments is None:
        arguments = _field(value, "arguments")
    if arguments is None:
        arguments = _field(value, "input")
    if isinstance(arguments, (dict, list)):
        arguments = json.dumps(arguments, ensure_ascii=False)

    return {
        "index": index,
        "id": _field(value, "id"),
        "type": "function",
        "function": {
            "name": str(name or ""),
            "arguments": str(arguments or ""),
        },
    }


def _extract_stream_tool_call_deltas(value: Any) -> list[dict[str, Any]]:
    direct = _field(value, "tool_calls")
    if isinstance(direct, list) and direct:
        return list(direct)

    if isinstance(value, list):
        entries: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            normalized = _normalize_tool_call_entry(item, index=index)
            if normalized is not None:
                entries.append(normalized)
        if entries:
            return entries
        for item in value:
            nested = _extract_stream_tool_call_deltas(item)
            if nested:
                return nested
        return []

    normalized = _normalize_tool_call_entry(value)
    if normalized is not None:
        return [normalized]

    for nested in _nested_container_values(value):
        tool_calls = _extract_stream_tool_call_deltas(nested)
        if tool_calls:
            return tool_calls
    return []


def _extract_tool_calls(value: Any) -> list[Any]:
    direct = _field(value, "tool_calls")
    if isinstance(direct, list) and direct:
        return list(direct)
    normalized = _extract_stream_tool_call_deltas(value)
    if not normalized:
        return []
    return [
        {
            "id": str(item.get("id") or f"normalized-tool-{index}"),
            "type": str(item.get("type") or "function"),
            "function": {
                "name": str(_field(item.get("function"), "name", "") or ""),
                "arguments": str(_field(item.get("function"), "arguments", "") or ""),
            },
        }
        for index, item in enumerate(normalized, start=1)
    ]


def _merge_stream_tool_call_delta(
    tool_call_map: dict[int, dict[str, Any]],
    delta: Any,
) -> None:
    index_raw = _field(delta, "index", 0)
    try:
        index = int(index_raw)
    except (TypeError, ValueError):
        index = 0

    entry = tool_call_map.setdefault(
        index,
        {
            "id": None,
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )
    tc_id = _field(delta, "id")
    if tc_id:
        entry["id"] = str(tc_id)

    tc_type = _field(delta, "type")
    if tc_type:
        entry["type"] = str(tc_type)

    function = _field(delta, "function")
    if function is None:
        return

    tc_name = _field(function, "name")
    if tc_name:
        if entry["function"]["name"]:
            entry["function"]["name"] += str(tc_name)
        else:
            entry["function"]["name"] = str(tc_name)

    tc_args = _field(function, "arguments")
    if tc_args:
        entry["function"]["arguments"] += str(tc_args)

def _usage_values(usage: Any) -> dict[str, Any]:
    return {key: _field(usage, key) for key in _USAGE_KEYS}


def _reported_usage(values: dict[str, Any]) -> dict[str, int] | None:
    counts = {key: value for key, value in values.items() if type(value) is int and value >= 0}
    return counts or None


def _coerce_usage(values: dict[str, Any]) -> dict[str, int] | None:
    counts: dict[str, int] = {}
    for key, value in values.items():
        if value is not None:
            try:
                counts[key] = int(value)
            except (TypeError, ValueError):
                continue
    return counts or None


def _decode_tool_calls(raw_calls: list[Any]) -> list[ModelToolCall]:
    calls = []
    for index, raw in enumerate(raw_calls, start=1):
        function = _field(raw, "function")
        calls.append(ModelToolCall(
            id=str(_field(raw, "id") or f"tool-call-{index}"),
            name=str(_field(function, "name") or ""),
            arguments=str(_field(function, "arguments") or ""),
        ))
    return calls


def decode_reply(response: Any, *, fallback: ReplyFallback = "missing_message") -> ModelReply:
    choice = _first_choice(response)
    message = _field(choice, "message")
    payload = message
    if ((fallback == "missing_message" and message is None)
            or (fallback == "missing_choice" and choice is None)):
        payload = response

    charge_payload = message or response
    charge_content = _extract_text(charge_payload)
    charge_reasoning = _extract_reasoning_text(charge_payload)
    charge_calls = _extract_tool_calls(charge_payload)
    if payload is charge_payload:
        content, reasoning, raw_calls = charge_content, charge_reasoning, charge_calls
    else:
        content = _extract_text(payload)
        reasoning = _extract_reasoning_text(payload)
        raw_calls = _extract_tool_calls(payload)

    finish = _field(choice, "finish_reason")
    if finish is None:
        finish = _field(response, "finish_reason") or _field(response, "status")
    values = _usage_values(_field(response, "usage"))
    return ModelReply(
        content=content,
        reasoning=reasoning,
        finish_reason=str(finish or "stop"),
        tool_calls=_decode_tool_calls(raw_calls),
        charge=ModelCharge(_reported_usage(values), (charge_content, charge_reasoning, charge_calls)),
        _display_usage_values=values,
    )


def reported_completion_usage(response: Any) -> dict[str, int] | None:
    return _reported_usage(_usage_values(getattr(response, "usage", None)))


@dataclass(frozen=True)
class _CompletionToolCalls(Sequence[ModelToolCall]):
    _calls: list[Any]

    def __len__(self) -> int:
        return len(self._calls)

    @overload
    def __getitem__(self, index: int) -> ModelToolCall: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[ModelToolCall]: ...

    def __getitem__(self, index: int | slice) -> ModelToolCall | Sequence[ModelToolCall]:
        if isinstance(index, slice):
            return _CompletionToolCalls(self._calls[index])
        call = self._calls[index]
        return ModelToolCall(call.id, call.function.name, call.function.arguments)


def decode_completion_reply(response: Any, usage: dict[str, int] | None) -> ModelReply:
    choice = response.choices[0]
    message = choice.message
    content = message.content or ""
    reasoning = str(getattr(message, "reasoning_content", None) or "")
    raw_calls = message.tool_calls or []
    return ModelReply(
        content=content,
        reasoning=reasoning,
        finish_reason=choice.finish_reason,
        tool_calls=_CompletionToolCalls(raw_calls),
        charge=ModelCharge(usage, (str(content), reasoning, raw_calls)),
    )


class StreamedModelReply:
    def __init__(self) -> None:
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._finish_reason: str | None = None
        self._usage: dict[str, int] | None = None
        self._calls: dict[int, dict[str, Any]] = {}

    def deltas(self, chunk: Any) -> Iterator[tuple[Literal["reasoning", "content"], str]]:
        payload, finish = _stream_payload(chunk)
        if payload is not None:
            reasoning = _extract_reasoning_text(payload)
            if reasoning:
                self._reasoning.append(reasoning)
                yield "reasoning", reasoning

            text = _extract_text(payload)
            if text:
                self._content.append(text)
                yield "content", text

            for delta in _extract_stream_tool_call_deltas(payload):
                _merge_stream_tool_call_delta(self._calls, delta)

        if finish:
            self._finish_reason = finish
        self._usage = _coerce_usage(_usage_values(_field(chunk, "usage"))) or self._usage

    def partial_charge(self) -> ModelCharge:
        return ModelCharge(
            self._usage,
            ("".join(self._content), "".join(self._reasoning), list(self._calls.values())),
        )

    def finish(self) -> ModelReply:
        raw_calls = []
        for index, entry in sorted(self._calls.items()):
            if not entry["id"]:
                entry["id"] = f"stream-call-{index}"
            if not entry["function"]["name"]:
                entry["function"]["name"] = "unknown_tool"
            raw_calls.append(entry)

        content = "".join(self._content)
        reasoning = "".join(self._reasoning)
        return ModelReply(
            content=content,
            reasoning=reasoning,
            finish_reason=self._finish_reason or "stop",
            tool_calls=_decode_tool_calls(raw_calls),
            charge=ModelCharge(self._usage, (content, reasoning, raw_calls)),
        )
