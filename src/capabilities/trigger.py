from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from core.bot import CodingBot


@dataclass(frozen=True)
class TriggerRequest:
    text: str
    chat_id: int | None = None
    context: str = ""
    source: str = "external"
    idempotency_key: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class TriggerRunResult:
    session_id: str
    prompt: str
    reply: str


def build_trigger_prompt(request: TriggerRequest) -> str:
    lines = ["[external_trigger]"]
    lines.append(f"source: {str(request.source or '').strip() or 'external'}")
    if request.chat_id is not None:
        lines.append(f"chat_id: {request.chat_id}")
    if request.idempotency_key:
        lines.append(f"idempotency_key: {request.idempotency_key}")
    lines.extend(["", "Task:", str(request.text or "").strip()])
    context = str(request.context or "").strip()
    if context:
        lines.extend(["", "Context:", context])
    return "\n".join(lines).strip()


def resolve_trigger_session_id(request: TriggerRequest) -> str:
    explicit = str(request.session_id or "").strip()
    if explicit:
        return explicit
    if request.chat_id is not None:
        return f"tg_{request.chat_id}"
    return "trigger_default"


def _create_trigger_bot(bot_factory: Callable[..., CodingBot], session_id: str) -> CodingBot:
    try:
        signature = inspect.signature(bot_factory)
    except (TypeError, ValueError):
        signature = None

    if signature is not None:
        parameters = signature.parameters.values()
        if "session_id" in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
        ):
            return bot_factory(session_id=session_id)
        return bot_factory()

    try:
        return bot_factory(session_id=session_id)
    except TypeError:
        return bot_factory()


def _call_bot_ask(bot: CodingBot, text: str) -> str:
    ask = getattr(bot, "ask")
    try:
        signature = inspect.signature(ask)
    except (TypeError, ValueError):
        signature = None

    kwargs: dict[str, Any] = {}
    if signature is not None:
        parameters = signature.parameters.values()
        accepts_var_kw = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)
        if accepts_var_kw or "user_content" in signature.parameters:
            kwargs["user_content"] = text
        return ask(text, **kwargs)

    try:
        return ask(text, user_content=text)
    except TypeError:
        return ask(text)


def run_trigger(
    request: TriggerRequest,
    *,
    bot_factory: Callable[..., CodingBot],
) -> TriggerRunResult:
    prompt = build_trigger_prompt(request)
    session_id = resolve_trigger_session_id(request)
    bot = _create_trigger_bot(bot_factory, session_id)
    reply = str(_call_bot_ask(bot, prompt) or "").strip() or "(empty response)"
    return TriggerRunResult(session_id=session_id, prompt=prompt, reply=reply)
