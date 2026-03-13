from __future__ import annotations


def has_main_model_auth(settings: dict[str, str | int | None]) -> bool:
    return bool(str(settings.get("api_key") or "").strip())


def main_model_auth_error(settings: dict[str, str | int | None]) -> str:
    if has_main_model_auth(settings):
        return ""
    return "Missing API_KEY"
