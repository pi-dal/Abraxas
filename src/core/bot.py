import os

from openai import OpenAI
from typing import Protocol

from .settings import load_settings
from .skills import DEFAULT_SKILLS_DIR, load_skills_prompt
from .tools import create_default_registry, tool_label


class ToolRuntime(Protocol):
    def tool_specs(self) -> list[dict]:
        ...

    def call(self, name: str, arguments: str) -> str:
        ...

SYSTEM_PROMPT = (
    "You are Abraxas, a coding bot. Be concise. "
    "Treat src/core and src/channel as protected runtime code and do not edit them unless the user explicitly asks. "
    "Prefer applying skills in src/skills first. "
    "If skills cannot solve the task, extend behavior via plugins in src/plugins. "
    "You may also follow optional skill instructions loaded from src/skills. "
    "Plugin contract: create a module in src/plugins that defines register(registry) and registers ToolPlugin from core.tools. "
    "Plugins must fail safely and return readable error text instead of crashing runtime. "
    "Distinguish tool source by tag: descriptions starting with [builtin] are built-in runtime tools; descriptions starting with [plugin] are external plugin tools. "
    "When reasoning about capabilities, treat [builtin] and [plugin] as separate groups. "
    "Telegram configuration can be extended through plugin tools that manage TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_CHAT_IDS in .env. "
    "Use the bash tool when shell execution is needed. "
    "When task is done, return the final answer in plain text."
)


def build_system_prompt(skills_dir: str | None = None) -> str:
    resolved_skills_dir = skills_dir or os.getenv("ABRAXAS_SKILLS_DIR", DEFAULT_SKILLS_DIR)
    skills_prompt = load_skills_prompt(resolved_skills_dir)
    if not skills_prompt:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{skills_prompt}"


class CodingBot:
    def __init__(
        self,
        model: str | None = None,
        tool_registry: ToolRuntime | None = None,
    ):
        config = load_settings()
        self.client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
        self.model = model or str(config["model"])
        self.tool_registry = tool_registry or create_default_registry()
        self.messages = [{"role": "system", "content": build_system_prompt()}]

    def ask(self, user_text: str, on_tool=None) -> str:
        self.messages.append({"role": "user", "content": user_text})
        while True:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tool_registry.tool_specs(),
                tool_choice="auto",
                temperature=0.2,
            )
            message = response.choices[0].message
            tool_calls = message.tool_calls or []
            entry = {"role": "assistant", "content": message.content or ""}
            if tool_calls:
                entry["tool_calls"] = [tool_call.model_dump() for tool_call in tool_calls]
            self.messages.append(entry)
            if not tool_calls:
                return message.content or ""
            for tool_call in tool_calls:
                if on_tool:
                    on_tool(tool_label(tool_call.function.name, tool_call.function.arguments))
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": self.tool_registry.call(
                            tool_call.function.name, tool_call.function.arguments
                        ),
                    }
                )
