from typing import Any

from core.bot import CodingBot


def run_daily_memory_sync(sessions: dict[int, CodingBot]) -> dict[str, Any]:
    return _run_memory_sync(
        sessions,
        reason="daily-sync",
        refresh_index=True,
        promote_mission=True,
    )


def run_micro_memory_sync(sessions: dict[int, CodingBot]) -> dict[str, Any]:
    return _run_memory_sync(
        sessions,
        reason="micro-sync",
        refresh_index=True,
        promote_mission=False,
    )


def run_weekly_memory_compound(sessions: dict[int, CodingBot]) -> dict[str, Any]:
    result = _run_memory_sync(
        sessions,
        reason="weekly-compound",
        refresh_index=False,
        promote_mission=False,
    )
    compounded = 0
    for runtime in _collect_unique_runtimes(sessions):
        if hasattr(runtime, "compound_weekly_memory"):
            try:
                out = runtime.compound_weekly_memory()
                if isinstance(out, str) and "failed" in out.lower():
                    result["errors"].append(out)
                compounded += 1
            except Exception as exc:
                result["errors"].append(f"weekly compound error: {exc}")
                continue
        if hasattr(runtime, "sync_mission_to_memory"):
            try:
                mission_out = runtime.sync_mission_to_memory()
                if isinstance(mission_out, str) and "failed" in mission_out.lower():
                    result["errors"].append(mission_out)
                elif isinstance(mission_out, str) and "saved" in mission_out.lower():
                    result["mission_memory_synced_runtimes"] += 1
            except Exception as exc:
                result["errors"].append(f"weekly mission-memory sync error: {exc}")
        if hasattr(runtime, "refresh_index"):
            try:
                refresh_out = runtime.refresh_index()
                if isinstance(refresh_out, str) and "failed" in refresh_out.lower():
                    result["errors"].append(refresh_out)
                else:
                    result["refreshed_indexes"] += 1
            except Exception as exc:
                result["errors"].append(f"weekly index refresh error: {exc}")
    result["compounded_runtimes"] = compounded
    return result


def _collect_unique_runtimes(sessions: dict[int, CodingBot]) -> list[Any]:
    seen_runtime_ids: set[int] = set()
    runtimes: list[Any] = []
    for bot in sessions.values():
        runtime = getattr(bot, "memory_runtime", None)
        if runtime is not None and id(runtime) not in seen_runtime_ids:
            seen_runtime_ids.add(id(runtime))
            runtimes.append(runtime)
    return runtimes


def _run_memory_sync(
    sessions: dict[int, CodingBot],
    *,
    reason: str,
    refresh_index: bool,
    promote_mission: bool,
) -> dict[str, Any]:
    synced = 0
    skipped = 0
    promoted = 0
    mission_memory_synced = 0
    refreshed_indexes = 0
    errors: list[str] = []

    for bot in sessions.values():
        if not hasattr(bot, "flush_memory_snapshot"):
            continue
        try:
            out = bot.flush_memory_snapshot(reason=reason, refresh_index=False)
            text = str(out).lower()
            if "skipped" in text:
                skipped += 1
            else:
                synced += 1
        except Exception as exc:
            errors.append(f"memory snapshot error ({reason}): {exc}")

    if promote_mission:
        for runtime in _collect_unique_runtimes(sessions):
            if not hasattr(runtime, "promote_braindump_to_mission"):
                continue
            try:
                out = runtime.promote_braindump_to_mission()
                text = str(out).lower()
                if "failed" in text:
                    errors.append(str(out))
                elif "saved" in text:
                    promoted += 1
            except Exception as exc:
                errors.append(f"mission sync error ({reason}): {exc}")

            if hasattr(runtime, "sync_mission_to_memory"):
                try:
                    out = runtime.sync_mission_to_memory()
                    text = str(out).lower()
                    if "failed" in text:
                        errors.append(str(out))
                    elif "saved" in text:
                        mission_memory_synced += 1
                except Exception as exc:
                    errors.append(f"mission memory sync error ({reason}): {exc}")

    if refresh_index:
        for runtime in _collect_unique_runtimes(sessions):
            if not hasattr(runtime, "refresh_index"):
                continue
            try:
                refresh_out = runtime.refresh_index()
                if isinstance(refresh_out, str) and "failed" in refresh_out.lower():
                    errors.append(refresh_out)
                else:
                    refreshed_indexes += 1
            except Exception as exc:
                errors.append(f"memory index refresh error ({reason}): {exc}")

    return {
        "reason": reason,
        "synced_sessions": synced,
        "skipped_sessions": skipped,
        "promoted_runtimes": promoted,
        "mission_memory_synced_runtimes": mission_memory_synced,
        "refreshed_indexes": refreshed_indexes,
        "errors": errors,
    }
