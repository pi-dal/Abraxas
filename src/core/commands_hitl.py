"""
Human-in-the-Loop (HITL) Command Extensions

Adds execution control commands to Abraxas:
- /yolo - Switch to autonomous execution mode
- /safe - Switch to safe mode (manual approval required)
- /allow - Execute pending tool call
- /deny - Reject pending tool call
- /stop - Hard terminate current execution
"""

from typing import Any


def run_yolo_command(bot: Any) -> str:
    """
    Switch to YOLO mode (autonomous execution).

    All tools will execute without human approval.
    Use with caution - high-risk operations run unintercepted.

    Returns:
        Status message
    """
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return "execution controller not initialized"

    try:
        if hasattr(bot, "set_execution_mode"):
            bot.set_execution_mode("yolo")
        else:
            controller.set_mode("yolo")
    except Exception as exc:
        return f"execution mode change failed: {exc}"
    return (
        "⚡ **YOLO MODE ACTIVE**\n"
        "Autonomous execution enabled. All tools will run without approval.\n"
        "Use `/safe` to return to safe mode."
    )


def run_safe_command(bot: Any) -> str:
    """
    Switch to safe mode (manual approval required).

    High-risk tools (bash, write, python_eval) will be intercepted
    and require `/allow` approval before execution.

    Returns:
        Status message
    """
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return "execution controller not initialized"

    try:
        if hasattr(bot, "set_execution_mode"):
            bot.set_execution_mode("safe")
        else:
            controller.set_mode("safe")
    except Exception as exc:
        return f"execution mode change failed: {exc}"
    return (
        "🛡️ **SAFE MODE ACTIVE**\n"
        "High-risk tools require manual approval.\n"
        "Use `/yolo` for autonomous execution."
    )


def run_allow_command(bot: Any) -> str:
    """
    Execute the pending tool call.

    If a tool call was intercepted in safe mode, this command
    executes it and returns the tool output.

    Returns:
        Status message
    """
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return "execution controller not initialized"

    if not getattr(controller, "pending_tool_call", None):
        return "no pending tool call to approve"

    try:
        if hasattr(bot, "allow_pending_tool"):
            return str(bot.allow_pending_tool())
        return "allow operation unavailable on this bot instance"
    except Exception as exc:
        return f"❌ execution failed: {exc}"


def run_always_allow_command(bot: Any) -> str:
    """
    Execute the pending tool call AND switch to YOLO mode for this session.

    After calling this, all subsequent high-risk tools will run without
    approval for the rest of the conversation.

    Returns:
        Status message (LLM follow-up reply)
    """
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return "execution controller not initialized"

    if not getattr(controller, "pending_tool_call", None):
        return "no pending tool call to approve"

    try:
        if hasattr(bot, "always_allow_pending_tool"):
            return str(bot.always_allow_pending_tool())
        return "always_allow operation unavailable on this bot instance"
    except Exception as exc:
        return f"❌ execution failed: {exc}"


def run_deny_command(bot: Any) -> str:
    """
    Reject the pending tool call.

    Appends a denial tool message to conversation state and
    returns a local denial status.

    Returns:
        Status message
    """
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return "execution controller not initialized"

    if not getattr(controller, "pending_tool_call", None):
        return "no pending tool call to deny"

    try:
        if hasattr(bot, "deny_pending_tool"):
            return str(bot.deny_pending_tool())
        return "deny operation unavailable on this bot instance"
    except Exception as exc:
        return f"🚫 denial failed: {exc}"


def run_stop_command(bot: Any) -> str:
    """
    Hard terminate the current execution loop.

    This is an emergency brake that:
    1. Clears any pending tool calls
    2. Sets the stop event flag to abort the current loop

    NOTE: Does NOT change the execution mode — use /safe to switch back to safe mode.

    Returns:
        Status message
    """
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return "execution controller not initialized"

    try:
        if hasattr(bot, "stop_execution"):
            return str(bot.stop_execution())
        controller.stop()
        return "Execution stopped. Mode reset to safe."
    except Exception as exc:
        return f"stop failed: {exc}"


def get_execution_status(bot: Any) -> str:
    """
    Get current execution status summary.

    Returns:
        Status string showing mode, pending state, etc.
    """
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return "execution controller: not initialized"

    mode = str(getattr(controller, "mode", "safe")).lower()
    mode_emoji = "⚡" if mode == "yolo" else "🛡️"
    mode_str = mode.upper()

    lines = [
        f"**Execution Mode**: {mode_emoji} {mode_str}",
    ]

    pending = getattr(controller, "pending_tool_call", None)
    if pending is not None:
        lines.append(
            f"\nPending: {pending.tool_name} (tool_call_id={pending.tool_call_id})"
        )

    return "\n".join(lines)
