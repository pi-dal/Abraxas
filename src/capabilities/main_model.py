from __future__ import annotations

from typing import Any

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency path
    OpenAI = None  # type: ignore[assignment]


def build_main_model_client(settings: dict[str, str | int | None]) -> tuple[Any, str]:
    if OpenAI is None:
        raise RuntimeError(
            "openai package is required to initialize CodingBot. "
            "Install project dependencies (for example: `pdm install`)."
        )
    client = OpenAI(
        api_key=settings.get("api_key"),
        base_url=str(settings.get("base_url") or ""),
    )
    return client, str(settings.get("model") or "")


def model_profile_settings(
    settings: dict[str, str | int | None],
    profile: str,
) -> dict[str, str | int | None]:
    if profile not in {"default", "main"}:
        raise ValueError(f"unknown model profile: {profile}")
    return dict(settings)
