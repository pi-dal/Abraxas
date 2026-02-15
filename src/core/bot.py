import json
from typing import Any, Callable

from openai import OpenAI
from typing import Protocol

from .memory import create_memory_runtime
from .nous import load_nous_prompt
from .settings import (
    DEFAULT_AUTO_COMPACT_KEEP_LAST_MESSAGES,
    DEFAULT_AUTO_COMPACT_MAX_TOKENS,
    DEFAULT_NOUS_PATH,
    DEFAULT_SKILLS_DIR,
    load_runtime_settings,
)
from .skills import load_skills_prompt
from .tools import create_default_registry, tool_label


class ToolRuntime(Protocol):
    def tool_specs(self) -> list[dict]:
        ...

    def call(self, name: str, arguments: str) -> str:
        ...

SYSTEM_PROMPT = (
    "You are Abraxas, a coding bot. Be concise. "
    "Treat src/core and src/channel as protected runtime code and do not edit them unless the user explicitly asks. "
    "Prefer applying skills in src/skills first. "
    "Treat src/memory as a first-class memory layer, parallel to skills. "
    "If skills cannot solve the task, extend behavior via plugins in src/plugins. "
    "You may also follow optional skill instructions loaded from src/skills. "
    "Plugin contract: create a module in src/plugins that defines register(registry) and registers ToolPlugin from core.tools. "
    "Plugins must fail safely and return readable error text instead of crashing runtime. "
    "Distinguish tool source by tag: descriptions starting with [builtin] are built-in runtime tools; descriptions starting with [plugin] are external plugin tools. "
    "When reasoning about capabilities, treat [builtin] and [plugin] as separate groups. "
    "Telegram configuration can be extended through plugin tools that manage TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_CHAT_IDS in .env. "
    "Image delivery is handled by channel runtime code: tool outputs should be structured metadata (paths/urls), and images are sent outside LLM text. "
    "Do not place base64 image blobs into conversational messages. "
    "Use the bash tool when shell execution is needed. "
    "When task is done, return the final answer in plain text."
)

def build_system_prompt(
    skills_dir: str | None = None,
    nous_path: str | None = None,
    *,
    settings: dict[str, str | int | None] | None = None,
) -> str:
    runtime_settings = settings or load_runtime_settings()
    resolved_skills_dir = skills_dir or str(runtime_settings.get("skills_dir", DEFAULT_SKILLS_DIR))
    resolved_nous_path = nous_path or str(runtime_settings.get("nous_path", DEFAULT_NOUS_PATH))
    nous_prompt = load_nous_prompt(resolved_nous_path)
    skills_prompt = load_skills_prompt(resolved_skills_dir)
    if not nous_prompt and not skills_prompt:
        return SYSTEM_PROMPT
    sections = [SYSTEM_PROMPT]
    if nous_prompt:
        sections.append(nous_prompt)
    if skills_prompt:
        sections.append(skills_prompt)
    return "\n\n".join(sections)


