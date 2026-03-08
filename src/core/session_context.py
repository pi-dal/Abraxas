from __future__ import annotations

from typing import Any


BOUNDARY_EVENTS = {"handoff", "handoff_anchor", "checkpoint_anchor", "new_session", "tape_reset"}
BOUNDARY_MARKERS = ("[handoff_anchor]", "[checkpoint_anchor]", "[new_session]", "[tape_reset]")


def merge_base_messages(base_messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in base_messages:
        if str(message.get("role", "")).strip() != "system":
            continue
        content = message.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        normalized.append({"role": "system", "content": content})

    if len(normalized) <= 1:
        return normalized

    merged = "\n\n".join(item["content"].strip() for item in normalized if item["content"].strip())
    return [{"role": "system", "content": merged}] if merged else []


def boundary_event_name(entry: dict[str, Any]) -> str | None:
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        event = str(metadata.get("event", "")).strip()
        if event in BOUNDARY_EVENTS:
            return event
    content = entry.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    for marker in BOUNDARY_MARKERS:
        if content.startswith(marker):
            return marker.strip("[]")
    return None


def message_from_tape_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    role = str(entry.get("role", "")).strip()
    content = entry.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    metadata = entry.get("metadata")
    boundary = boundary_event_name(entry)
    if role == "system" and not boundary:
        return None

    if role == "assistant":
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if isinstance(metadata, dict) and isinstance(metadata.get("tool_calls"), list):
            message["tool_calls"] = metadata["tool_calls"]
        return message
    if role == "tool":
        message = {"role": "tool", "content": content}
        tool_call_id = entry.get("tool_call_id")
        if tool_call_id:
            message["tool_call_id"] = str(tool_call_id)
        name = entry.get("name")
        if name:
            message["name"] = str(name)
        return message
    if role == "user":
        return {"role": "user", "content": content}
    if boundary:
        return {"role": "assistant", "content": content}
    return None


def find_latest_boundary(entries: list[dict[str, Any]]) -> int:
    boundary_index = -1
    for index, entry in enumerate(entries):
        if boundary_event_name(entry):
            boundary_index = index
    return boundary_index


def build_request_view(
    *,
    base_messages: list[dict[str, Any]],
    tape_entries: list[dict[str, Any]],
    max_recent_entries: int,
    extra_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    combined_base = list(base_messages)
    if extra_messages:
        combined_base.extend(extra_messages)
    base = merge_base_messages(combined_base)
    if not tape_entries:
        return base

    boundary_index = find_latest_boundary(tape_entries)
    scoped = tape_entries[boundary_index:] if boundary_index >= 0 else list(tape_entries)

    if max_recent_entries > 0 and len(scoped) > max_recent_entries:
        if boundary_index >= 0 and scoped:
            boundary_entry = scoped[0]
            remainder = scoped[1:]
            scoped = [boundary_entry] + remainder[-max_recent_entries:]
        else:
            scoped = scoped[-max_recent_entries:]

    assembled = list(base)
    for entry in scoped:
        message = message_from_tape_entry(entry)
        if message is not None:
            assembled.append(message)
    return assembled
