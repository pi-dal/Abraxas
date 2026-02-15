import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

import channel.cli as cli
from channel.cli import (
    PROMPT_TEXT,
    make_input_session,
    make_reply_panel,
)
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
    def test_system_prompt_protects_core_and_channel(self):
        self.assertIn("src/core", SYSTEM_PROMPT)
        self.assertIn("src/channel", SYSTEM_PROMPT)
        self.assertIn("src/skills", SYSTEM_PROMPT)
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
            prompt = build_system_prompt(skills_dir=td)
            self.assertIn("Do not break runtime.", prompt)
            self.assertIn("Additional skills loaded", prompt)

    def test_build_system_prompt_without_skills_uses_base_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "skills-missing")
            prompt = build_system_prompt(skills_dir=missing)
            self.assertEqual(prompt, SYSTEM_PROMPT)

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
