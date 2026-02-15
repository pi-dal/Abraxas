import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from channel.telegram import (
    chunk_message,
    extract_message_payload,
    parse_allowed_chat_ids,
    process_update,
)


class _FakeClient:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, reply_to_message_id=None):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            }
        )


class _FakeBot:
    def __init__(self):
        self.inputs = []

    def ask(self, text):
        self.inputs.append(text)
        return f"echo:{text}"


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
