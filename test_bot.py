import importlib.util
import os
import pathlib
import sys
import tempfile
import tomllib
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

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
import core.scheduler as core_scheduler
import core.settings as core_settings
from core.bot import SYSTEM_PROMPT
from core.registry import build_tool_registry, create_reloadable_tool_registry
from core import tools as core_tools
from core.bot import build_system_prompt
from core.skills import load_skills_prompt
from core.settings import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
)
from core.tools import (
    TOOLS,
    call_tool,
    run_bash,
    tool_label,
)


class BotTests(unittest.TestCase):
    @staticmethod
    def _load_nano_banana_plugin_module():
        repo_root = pathlib.Path(__file__).resolve().parent
        module_path = repo_root / "src" / "plugins" / "nano_banana_image.py"
        spec = importlib.util.spec_from_file_location("nano_banana_image_test_module", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("failed to load nano_banana_image plugin module spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    class _FakeToolRegistry:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

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
                {
                    "type": "function",
                    "function": {"name": "tmux_manager", "description": "[plugin] tmux tool"},
                },
            ]

        def call(self, name: str, arguments: str):
            self.calls.append((name, arguments))
            if name == "tmux_manager":
                return "tmux sessions: (none)"
            return f"unknown tool: {name}"

    class _FakeCliBot:
        def __init__(self):
            self.tool_registry = BotTests._FakeToolRegistry()
            self.remember_calls: list[tuple[str, list[str]]] = []
            self.new_session_calls = 0

        def compact_session(self, keep_last_messages=12, instructions=None):
            return f"compacted:{keep_last_messages}:{instructions or ''}"

        def start_new_session(self):
            self.new_session_calls += 1
            return "new session started."

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

    def test_src_entrypoint_shims_removed(self):
        repo_root = pathlib.Path(__file__).resolve().parent
        self.assertFalse((repo_root / "src" / "cli.py").exists())
        self.assertFalse((repo_root / "src" / "telegram_bot.py").exists())

    def test_root_entrypoint_wrappers_removed(self):
        repo_root = pathlib.Path(__file__).resolve().parent
        self.assertFalse((repo_root / "bot.py").exists())
        self.assertFalse((repo_root / "telegram_bot.py").exists())

    def test_project_scripts_configured(self):
        repo_root = pathlib.Path(__file__).resolve().parent
        data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = data.get("tool", {}).get("pdm", {}).get("scripts", {})
        cli_script = scripts.get("abraxas-cli", {})
        tg_script = scripts.get("abraxas-telegram", {})
        self.assertIn("python -m channel.cli", cli_script.get("cmd", ""))
        self.assertIn("python -m channel.telegram_runner", tg_script.get("cmd", ""))
        self.assertEqual(cli_script.get("env", {}).get("PYTHONPATH"), "src")
        self.assertEqual(tg_script.get("env", {}).get("PYTHONPATH"), "src")

    def test_src_tools_shim_removed(self):
        repo_root = pathlib.Path(__file__).resolve().parent
        self.assertFalse((repo_root / "src" / "tools.py").exists())

    def test_nous_file_moved_to_core(self):
        repo_root = pathlib.Path(__file__).resolve().parent
        self.assertEqual(core_settings.DEFAULT_NOUS_PATH, "src/core/NOUS.md")
        self.assertTrue((repo_root / "src" / "core" / "NOUS.md").exists())
        self.assertFalse((repo_root / "src" / "NOUS.md").exists())

    def test_telegram_channel_is_split(self):
        repo_root = pathlib.Path(__file__).resolve().parent
        self.assertTrue((repo_root / "src" / "channel" / "telegram_client.py").exists())
        self.assertTrue((repo_root / "src" / "channel" / "telegram_handlers.py").exists())
        self.assertTrue((repo_root / "src" / "channel" / "telegram_runner.py").exists())

    def test_core_commands_module_exists(self):
        import core.commands as core_commands

        self.assertTrue(hasattr(core_commands, "build_help_text"))
        self.assertTrue(hasattr(core_commands, "build_commands_text"))
        self.assertTrue(hasattr(core_commands, "run_memory_command"))
        self.assertTrue(hasattr(core_commands, "run_tmux_plugin_command"))
        self.assertTrue(hasattr(core_commands, "run_new_session_command"))

    def test_settings_use_single_runtime_loader(self):
        self.assertFalse(hasattr(core_settings, "load_settings"))
        self.assertFalse(hasattr(core_settings, "load_telegram_settings"))

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

    def test_memory_runtime_query_uses_top_k_and_qmd_get(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(
                settings={
                    "memory_dir": td,
                    "qmd_command": "qmd",
                    "memory_top_k": 2,
                    "memory_max_inject_chars": 4000,
                    "qmd_timeout_sec": 30,
                    "memory_tz": "Asia/Shanghai",
                }
            )
            calls: list[list[str]] = []

            def fake_run(cmd, capture_output, text, timeout):
                calls.append(cmd)
                if cmd[1] == "query":
                    return SimpleNamespace(
                        returncode=0,
                        stdout=(
                            "src/memory/MEMORY.md:12\n"
                            "src/memory/daily/2026-02-15.md:8\n"
                            "src/memory/braindump.md:3\n"
                        ),
                        stderr="",
                    )
                if cmd[1] == "get":
                    target = cmd[2]
                    return SimpleNamespace(returncode=0, stdout=f"snippet for {target}\n", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="bad")

            with patch("core.memory.subprocess.run", side_effect=fake_run):
                out = runtime.query("what happened?")

            self.assertIn("snippet for src/memory/MEMORY.md:12", out)
            self.assertIn("snippet for src/memory/daily/2026-02-15.md:8", out)
            self.assertNotIn("braindump.md:3", out)
            query_calls = [cmd for cmd in calls if len(cmd) > 1 and cmd[1] == "query"]
            self.assertTrue(query_calls)
            self.assertIn("--top-k", query_calls[0])
            get_calls = [cmd for cmd in calls if len(cmd) > 1 and cmd[1] == "get"]
            self.assertEqual(len(get_calls), 2)

    def test_memory_runtime_query_falls_back_to_raw_output_when_no_refs(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(
                settings={
                    "memory_dir": td,
                    "qmd_command": "qmd",
                    "memory_top_k": 3,
                    "memory_max_inject_chars": 4000,
                    "qmd_timeout_sec": 30,
                    "memory_tz": "Asia/Shanghai",
                }
            )

            def fake_run(cmd, capture_output, text, timeout):
                if cmd[1] == "query":
                    return SimpleNamespace(returncode=0, stdout="plain output without refs", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="bad")

            with patch("core.memory.subprocess.run", side_effect=fake_run):
                out = runtime.query("anything")
            self.assertEqual(out, "plain output without refs")

    def test_memory_runtime_weekly_compound_updates_memory_file(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            daily_dir = os.path.join(td, "daily")
            os.makedirs(daily_dir, exist_ok=True)
            with open(os.path.join(daily_dir, "2026-02-10.md"), "w", encoding="utf-8") as file:
                file.write("# 2026-02-10 Daily Log\n\n## Decisions\n- choose architecture A\n")
            with open(os.path.join(daily_dir, "2026-02-11.md"), "w", encoding="utf-8") as file:
                file.write("# 2026-02-11 Daily Log\n\n## Action Items\n- ship memory v2\n")

            out = runtime.compound_weekly_memory()
            self.assertIn("weekly compound", out)
            with open(os.path.join(td, "MEMORY.md"), "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("Weekly Compound", content)
            self.assertIn("choose architecture A", content)

    def test_memory_runtime_promotes_braindump_to_mission_log(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            runtime.append_braindump("idea alpha", tags=["idea"])
            runtime.append_braindump("idea beta", tags=["todo"])

            first = runtime.promote_braindump_to_mission(limit=10)
            self.assertIn("mission sync saved", first)
            with open(os.path.join(td, "mission-log.md"), "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("[braindump:", content)
            self.assertIn("idea alpha", content)
            self.assertIn("idea beta", content)

            second = runtime.promote_braindump_to_mission(limit=10)
            self.assertIn("up-to-date", second)

    def test_memory_runtime_promote_braindump_dedupes_same_body(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            braindump = os.path.join(td, "braindump.md")
            with open(braindump, "w", encoding="utf-8") as file:
                file.write("# Braindump\n\n")
                file.write("- [2026-02-10 10:00 Asia/Shanghai] [idea] same idea\n")
                file.write("- [2026-02-10 12:00 Asia/Shanghai] [todo] same idea\n")

            out = runtime.promote_braindump_to_mission(limit=10)
            self.assertIn("1 item(s)", out)

    def test_memory_runtime_sync_mission_to_memory_upserts_section(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            runtime.record_mission_log("ship feature A")
            first = runtime.sync_mission_to_memory(limit=20)
            self.assertIn("saved", first)
            second = runtime.sync_mission_to_memory(limit=20)
            self.assertIn("up-to-date", second)
            with open(os.path.join(td, "MEMORY.md"), "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("Mission Memory", content)
            self.assertIn("ship feature A", content)
            self.assertEqual(content.count("Mission Memory"), 1)

    def test_memory_runtime_doctor_report_shows_qmd_health(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            runtime._run_qmd = lambda command: (127, "", "qmd not found")
            out = runtime.doctor_report()
            self.assertIn("memory doctor:", out)
            self.assertIn("qmd_available: no", out)
            self.assertIn("suggestion:", out)

    def test_memory_runtime_weekly_compound_upserts_section(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = core_memory.create_memory_runtime(memory_dir=td)
            daily_dir = os.path.join(td, "daily")
            os.makedirs(daily_dir, exist_ok=True)
            with open(os.path.join(daily_dir, "2026-02-10.md"), "w", encoding="utf-8") as file:
                file.write("# 2026-02-10 Daily Log\n\n- first point\n")
            out1 = runtime.compound_weekly_memory()
            self.assertIn("saved", out1)

            with open(os.path.join(daily_dir, "2026-02-11.md"), "w", encoding="utf-8") as file:
                file.write("# 2026-02-11 Daily Log\n\n- second point\n")
            out2 = runtime.compound_weekly_memory()
            self.assertIn("saved", out2)

            with open(os.path.join(td, "MEMORY.md"), "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("Weekly Compound", content)
            self.assertEqual(content.count("Weekly Compound"), 1)

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

    def test_estimate_message_tokens_counts_tool_calls(self):
        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        messages = [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "nano_banana_image",
                            "arguments": "x" * 8000,
                        },
                    }
                ],
            },
        ]
        estimated = core_bot.CodingBot._estimate_message_tokens(bot, messages)
        self.assertGreater(estimated, 1000)

    def test_prepare_messages_for_api_normalizes_assistant_tool_calls(self):
        messages = [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "nano_banana_image",
                            "arguments": {"mode": "text_to_image", "prompt": "cat"},
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "nano_banana_image",
                "content": "image_saved: /tmp/a.png",
            },
        ]

        out = core_bot.CodingBot._prepare_messages_for_api(messages)
        self.assertEqual(out[1]["role"], "assistant")
        self.assertIsNone(out[1]["content"])
        self.assertEqual(out[1]["tool_calls"][0]["function"]["name"], "nano_banana_image")
        self.assertIsInstance(out[1]["tool_calls"][0]["function"]["arguments"], str)
        self.assertEqual(out[2]["role"], "tool")
        self.assertEqual(out[2]["name"], "nano_banana_image")
        self.assertEqual(out[2]["tool_call_id"], "call_1")

    def test_ask_retries_once_on_context_overflow(self):
        class _FakeToolRegistry:
            def tool_specs(self):
                return []

            def call(self, name, arguments):
                _ = (name, arguments)
                return ""

        class _Message:
            def __init__(self, content: str):
                self.content = content
                self.tool_calls = []

        class _Response:
            def __init__(self, content: str):
                self.choices = [SimpleNamespace(message=_Message(content))]

        class _Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                _ = kwargs
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError(
                        "Error code: 400 - {'error': {'code': '1210', 'message': "
                        "\"input tokens exceeds maximum context length\"}}"
                    )
                return _Response("ok")

        completions = _Completions()
        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        bot.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        bot.model = "glm-4.7"
        bot.tool_registry = _FakeToolRegistry()
        bot.memory_runtime = None
        bot.auto_braindump_enabled = False
        bot.auto_compact_max_tokens = 0
        bot.auto_compact_keep_last_messages = 2
        bot.auto_compact_instructions = None
        bot.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "older"},
            {"role": "assistant", "content": "older-reply"},
        ]
        compactions = {"count": 0}

        def _compact_stub(keep_last_messages=12, instructions=None):
            _ = (keep_last_messages, instructions)
            compactions["count"] += 1
            bot.messages = [bot.messages[0], {"role": "assistant", "content": "[compaction_summary]\ntrim"}]
            return "session compacted"

        bot.compact_session = _compact_stub

        out = core_bot.CodingBot.ask(bot, "make image")
        self.assertEqual(out, "ok")
        self.assertEqual(compactions["count"], 1)
        self.assertEqual(completions.calls, 2)

    def test_ask_appends_tool_message_with_name(self):
        class _FakeToolRegistry:
            def __init__(self):
                self.calls = []

            def tool_specs(self):
                return []

            def call(self, name, arguments):
                self.calls.append((name, arguments))
                return "ok"

        class _ToolCall:
            def __init__(self):
                self.id = "call_1"
                self.function = SimpleNamespace(name="bash", arguments='{"command":"echo hi"}')

        class _Message:
            def __init__(self, content: str, tool_calls):
                self.content = content
                self.tool_calls = tool_calls

        class _Response:
            def __init__(self, content: str, tool_calls):
                self.choices = [SimpleNamespace(message=_Message(content, tool_calls))]

        class _Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                _ = kwargs
                self.calls += 1
                if self.calls == 1:
                    return _Response("", [_ToolCall()])
                return _Response("done", [])

        registry = _FakeToolRegistry()
        bot = core_bot.CodingBot.__new__(core_bot.CodingBot)
        bot.client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
        bot.model = "glm-4.7"
        bot.tool_registry = registry
        bot.memory_runtime = None
        bot.auto_braindump_enabled = False
        bot.auto_compact_max_tokens = 0
        bot.auto_compact_keep_last_messages = 2
        bot.auto_compact_instructions = None
        bot.messages = [{"role": "system", "content": "sys"}]

        out = core_bot.CodingBot.ask(bot, "run")
        self.assertEqual(out, "done")
        tool_entries = [item for item in bot.messages if item.get("role") == "tool"]
        self.assertTrue(tool_entries)
        self.assertEqual(tool_entries[-1].get("name"), "bash")

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
                for module_name in list(sys.modules.keys()):
                    if module_name == "plugins" or module_name.startswith("plugins."):
                        sys.modules.pop(module_name, None)
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

    def test_cli_tmux_command_routes_to_handler(self):
        bot = self._FakeCliBot()
        handled, out, should_exit = handle_cli_command("/tmux list", bot)
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertTrue(bot.tool_registry.calls)
        self.assertEqual(bot.tool_registry.calls[0][0], "tmux_manager")
        self.assertIn("list", bot.tool_registry.calls[0][1])
        self.assertIn("tmux sessions", out)

    def test_cli_new_command_starts_new_session(self):
        bot = self._FakeCliBot()
        handled, out, should_exit = handle_cli_command("/new", bot)
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertEqual(bot.new_session_calls, 1)
        self.assertIn("new session started", out)

    def test_cli_memory_status_command(self):
        class _Memory:
            def qmd_status(self):
                return "ok"

            def memory_status(self):
                return "memory status: ok"

        bot = self._FakeCliBot()
        bot.memory_runtime = _Memory()
        handled, out, should_exit = handle_cli_command("/memory status", bot)
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIn("memory status", out)

    def test_cli_memory_sync_command(self):
        class _Memory:
            def promote_braindump_to_mission(self):
                return "mission sync saved: 1 item(s)"

            def sync_mission_to_memory(self):
                return "mission memory sync saved: 1 item(s)"

            def refresh_index(self):
                return "memory index refreshed"

        bot = self._FakeCliBot()
        bot.memory_runtime = _Memory()
        bot.flush_memory_snapshot = lambda reason="manual", refresh_index=True: "memory saved to 2026-02-15.md"
        handled, out, should_exit = handle_cli_command("/memory sync", bot)
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIn("mission sync saved", out)
        self.assertIn("mission memory sync saved", out)

    def test_cli_memory_doctor_command(self):
        class _Memory:
            def doctor_report(self):
                return "memory doctor:\n- qmd_available: yes"

        bot = self._FakeCliBot()
        bot.memory_runtime = _Memory()
        handled, out, should_exit = handle_cli_command("/memory doctor", bot)
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIn("memory doctor", out)

    def test_auto_capture_braindump_keyword_detected(self):
        self.assertTrue(core_bot.CodingBot._should_auto_capture_braindump("记一下这个想法，后面做"))
        self.assertTrue(core_bot.CodingBot._should_auto_capture_braindump("note to self: build plugin"))
        self.assertFalse(core_bot.CodingBot._should_auto_capture_braindump("今天上海天气不错"))

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

    def test_plugin_creator_skill_exists(self):
        skill_path = pathlib.Path(__file__).resolve().parent / "src" / "skills" / "plugin-creator.md"
        self.assertTrue(skill_path.exists())
        content = skill_path.read_text(encoding="utf-8")
        self.assertIn("ToolPlugin", content)
        self.assertIn("core.tools", content)

    def test_nano_banana_photo_skill_exists(self):
        skill_path = pathlib.Path(__file__).resolve().parent / "src" / "skills" / "nano-banana-pro-photo.md"
        self.assertTrue(skill_path.exists())
        content = skill_path.read_text(encoding="utf-8")
        self.assertIn("gemini-3-pro-image-preview", content)
        self.assertIn("gemini-2.5-flash-image", content)
        self.assertIn("https://ai.google.dev/gemini-api/docs/image-generation", content)
        self.assertIn("Generate images in batch", content)
        self.assertIn("Inpainting (Semantic masking)", content)
        self.assertIn("Advanced composition: Combining multiple images", content)

    def test_nano_banana_plugin_registers(self):
        registry, errors = build_tool_registry()
        self.assertIn("nano_banana_image", registry.plugin_names())
        self.assertFalse(any("nano_banana_image" in item for item in errors))

    def test_nano_banana_plugin_missing_key_error(self):
        nano_banana_image = self._load_nano_banana_plugin_module()

        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            out = nano_banana_image._handle(
                {
                    "mode": "text_to_image",
                    "prompt": "A red apple on wooden table",
                    "api_key": "",
                }
            )
        self.assertIn("missing GEMINI_API_KEY", out)

    def test_nano_banana_plugin_search_mode_adds_google_search_tool(self):
        nano_banana_image = self._load_nano_banana_plugin_module()

        body, err = nano_banana_image._build_single_request(
            {"prompt": "today weather in shanghai"},
            "search_grounded_generate",
            "today weather in shanghai",
        )
        self.assertIsNone(err)
        self.assertIn("tools", body)
        self.assertEqual(body["tools"], [{"google_search": {}}])

    def test_nano_banana_extract_output_defaults_output_dir(self):
        nano_banana_image = self._load_nano_banana_plugin_module()
        response_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inline_data": {
                                    "mime_type": "image/png",
                                    "data": "aGVsbG8=",
                                }
                            }
                        ]
                    }
                }
            ]
        }

        previous_cwd = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as td:
                os.chdir(td)
                out = nano_banana_image._extract_output({}, response_data, index=1)
                self.assertIn("image_saved:", out)
                expected = os.path.join(td, "outputs", "images", "nano_banana_1_1.png")
                self.assertTrue(os.path.exists(expected))
        finally:
            os.chdir(previous_cwd)

    def test_skill_installer_mentions_current_entrypoints(self):
        skill_path = pathlib.Path(__file__).resolve().parent / "src" / "skills" / "skill-installer.md"
        self.assertTrue(skill_path.exists())
        content = skill_path.read_text(encoding="utf-8")
        self.assertIn("npx skills find <query>", content)
        self.assertIn("https://github.com/vercel-labs/skills --skill find-skills", content)
        self.assertIn("npx skills add <owner/repo@skill>", content)
        self.assertIn("npx skills check", content)
        self.assertIn("npx skills update", content)
        self.assertIn("update `src/skills/README.md`", content)
        self.assertIn("pdm run abraxas-cli", content)
        self.assertIn("pdm run abraxas-telegram", content)
        self.assertIn("plugins are hot-reloaded", content.lower())

    def test_load_settings_reads_openai_base_url_and_model(self):
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
                cfg = core_settings.load_runtime_settings(path)
                self.assertEqual(cfg["api_key"], "generic-key")
                self.assertEqual(cfg["base_url"], "https://api.should.not.use")
                self.assertEqual(cfg["model"], "should-not-use")
        finally:
            for k, v in snapshot.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_load_runtime_settings_reads_abraxas_overrides(self):
        keys = [
            "API_KEY",
            "ABRAXAS_SKILLS_DIR",
            "ABRAXAS_MEMORY_DIR",
            "ABRAXAS_MEMORY_DAILY_SYNC_TIME",
            "ABRAXAS_NOUS_PATH",
            "ABRAXAS_QMD_COMMAND",
            "ABRAXAS_AUTO_COMPACT_MAX_TOKENS",
        ]
        snapshot = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        try:
            with tempfile.TemporaryDirectory() as td:
                env_path = os.path.join(td, ".env")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(
                        "API_KEY=test-key\n"
                        "ABRAXAS_SKILLS_DIR=custom/skills\n"
                        "ABRAXAS_MEMORY_DIR=custom/memory\n"
                        "ABRAXAS_MEMORY_DAILY_SYNC_TIME=03:30\n"
                        "ABRAXAS_NOUS_PATH=custom/NOUS.md\n"
                        "ABRAXAS_QMD_COMMAND=qmdx\n"
                        "ABRAXAS_AUTO_COMPACT_MAX_TOKENS=9999\n"
                    )
                cfg = core_settings.load_runtime_settings(env_path)
                self.assertEqual(cfg["api_key"], "test-key")
                self.assertEqual(cfg["skills_dir"], "custom/skills")
                self.assertEqual(cfg["memory_dir"], "custom/memory")
                self.assertEqual(cfg["memory_daily_sync_time"], "03:30")
                self.assertEqual(cfg["nous_path"], "custom/NOUS.md")
                self.assertEqual(cfg["qmd_command"], "qmdx")
                self.assertEqual(cfg["auto_compact_max_tokens"], 9999)
        finally:
            for k, v in snapshot.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_create_memory_runtime_from_runtime_settings(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {
                "memory_dir": os.path.join(td, "memory-x"),
                "qmd_command": "qmdx",
                "memory_top_k": 9,
                "memory_max_inject_chars": 1111,
                "qmd_timeout_sec": 77,
                "memory_tz": "Asia/Tokyo",
            }
            runtime = core_memory.create_memory_runtime(settings=cfg)
            self.assertEqual(str(runtime.memory_dir), cfg["memory_dir"])
            self.assertEqual(runtime.qmd_command, "qmdx")
            self.assertEqual(runtime.top_k, 9)
            self.assertEqual(runtime.max_inject_chars, 1111)
            self.assertEqual(runtime.qmd_timeout_sec, 77)
            self.assertEqual(runtime.tz_name, "Asia/Tokyo")

    def test_multi_daily_scheduler_runs_due_slots_once(self):
        scheduler = core_scheduler.MultiDailyScheduler(
            times_text="10:00,13:00,16:00",
            tz_name="Asia/Shanghai",
        )
        ran: list[str] = []
        now = datetime(2026, 2, 16, 16, 30)
        count = scheduler.run_if_due(lambda key: ran.append(key), now=now)
        self.assertEqual(count, 3)
        count_second = scheduler.run_if_due(lambda key: ran.append(key), now=now)
        self.assertEqual(count_second, 0)

    def test_weekly_scheduler_runs_only_on_target_weekday(self):
        scheduler = core_scheduler.WeeklyScheduler(
            time_text="22:00",
            tz_name="Asia/Shanghai",
            weekday=6,
        )
        ran = {"value": 0}
        monday = datetime(2026, 2, 16, 22, 30)
        sunday = datetime(2026, 2, 22, 22, 30)

        self.assertFalse(scheduler.run_if_due(lambda: ran.__setitem__("value", 1), now=monday))
        self.assertTrue(scheduler.run_if_due(lambda: ran.__setitem__("value", 2), now=sunday))
        self.assertFalse(scheduler.run_if_due(lambda: ran.__setitem__("value", 3), now=sunday))
        self.assertEqual(ran["value"], 2)

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
                cfg = core_settings.load_runtime_settings(path)
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
                cfg = core_settings.load_runtime_settings(path)
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
                cfg = core_settings.load_runtime_settings(path)
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
