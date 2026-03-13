import json
import re
from typing import Any, Callable

from typing import Protocol
from types import SimpleNamespace

from capabilities.main_model import build_main_model_client

from .memory import create_memory_runtime
from .nous import load_nous_prompt
from .rci_state import RCISessionState
from .session_context import (
    boundary_event_name as session_boundary_event_name,
    build_request_view,
    message_from_tape_entry as session_message_from_tape_entry,
)
from .settings import (
    DEFAULT_AUTO_COMPACT_KEEP_LAST_MESSAGES,
    DEFAULT_AUTO_COMPACT_MAX_TOKENS,
    DEFAULT_CHECKPOINT_RECENT_ENTRIES,
    DEFAULT_CHECKPOINT_TOKEN_THRESHOLD,
    DEFAULT_CONTEXT_RECENT_ENTRIES,
    DEFAULT_NOUS_PATH,
    DEFAULT_SKILLS_DIR,
    load_runtime_settings,
)
from .skills import load_skills_prompt
from .tools import create_default_registry, tool_label
from .bot_hitl import ExecutionStoppedError, inject_execution_controller
from . import tool_protocol


class ToolRuntime(Protocol):
    def tool_specs(self) -> list[dict]:
        ...

    def call(self, name: str, arguments: str) -> str:
        ...

