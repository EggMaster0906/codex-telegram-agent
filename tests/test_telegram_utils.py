from __future__ import annotations

import unittest

from app.telegram_utils import (
    COMMAND_HELP,
    build_help_message,
    prepare_telegram_html,
    strip_telegram_html,
)


class TelegramUtilsTests(unittest.TestCase):
    def test_prepares_common_markdown_for_telegram(self) -> None:
        message = prepare_telegram_html(
            "# Heading\n\n**bold** and ~~removed~~\n\n"
            "```markdown\n# literal\n**literal**\n```"
        )

        self.assertEqual(
            message,
            "<b>Heading</b>\n\n<b>bold</b> and <s>removed</s>\n\n"
            "<pre># literal\n**literal**</pre>",
        )

    def test_prepares_gemini_markdown_for_telegram_html(self) -> None:
        message = prepare_telegram_html(
            "## Gemini 3.5 Flash\n\n"
            "- Use `agy --print`\n"
            "- Read [docs](https://example.com?a=1&b=2)\n"
            "- Keep x_y and <raw> safe\n"
            "- *italic*"
        )

        self.assertEqual(
            message,
            "<b>Gemini 3.5 Flash</b>\n\n"
            "- Use <code>agy --print</code>\n"
            '- Read <a href="https://example.com?a=1&amp;b=2">docs</a>\n'
            "- Keep x_y and &lt;raw&gt; safe\n"
            "- <i>italic</i>",
        )

    def test_strips_telegram_html_for_plain_text_fallback(self) -> None:
        self.assertEqual(
            strip_telegram_html(
                '<b>Title</b> and <a href="https://example.com">link</a>'
            ),
            "Title and link",
        )

    def test_help_message_lists_every_supported_command(self) -> None:
        message = build_help_message()

        self.assertEqual(
            {command for command, _, _ in COMMAND_HELP},
            {
                "start",
                "help",
                "new",
                "end",
                "run",
                "status",
                "usage",
                "model",
                "file",
                "result",
                "log",
                "continue",
            },
        )
        for _, usage, description in COMMAND_HELP:
            self.assertIn(usage, message)
            self.assertIn(description, message)
        self.assertIn("caption", message)


if __name__ == "__main__":
    unittest.main()
