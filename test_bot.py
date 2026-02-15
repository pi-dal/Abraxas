import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

import channel.cli as cli
from channel.cli import (
    PROMPT_TEXT,
    handle_cli_command,
    make_input_session,
    make_reply_panel,
)
import core.bot as core_bot
import core.memory as core_memory
import core.nous as core_nous
from core.bot import SYSTEM_PROMPT
from core.registry import build_tool_registry, create_reloadable_tool_registry
from core import tools as core_tools
from core.bot import build_system_prompt
from core.skills import load_skills_prompt
from core.settings import load_settings as core_load_settings
from dotenv_config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    load_settings,
    load_telegram_settings,
)
from tools import (
    TOOLS,
    call_tool,
    run_bash,
    tool_label,
)


class BotTests(unittest.TestCase):
    class _FakeToolRegistry:
        def tool_specs(self):
            return [
                {
                    "type": "function",
                    "function": {"name": "bash", "description": "[builtin] bash tool"},
                },
                {
                    "type": "function",
                    "function": {"name": "demo_plugin", "description": "[plugin] plugin tool"},
                },
            ]

    class _FakeCliBot:
        def __init__(self):
            self.tool_registry = BotTests._FakeToolRegistry()
            self.remember_calls: list[tuple[str, list[str]]] = []

        def compact_session(self, keep_last_messages=12, instructions=None):
            return f"compacted:{keep_last_messages}:{instructions or ''}"

        def remember(self, note, tags=None):
            clean_tags = tags or []
            self.remember_calls.append((note, clean_tags))
            return "remembered"

        def refresh_system_prompt(self):
            return "system prompt refreshed"

    def test_system_prompt_protects_core_and_channel(self):
        self.assertIn("src/core", SYSTEM_PROMPT)
        self.assertIn("src/channel", SYSTEM_PROMPT)
        self.assertIn("src/skills", SYSTEM_PROMPT)
        self.assertIn("src/memory", SYSTEM_PROMPT)
        self.assertIn("skills in src/skills first", SYSTEM_PROMPT)
        self.assertIn("plugin", SYSTEM_PROMPT.lower())
        self.assertIn("[builtin]", SYSTEM_PROMPT)
        self.assertIn("[plugin]", SYSTEM_PROMPT)

    def test_core_layer_exists(self):
        self.assertEqual(core_tools.TOOLS[0]["function"]["name"], "bash")

    def test_default_tool_spec_has_builtin_tag(self):
        registry = core_tools.create_default_registry()
        specs = registry.tool_specs()
        self.assertTrue(specs)
        desc = specs[0]["function"]["description"]
        self.assertTrue(desc.startswith("[builtin] "))

    def test_plugin_tool_spec_has_plugin_tag(self):
        registry = core_tools.create_default_registry()
        registry.register(
            core_tools.ToolPlugin(
                name="echo_tagged",
                description="Echo input text.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=lambda payload: str(payload.get("text", "")),
            )
        )
        specs = registry.tool_specs()
        echo_spec = next(
            spec for spec in specs if spec["function"]["name"] == "echo_tagged"
        )
        self.assertTrue(echo_spec["function"]["description"].startswith("[plugin] "))

    def test_top_level_shims_point_to_core(self):
        self.assertIs(TOOLS, core_tools.TOOLS)
        self.assertIs(load_settings, core_load_settings)

    def test_load_skills_prompt_reads_markdown_files(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "alpha.md"), "w", encoding="utf-8") as f:
                f.write("Always answer briefly.")
            with open(os.path.join(td, "beta.txt"), "w", encoding="utf-8") as f:
                f.write("Prefer plugins for extension.")

            prompt = load_skills_prompt(td)
            self.assertIn("Additional skills loaded", prompt)
            self.assertIn("alpha.md", prompt)
            self.assertIn("beta.txt", prompt)
            self.assertIn("Always answer briefly.", prompt)
            self.assertIn("Prefer plugins for extension.", prompt)

    def test_load_skills_prompt_missing_directory(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "skills-missing")
            prompt = load_skills_prompt(missing)
            self.assertEqual(prompt, "")

    def test_build_system_prompt_includes_skills(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "ops.md"), "w", encoding="utf-8") as f:
                f.write("Do not break runtime.")
            prompt = build_system_prompt(skills_dir=td, nous_path=os.path.join(td, "NOUS.md"))
            self.assertIn("Do not break runtime.", prompt)
            self.assertIn("Additional skills loaded", prompt)

    def test_build_system_prompt_without_skills_uses_base_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "skills-missing")
            missing_nous = os.path.join(td, "NOUS-missing.md")
            prompt = build_system_prompt(skills_dir=missing, nous_path=missing_nous)
            self.assertEqual(prompt, SYSTEM_PROMPT)

    def test_build_system_prompt_includes_nous(self):
        with tempfile.TemporaryDirectory() as td:
            nous_path = os.path.join(td, "NOUS.md")
            with open(nous_path, "w", encoding="utf-8") as f:
                f.write("Name: Abraxas\nPrime Directive: Reveal structure beneath chaos.")
            prompt = build_system_prompt(skills_dir=td, nous_path=nous_path)
            self.assertIn("NOUS profile loaded", prompt)
            self.assertIn("Prime Directive", prompt)

    def test_nous_persist_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            nous_path = os.path.join(td, "NOUS.md")
            core_nous.write_nous_text("line one", nous_path=nous_path)
            core_nous.append_nous_text("line two", nous_path=nous_path)
            text = core_nous.load_nous_text(nous_path=nous_path)
            self.assertIn("line one", text)
            self.assertIn("line two", text)

    def test_nous_reinforce_from_dialogue(self):
        with tempfile.TemporaryDirectory() as td:
            nous_path = os.path.join(td, "NOUS.md")
            core_nous.write_nous_text("## Identity\nName: Abraxas", nous_path=nous_path)
            _, section = core_nous.reinforce_nous_from_dialogue(
                "When uncertain, run a quick experiment before theorizing.",
                nous_path=nous_path,
            )
            text = core_nous.load_nous_text(nous_path=nous_path)
            self.assertEqual(section, "NOUS Reinforcements")
            self.assertIn("NOUS Reinforcements", text)
            self.assertIn("run a quick experiment", text)

    def test_nous_reinforce_habit_from_dialogue(self):
        with tempfile.TemporaryDirectory() as td:
            nous_path = os.path.join(td, "NOUS.md")
            core_nous.write_nous_text("## Identity\nName: Abraxas", nous_path=nous_path)
            _, section = core_nous.reinforce_nous_from_dialogue(
                "用户习惯：偏好先看结论，再看证据。",
                nous_path=nous_path,
            )
            text = core_nous.load_nous_text(nous_path=nous_path)
            self.assertEqual(section, "User Habits (Persistent)")
            self.assertIn("User Habits (Persistent)", text)
            self.assertIn("偏好先看结论", text)

    def test_memory_runtime_loads_brief(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "MEMORY.md"), "w", encoding="utf-8") as f:
                f.write("durable memory line")
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            text = runtime.load_memory_brief()
            self.assertIn("durable memory line", text)

    def test_memory_runtime_appends_braindump(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            out = runtime.append_braindump("capture this idea", tags=["idea", "product"])
            self.assertIn("saved", out)
            path = os.path.join(td, "braindump.md")
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("capture this idea", content)
            self.assertIn("[idea,product]", content)

    def test_coding_bot_remember_uses_memory_runtime(self):
        class _FakeMemory:
            def __init__(self):
                self.calls = []

            def append_braindump(self, note, tags=None):
                self.calls.append((note, tags))
                return "saved"

        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        bot.memory_runtime = _FakeMemory()
        out = core_bot.CodingBot.remember(bot, "new thought", tags=["task"])
        self.assertEqual(out, "saved")
        self.assertEqual(bot.memory_runtime.calls[0][0], "new thought")
        self.assertEqual(bot.memory_runtime.calls[0][1], ["task"])

    def test_coding_bot_compact_session_keeps_recent_messages(self):
        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        bot.memory_runtime = None
        bot.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
        ]
        result = core_bot.CodingBot.compact_session(
            bot,
            keep_last_messages=2,
            instructions="Focus on decisions and open questions",
        )
        self.assertIn("session compacted", result)
        self.assertEqual(bot.messages[0]["role"], "system")
        self.assertEqual(bot.messages[1]["role"], "assistant")
        self.assertIn("[compaction_summary]", bot.messages[1]["content"])
        self.assertIn("Focus on decisions and open questions", bot.messages[1]["content"])
        self.assertEqual(len(bot.messages), 4)
        self.assertEqual(bot.messages[-1]["content"], "u3")

    def test_auto_compact_if_needed_triggers_compaction(self):
        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        bot.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "A" * 500},
            {"role": "assistant", "content": "B" * 500},
            {"role": "user", "content": "C" * 500},
        ]
        bot.auto_compact_max_tokens = 10
        bot.auto_compact_keep_last_messages = 2
        bot.auto_compact_instructions = "Focus on constraints"

        result = core_bot.CodingBot._auto_compact_if_needed(bot, "new input")
        self.assertIn("session compacted", result)
        self.assertEqual(bot.messages[0]["role"], "system")
        self.assertIn("[compaction_summary]", bot.messages[1]["content"])
        self.assertIn("Focus on constraints", bot.messages[1]["content"])

    def test_auto_compact_if_needed_disabled(self):
        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        bot.memory_runtime = None
        bot.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        bot.auto_compact_max_tokens = 0
        bot.auto_compact_keep_last_messages = 2
        bot.auto_compact_instructions = None

        result = core_bot.CodingBot._auto_compact_if_needed(bot, "new input")
        self.assertIsNone(result)
        self.assertEqual(len(bot.messages), 3)

    def test_compact_session_writes_memory_before_rewrite(self):
        class _FakeMemory:
            def __init__(self):
                self.compactions = []

            def record_compaction(self, summary):
                self.compactions.append(summary)
                return "ok"

        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        bot.memory_runtime = _FakeMemory()
        bot.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
        ]
        out = core_bot.CodingBot.compact_session(bot, keep_last_messages=2)
        self.assertIn("session compacted", out)
        self.assertTrue(bot.memory_runtime.compactions)

    def test_plugin_registry_extensible(self):
        registry = core_tools.create_default_registry()
        plugin = core_tools.ToolPlugin(
            name="echo",
            description="Echo input text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=lambda payload: str(payload.get("text", "")),
        )
        registry.register(plugin)
        self.assertIn("echo", registry.plugin_names())
        self.assertEqual(registry.call("echo", '{"text":"hi"}'), "hi")

    def test_plugin_failure_does_not_break_registry(self):
        registry = core_tools.create_default_registry()
        registry.register(
            core_tools.ToolPlugin(
                name="boom",
                description="Always fails.",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda payload: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        )
        out = registry.call("boom", "{}")
        self.assertIn("tool error", out)
        self.assertIn("boom", out)

    def test_build_tool_registry_loads_external_plugins(self):
        with tempfile.TemporaryDirectory() as td:
            plugins_dir = os.path.join(td, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            with open(os.path.join(plugins_dir, "__init__.py"), "w", encoding="utf-8") as f:
                f.write("")
            with open(os.path.join(plugins_dir, "echo.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from core.tools import ToolPlugin\n\n"
                    "def register(registry):\n"
                    "    registry.register(ToolPlugin(\n"
                    "        name='echo',\n"
                    "        description='echo',\n"
                    "        parameters={'type':'object','properties':{'text':{'type':'string'}},'required':['text']},\n"
                    "        handler=lambda payload: str(payload.get('text','')),\n"
                    "    ))\n"
                )
            sys.path.insert(0, td)
            try:
                registry, errors = build_tool_registry(plugin_package="plugins")
            finally:
                sys.path.remove(td)
                for module_name in list(sys.modules.keys()):
                    if module_name == "plugins" or module_name.startswith("plugins."):
                        sys.modules.pop(module_name, None)

        self.assertEqual(errors, [])
        self.assertIn("echo", registry.plugin_names())
        self.assertEqual(registry.call("echo", '{"text":"ok"}'), "ok")

    def test_build_tool_registry_isolates_bad_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            plugins_dir = os.path.join(td, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            with open(os.path.join(plugins_dir, "__init__.py"), "w", encoding="utf-8") as f:
                f.write("")
            with open(os.path.join(plugins_dir, "bad.py"), "w", encoding="utf-8") as f:
                f.write("def register(registry):\n    raise RuntimeError('bad plugin')\n")
            sys.path.insert(0, td)
            try:
                registry, errors = build_tool_registry(plugin_package="plugins")
            finally:
                sys.path.remove(td)
                for module_name in list(sys.modules.keys()):
                    if module_name == "plugins" or module_name.startswith("plugins."):
                        sys.modules.pop(module_name, None)

        self.assertIn("bash", registry.plugin_names())
        self.assertEqual(len(errors), 1)
        self.assertIn("bad", errors[0])

    def test_telegram_config_plugin_can_update_allowed_chat_ids(self):
        keys = ["ABRAXAS_ENV_PATH"]
        snapshot = {k: os.environ.get(k) for k in keys}
        try:
            with tempfile.TemporaryDirectory() as td:
                env_path = os.path.join(td, ".env")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(
                        "API_KEY=test-key\n"
                        "TELEGRAM_BOT_TOKEN=test-token\n"
                        "ALLOWED_TELEGRAM_CHAT_IDS=1,2\n"
                    )
                os.environ["ABRAXAS_ENV_PATH"] = env_path
                registry, errors = build_tool_registry(plugin_package="plugins")
                self.assertEqual(errors, [])
                self.assertIn("telegram_config", registry.plugin_names())
                out = registry.call(
                    "telegram_config",
                    '{"action":"add_allowed_chat_id","chat_id":3}',
                )
                self.assertIn("updated", out)
                show = registry.call("telegram_config", '{"action":"show"}')
                self.assertIn('"allowed_telegram_chat_ids": "1,2,3"', show)
        finally:
            for k, v in snapshot.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_reloadable_registry_hot_loads_new_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            plugins_dir = os.path.join(td, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            with open(os.path.join(plugins_dir, "__init__.py"), "w", encoding="utf-8") as f:
                f.write("")
            sys.path.insert(0, td)
            try:
                registry = create_reloadable_tool_registry(
                    plugin_package="plugins",
                    reload_interval=0,
                )
                self.assertNotIn("echo_hot", registry.plugin_names())
                with open(os.path.join(plugins_dir, "echo_hot.py"), "w", encoding="utf-8") as f:
                    f.write(
                        "from core.tools import ToolPlugin\n\n"
                        "def register(registry):\n"
                        "    registry.register(ToolPlugin(\n"
                        "        name='echo_hot',\n"
                        "        description='echo hot',\n"
                        "        parameters={'type':'object','properties':{'text':{'type':'string'}},'required':['text']},\n"
                        "        handler=lambda payload: str(payload.get('text','')),\n"
                        "    ))\n"
                    )
                registry.reload(force=True)
                self.assertIn("echo_hot", registry.plugin_names())
                self.assertEqual(registry.call("echo_hot", '{"text":"loaded"}'), "loaded")
            finally:
                sys.path.remove(td)
                for module_name in list(sys.modules.keys()):
                    if module_name == "plugins" or module_name.startswith("plugins."):
                        sys.modules.pop(module_name, None)

    def test_reloadable_registry_recovers_after_plugin_fix(self):
        with tempfile.TemporaryDirectory() as td:
            plugins_dir = os.path.join(td, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            with open(os.path.join(plugins_dir, "__init__.py"), "w", encoding="utf-8") as f:
                f.write("")
            bad_file = os.path.join(plugins_dir, "flaky.py")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("def register(registry):\n    raise RuntimeError('oops')\n")

            sys.path.insert(0, td)
            try:
                registry = create_reloadable_tool_registry(
                    plugin_package="plugins",
                    reload_interval=0,
                )
                self.assertTrue(any("flaky" in err for err in registry.drain_errors()))

                with open(bad_file, "w", encoding="utf-8") as f:
                    f.write(
                        "from core.tools import ToolPlugin\n\n"
                        "def register(registry):\n"
                        "    registry.register(ToolPlugin(\n"
                        "        name='flaky',\n"
                        "        description='fixed',\n"
                        "        parameters={'type':'object','properties':{},'required':[]},\n"
                        "        handler=lambda payload: 'ok',\n"
                        "    ))\n"
                    )
                registry.reload(force=True)
                self.assertIn("flaky", registry.plugin_names())
                self.assertEqual(registry.call("flaky", "{}"), "ok")
            finally:
                sys.path.remove(td)
                for module_name in list(sys.modules.keys()):
                    if module_name == "plugins" or module_name.startswith("plugins."):
                        sys.modules.pop(module_name, None)

    def test_reloadable_registry_default_is_immediate(self):
        with tempfile.TemporaryDirectory() as td:
            plugins_dir = os.path.join(td, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            with open(os.path.join(plugins_dir, "__init__.py"), "w", encoding="utf-8") as f:
                f.write("")
            sys.path.insert(0, td)
            try:
                registry = create_reloadable_tool_registry(plugin_package="plugins")
                self.assertNotIn("hot_now", registry.plugin_names())
                with open(os.path.join(plugins_dir, "hot_now.py"), "w", encoding="utf-8") as f:
                    f.write(
                        "from core.tools import ToolPlugin\n\n"
                        "def register(registry):\n"
                        "    registry.register(ToolPlugin(\n"
                        "        name='hot_now',\n"
                        "        description='hot',\n"
                        "        parameters={'type':'object','properties':{},'required':[]},\n"
                        "        handler=lambda payload: 'ok',\n"
                        "    ))\n"
                    )
                self.assertIn("hot_now", registry.plugin_names())
                self.assertEqual(registry.call("hot_now", "{}"), "ok")
            finally:
                sys.path.remove(td)
                for module_name in list(sys.modules.keys()):
                    if module_name == "plugins" or module_name.startswith("plugins."):
                        sys.modules.pop(module_name, None)

    def test_run_bash(self):
        self.assertIn("hello", run_bash("printf hello"))

    def test_prompt_text_no_colon(self):
        self.assertNotIn(":", PROMPT_TEXT)
        self.assertEqual(PROMPT_TEXT, "you> ")
        self.assertNotIn("[", PROMPT_TEXT)

    def test_tools_has_bash(self):
        self.assertEqual(len(TOOLS), 1)
        self.assertEqual(TOOLS[0]["function"]["name"], "bash")

    def test_call_tool_bash(self):
        out = call_tool("bash", '{"command":"printf hello"}')
        self.assertIn("hello", out)

    def test_tool_label(self):
        self.assertEqual(tool_label("bash", '{"command":"ls"}'), "bash: ls")

    def test_make_reply_panel_style(self):
        panel = make_reply_panel("ok")
        self.assertEqual(str(panel.title), "assistant")
        self.assertEqual(str(panel.border_style), "bright_green")

    def test_make_input_session(self):
        session = make_input_session()
        self.assertTrue(hasattr(session, "prompt"))

    def test_cli_help_command(self):
        handled, out, should_exit = handle_cli_command("/help", self._FakeCliBot())
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIn("I am Abraxas", out)
        self.assertIn("/commands", out)

    def test_cli_commands_command_lists_capabilities(self):
        env_snapshot = os.environ.get("ABRAXAS_SKILLS_DIR")
        try:
            with tempfile.TemporaryDirectory() as td:
                with open(os.path.join(td, "alpha.md"), "w", encoding="utf-8") as file:
                    file.write("skill alpha")
                os.environ["ABRAXAS_SKILLS_DIR"] = td
                handled, out, should_exit = handle_cli_command("/commands", self._FakeCliBot())
                self.assertTrue(handled)
                self.assertFalse(should_exit)
                self.assertIn("Capabilities", out)
                self.assertIn("builtin tools: bash", out)
                self.assertIn("plugin tools: demo_plugin", out)
                self.assertIn("skills: alpha.md", out)
        finally:
            if env_snapshot is None:
                os.environ.pop("ABRAXAS_SKILLS_DIR", None)
            else:
                os.environ["ABRAXAS_SKILLS_DIR"] = env_snapshot

    def test_cli_remember_command(self):
        bot = self._FakeCliBot()
        handled, out, should_exit = handle_cli_command("/remember test #alpha", bot)
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertEqual(out, "remembered")
        self.assertEqual(bot.remember_calls[0], ("test #alpha", ["alpha"]))

    def test_cli_sync_commands_is_telegram_only(self):
        handled, out, should_exit = handle_cli_command("/sync_commands", self._FakeCliBot())
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIn("Telegram-only", out)

    def test_cli_nous_set_command(self):
        env_snapshot = os.environ.get("ABRAXAS_NOUS_PATH")
        try:
            with tempfile.TemporaryDirectory() as td:
                nous_path = os.path.join(td, "NOUS.md")
                os.environ["ABRAXAS_NOUS_PATH"] = nous_path
                handled, out, should_exit = handle_cli_command(
                    "/nous set Name: Abraxas",
                    self._FakeCliBot(),
                )
                self.assertTrue(handled)
                self.assertFalse(should_exit)
                with open(nous_path, "r", encoding="utf-8") as file:
                    content = file.read()
                self.assertIn("Name: Abraxas", content)
                self.assertIn("NOUS updated", out)
        finally:
            if env_snapshot is None:
                os.environ.pop("ABRAXAS_NOUS_PATH", None)
            else:
                os.environ["ABRAXAS_NOUS_PATH"] = env_snapshot

    def test_app_name_constant_removed(self):
        self.assertFalse(hasattr(cli, "APP_NAME"))

    def test_load_settings_uses_api_key_only(self):
        keys = [
            "API_KEY",
            "OPENAI_API_KEY",
            "GLM_API_KEY",
            "ZAI_API_KEY",
            "OPENAI_BASE_URL",
            "API_BASE_URL",
            "OPENAI_MODEL",
            "MODEL",
        ]
        snapshot = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        try:
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, ".env")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(
                        "API_KEY=generic-key\n"
                        "OPENAI_BASE_URL=https://api.should.not.use\n"
                        "OPENAI_MODEL=should-not-use\n"
                    )
                cfg = load_settings(path)
                self.assertEqual(cfg["api_key"], "generic-key")
                self.assertEqual(cfg["base_url"], DEFAULT_BASE_URL)
                self.assertEqual(cfg["model"], DEFAULT_MODEL)
        finally:
            for k, v in snapshot.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_load_settings_ignores_legacy_keys(self):
        keys = ["API_KEY", "OPENAI_API_KEY", "GLM_API_KEY", "ZAI_API_KEY"]
        snapshot = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        try:
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, ".env")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("GLM_API_KEY=glm-key\nOPENAI_API_KEY=openai-key\n")
                cfg = load_settings(path)
                self.assertIsNone(cfg["api_key"])
                self.assertEqual(cfg["base_url"], DEFAULT_BASE_URL)
                self.assertEqual(cfg["model"], DEFAULT_MODEL)
        finally:
            for k, v in snapshot.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_load_telegram_settings_from_env_file(self):
        keys = ["TELEGRAM_BOT_TOKEN", "ALLOWED_TELEGRAM_CHAT_IDS"]
        snapshot = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        try:
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, ".env")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(
                        "TELEGRAM_BOT_TOKEN=test-token\n"
                        "ALLOWED_TELEGRAM_CHAT_IDS=1,2\n"
                    )
                cfg = load_telegram_settings(path)
                self.assertEqual(cfg["telegram_bot_token"], "test-token")
                self.assertEqual(cfg["allowed_telegram_chat_ids"], "1,2")
        finally:
            for k, v in snapshot.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_load_telegram_settings_whitelist_optional(self):
        keys = ["TELEGRAM_BOT_TOKEN", "ALLOWED_TELEGRAM_CHAT_IDS"]
        snapshot = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        try:
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, ".env")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("TELEGRAM_BOT_TOKEN=test-token\n")
                cfg = load_telegram_settings(path)
                self.assertEqual(cfg["telegram_bot_token"], "test-token")
                self.assertIsNone(cfg["allowed_telegram_chat_ids"])
        finally:
            for k, v in snapshot.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