SYSTEM_PROMPT = (
    "You are Abraxas, a coding bot. Be concise. "
    "Treat src/core and src/channel as protected runtime code and do not edit them unless the user explicitly asks. "
    "Treat src/capabilities as the reusable runtime capability layer that sits beside the agent core. "
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

EXECUTION_PROTOCOL = (
    "\n\n"
    "## CRITICAL EXECUTION RULE\n\n"
    "Before utilizing high-risk tools, you MUST immediately HALT tool execution. "
    "Instead, output a normal text message containing:\n"
    "1. [PLAN]: What you are going to do.\n"
    "2. [CRITIQUE]: Why it might fail or what edge cases you considered.\n\n"
    "Only after emitting this text to the user, are you allowed to use execution tools in the subsequent turn. "
    "Do not embed [PLAN] inside tool arguments.\n\n"
    "High-risk tools include:\n"
    "- bash: for shell execution\n"
    "- write: for file modification >= 20 lines or batch writes >= 3 files\n"
    "- Any tool modifying src/core or src/channel\n\n"
    "This rule can be overridden only by explicit user instruction."
)

THINKING_BLOCK_RE = re.compile(
    r"<(?P<tag>think|thinking|thought)\b[^>]*>[\s\S]*?</(?P=tag)>",
    re.IGNORECASE,
)
THINKING_OPEN_TAG_RE = re.compile(
    r"<(?P<tag>think|thinking|thought)\b[^>]*>",
    re.IGNORECASE,
)
BOUNDARY_EVENTS = {"handoff", "handoff_anchor", "checkpoint_anchor", "new_session", "tape_reset"}

def build_system_prompt(
    skills_dir: str | None = None,
    nous_path: str | None = None,
    *,
    settings: dict[str, str | int | None] | None = None,
    rci_state: "RCISessionState | None" = None,
) -> str:
    runtime_settings = settings or load_runtime_settings()
    resolved_skills_dir = skills_dir or str(runtime_settings.get("skills_dir", DEFAULT_SKILLS_DIR))
    resolved_nous_path = nous_path or str(runtime_settings.get("nous_path", DEFAULT_NOUS_PATH))
    nous_prompt = load_nous_prompt(resolved_nous_path)
    skills_prompt = load_skills_prompt(resolved_skills_dir)
    sections = [SYSTEM_PROMPT]
    if nous_prompt:
        sections.append(nous_prompt)
    if skills_prompt:
        sections.append(skills_prompt)
    sections.append(EXECUTION_PROTOCOL)
    if rci_state and rci_state.is_strict_mode_active():
        sections.append(
            "⚡ [RCI_STRICT] ACTIVE: HALT auto-execution. MUST output text [PLAN], [CRITIQUE] before ANY tool call."
        )
    return "\n\n".join(sections)


@inject_execution_controller
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
        session_id: str | None = None,
        tool_registry: ToolRuntime | None = None,
    ):
        config = load_runtime_settings()
        self.client, default_model = build_main_model_client(config)
        self.model = model or default_model
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
        self.checkpoint_token_threshold = max(
            int(config.get("checkpoint_token_threshold", DEFAULT_CHECKPOINT_TOKEN_THRESHOLD) or 0),
            0,
        )
        self.checkpoint_recent_entries = max(
            int(config.get("checkpoint_recent_entries", DEFAULT_CHECKPOINT_RECENT_ENTRIES) or 0),
            1,
        )
        self.context_recent_entries = max(
            int(config.get("context_recent_entries", DEFAULT_CONTEXT_RECENT_ENTRIES) or 0),
            self.checkpoint_recent_entries,
        )
        self.auto_braindump_enabled = bool(config.get("auto_braindump_enabled", True))
        self.pending_checkpoint_proposal: dict[str, Any] | None = None
        self.rci_state = RCISessionState()
        from .tape import TapeEngine

        if session_id is None:
            session_id = "cli_default"
        self.tape = TapeEngine(session_id=session_id)
        self.tape.append(
            "system",
            f"Session initialized: model={self.model}, session_id={session_id}",
            metadata={"event": "session_start"},
        )

    def refresh_system_prompt(self) -> str:
        prompt = build_system_prompt(rci_state=self.rci_state)
        if not self.messages:
            self.messages = [{"role": "system", "content": prompt}]
            return "system prompt refreshed"
        first = self.messages[0]
        if first.get("role") == "system":
            first["content"] = prompt
            return "system prompt refreshed"
        self.messages.insert(0, {"role": "system", "content": prompt})
        return "system prompt refreshed"

    def enable_strict_rci_mode(self, duration_minutes: int = 30) -> str:
        self.rci_state.enable_strict_mode(duration_minutes)
        self.refresh_system_prompt()
        return f"strict RCI mode enabled for {duration_minutes} minutes"

    def disable_strict_rci_mode(self) -> str:
        self.rci_state.disable_strict_mode()
        self.refresh_system_prompt()
        return "strict RCI mode disabled"

    def get_rci_status(self) -> str:
        return self.rci_state.get_status_summary()

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

    @staticmethod
    def _memory_layer_message(key: str, value: str) -> dict[str, str] | None:
        content = value.strip()
        if not content:
            return None
        tag_map = {
            "memory_brief": "[memory_brief]",
            "mission_memory": "[mission_memory]",
            "memory_query": "[memory_context]",
        }
        tag = tag_map.get(key)
        if not tag:
            return None
        return {"role": "system", "content": f"{tag}\n{content}"}

    def _load_memory_layer_messages(
        self,
        *,
        query_text: str = "",
        include_base: bool = True,
        include_query: bool = False,
    ) -> list[dict[str, str]]:
        runtime = getattr(self, "memory_runtime", None)
        if runtime is None:
            return []
        layers: dict[str, str] = {}
        try:
            if hasattr(runtime, "load_memory_layers"):
                layers = runtime.load_memory_layers(query_text if include_query else "")
            else:
                layers = {
                    "memory_brief": runtime.load_memory_brief() if hasattr(runtime, "load_memory_brief") else "",
                    "mission_memory": runtime.load_mission_memory() if hasattr(runtime, "load_mission_memory") else "",
                    "memory_query": runtime.query(query_text) if include_query and query_text and hasattr(runtime, "query") else "",
                }
        except Exception:
            return []

        messages: list[dict[str, str]] = []
        if include_base:
            for key in ("memory_brief", "mission_memory"):
                message = self._memory_layer_message(key, str(layers.get(key, "")))
                if message is not None:
                    messages.append(message)
        if include_query:
            message = self._memory_layer_message("memory_query", str(layers.get("memory_query", "")))
            if message is not None:
                messages.append(message)
        return messages

    def _base_prompt_messages(self, include_memory_layers: bool = True) -> list[dict[str, str]]:
        base: list[dict[str, str]] = []
        for message in self.messages:
            if str(message.get("role", "")).strip() != "system":
                continue
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if content.startswith("[memory_context]") or content.startswith("[memory_brief]") or content.startswith("[mission_memory]"):
                continue
            base.append({"role": "system", "content": content})
        if include_memory_layers:
            base.extend(self._load_memory_layer_messages(include_base=True, include_query=False))
        return base

    @staticmethod
    def _boundary_event_name(entry: dict[str, Any]) -> str | None:
        return session_boundary_event_name(entry)

    @classmethod
    def _message_from_tape_entry(cls, entry: dict[str, Any]) -> dict[str, Any] | None:
        return session_message_from_tape_entry(entry)

    def _build_context_from_tape(
        self,
        max_recent_entries: int | None = None,
        extra_messages: list[dict[str, Any]] | None = None,
        include_memory_layers: bool = True,
    ) -> list[dict[str, Any]]:
        if max_recent_entries is None:
            max_recent_entries = int(
                getattr(
                    self,
                    "context_recent_entries",
                    getattr(self, "checkpoint_recent_entries", DEFAULT_CONTEXT_RECENT_ENTRIES),
                )
            )
        base = self._base_prompt_messages(include_memory_layers=include_memory_layers)
        tape = getattr(self, "tape", None)
        if tape is None or not hasattr(tape, "read_entries"):
            return base + self._sanitize_recent_messages(self.messages[1:])
        return build_request_view(
            base_messages=base,
            tape_entries=tape.read_entries(),
            max_recent_entries=max_recent_entries,
            extra_messages=extra_messages,
        )

    def _rebuild_working_set_from_tape(self, max_recent_entries: int | None = None) -> int:
        rebuilt = self._build_context_from_tape(
            max_recent_entries=max_recent_entries,
            include_memory_layers=False,
        )
        self.messages = rebuilt if rebuilt else self._base_prompt_messages(include_memory_layers=False)
        return len(self.messages)

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
        self.pending_checkpoint_proposal = None
        if hasattr(self, "tape"):
            self.tape.append(
                "system",
                "[new_session]\nStarted a fresh working set.",
                metadata={"event": "new_session"},
            )
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
    def _sanitize_visible_assistant_content(content: Any) -> str:
        text = content if isinstance(content, str) else str(content or "")
        if not text:
            return ""
        cleaned = THINKING_BLOCK_RE.sub("", text)
        dangling = list(THINKING_OPEN_TAG_RE.finditer(cleaned))
        if dangling:
            cleaned = cleaned[: dangling[-1].start()]
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _normalize_tool_call(tool_call: Any) -> dict[str, Any]:
        return tool_protocol.normalize_tool_call(tool_call)

    @staticmethod
    def _prepare_messages_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return tool_protocol.render_messages_for_api(
            messages,
            normalize_tool_call=tool_protocol.normalize_tool_call,
        )

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

    def _should_offer_checkpoint_proposal(self) -> bool:
        if getattr(self, "pending_checkpoint_proposal", None) is not None:
            return False
        threshold = int(getattr(self, "checkpoint_token_threshold", 0) or 0)
        if threshold <= 0:
            return False
        controller = getattr(self, "_execution_controller", None)
        if controller is not None and getattr(controller, "pending_tool_call", None) is not None:
            return False
        context_messages = self._build_context_from_tape()
        estimated = max(
            self._estimate_message_tokens(context_messages),
            self._estimate_message_tokens(self.messages),
        )
        if estimated < threshold:
            return False
        non_system_messages = [msg for msg in self.messages if msg.get("role") != "system"]
        return len(non_system_messages) >= 2

    def _request_checkpoint_proposal(self) -> dict[str, Any] | None:
        if not hasattr(self, "client") or not hasattr(self, "model"):
            return None
        transcript = "\n".join(
            self._stringify_message(message)
            for message in self._prepare_messages_for_api(self._build_context_from_tape())[1:]
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You decide whether the current coding phase should pause for a user-approved "
                            "checkpoint. Reply with strict JSON containing: should_propose, reason, goal, "
                            "summary, user_message."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Review this working set and decide whether to suggest a checkpoint.\n\n"
                            f"{transcript}"
                        ),
                    },
                ],
                temperature=0.0,
            )
            proposal = json.loads(response.choices[0].message.content or "")
        except Exception:
            return None
        if not isinstance(proposal, dict) or not proposal.get("should_propose"):
            return None
        proposal.setdefault(
            "user_message",
            "I think this phase is ready to be anchored. Do you want me to checkpoint it and continue?",
        )
        return proposal

    @staticmethod
    def _format_checkpoint_notice(proposal: dict[str, Any]) -> str:
        user_message = str(proposal.get("user_message", "")).strip()
        if not user_message:
            user_message = "I think this phase is ready to be anchored. Do you want me to checkpoint it and continue?"
        return (
            f"{user_message}\n"
            "Reply with /checkpoint yes to accept, /checkpoint no to dismiss, or /checkpoint show to inspect it again."
        )

    @staticmethod
    def _normalize_next_steps(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [text for item in value if (text := str(item).strip())]

    @classmethod
    def _build_anchor_metadata(
        cls,
        *,
        event: str,
        summary: str,
        goal: str,
        tags: list[str] | None = None,
        next_steps: list[str] | None = None,
        proposal: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "event": event,
            "summary": summary.strip(),
            "goal": goal.strip(),
            "tags": list(tags or []),
            "next_steps": cls._normalize_next_steps(next_steps or []),
        }
        if proposal is not None:
            metadata["proposal"] = proposal
        return metadata

    def show_checkpoint_proposal(self) -> str:
        proposal = getattr(self, "pending_checkpoint_proposal", None)
        if not proposal:
            return "no checkpoint proposal pending"
        return self._format_checkpoint_notice(proposal)

    def approve_checkpoint_proposal(self) -> str:
        proposal = getattr(self, "pending_checkpoint_proposal", None)
        if not proposal:
            return "no checkpoint proposal pending"
        goal = str(proposal.get("goal", "")).strip() or "Current phase"
        summary = str(proposal.get("summary", "")).strip() or "Checkpoint approved."
        anchor_content = (
            "[checkpoint_anchor]\n"
            f"Goal: {goal}\n"
            f"Summary: {summary}"
        )
        config = load_runtime_settings()
        self.messages = [{"role": "system", "content": build_system_prompt(settings=config, rci_state=self.rci_state)}]
        self.messages.append({"role": "assistant", "content": anchor_content})
        self.pending_checkpoint_proposal = None
        if hasattr(self, "tape"):
            self.tape.append(
                "system",
                anchor_content,
                metadata=self._build_anchor_metadata(
                    event="checkpoint_anchor",
                    summary=summary,
                    goal=goal,
                    next_steps=proposal.get("next_steps", []),
                    proposal=proposal,
                ),
            )
        return f"checkpoint approved.\nAnchor: {summary}"

    def reject_checkpoint_proposal(self) -> str:
        if getattr(self, "pending_checkpoint_proposal", None) is None:
            return "no checkpoint proposal pending"
        self.pending_checkpoint_proposal = None
        return "checkpoint dismissed"

    def list_tape_anchors(self) -> list[dict[str, str]]:
        tape = getattr(self, "tape", None)
        if tape is None or not hasattr(tape, "read_entries"):
            return []
        anchors: list[dict[str, str]] = []
        for entry in tape.read_entries():
            event = self._boundary_event_name(entry)
            if not event:
                continue
            metadata = entry.get("metadata")
            summary = ""
            if isinstance(metadata, dict):
                summary = str(metadata.get("summary", "") or metadata.get("reason", "")).strip()
            content = entry.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if not summary:
                parts = [line.strip() for line in content.splitlines() if line.strip()]
                summary = parts[1] if len(parts) > 1 else content.strip()
            anchors.append(
                {"ts": str(entry.get("ts", "")), "event": event, "summary": summary}
            )
        return anchors

    def reset_tape(self, archive: bool = False) -> str:
        tape = getattr(self, "tape", None)
        if tape is None:
            return "tape engine not initialized"
        self.pending_checkpoint_proposal = None
        tape.append(
            "system",
            "[tape_reset]\nWorking set reset requested.",
            metadata={"event": "tape_reset", "archive": bool(archive)},
        )
        archived_path = None
        if archive and hasattr(tape, "archive_current"):
            archived_path = tape.archive_current()
        self.start_new_session()
        if archived_path:
            return f"tape reset: archived old tape to {archived_path} and started a fresh working set."
        return "tape reset: started a fresh working set."

    def handoff_session(self, summary: str, tags: list | None = None) -> str:
        summary_clean = summary.strip()
        if not summary_clean:
            return "handoff error: summary cannot be empty"
        tags = tags or []
        self.pending_checkpoint_proposal = None
        runtime = getattr(self, "memory_runtime", None)
        if runtime is not None:
            try:
                milestone_entry = f"[handoff] Stage completed: {summary_clean}"
                runtime.record_mission_log(milestone_entry)
                if hasattr(runtime, "append_daily_entry"):
                    runtime.append_daily_entry(f"[handoff] {summary_clean}", section="Handoff")
                if hasattr(runtime, "refresh_index"):
                    runtime.refresh_index()
            except Exception:
                pass
        anchor_content = f"[handoff_anchor]\nPrevious stage: {summary_clean}\nTags: {', '.join(tags) if tags else 'none'}"
        if self.messages and self.messages[0].get("role") == "system":
            self.messages = [self.messages[0]]
        else:
            self.messages = []
        self.messages.append({"role": "assistant", "content": anchor_content})
        if hasattr(self, "tape"):
            self.tape.append(
                "system",
                anchor_content,
                metadata=self._build_anchor_metadata(
                    event="handoff_anchor",
                    summary=summary_clean,
                    goal=summary_clean,
                    tags=[str(tag).strip() for tag in tags if str(tag).strip()],
                ),
            )
        return f"session handoff complete.\nAnchor: {summary_clean}"

    def compact_or_handoff(self, summary: str, tags: list | None = None, keep_last_messages: int = 4) -> str:
        summary_clean = summary.strip()
        if not summary_clean:
            return "handoff error: summary cannot be empty"
        if keep_last_messages <= 0:
            keep_last_messages = 1
        conversation = self.messages[1:]
        recent_messages = conversation[-keep_last_messages:]
        sanitized_recent = self._sanitize_recent_messages(recent_messages)
        anchor_content = f"[handoff_anchor]\nPrevious stage: {summary_clean}\nRecent context preserved ({len(sanitized_recent)} message(s))."
        self.messages = [self.messages[0], {"role": "assistant", "content": anchor_content}] + sanitized_recent
        if hasattr(self, "tape"):
            self.tape.append(
                "system",
                anchor_content,
                metadata=self._build_anchor_metadata(
                    event="handoff_anchor",
                    summary=summary_clean,
                    goal=summary_clean,
                    tags=[str(tag).strip() for tag in (tags or []) if str(tag).strip()],
                ),
            )
        return f"compact or handoff complete.\nAnchor: {summary_clean}"

    def ask(
        self,
        user_text: str,
        on_tool=None,
        on_tool_result: Callable[[str, str, str], None] | None = None,
        on_partial_response: Callable[[str], None] | None = None,
        user_content: Any | None = None,
    ) -> str:
        controller = getattr(self, "_execution_controller", None)
        if controller is not None and hasattr(controller, "begin_run"):
            controller.begin_run()
        try:
            self._auto_capture_braindump_if_needed(user_text)
            self._auto_compact_if_needed(user_text)
            self.pending_checkpoint_proposal = None
            extra_messages = self._load_memory_layer_messages(
                query_text=user_text,
                include_base=False,
                include_query=True,
            )
            message_content = user_content if user_content is not None else user_text
            self.messages.append({"role": "user", "content": message_content})
            if hasattr(self, "tape"):
                self.tape.append("user", user_text)

            overflow_retried = False
            illegal_retried = False
            while True:
                if controller is not None and hasattr(controller, "check_stop_signal"):
                    controller.check_stop_signal()
                try:
                    request_messages = self._build_context_from_tape(extra_messages=extra_messages)
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=self._prepare_messages_for_api(request_messages),
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
                content = str(content)
                if on_partial_response is not None and content:
                    on_partial_response(content)
                entry = {"role": "assistant", "content": content}
                if tool_calls:
                    entry["tool_calls"] = [self._normalize_tool_call(tool_call) for tool_call in tool_calls]
                self.messages.append(entry)

                if not tool_calls:
                    final_content = content or ""
                    if self._should_offer_checkpoint_proposal():
                        proposal = self._request_checkpoint_proposal()
                        if proposal is not None:
                            self.pending_checkpoint_proposal = proposal
                            notice = self._format_checkpoint_notice(proposal)
                            final_content = f"{final_content}\n\n[checkpoint_suggestion]\n{notice}".strip()
                            self.messages[-1]["content"] = final_content
                    if hasattr(self, "tape"):
                        self.tape.append("assistant", final_content)
                    return final_content

                if hasattr(self, "tape"):
                    self.tape.append(
                        "assistant",
                        content or "",
                        metadata={"tool_calls": entry.get("tool_calls", [])},
                    )

                intercepted = False
                for tool_call in tool_calls:
                    function_obj = getattr(tool_call, "function", None)
                    tool_name = str(getattr(function_obj, "name", "")).strip()
                    tool_arguments = getattr(function_obj, "arguments", "{}")
                    if not isinstance(tool_arguments, str):
                        try:
                            tool_arguments = json.dumps(tool_arguments, ensure_ascii=True)
                        except Exception:
                            tool_arguments = "{}"
                    if controller is not None and controller.should_intercept(tool_name, tool_arguments):
                        controller.intercept_and_prompt(tool_call)
                        intercepted = True
                        break

                if intercepted:
                    pending = controller.pending_tool_call if controller is not None else None
                    skipped = tool_protocol.build_skipped_results_for_intercepted_batch(
                        list(tool_calls),
                        intercepted_tool_call_id=pending.tool_call_id if pending is not None else "",
                    )
                    self.messages.extend(skipped)
                    return tool_protocol.format_intercepted_message(pending)

                for tool_call in tool_calls:
                    function_obj = getattr(tool_call, "function", None)
                    tool_name = str(getattr(function_obj, "name", "")).strip()
                    tool_arguments = getattr(function_obj, "arguments", "")
                    if on_tool:
                        on_tool(tool_label(tool_name, tool_arguments))
                    tool_output = str(self.tool_registry.call(tool_name, tool_arguments))
                    if on_tool_result:
                        on_tool_result(tool_name, tool_arguments, tool_output)
                    tool_call_id = str(getattr(tool_call, "id", ""))
                    self.messages.append(
                        tool_protocol.build_tool_result_message(tool_call_id, tool_name, tool_output)
                    )
                    if hasattr(self, "tape"):
                        self.tape.append("tool", tool_output, name=tool_name, tool_call_id=tool_call_id)
        except ExecutionStoppedError:
            if controller is not None and hasattr(controller, "reset_stop_signal"):
                controller.reset_stop_signal()
            mode = controller.mode if controller is not None else "safe"
            return f"Execution stopped by user. (mode remains: {mode})"
        finally:
            if controller is not None and hasattr(controller, "end_run"):
                controller.end_run()
