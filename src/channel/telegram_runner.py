import time
import threading
from typing import Any

from core.bot import CodingBot
from core.registry import ReloadableToolRegistry, create_reloadable_tool_registry
from core.scheduler import DailyScheduler, MultiDailyScheduler, WeeklyScheduler
from core.settings import load_runtime_settings

from .telegram_client import TelegramClient, sync_telegram_commands
from .telegram_handlers import (
    create_chat_session_bot,
    extract_message_payload,
    parse_allowed_chat_ids,
    process_update,
)
from .telegram_scheduler import (
    run_daily_memory_sync,
    run_micro_memory_sync,
    run_weekly_memory_compound,
)


def run_telegram_bot(
    token: str,
    allowed_chat_ids: set[int] | None = None,
    poll_timeout: int = 25,
    idle_sleep: float = 0.2,
    bot_factory=CodingBot,
    tool_registry: ReloadableToolRegistry | None = None,
    sync_commands_on_start: bool = True,
    runtime_settings: dict[str, str | int | None] | None = None,
) -> None:
    settings = runtime_settings or load_runtime_settings()
    client = TelegramClient(token)
    if sync_commands_on_start:
        if sync_telegram_commands(client):
            print("telegram commands synced.")
        else:
            print("warning: telegram commands sync failed.")
    sessions: dict[int, CodingBot] = {}
    daily_scheduler = DailyScheduler(
        time_text=str(settings.get("memory_daily_sync_time", "02:00")),
        tz_name=str(settings.get("memory_tz", "Asia/Shanghai")),
    )
    micro_scheduler = MultiDailyScheduler(
        times_text=str(settings.get("memory_micro_sync_times", "")),
        tz_name=str(settings.get("memory_tz", "Asia/Shanghai")),
    )
    weekly_scheduler = WeeklyScheduler(
        time_text=str(settings.get("memory_weekly_compound_time", "22:00")),
        tz_name=str(settings.get("memory_tz", "Asia/Shanghai")),
        weekday=int(settings.get("memory_weekly_compound_weekday", 6)),
    )
    offset: int | None = None
    in_flight_chat_workers: dict[int, threading.Thread] = {}
    in_flight_lock = threading.Lock()

    while True:
        updates = client.get_updates(offset=offset, timeout=poll_timeout)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            payload = extract_message_payload(update)
            if payload is None:
                # callback_query or other non-message update
                # Extract chat_id for routing
                cb = update.get("callback_query", {})
                cb_msg = cb.get("message", {}) if isinstance(cb, dict) else {}
                cb_chat = cb_msg.get("chat", {}) if isinstance(cb_msg, dict) else {}
                cb_chat_id = cb_chat.get("id") if isinstance(cb_chat, dict) else None

                if isinstance(cb_chat_id, int):
                    # Allow/Deny/Always-Allow all invoke tool execution + LLM continuation
                    # — run in a worker thread to keep polling loop unblocked.
                    with in_flight_lock:
                        active = in_flight_chat_workers.get(cb_chat_id)
                        already_busy = active is not None and active.is_alive()

                    if already_busy:
                        # Don't queue a second HITL decision while bot is mid-execution
                        client.answer_callback_query(
                            str(cb.get("id", "")),
                            text="Bot is busy — wait for current execution to finish.",
                            show_alert=True,
                        ) if isinstance(cb, dict) and cb.get("id") else None
                    else:
                        def _run_callback_update(
                            chat_id_cb: int = cb_chat_id,
                            update_payload: dict[str, Any] = update,
                        ) -> None:
                            try:
                                process_update(
                                    update_payload,
                                    sessions,
                                    client,
                                    bot_factory,
                                    allowed_chat_ids,
                                )
                            finally:
                                with in_flight_lock:
                                    current = in_flight_chat_workers.get(chat_id_cb)
                                    if current is threading.current_thread():
                                        in_flight_chat_workers.pop(chat_id_cb, None)

                        cb_worker = threading.Thread(
                            target=_run_callback_update,
                            daemon=True,
                            name=f"tg-cb-{cb_chat_id}",
                        )
                        with in_flight_lock:
                            in_flight_chat_workers[cb_chat_id] = cb_worker
                        cb_worker.start()
                else:
                    # Non-callback (e.g. channel posts) — process synchronously, no blocking
                    process_update(update, sessions, client, bot_factory, allowed_chat_ids)

            else:
                chat_id, message_id, text = payload
                normalized = text.strip()
                is_command = normalized.startswith("/")
                is_stop = normalized.startswith("/stop")

                if not is_command:
                    with in_flight_lock:
                        active = in_flight_chat_workers.get(chat_id)
                        if active is not None and active.is_alive():
                            client.send_message(
                                chat_id,
                                "execution already in progress. send /stop to interrupt.",
                                reply_to_message_id=message_id,
                            )
                            continue
                        if chat_id not in sessions:
                            sessions[chat_id] = create_chat_session_bot(bot_factory, chat_id)

                        def _run_non_command_update(
                            chat_id_for_cleanup: int = chat_id,
                            update_payload: dict[str, Any] = update,
                        ) -> None:
                            try:
                                process_update(
                                    update_payload,
                                    sessions,
                                    client,
                                    bot_factory,
                                    allowed_chat_ids,
                                )
                            finally:
                                with in_flight_lock:
                                    current = in_flight_chat_workers.get(chat_id_for_cleanup)
                                    if current is threading.current_thread():
                                        in_flight_chat_workers.pop(chat_id_for_cleanup, None)

                        worker = threading.Thread(
                            target=_run_non_command_update,
                            daemon=True,
                            name=f"tg-chat-{chat_id}",
                        )
                        in_flight_chat_workers[chat_id] = worker
                    worker.start()
                elif is_stop:
                    process_update(update, sessions, client, bot_factory, allowed_chat_ids)
                else:
                    with in_flight_lock:
                        active = in_flight_chat_workers.get(chat_id)
                        busy = active is not None and active.is_alive()
                    if busy:
                        client.send_message(
                            chat_id,
                            "execution in progress. use /stop first, then retry this command.",
                            reply_to_message_id=message_id,
                        )
                        continue
                    process_update(update, sessions, client, bot_factory, allowed_chat_ids)
            if tool_registry is not None:
                for plugin_error in tool_registry.drain_errors():
                    print(f"plugin warning: {plugin_error}")
        daily_result: dict[str, Any] | None = None

        def _daily_job() -> None:
            nonlocal daily_result
            daily_result = run_daily_memory_sync(sessions)

        if daily_scheduler.run_if_due(_daily_job):
            print(f"daily memory sync executed: {daily_result}")
            for error in (daily_result or {}).get("errors", []):
                print(f"memory warning: {error}")

        micro_results: list[dict[str, Any]] = []

        def _micro_job(slot_key: str) -> None:
            _ = slot_key
            micro_results.append(run_micro_memory_sync(sessions))

        micro_count = micro_scheduler.run_if_due(_micro_job)
        if micro_count > 0:
            print(f"micro memory sync executed: {micro_count} slot(s)")
            for result in micro_results:
                for error in result.get("errors", []):
                    print(f"memory warning: {error}")

        weekly_result: dict[str, Any] | None = None

        def _weekly_job() -> None:
            nonlocal weekly_result
            weekly_result = run_weekly_memory_compound(sessions)

        if weekly_scheduler.run_if_due(_weekly_job):
            print(f"weekly memory compound executed: {weekly_result}")
            for error in (weekly_result or {}).get("errors", []):
                print(f"memory warning: {error}")

        if not updates:
            time.sleep(idle_sleep)


def main() -> None:
    settings = load_runtime_settings()
    token = settings["telegram_bot_token"]
    if not token:
        print("Missing TELEGRAM_BOT_TOKEN")
        return

    if not settings["api_key"]:
        print("Missing API_KEY")
        return

    raw_allowed = settings["allowed_telegram_chat_ids"] or ""
    allowed_chat_ids: set[int] | None = None
    if raw_allowed.strip():
        try:
            allowed_chat_ids = parse_allowed_chat_ids(raw_allowed)
        except ValueError as exc:
            print(f"Invalid ALLOWED_TELEGRAM_CHAT_IDS: {exc}")
            return

    tool_registry = create_reloadable_tool_registry()
    for plugin_error in tool_registry.drain_errors():
        print(f"plugin warning: {plugin_error}")

    print("Starting Telegram bot polling...")
    run_telegram_bot(
        str(token),
        allowed_chat_ids=allowed_chat_ids,
        bot_factory=lambda session_id=None: CodingBot(tool_registry=tool_registry, session_id=session_id),
        tool_registry=tool_registry,
        runtime_settings=settings,
    )


if __name__ == "__main__":
    main()
