"""
Human-in-the-Loop (HITL) Execution Controller
Provides interactive safety layer for high-risk tool executions.
"""
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any


HIGH_RISK_TOOLS = {"bash", "write", "python_eval", "tmux_manager"}


@dataclass
class PendingToolCall:
    """Represents a tool call awaiting user approval."""
    tool_call_id: str
    tool_name: str
    arguments: str
    parameters: dict[str, Any]
    original_tool_call: Any  # Raw OpenAI tool_call object (dict or SDK object)
    created_at: float = field(default_factory=time.time)  # 创建时间戳，用于 TTL 检查

    @property
    def id(self) -> str:
        """Alias for tool_call_id — used by Telegram inline keyboard callback data."""
        return self.tool_call_id


class ExecutionStoppedError(Exception):
    """Raised when a user stop signal should abort the current execution."""


class ExecutionController:
    """
    Manages execution modes and tool interception.

    Modes:
    - safe: High-risk tools require approval
    - yolo: Autonomous execution (no approval)
    """

    HIGH_RISK_TOOLS = HIGH_RISK_TOOLS
    PENDING_TTL_SECONDS = 600  # 10 分钟过期时间

    def __init__(self):
        self._mode = "safe"  # safe | yolo
        self._pending: PendingToolCall | None = None
        self._stop_event = threading.Event()
        self._active_runs = 0
        self._active_runs_lock = threading.Lock()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Set execution mode ('safe' or 'yolo')."""
        if mode not in ("safe", "yolo"):
            raise ValueError(f"Invalid mode: {mode}")
        self._mode = mode

    @property
    def pending_tool_call(self) -> PendingToolCall | None:
        return self._pending

    def stop(self) -> None:
        """Trigger stop signal for current execution.
        
        Clears the pending tool call and sets the stop event, but intentionally
        does NOT reset the execution mode. YOLO mode is a user preference that
        persists across individual executions — only /safe resets it.
        """
        self._stop_event.set()
        self._pending = None

    def reset_stop_signal(self) -> None:
        """Clear stop signal after it has been consumed."""
        self._stop_event.clear()

    def check_stop_signal(self) -> None:
        """Raise if stop signal is set."""
        if self._stop_event.is_set():
            raise ExecutionStoppedError("Execution stopped by user")

    def begin_run(self) -> None:
        """Mark a bot.ask execution as active."""
        with self._active_runs_lock:
            self._active_runs += 1

    def end_run(self) -> None:
        """Mark a bot.ask execution as complete."""
        with self._active_runs_lock:
            if self._active_runs > 0:
                self._active_runs -= 1

    def has_active_run(self) -> bool:
        """Return True when at least one ask call is currently active."""
        with self._active_runs_lock:
            return self._active_runs > 0

    def should_intercept(self, tool_name: str, arguments: str = "{}") -> bool:
        """Check if tool requires interception in current mode."""
        if self._mode == "yolo":
            return False
            
        if tool_name not in self.HIGH_RISK_TOOLS:
            return False
            
        # Safety Heuristic for Bash
        if tool_name == "bash":
            try:
                import json
                import shlex
                import re
                args_dict = json.loads(arguments)
                cmd = args_dict.get("command", "").strip()
                if cmd:
                    # Tokenize the command to check the primary program
                    tokens = shlex.split(cmd)
                    if tokens:
                        base_cmd = tokens[0]
                        safe_commands = {"ls", "cat", "grep", "pwd", "date", "echo", "which", "whereis", "find", "fd", "head", "tail"}
                        
                        # Check explicitly for chained commands or unsafe operators
                        # This is a basic heuristic, advanced users could still potentially bypass
                        has_unsafe_chars = any(c in cmd for c in ["|", ">", "<", "&", ";", "$("])
                        
                        if base_cmd in safe_commands and not has_unsafe_chars:
                            return False # Safe to auto-execute
            except Exception:
                pass # Parse error, fall through to intercept
                
        return True

    @staticmethod
    def _tool_call_field(tool_call: Any, field: str, default: Any = None) -> Any:
        if isinstance(tool_call, dict):
            return tool_call.get(field, default)
        return getattr(tool_call, field, default)

    @classmethod
    def _extract_tool_call_parts(cls, tool_call: Any) -> tuple[str, str, str]:
        function = cls._tool_call_field(tool_call, "function", {})
        if isinstance(function, dict):
            tool_name = str(function.get("name", "")).strip()
            arguments_str = function.get("arguments", "{}")
        else:
            tool_name = str(getattr(function, "name", "")).strip()
            arguments_str = getattr(function, "arguments", "{}")

        if not isinstance(arguments_str, str):
            try:
                import json

                arguments_str = json.dumps(arguments_str, ensure_ascii=True)
            except Exception:
                arguments_str = "{}"

        tool_call_id = str(cls._tool_call_field(tool_call, "id", "")).strip()
        return tool_call_id, tool_name, arguments_str

    def intercept_and_prompt(self, tool_call: Any) -> PendingToolCall:
        """
        Intercept a tool call and create pending approval.

        Returns:
            PendingToolCall object for UI rendering

        The caller is responsible for:
        - Rendering the prompt to user (with buttons if supported)
        - Breaking the execution loop to await user response
        """
        tool_call_id, tool_name, arguments_str = self._extract_tool_call_parts(tool_call)

        try:
            import json
            parameters = json.loads(arguments_str)
        except json.JSONDecodeError:
            parameters = {"raw_arguments": arguments_str}

        self._pending = PendingToolCall(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments_str,
            parameters=parameters,
            original_tool_call=tool_call,
        )

        return self._pending

    def execute_pending(self) -> tuple[str, str]:
        """
        Get pending tool call for execution.

        Returns:
            (tool_name, arguments_json) tuple

        Raises:
            RuntimeError: If no pending call exists
        """
        if not self._pending:
            raise RuntimeError("No pending tool call to execute")

        return self._pending.tool_name, self._pending.arguments

    def get_pending_tool_call_id(self) -> str:
        """
        Get tool_call_id for validation with TTL check.

        Returns:
            The pending tool_call_id

        Raises:
            RuntimeError: If no pending call exists or has expired
        """
        if not self._pending:
            raise RuntimeError("No pending tool call")
        
        # 检查是否过期
        if time.time() - self._pending.created_at > self.PENDING_TTL_SECONDS:
            self._pending = None
            raise RuntimeError("Pending tool call expired")
        
        return self._pending.tool_call_id

    def deny_pending(self) -> str:
        """
        Deny pending tool call.

        Returns:
            tool_call_id for synthetic response generation

        Raises:
            RuntimeError: If no pending call exists
        """
        if not self._pending:
            raise RuntimeError("No pending tool call to deny")

        tool_call_id = self._pending.tool_call_id
        self._pending = None
        return tool_call_id

    def clear_pending(self) -> None:
        """Clear pending tool call state."""
        self._pending = None


def _run_hitl_continuation(bot: Any) -> str:
    """
    Continue the LLM conversation from the current messages state after a HITL decision.

    This is called after allow_pending_tool or deny_pending_tool injects a tool result.
    It does NOT inject a new user message — it drives the while-loop that ask() would
    normally run, including:
      - Handling chained tool calls in the follow-up response
      - Re-checking new tool calls for HITL interception
      - Recording all events to tape

    When no LLM client is available (unit-test stubs), falls back to returning
    the content of the most recent tool message.
    """
    client = getattr(bot, "client", None)
    if client is None:
        # Unit-test stub — return the content of the most recently injected tool message.
        last_tool_content = next(
            (m["content"] for m in reversed(bot.messages) if m.get("role") == "tool"),
            "done",
        )
        return last_tool_content

    controller = getattr(bot, "_execution_controller", None)

    for _ in range(50):  # Safety cap: max 50 continuation rounds
        # Allow /stop to interrupt mid-chain (e.g., after allow triggers further tool calls)
        if controller and hasattr(controller, "check_stop_signal"):
            try:
                controller.check_stop_signal()
            except Exception as exc:
                return f"Execution interrupted: {exc}"
        try:
            response = client.chat.completions.create(
                model=bot.model,
                messages=bot._prepare_messages_for_api(bot.messages),
                tools=bot.tool_registry.tool_specs(),
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as exc:
            return f"AI follow-up failed: {exc}"

        message = response.choices[0].message
        raw_content = message.content or ""
        sanitizer = getattr(bot, "_sanitize_visible_assistant_content", None)
        if callable(sanitizer):
            content = sanitizer(raw_content)
        else:
            content = raw_content
        tool_calls = message.tool_calls or []

        entry: dict = {"role": "assistant", "content": content}
        if tool_calls:
            entry["tool_calls"] = [bot._normalize_tool_call(tc) for tc in tool_calls]
        bot.messages.append(entry)

        if not tool_calls:
            if hasattr(bot, "tape"):
                bot.tape.append("assistant", content)
            return content

        # Tape: record assistant intent with tool calls
        if hasattr(bot, "tape"):
            bot.tape.append("assistant", content, metadata={"tool_calls": entry.get("tool_calls", [])})

        # Check new tool calls for HITL interception
        intercepted = False
        intercepted_id: str | None = None
        for tool_call in tool_calls:
            fn = getattr(tool_call, "function", None)
            tool_name = str(getattr(fn, "name", "")).strip()
            tool_args = getattr(fn, "arguments", "{}")
            if not isinstance(tool_args, str):
                try:
                    tool_args = json.dumps(tool_args, ensure_ascii=True)
                except Exception:
                    tool_args = "{}"

            if controller and controller.should_intercept(tool_name, tool_args):
                controller.intercept_and_prompt(tool_call)
                intercepted = True
                intercepted_id = str(getattr(tool_call, "id", "")).strip()
                break

        if intercepted:
            # Inject synthetic "skipped" responses for all other tool calls in this batch
            # so the message history remains valid for the next API call.
            for tc in tool_calls:
                tc_id = str(getattr(tc, "id", "")).strip()
                if tc_id == intercepted_id:
                    continue
                fn = getattr(tc, "function", None)
                tc_name = str(getattr(fn, "name", "")).strip()
                if tc_id:
                    bot.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": tc_name,
                        "content": "[Skipped: another tool in this batch was intercepted for human approval]",
                    })
            pending = controller.pending_tool_call
            return (
                f"[INTERCEPTED] Tool call requires approval.\n\n"
                f"⚠️ Pending Tool Call: {pending.tool_name}\n"
                f"Parameters: {pending.parameters}"
            )

        # No interception — execute all tool calls in this batch
        for tool_call in tool_calls:
            fn = getattr(tool_call, "function", None)
            tool_name = str(getattr(fn, "name", "")).strip()
            tool_args = getattr(fn, "arguments", "{}")
            if not isinstance(tool_args, str):
                try:
                    tool_args = json.dumps(tool_args, ensure_ascii=True)
                except Exception:
                    tool_args = "{}"
            tool_call_id = str(getattr(tool_call, "id", ""))

            try:
                tool_output = str(bot.tool_registry.call(tool_name, tool_args))
            except Exception as exc:
                tool_output = f"Tool error: {exc}"

            bot.messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": tool_output,
            })
            if hasattr(bot, "tape"):
                bot.tape.append("tool", tool_output, name=tool_name, tool_call_id=tool_call_id)

    return (
        "[NEEDS_ATTENTION] The conversation reached the maximum tool-call chain length. "
        "The agent may have been stuck in a loop. "
        "Please review the recent actions and send a follow-up message to continue."
    )


def inject_execution_controller(cls) -> type:
    """
    Class decorator to inject HITL capabilities into CodingBot.

    This adds:
    - self._execution_controller (ExecutionController)
    - self.allow_pending_tool()
    - self.deny_pending_tool()
    - self.set_execution_mode()
    - self.stop_execution()
    """
    original_init = cls.__init__

    def new_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._execution_controller = ExecutionController()

    cls.__init__ = new_init

    def allow_pending_tool(self) -> str:
        """Execute the pending tool and continue the conversation via _run_hitl_continuation."""
        pending = self._execution_controller.pending_tool_call
        if not pending:
            return "No pending tool call to allow."

        # TTL check — reject if the approval request has been waiting too long
        ttl = self._execution_controller.PENDING_TTL_SECONDS
        if time.time() - pending.created_at > ttl:
            self._execution_controller.clear_pending()
            return "Pending tool call has expired (> 10 min). Please re-run your request."

        tool_name = pending.tool_name
        arguments = pending.arguments
        tool_call_id = pending.tool_call_id

        try:
            tool_output = str(self.tool_registry.call(tool_name, arguments))
        except Exception as exc:
            self._execution_controller.clear_pending()
            return f"Tool execution failed: {exc}"

        # Inject tool result at the correct position.
        # The assistant message with tool_calls was already appended when interception
        # happened, so the message order here is:
        #   assistant(tool_calls=[...]) → tool(result) → [_run_hitl_continuation LLM call]
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": tool_output,
        })

        if hasattr(self, "tape"):
            self.tape.append("tool", tool_output, name=tool_name, tool_call_id=tool_call_id)

        self._execution_controller.clear_pending()

        # Inject an ephemeral guidance message so the LLM summarises tool output
        # instead of verbatim-repeating logs, env vars, etc.
        # Marked [hitl_guidance] so we can find and purge it afterwards.
        _GUIDANCE_PREFIX = "[hitl_guidance]"
        guidance_content = (
            f"{_GUIDANCE_PREFIX} The tool has just executed. "
            "Respond with a concise, user-facing summary. "
            "Do NOT reproduce raw tool output verbatim (env vars, full logs, file listings). "
            "Highlight only what is relevant to the user's original request."
        )
        self.messages.append({"role": "system", "content": guidance_content})

        result = _run_hitl_continuation(self)

        # Purge the ephemeral guidance from history — it was a one-shot instruction
        # and should not persist across future turns.
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "system" and msg.get("content", "").startswith(_GUIDANCE_PREFIX):
                self.messages.pop(i)
                break

        return result

    def deny_pending_tool(self) -> str:
        """Deny the pending tool and continue the conversation via _run_hitl_continuation."""
        # Read pending BEFORE calling deny_pending() which clears _pending.
        pending = self._execution_controller.pending_tool_call
        if not pending:
            return "No pending tool call to deny."

        tool_name = pending.tool_name
        tool_call_id = self._execution_controller.deny_pending()

        denial_content = (
            "[Action denied by human user - tool execution rejected. "
            "Please try an alternative approach or ask for approval.]"
        )
        # Inject synthetic tool response so the message history stays valid:
        #   assistant(tool_calls=[...]) → tool(denial) → [_run_hitl_continuation LLM call]
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,  # consistent with allow_pending_tool()
            "content": denial_content,
        })

        if hasattr(self, "tape"):
            self.tape.append("tool", denial_content, name=tool_name, tool_call_id=tool_call_id)

        # Continue the conversation — LLM will see the denial and respond accordingly.
        return _run_hitl_continuation(self)

    def set_execution_mode(self, mode: str) -> str:
        """Set execution mode ('safe' or 'yolo')."""
        self._execution_controller.set_mode(mode)
        return f"Execution mode set to: {mode}"

    def stop_execution(self) -> str:
        """Stop current execution. Does NOT change the execution mode (YOLO/safe)."""
        was_active = self._execution_controller.has_active_run()
        self._execution_controller.stop()
        mode = self._execution_controller.mode
        if was_active:
            return f"Stop signal sent. Active execution will halt. (mode remains: {mode})"
        return f"Execution stopped. (mode remains: {mode})"

    def always_allow_pending_tool(self) -> str:
        """Execute the pending tool AND switch to YOLO mode for the rest of this session.

        This is the 'Trust once, remember always' shortcut:
        - The current intercepted tool runs immediately.
        - All subsequent tool calls skip approval for the duration of this conversation.
        - Equivalent to: allow + /yolo
        """
        # Switch to YOLO first so _run_hitl_continuation won't intercept future tools.
        self._execution_controller.set_mode("yolo")
        # Delegate to the normal allow flow (executes tool + drives LLM continuation).
        return self.allow_pending_tool()

    cls.allow_pending_tool = allow_pending_tool
    cls.deny_pending_tool = deny_pending_tool
    cls.always_allow_pending_tool = always_allow_pending_tool
    cls.set_execution_mode = set_execution_mode
    cls.stop_execution = stop_execution

    return cls