class CodingBot:
    AUTO_BRAINDUMP_KEYWORDS = (
        "想法",
        "灵感",
        "记一下",
        "记住这个",
        "待办",
        "todo",
        "idea",
        "note to self",
        "brain dump",
    )

    def __init__(
        self,
        model: str | None = None,
        tool_registry: ToolRuntime | None = None,
    ):
        config = load_runtime_settings()
        self.client = OpenAI(api_key=config["api_key"], base_url=str(config["base_url"]))
        self.model = model or str(config["model"])
        self.tool_registry = tool_registry or create_default_registry()
        self.messages = [{"role": "system", "content": build_system_prompt(settings=config)}]
        self.memory_runtime = create_memory_runtime(settings=config)
        memory_brief = self.memory_runtime.load_memory_brief()
        if memory_brief:
            self.messages.append({"role": "system", "content": f"[memory_brief]\n{memory_brief}"})
        self.auto_compact_max_tokens = int(
            config.get(
                "auto_compact_max_tokens",
                DEFAULT_AUTO_COMPACT_MAX_TOKENS,
            )
        )
        self.auto_compact_keep_last_messages = int(
            config.get(
                "auto_compact_keep_last_messages",
                DEFAULT_AUTO_COMPACT_KEEP_LAST_MESSAGES,
            )
        )
        instructions = config.get("auto_compact_instructions")
        self.auto_compact_instructions = str(instructions) if instructions else None
        self.auto_braindump_enabled = bool(config.get("auto_braindump_enabled", True))

    def refresh_system_prompt(self) -> str:
        prompt = build_system_prompt()
        if not self.messages:
            self.messages = [{"role": "system", "content": prompt}]
            return "system prompt refreshed"
        first = self.messages[0]
        if first.get("role") == "system":
            first["content"] = prompt
            return "system prompt refreshed"
        self.messages.insert(0, {"role": "system", "content": prompt})
        return "system prompt refreshed"

    @staticmethod
    def _sanitize_recent_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        sanitized: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role", ""))
            if role == "tool":
                continue
            if role not in {"system", "user", "assistant"}:
                continue

            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if role == "assistant" and message.get("tool_calls") and not content.strip():
                continue
            sanitized.append({"role": role, "content": content})
        return sanitized

    @staticmethod
    def _stringify_message(message: dict[str, Any]) -> str:
        role = str(message.get("role", "unknown"))
        content = message.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            content = "(no content)"
        return f"{role}: {content}"

    def _fallback_compaction_summary(
        self,
        older_messages: list[dict[str, Any]],
        instructions: str | None = None,
    ) -> str:
        lines: list[str] = []
        if instructions:
            lines.append(f"compaction instructions: {instructions}")
        lines.append("history summary:")
        window = older_messages[-10:]
        for message in window:
            excerpt = self._stringify_message(message)
            if len(excerpt) > 280:
                excerpt = excerpt[:277] + "..."
            lines.append(f"- {excerpt}")
        if len(older_messages) > len(window):
            lines.append(f"- ... ({len(older_messages) - len(window)} earlier message(s) omitted)")
        return "\n".join(lines)

    def _llm_compaction_summary(
        self,
        older_messages: list[dict[str, Any]],
        instructions: str | None = None,
    ) -> str:
        fallback = self._fallback_compaction_summary(older_messages, instructions)
        if not hasattr(self, "client") or not hasattr(self, "model"):
            return fallback

        transcript = "\n".join(self._stringify_message(message) for message in older_messages)
        compact_instruction = instructions or (
            "Focus on goals, decisions, constraints, open questions, and next steps."
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You compact coding conversations for long-running sessions. "
                            "Output concise markdown with sections: Goals, Decisions, Constraints, "
                            "Open Questions, Next Steps. Keep only durable information."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Compaction instructions: {compact_instruction}\n\n"
                            f"Conversation transcript:\n{transcript}"
                        ),
                    },
                ],
                temperature=0.0,
            )
            content = response.choices[0].message.content or ""
            summary = content.strip()
            return summary or fallback
        except Exception:
            return fallback

    def remember(self, note: str, tags: list[str] | None = None) -> str:
        runtime = getattr(self, "memory_runtime", None)
        if runtime is None:
            return "memory unavailable"
        try:
            result = runtime.append_braindump(note, tags=tags or [])
            if hasattr(runtime, "record_mission_log"):
                runtime.record_mission_log(f"skill/memory note captured: {note[:120]}")
            if hasattr(runtime, "refresh_index"):
                runtime.refresh_index()
            return result
        except Exception as exc:
            return f"memory error: {exc}"

    def flush_memory_snapshot(self, reason: str = "manual", refresh_index: bool = True) -> str:
        runtime = getattr(self, "memory_runtime", None)
        if runtime is None:
            return "memory unavailable"

        conversation = self.messages[1:] if len(self.messages) > 1 else []
        if not conversation:
            return "memory snapshot skipped: empty session"

        summary = self._llm_compaction_summary(
            conversation,
            instructions=f"Create durable memory summary for reason: {reason}",
        )
        result = runtime.record_daily_sync(summary)
        if refresh_index:
            runtime.refresh_index()
        return result

    def compact_session(
        self,
        keep_last_messages: int = 12,
        instructions: str | None = None,
    ) -> str:
        if keep_last_messages <= 0:
            keep_last_messages = 1
        if len(self.messages) <= 1:
            return "session compacted: no conversation messages."

        conversation = self.messages[1:]
        if len(conversation) <= keep_last_messages:
            return "session compacted: nothing to compact."

        keep_count = min(keep_last_messages, len(conversation))
        older_messages = conversation[:-keep_count]
        recent_messages = conversation[-keep_count:]
        summary = self._llm_compaction_summary(older_messages, instructions)
        runtime = getattr(self, "memory_runtime", None)
        if runtime is not None:
            try:
                runtime.record_compaction(summary)
                runtime.refresh_index()
            except Exception:
                pass

        summary_message = {"role": "assistant", "content": f"[compaction_summary]\n{summary}"}
        sanitized_recent = self._sanitize_recent_messages(recent_messages)

        original_len = len(self.messages)
        self.messages = [self.messages[0], summary_message] + sanitized_recent
        removed = original_len - len(self.messages)
        return (
            f"session compacted: removed {removed} message(s), "
            f"kept {len(self.messages) - 2} recent message(s) plus summary."
        )

    def start_new_session(self) -> str:
        config = load_runtime_settings()
        self.messages = [{"role": "system", "content": build_system_prompt(settings=config)}]
        runtime = getattr(self, "memory_runtime", None)
        if runtime is not None:
            try:
                memory_brief = runtime.load_memory_brief()
            except Exception:
                memory_brief = ""
            if memory_brief:
                self.messages.append({"role": "system", "content": f"[memory_brief]\n{memory_brief}"})
        return "new session started."

    def _estimate_message_tokens(self, messages: list[dict[str, Any]]) -> int:
        total_chars = 0
        for message in messages:
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            total_chars += len(content) + 12
            tool_calls = message.get("tool_calls")
            if tool_calls:
                try:
                    total_chars += len(json.dumps(tool_calls, ensure_ascii=True))
                except Exception:
                    total_chars += len(str(tool_calls))
        return max(1, total_chars // 4)

    @staticmethod
    def _normalize_tool_call(tool_call: Any) -> dict[str, Any]:
        fn = getattr(tool_call, "function", None)
        name = str(getattr(fn, "name", "")).strip()
        raw_arguments = getattr(fn, "arguments", "")
        if isinstance(raw_arguments, str):
            arguments = raw_arguments
        else:
            try:
                arguments = json.dumps(raw_arguments, ensure_ascii=True)
            except Exception:
                arguments = "{}"
        return {
            "id": str(getattr(tool_call, "id", "")).strip(),
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            },
        }

    @staticmethod
    def _prepare_messages_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        system_index: int | None = None
        pending_tool_call_ids: set[str] = set()

        for message in messages:
            role = str(message.get("role", "")).strip()
            if role not in {"system", "user", "assistant", "tool"}:
                continue

            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)

            if role == "system":
                pending_tool_call_ids.clear()
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
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    normalized_calls: list[dict[str, Any]] = []
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        fn = call.get("function")
                        if not isinstance(fn, dict):
                            continue
                        call_id = str(call.get("id", "")).strip()
                        fn_name = str(fn.get("name", "")).strip()
                        fn_args = fn.get("arguments", "")
                        if not isinstance(fn_args, str):
                            try:
                                fn_args = json.dumps(fn_args, ensure_ascii=True)
                            except Exception:
                                fn_args = "{}"
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
                        entry["tool_calls"] = normalized_calls
                        entry["content"] = content if content.strip() else ""
                        pending_tool_call_ids = {
                            str(item.get("id", "")).strip()
                            for item in normalized_calls
                            if str(item.get("id", "")).strip()
                        }
                    else:
                        entry["content"] = content
                        pending_tool_call_ids.clear()
                else:
                    entry["content"] = content
                    pending_tool_call_ids.clear()
            elif role == "tool":
                tool_call_id = str(message.get("tool_call_id", "")).strip()
                if not tool_call_id or tool_call_id not in pending_tool_call_ids:
                    continue
                entry["content"] = content
                entry["tool_call_id"] = tool_call_id
                tool_name = str(message.get("name", "")).strip()
                if tool_name:
                    entry["name"] = tool_name
                pending_tool_call_ids.discard(tool_call_id)
            else:
                entry["content"] = content
                pending_tool_call_ids.clear()

            prepared.append(entry)
        return prepared

    @staticmethod
    def _is_context_overflow_error(exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "maximum context length",
            "input tokens exceeds",
            "context length",
            "too many tokens",
            "\"code\": \"1210\"",
            "'code': '1210'",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_illegal_messages_error(exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "messages parameter is illegal",
            "\"code\": \"1214\"",
            "'code': '1214'",
            "invalid messages",
        )
        return any(marker in text for marker in markers)

    def _recover_from_context_overflow(self) -> str:
        keep_last_messages = max(
            1,
            min(4, int(getattr(self, "auto_compact_keep_last_messages", 2))),
        )
        result = self.compact_session(
            keep_last_messages=keep_last_messages,
            instructions=(
                "Emergency compact due to context overflow. Keep current user intent, "
                "constraints, and next actions. Drop large payloads and base64/tool blobs."
            ),
        )
        if "nothing to compact" in result or "no conversation messages" in result:
            conversation = self.messages[1:] if len(self.messages) > 1 else []
            if conversation:
                recent = self._sanitize_recent_messages(conversation[-2:])
                self.messages = [self.messages[0]] + recent
                return "session compacted: emergency trim for context overflow."
        return result

    def _recover_from_illegal_messages(self) -> str:
        repaired = self._prepare_messages_for_api(self.messages)
        if not repaired:
            self.messages = [{"role": "system", "content": build_system_prompt()}]
            return "session repaired: reset to fresh prompt after illegal messages."
        if repaired[0].get("role") != "system":
            repaired.insert(0, {"role": "system", "content": build_system_prompt()})
        if len(repaired) > 64:
            repaired = [repaired[0]] + repaired[-63:]
        self.messages = repaired
        return "session repaired: normalized illegal message history."

    def _auto_compact_if_needed(self, next_user_text: str = "") -> str | None:
        max_tokens = getattr(self, "auto_compact_max_tokens", DEFAULT_AUTO_COMPACT_MAX_TOKENS)
        if max_tokens <= 0:
            return None

        estimated = self._estimate_message_tokens(self.messages)
        if next_user_text:
            estimated += max(1, len(next_user_text) // 4)

        if estimated < max_tokens:
            return None

        keep_last_messages = getattr(
            self,
            "auto_compact_keep_last_messages",
            DEFAULT_AUTO_COMPACT_KEEP_LAST_MESSAGES,
        )
        instructions = getattr(self, "auto_compact_instructions", None)
        return self.compact_session(
            keep_last_messages=keep_last_messages,
            instructions=instructions,
        )

    @staticmethod
    def _should_auto_capture_braindump(user_text: str) -> bool:
        text = user_text.strip()
        if not text:
            return False
        if text.startswith("/"):
            return False
        lowered = text.lower()
        for keyword in CodingBot.AUTO_BRAINDUMP_KEYWORDS:
            if keyword in lowered or keyword in text:
                return True
        return False

    def _auto_capture_braindump_if_needed(self, user_text: str) -> str | None:
        if not getattr(self, "auto_braindump_enabled", True):
            return None
        runtime = getattr(self, "memory_runtime", None)
        if runtime is None:
            return None
        if not self._should_auto_capture_braindump(user_text):
            return None
        try:
            result = runtime.append_braindump(user_text, tags=["auto", "conversation"])
            if hasattr(runtime, "record_mission_log"):
                runtime.record_mission_log(f"auto memory capture: {user_text[:120]}")
            if hasattr(runtime, "refresh_index"):
                runtime.refresh_index()
            return result
        except Exception:
            return None

    def ask(
        self,
        user_text: str,
        on_tool=None,
        on_tool_result: Callable[[str, str, str], None] | None = None,
    ) -> str:
        self._auto_capture_braindump_if_needed(user_text)
        self._auto_compact_if_needed(user_text)
        runtime = getattr(self, "memory_runtime", None)
        if runtime is not None:
            try:
                memory_context = runtime.query(user_text)
                if memory_context:
                    self.messages.append(
                        {"role": "system", "content": f"[memory_context]\n{memory_context}"}
                    )
            except Exception:
                pass
        self.messages.append({"role": "user", "content": user_text})
        overflow_retried = False
        illegal_retried = False
        while True:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self._prepare_messages_for_api(self.messages),
                    tools=self.tool_registry.tool_specs(),
                    tool_choice="auto",
                    temperature=0.2,
                )
            except Exception as exc:
                if not overflow_retried and self._is_context_overflow_error(exc):
                    self._recover_from_context_overflow()
                    overflow_retried = True
                    continue
                if not illegal_retried and self._is_illegal_messages_error(exc):
                    self._recover_from_illegal_messages()
                    illegal_retried = True
                    continue
                raise
            message = response.choices[0].message
            tool_calls = message.tool_calls or []
            content = message.content
            if content is None:
                content = ""
            entry = {"role": "assistant", "content": content}
            if tool_calls:
                entry["tool_calls"] = [self._normalize_tool_call(tool_call) for tool_call in tool_calls]
            self.messages.append(entry)
            if not tool_calls:
                return message.content or ""
            for tool_call in tool_calls:
                function_obj = getattr(tool_call, "function", None)
                tool_name = str(getattr(function_obj, "name", "")).strip()
                tool_arguments = getattr(function_obj, "arguments", "")
                if on_tool:
                    on_tool(tool_label(tool_name, tool_arguments))
                tool_output = str(
                    self.tool_registry.call(
                        tool_name,
                        tool_arguments,
                    )
                )
                if on_tool_result:
                    on_tool_result(
                        tool_name,
                        tool_arguments,
                        tool_output,
                    )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(getattr(tool_call, "id", "")),
                        "name": tool_name,
                        "content": tool_output,
                    }
                )
