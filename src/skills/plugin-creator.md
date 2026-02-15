Use when the user asks to create, fix, or extend runtime plugins under `src/plugins`.

## Goal
Create plugins that are hot-load safe, error-tolerant, and compatible with Abraxas runtime.

## Non-goals
- Do not edit `src/core` or `src/channel` unless the user explicitly asks.
- Do not invent custom plugin loaders.

## Mandatory Plugin Contract
1. File location: `src/plugins/<name>.py`
2. Expose `register(registry) -> None`
3. Register `ToolPlugin` from `core.tools`
4. Handler input/output: `dict -> str`
5. Handler must return readable errors instead of raising

Use this exact import pattern:

```python
from core.tools import ToolPlugin
```

Do not use `from core.tools import tool` (that symbol does not exist).

## Minimal Template
```python
from core.tools import ToolPlugin

def _handle(payload: dict) -> str:
    try:
        value = str(payload.get("value", "")).strip()
        if not value:
            return "plugin error: value is required"
        return f"ok: {value}"
    except Exception as exc:
        return f"plugin error: {exc}"

def register(registry) -> None:
    registry.register(
        ToolPlugin(
            name="example_plugin",
            description="Example plugin tool.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=_handle,
        )
    )
```

## Verification
1. Ensure plugin file imports successfully.
2. Check `plugin warning` logs are empty after reload.
3. Confirm `/commands` shows the new plugin tool.
4. Run tests:
- `pdm run python -m unittest -v test_bot.py test_telegram_bot.py`

## Failure Recovery
- If plugin load fails, fix import/signature first.
- Keep core runtime untouched; recover by patching only plugin file.
