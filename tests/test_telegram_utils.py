from __future__ import annotations

import unittest

from app.telegram_utils import (
    COMMAND_HELP,
    build_help_message,
    prepare_telegram_markdown,
)


class TelegramUtilsTests(unittest.TestCase):
    def test_prepares_common_markdown_for_telegram(self) -> None:
        message = prepare_telegram_markdown(
            "# Heading\n\n**bold** and ~~removed~~\n\n"
            "```markdown\n# literal\n**literal**\n```"
        )

        self.assertEqual(
            message,
            "*Heading*\n\n*bold* and removed\n\n"
            "```markdown\n# literal\n**literal**\n```",
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
