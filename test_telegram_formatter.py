import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from channel.telegram_formatter import (
    escape_markdown_v2,
    format_response,
    render_telegram_message,
)


class TelegramFormatterTests(unittest.TestCase):
    def test_escape_markdown_v2_escapes_special_chars(self):
        self.assertEqual(
            escape_markdown_v2("*[]()~`>#+-=|{}.!"),
            r"\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!",
        )

    def test_render_telegram_message_prefers_html_mode(self):
        rendered = render_telegram_message("**bold** text")
        self.assertEqual(rendered.parse_mode, "HTML")
        self.assertIn("<b>bold</b>", rendered.text)
        self.assertEqual(rendered.fallback_text, "**bold** text")

    def test_format_response_returns_plain_text_when_formatting_disabled(self):
        self.assertEqual(format_response("**bold**", use_formatting=False), "**bold**")


if __name__ == "__main__":
    unittest.main()
