import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from channel.telegram import (
    DEFAULT_TELEGRAM_COMMANDS,
    chunk_message,
    extract_message_payload,
    parse_allowed_chat_ids,
    process_update,
    run_daily_memory_sync,
    run_micro_memory_sync,
    run_weekly_memory_compound,
    sync_telegram_commands,
)


class _FakeClient:
    def __init__(self):
        self.sent = []
        self.commands_synced = []
        self.photos = []

    def send_message(self, chat_id, text, reply_to_message_id=None):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            }
        )

    def set_my_commands(self, commands):
        self.commands_synced.append(commands)
        return True

    def send_photo(self, chat_id, photo, caption=None, reply_to_message_id=None):
        self.photos.append(
            {
                "chat_id": chat_id,
                "photo": photo,
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"ok": True}


class _FakeBot:
    def __init__(self):
        self.inputs = []
        self.compacted = 0
        self.compact_calls = []
        self.remembered = []
        self.daily_syncs = 0
        self.memory_runtime = None
        self.tool_registry = _FakeRegistry()

    def ask(self, text):
        self.inputs.append(text)
        return f"echo:{text}"

    def compact_session(self, keep_last_messages=12, instructions=None):
        self.compacted += 1
        self.compact_calls.append(
            {
                "keep_last_messages": keep_last_messages,
                "instructions": instructions,
            }
        )
        return "session compacted: kept recent context"

    def remember(self, note, tags=None):
        self.remembered.append({"note": note, "tags": tags or []})
        return "memory saved"

    def flush_memory_snapshot(self, reason="daily-sync", refresh_index=False):
        self.daily_syncs += 1
        return f"memory snapshot flushed: {reason}"


class _FakeRegistry:
    def __init__(self):
        self.calls = []

    def tool_specs(self):
        return [
            {
                "type": "function",
                "function": {"name": "bash", "description": "[builtin] Run shell command."},
            },
            {
                "type": "function",
                "function": {"name": "telegram_config", "description": "[plugin] Configure telegram."},
            },
            {
                "type": "function",
                "function": {"name": "tmux_manager", "description": "[plugin] Manage tmux."},
            },
        ]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "tmux_manager":
            return "tmux sessions: (none)"
        return f"unknown tool: {name}"


class TelegramBotTests(unittest.TestCase):
    def test_extract_message_payload_text(self):
        payload = extract_message_payload(
            {
                "update_id": 1,
                "message": {"message_id": 11, "chat": {"id": 42}, "text": "hello"},
            }
        )
        self.assertEqual(payload, (42, 11, "hello"))

    def test_extract_message_payload_no_text(self):
        payload = extract_message_payload(
            {"update_id": 1, "message": {"message_id": 11, "chat": {"id": 42}}}
        )
        self.assertIsNone(payload)

    def test_chunk_message_split(self):
        text = "a" * 15
        chunks = chunk_message(text, limit=6)
        self.assertEqual(chunks, ["aaaaaa", "aaaaaa", "aaa"])

    def test_parse_allowed_chat_ids(self):
        allowed = parse_allowed_chat_ids("1, 2,3")
        self.assertEqual(allowed, {1, 2, 3})

    def test_parse_allowed_chat_ids_invalid(self):
        with self.assertRaises(ValueError):
            parse_allowed_chat_ids("1,abc")

    def test_process_update_reuses_chat_session(self):
        sessions = {}
        client = _FakeClient()

        bot_instances = []

        def factory():
            bot = _FakeBot()
            bot_instances.append(bot)
            return bot

        process_update(
            {"update_id": 1, "message": {"message_id": 11, "chat": {"id": 7}, "text": "A"}},
            sessions,
            client,
            factory,
            {7},
        )
        process_update(
            {"update_id": 2, "message": {"message_id": 12, "chat": {"id": 7}, "text": "B"}},
            sessions,
            client,
            factory,
            {7},
        )

        self.assertEqual(len(bot_instances), 1)
        self.assertEqual(bot_instances[0].inputs, ["A", "B"])
        self.assertEqual(client.sent[0]["text"], "echo:A")
        self.assertEqual(client.sent[1]["text"], "echo:B")
        self.assertEqual(client.sent[0]["reply_to_message_id"], 11)

    def test_process_update_sends_generated_image_from_tool_output(self):
        class _PhotoBot(_FakeBot):
            def __init__(self, image_path):
                super().__init__()
                self.image_path = image_path

            def ask(self, text, on_tool_result=None):
                _ = text
                if on_tool_result is not None:
                    on_tool_result(
                        "nano_banana_image",
                        '{"mode":"text_to_image"}',
                        f"image_saved: {self.image_path}\nstatus: ok",
                    )
                return "image generated."

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "photo.png")
            with open(image_path, "wb") as f:
                f.write(b"fake-image")

            bot = _PhotoBot(image_path)
            sessions = {7: bot}
            client = _FakeClient()
            process_update(
                {
                    "update_id": 100,
                    "message": {"message_id": 55, "chat": {"id": 7}, "text": "draw a cat"},
                },
                sessions,
                client,
                lambda: _FakeBot(),
                {7},
            )

            self.assertIn("image generated.", client.sent[-1]["text"])
            self.assertEqual(len(client.photos), 1)
            self.assertEqual(client.photos[0]["photo"], image_path)

    def test_process_update_help_mentions_compact(self):
        sessions = {}
        client = _FakeClient()

        process_update(
            {
                "update_id": 5,
                "message": {"message_id": 22, "chat": {"id": 7}, "text": "/help"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertTrue(client.sent)
        self.assertIn("I am Abraxas", client.sent[0]["text"])
        self.assertIn("/commands", client.sent[0]["text"])
        self.assertIn("normal language", client.sent[0]["text"])
        self.assertIn("/tmux", client.sent[0]["text"])
        self.assertIn("/memory", client.sent[0]["text"])

    def test_process_update_commands_lists_inventory(self):
        env_snapshot = os.environ.get("ABRAXAS_SKILLS_DIR")
        try:
            with tempfile.TemporaryDirectory() as td:
                with open(os.path.join(td, "alpha.md"), "w", encoding="utf-8") as f:
                    f.write("skill alpha")
                with open(os.path.join(td, "beta.txt"), "w", encoding="utf-8") as f:
                    f.write("skill beta")
                os.environ["ABRAXAS_SKILLS_DIR"] = td
                sessions = {7: _FakeBot()}
                client = _FakeClient()

                process_update(
                    {
                        "update_id": 15,
                        "message": {"message_id": 31, "chat": {"id": 7}, "text": "/commands"},
                    },
                    sessions,
                    client,
                    lambda: _FakeBot(),
                    {7},
                )

                text = client.sent[-1]["text"]
                self.assertIn("Capabilities", text)
                self.assertIn("/tmux", text)
                self.assertIn("builtin tools", text)
                self.assertIn("bash", text)
                self.assertIn("plugin tools", text)
                self.assertIn("telegram_config", text)
                self.assertIn("tmux_manager", text)
                self.assertIn("skills", text)
                self.assertIn("alpha.md", text)
                self.assertIn("beta.txt", text)
        finally:
            if env_snapshot is None:
                os.environ.pop("ABRAXAS_SKILLS_DIR", None)
            else:
                os.environ["ABRAXAS_SKILLS_DIR"] = env_snapshot

    def test_process_update_compact_calls_bot_compact_session(self):
        sessions = {7: _FakeBot()}
        client = _FakeClient()

        process_update(
            {
                "update_id": 6,
                "message": {"message_id": 23, "chat": {"id": 7}, "text": "/compact"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertEqual(sessions[7].compacted, 1)
        self.assertEqual(sessions[7].compact_calls[0]["keep_last_messages"], 12)
        self.assertIn("session compacted", client.sent[0]["text"])

    def test_process_update_compact_with_instructions(self):
        sessions = {7: _FakeBot()}
        client = _FakeClient()

        process_update(
            {
                "update_id": 7,
                "message": {
                    "message_id": 24,
                    "chat": {"id": 7},
                    "text": "/compact Focus on decisions only",
                },
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertEqual(sessions[7].compacted, 1)
        self.assertEqual(
            sessions[7].compact_calls[0]["instructions"],
            "Focus on decisions only",
        )

    def test_sync_telegram_commands_uses_client_api(self):
        client = _FakeClient()
        result = sync_telegram_commands(client)
        self.assertTrue(result)
        self.assertEqual(client.commands_synced[-1], DEFAULT_TELEGRAM_COMMANDS)

    def test_default_commands_include_tmux(self):
        names = [item["command"] for item in DEFAULT_TELEGRAM_COMMANDS]
        self.assertIn("tmux", names)
        self.assertIn("memory", names)
        self.assertIn("new", names)

    def test_process_update_new_resets_chat_session(self):
        old_bot = _FakeBot()
        sessions = {7: old_bot}
        client = _FakeClient()
        created: list[_FakeBot] = []

        def factory():
            bot = _FakeBot()
            created.append(bot)
            return bot

        process_update(
            {
                "update_id": 66,
                "message": {"message_id": 35, "chat": {"id": 7}, "text": "/new"},
            },
            sessions,
            client,
            factory,
            {7},
        )

        self.assertEqual(len(created), 1)
        self.assertIsNot(sessions[7], old_bot)
        self.assertIs(sessions[7], created[0])
        self.assertIn("new session started", client.sent[-1]["text"])

    def test_process_update_tmux_command(self):
        bot = _FakeBot()
        sessions = {7: bot}
        client = _FakeClient()
        process_update(
            {
                "update_id": 88,
                "message": {"message_id": 45, "chat": {"id": 7}, "text": "/tmux list"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )
        self.assertTrue(bot.tool_registry.calls)
        self.assertEqual(bot.tool_registry.calls[0][0], "tmux_manager")
        self.assertIn("list", bot.tool_registry.calls[0][1])
        self.assertIn("tmux sessions", client.sent[-1]["text"])

    def test_process_update_memory_status(self):
        class _Runtime:
            def memory_status(self):
                return "memory status: ok"

            def qmd_status(self):
                return "ok"

        bot = _FakeBot()
        bot.memory_runtime = _Runtime()
        sessions = {7: bot}
        client = _FakeClient()

        process_update(
            {
                "update_id": 89,
                "message": {"message_id": 46, "chat": {"id": 7}, "text": "/memory status"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertIn("memory status", client.sent[-1]["text"])

    def test_process_update_memory_sync(self):
        class _Runtime:
            def promote_braindump_to_mission(self):
                return "mission sync saved: 1 item(s)"

            def sync_mission_to_memory(self):
                return "mission memory sync saved: 1 item(s)"

            def refresh_index(self):
                return "memory index refreshed"

        bot = _FakeBot()
        bot.memory_runtime = _Runtime()
        sessions = {7: bot}
        client = _FakeClient()

        process_update(
            {
                "update_id": 90,
                "message": {"message_id": 47, "chat": {"id": 7}, "text": "/memory sync"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertIn("mission sync saved", client.sent[-1]["text"])
        self.assertIn("mission memory sync saved", client.sent[-1]["text"])

    def test_process_update_memory_doctor(self):
        class _Runtime:
            def doctor_report(self):
                return "memory doctor:\n- qmd_available: yes"

        bot = _FakeBot()
        bot.memory_runtime = _Runtime()
        sessions = {7: bot}
        client = _FakeClient()

        process_update(
            {
                "update_id": 91,
                "message": {"message_id": 48, "chat": {"id": 7}, "text": "/memory doctor"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertIn("memory doctor", client.sent[-1]["text"])

    def test_process_update_sync_commands(self):
        sessions = {}
        client = _FakeClient()

        process_update(
            {
                "update_id": 8,
                "message": {"message_id": 25, "chat": {"id": 7}, "text": "/sync_commands"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertEqual(client.commands_synced[-1], DEFAULT_TELEGRAM_COMMANDS)
        self.assertIn("command menu synced", client.sent[-1]["text"])

    def test_process_update_remember(self):
        bot = _FakeBot()
        sessions = {7: bot}
        client = _FakeClient()

        process_update(
            {
                "update_id": 9,
                "message": {"message_id": 26, "chat": {"id": 7}, "text": "/remember ship v2"},
            },
            sessions,
            client,
            lambda: _FakeBot(),
            {7},
        )

        self.assertEqual(bot.remembered[0]["note"], "ship v2")
        self.assertIn("memory saved", client.sent[-1]["text"])

    def test_process_update_nous_show(self):
        snapshot = os.environ.get("ABRAXAS_NOUS_PATH")
        try:
            with tempfile.TemporaryDirectory() as td:
                nous_path = os.path.join(td, "NOUS.md")
                with open(nous_path, "w", encoding="utf-8") as f:
                    f.write("Name: Abraxas\nMotto: Mirror + catalyst.")
                os.environ["ABRAXAS_NOUS_PATH"] = nous_path
                client = _FakeClient()

                process_update(
                    {
                        "update_id": 10,
                        "message": {"message_id": 27, "chat": {"id": 7}, "text": "/nous"},
                    },
                    {},
                    client,
                    lambda: _FakeBot(),
                    {7},
                )
                self.assertIn("Motto: Mirror + catalyst.", client.sent[-1]["text"])
        finally:
            if snapshot is None:
                os.environ.pop("ABRAXAS_NOUS_PATH", None)
            else:
                os.environ["ABRAXAS_NOUS_PATH"] = snapshot

    def test_process_update_nous_set(self):
        snapshot = os.environ.get("ABRAXAS_NOUS_PATH")
        try:
            with tempfile.TemporaryDirectory() as td:
                nous_path = os.path.join(td, "NOUS.md")
                os.environ["ABRAXAS_NOUS_PATH"] = nous_path
                client = _FakeClient()

                process_update(
                    {
                        "update_id": 11,
                        "message": {
                            "message_id": 28,
                            "chat": {"id": 7},
                            "text": "/nous set Name: Abraxas",
                        },
                    },
                    {},
                    client,
                    lambda: _FakeBot(),
                    {7},
                )

                with open(nous_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("Name: Abraxas", content)
                self.assertIn("NOUS updated", client.sent[-1]["text"])
        finally:
            if snapshot is None:
                os.environ.pop("ABRAXAS_NOUS_PATH", None)
            else:
                os.environ["ABRAXAS_NOUS_PATH"] = snapshot

    def test_process_update_nous_conversational_reinforcement(self):
        snapshot = os.environ.get("ABRAXAS_NOUS_PATH")
        try:
            with tempfile.TemporaryDirectory() as td:
                nous_path = os.path.join(td, "NOUS.md")
                with open(nous_path, "w", encoding="utf-8") as f:
                    f.write("## Identity\nName: Abraxas\n")
                os.environ["ABRAXAS_NOUS_PATH"] = nous_path
                client = _FakeClient()

                process_update(
                    {
                        "update_id": 12,
                        "message": {
                            "message_id": 29,
                            "chat": {"id": 7},
                            "text": "/nous Speak more in compressed models.",
                        },
                    },
                    {},
                    client,
                    lambda: _FakeBot(),
                    {7},
                )

                with open(nous_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("NOUS Reinforcements", content)
                self.assertIn("compressed models", content)
                self.assertIn("NOUS reinforced", client.sent[-1]["text"])
        finally:
            if snapshot is None:
                os.environ.pop("ABRAXAS_NOUS_PATH", None)
            else:
                os.environ["ABRAXAS_NOUS_PATH"] = snapshot

    def test_run_daily_memory_sync_flushes_all_sessions(self):
        class _Runtime:
            def __init__(self):
                self.promoted = 0
                self.synced = 0
                self.refreshed = 0

            def promote_braindump_to_mission(self):
                self.promoted += 1
                return "mission sync saved: 1 item(s)"

            def sync_mission_to_memory(self):
                self.synced += 1
                return "mission memory sync saved: 1 item(s)"

            def refresh_index(self):
                self.refreshed += 1
                return "memory index refreshed"

        runtime = _Runtime()
        bot1 = _FakeBot()
        bot2 = _FakeBot()
        bot1.memory_runtime = runtime
        bot2.memory_runtime = runtime
        sessions = {1: bot1, 2: bot2}

        result = run_daily_memory_sync(sessions)
        self.assertEqual(result["reason"], "daily-sync")
        self.assertEqual(result["synced_sessions"], 2)
        self.assertEqual(result["promoted_runtimes"], 1)
        self.assertEqual(result["mission_memory_synced_runtimes"], 1)
        self.assertEqual(runtime.promoted, 1)
        self.assertEqual(runtime.synced, 1)
        self.assertEqual(runtime.refreshed, 1)
        self.assertEqual(sessions[1].daily_syncs, 1)
        self.assertEqual(sessions[2].daily_syncs, 1)
        self.assertIn("errors", result)

    def test_run_daily_memory_sync_flushes_all_sessions_without_runtime(self):
        sessions = {1: _FakeBot(), 2: _FakeBot()}
        result = run_daily_memory_sync(sessions)
        self.assertEqual(result["synced_sessions"], 2)
        self.assertEqual(sessions[1].daily_syncs, 1)
        self.assertEqual(sessions[2].daily_syncs, 1)
        self.assertIn("errors", result)

    def test_run_micro_memory_sync_runs(self):
        sessions = {1: _FakeBot(), 2: _FakeBot()}
        result = run_micro_memory_sync(sessions)
        self.assertEqual(result["reason"], "micro-sync")
        self.assertEqual(result["synced_sessions"], 2)

    def test_run_weekly_memory_compound_compounds_unique_runtimes(self):
        class _Runtime:
            def __init__(self):
                self.compounded = 0
                self.refreshed = 0

            def compound_weekly_memory(self):
                self.compounded += 1
                return "ok"

            def refresh_index(self):
                self.refreshed += 1
                return "memory index refreshed"

        runtime = _Runtime()
        bot1 = _FakeBot()
        bot2 = _FakeBot()
        bot1.memory_runtime = runtime
        bot2.memory_runtime = runtime
        sessions = {1: bot1, 2: bot2}

        result = run_weekly_memory_compound(sessions)
        self.assertEqual(result["reason"], "weekly-compound")
        self.assertEqual(result["compounded_runtimes"], 1)
        self.assertEqual(runtime.compounded, 1)
        self.assertEqual(runtime.refreshed, 1)

    def test_process_update_blocks_non_whitelisted_chat(self):
        sessions = {}
        client = _FakeClient()
        factory_called = False

        def factory():
            nonlocal factory_called
            factory_called = True
            return _FakeBot()

        process_update(
            {"update_id": 3, "message": {"message_id": 15, "chat": {"id": 99}, "text": "A"}},
            sessions,
            client,
            factory,
            {7},
        )

        self.assertFalse(factory_called)
        self.assertEqual(sessions, {})
        self.assertEqual(client.sent, [])

    def test_process_update_allows_when_whitelist_is_none(self):
        sessions = {}
        client = _FakeClient()

        bot_instances = []

        def factory():
            bot = _FakeBot()
            bot_instances.append(bot)
            return bot

        process_update(
            {"update_id": 4, "message": {"message_id": 21, "chat": {"id": 99}, "text": "Hi"}},
            sessions,
            client,
            factory,
            None,
        )

        self.assertEqual(len(bot_instances), 1)
        self.assertEqual(bot_instances[0].inputs, ["Hi"])
        self.assertEqual(client.sent[0]["chat_id"], 99)


if __name__ == "__main__":
    unittest.main()
