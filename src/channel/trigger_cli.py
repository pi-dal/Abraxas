import argparse
import sys
from typing import Callable

from capabilities.runtime_auth import has_main_model_auth, main_model_auth_error
from capabilities.trigger import TriggerRequest, run_trigger
from core.bot import CodingBot
from core.settings import load_runtime_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abraxas-trigger",
        description="Inject an external trigger into Abraxas and print the reply.",
    )
    parser.add_argument("--text", required=True, help="Instruction for Abraxas.")
    parser.add_argument("--context", default="", help="Inline context for Abraxas.")
    parser.add_argument("--source", default="external", help="Trigger source label.")
    parser.add_argument("--chat-id", type=int, help="Optional chat id used for prompt metadata.")
    parser.add_argument("--session-id", help="Optional explicit session id.")
    parser.add_argument("--idempotency-key", help="Optional idempotency key metadata.")
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="Kept for interface compatibility; output is always written to stdout.",
    )
    return parser


def _default_bot_factory(session_id: str | None = None) -> CodingBot:
    return CodingBot(session_id=session_id)


def run_trigger_command(
    argv: list[str] | None = None,
    *,
    bot_factory: Callable[..., CodingBot] | None = None,
    settings_loader: Callable[[], dict[str, str | int | None]] = load_runtime_settings,
    stdout_writer: Callable[[str], None] = print,
    stderr_writer: Callable[[str], None] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = settings_loader()
    error_writer = stderr_writer or (lambda message: print(message, file=sys.stderr))

    if not has_main_model_auth(settings):
        error_writer(main_model_auth_error(settings))
        return 2

    resolved_bot_factory = bot_factory or _default_bot_factory
    request = TriggerRequest(
        text=str(args.text or "").strip(),
        chat_id=args.chat_id,
        context=str(args.context or "").strip(),
        source=str(args.source or "").strip() or "external",
        idempotency_key=str(args.idempotency_key or "").strip() or None,
        session_id=str(args.session_id or "").strip() or None,
    )
    result = run_trigger(request, bot_factory=resolved_bot_factory)
    stdout_writer(result.reply)
    return 0


def main() -> None:
    raise SystemExit(run_trigger_command())


if __name__ == "__main__":
    main()
