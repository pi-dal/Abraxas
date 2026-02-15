import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

TOOL_TAG_BUILTIN = "builtin"
TOOL_TAG_PLUGIN = "plugin"


def run_bash(command: str, timeout: int = 20) -> str:
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = (proc.stdout + proc.stderr).strip()
        return output or f"(exit={proc.returncode}, no output)"
    except Exception as exc:
        return f"bash error: {exc}"


def _parse_arguments(arguments: str) -> tuple[dict[str, Any], str | None]:
    try:
        payload = json.loads(arguments or "{}")
        if isinstance(payload, dict):
            return payload, None
        return {}, "invalid arguments: payload must be a JSON object"
    except Exception as exc:
        return {}, f"invalid arguments: {exc}"


@dataclass(frozen=True)
class ToolPlugin:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    tag: str = TOOL_TAG_PLUGIN

    def tagged_description(self) -> str:
        prefix = f"[{self.tag}] "
        if self.description.startswith(prefix):
            return self.description
        return f"{prefix}{self.description}"

    def to_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.tagged_description(),
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, plugins: list[ToolPlugin] | None = None):
        self._plugins: dict[str, ToolPlugin] = {}
        for plugin in plugins or []:
            self.register(plugin)

    def register(self, plugin: ToolPlugin, *, replace: bool = False) -> None:
        if not replace and plugin.name in self._plugins:
            raise ValueError(f"tool already registered: {plugin.name}")
        self._plugins[plugin.name] = plugin

    def plugin_names(self) -> list[str]:
        return list(self._plugins.keys())

    def tool_specs(self) -> list[dict[str, Any]]:
        return [plugin.to_spec() for plugin in self._plugins.values()]

    def call(self, name: str, arguments: str) -> str:
        plugin = self._plugins.get(name)
        if plugin is None:
            return f"unknown tool: {name}"

        payload, error = _parse_arguments(arguments)
        if error:
            return error

        try:
            result = plugin.handler(payload)
            return str(result)
        except Exception as exc:
            return f"tool error ({name}): {exc}"


def make_bash_plugin() -> ToolPlugin:
    return ToolPlugin(
        name="bash",
        description="Run a bash command and return stdout/stderr.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        handler=lambda payload: run_bash(str(payload.get("command", ""))),
        tag=TOOL_TAG_BUILTIN,
    )


def create_default_registry() -> ToolRegistry:
    return ToolRegistry(plugins=[make_bash_plugin()])


_DEFAULT_REGISTRY = create_default_registry()
TOOLS = _DEFAULT_REGISTRY.tool_specs()


def call_tool(name: str, arguments: str) -> str:
    return _DEFAULT_REGISTRY.call(name, arguments)


def tool_label(name: str, arguments: str) -> str:
    payload, error = _parse_arguments(arguments)
    if error:
        return name
    if name == "bash":
        command = str(payload.get("command", "")).strip()
        return f"{name}: {command}" if command else name
    return name
