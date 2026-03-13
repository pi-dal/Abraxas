from __future__ import annotations

import json
from typing import Any, Callable


DENIED_TOOL_RESULT = (
    "[Action denied by human user - tool execution rejected. "
    "Please try an alternative approach or ask for approval.]"
)
SKIPPED_TOOL_RESULT = "[Skipped: another tool in this batch was intercepted for human approval]"


def normalize_tool_call(tool_call: Any) -> dict[str, Any]:
    if isinstance(tool_call, dict):
        tool_call_id = str(tool_call.get("id", "")).strip()
        function = tool_call.get("function", {})
        if isinstance(function, dict):
            tool_name = str(function.get("name", "")).strip()
            raw_arguments = function.get("arguments", "{}")
        else:
            tool_name = str(getattr(function, "name", "")).strip()
            raw_arguments = getattr(function, "arguments", "{}")
    else:
        tool_call_id = str(getattr(tool_call, "id", "")).strip()
        function = getattr(tool_call, "function", None)
        tool_name = str(getattr(function, "name", "")).strip() if function is not None else ""
        raw_arguments = getattr(function, "arguments", "{}") if function is not None else "{}"

    if isinstance(raw_arguments, str):
        arguments = raw_arguments
    else:
        try:
            arguments = json.dumps(raw_arguments, ensure_ascii=True)
        except Exception:
            arguments = "{}"

    return {
        "id": tool_call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": arguments,
        },
    }


def build_tool_result_message(tool_call_id: str, tool_name: str, content: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": str(tool_call_id).strip(),
        "content": str(content),
    }
    name = str(tool_name).strip()
    if name:
        entry["name"] = name
    return entry


def build_skipped_tool_result_message(tool_call_id: str, tool_name: str) -> dict[str, Any]:
    return build_tool_result_message(tool_call_id, tool_name, SKIPPED_TOOL_RESULT)


def tool_call_identity(tool_call: Any) -> tuple[str, str]:
    normalized = normalize_tool_call(tool_call)
    function = normalized.get("function", {})
    tool_name = str(function.get("name", "")).strip() if isinstance(function, dict) else ""
    return str(normalized.get("id", "")).strip(), tool_name


def tool_call_arguments(tool_call: Any) -> str:
    normalized = normalize_tool_call(tool_call)
    function = normalized.get("function", {})
    if isinstance(function, dict):
        arguments = function.get("arguments", "{}")
    else:
        arguments = "{}"
    return arguments if isinstance(arguments, str) else str(arguments)


def format_intercepted_message(pending_tool_call: Any) -> str:
    tool_name = str(getattr(pending_tool_call, "tool_name", "")).strip()
    parameters = getattr(pending_tool_call, "parameters", {})
    return (
        "[INTERCEPTED] Tool call requires approval.\n\n"
        f"⚠️ Pending Tool Call: {tool_name}\n"
        f"Parameters: {parameters}"
    )


def build_skipped_results_for_intercepted_batch(
    tool_calls: list[Any],
    *,
    intercepted_tool_call_id: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    skipped_id = str(intercepted_tool_call_id).strip()
    for tool_call in tool_calls:
        tool_call_id, tool_name = tool_call_identity(tool_call)
        if not tool_call_id or tool_call_id == skipped_id:
            continue
        entries.append(build_skipped_tool_result_message(tool_call_id, tool_name))
    return entries


def render_messages_for_api(
    messages: list[dict[str, Any]],
    *,
    normalize_tool_call: Callable[[Any], dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    system_index: int | None = None
    pending_tool_call_ids: set[str] = set()
    pending_tool_sequence: list[dict[str, Any]] = []

    def flush_incomplete_tool_sequence() -> None:
        nonlocal pending_tool_call_ids, pending_tool_sequence
        if pending_tool_sequence:
            assistant_entry = pending_tool_sequence[0]
            content = str(assistant_entry.get("content", ""))
            if content.strip():
                prepared.append({"role": "assistant", "content": content})
        pending_tool_call_ids.clear()
        pending_tool_sequence = []

    for message in messages:
        role = str(message.get("role", "")).strip()
        if role not in {"system", "user", "assistant", "tool"}:
            continue

        raw_content = message.get("content", "")

        if role == "system":
            content = raw_content if isinstance(raw_content, str) else str(raw_content)
            flush_incomplete_tool_sequence()
            if system_index is None:
                prepared.append({"role": "system", "content": content})
                system_index = len(prepared) - 1
            else:
                merged = str(prepared[system_index].get("content", ""))
                if content.strip():
                    prepared[system_index]["content"] = f"{merged}\n\n{content}" if merged else content
            continue

        entry: dict[str, Any] = {"role": role}

        if role == "assistant":
            content = raw_content if isinstance(raw_content, str) else str(raw_content)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                normalized_calls: list[dict[str, Any]] = []
                for call in tool_calls:
                    normalized = normalize_tool_call(call)
                    call_id = str(normalized.get("id", "")).strip()
                    function = normalized.get("function", {})
                    fn_name = str(function.get("name", "")).strip() if isinstance(function, dict) else ""
                    fn_args = function.get("arguments", "") if isinstance(function, dict) else ""
                    if not isinstance(fn_args, str):
                        fn_args = str(fn_args)
                    if not call_id or not fn_name:
                        continue
                    normalized_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": fn_name, "arguments": fn_args},
                        }
                    )
                if normalized_calls:
                    flush_incomplete_tool_sequence()
                    entry["tool_calls"] = normalized_calls
                    entry["content"] = content if content.strip() else ""
                    pending_tool_sequence = [entry]
                    pending_tool_call_ids = {
                        str(item.get("id", "")).strip()
                        for item in normalized_calls
                        if str(item.get("id", "")).strip()
                    }
                else:
                    entry["content"] = content
                    flush_incomplete_tool_sequence()
                    prepared.append(entry)
            else:
                entry["content"] = content
                flush_incomplete_tool_sequence()
                prepared.append(entry)
        elif role == "tool":
            content = raw_content if isinstance(raw_content, str) else str(raw_content)
            tool_call_id = str(message.get("tool_call_id", "")).strip()
            if not tool_call_id or tool_call_id not in pending_tool_call_ids:
                continue
            entry["content"] = content
            entry["tool_call_id"] = tool_call_id
            tool_name = str(message.get("name", "")).strip()
            if tool_name:
                entry["name"] = tool_name
            pending_tool_sequence.append(entry)
            pending_tool_call_ids.discard(tool_call_id)
            if not pending_tool_call_ids:
                prepared.extend(pending_tool_sequence)
                pending_tool_sequence = []
        else:
            if isinstance(raw_content, (str, list, dict)):
                entry["content"] = raw_content
            else:
                entry["content"] = str(raw_content)
            flush_incomplete_tool_sequence()
            prepared.append(entry)

    flush_incomplete_tool_sequence()
    return prepared
