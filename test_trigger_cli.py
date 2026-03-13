import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from capabilities.trigger import TriggerRequest, build_trigger_prompt, resolve_trigger_session_id
from channel.trigger_cli import run_trigger_command


class TriggerCliTests(unittest.TestCase):
    def test_build_trigger_prompt_includes_context_and_source(self):
        prompt = build_trigger_prompt(
            TriggerRequest(
                text="Summarize the inbox",
                chat_id=42,
                context="Only mention action items.",
                source="cron",
                idempotency_key="abc123",
            )
        )

        self.assertIn("[external_trigger]", prompt)
        self.assertIn("source: cron", prompt)
        self.assertIn("chat_id: 42", prompt)
        self.assertIn("idempotency_key: abc123", prompt)
        self.assertIn("Task:", prompt)
        self.assertIn("Context:", prompt)

    def test_resolve_trigger_session_id_prefers_explicit_session(self):
        request = TriggerRequest(text="Ping", chat_id=9, session_id="manual-session")
        self.assertEqual(resolve_trigger_session_id(request), "manual-session")

    def test_run_trigger_command_requires_main_model_auth(self):
        exit_code = run_trigger_command(
            ["--text", "Ping", "--stdout-only"],
            settings_loader=lambda: {"api_key": None},
        )
        self.assertEqual(exit_code, 2)

    def test_run_trigger_command_prints_reply_when_stdout_only(self):
        replies: list[str] = []

        class _FakeBot:
            def __init__(self, session_id=None):
                self.session_id = session_id

            def ask(self, text, user_content=None):
                return f"reply:{user_content or text}"

        exit_code = run_trigger_command(
            [
                "--text",
                "Ping",
                "--context",
                "Keep it short.",
                "--source",
                "test",
                "--stdout-only",
            ],
            settings_loader=lambda: {"api_key": "token"},
            bot_factory=lambda session_id=None: _FakeBot(session_id=session_id),
            stdout_writer=replies.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(replies), 1)
        self.assertIn("[external_trigger]", replies[0])
        self.assertIn("Keep it short.", replies[0])


if __name__ == "__main__":
    unittest.main()
