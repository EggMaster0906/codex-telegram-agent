from __future__ import annotations

import sys
import types
import unittest

try:
    import telegram  # noqa: F401
except ModuleNotFoundError:
    telegram_module = types.ModuleType("telegram")
    telegram_module.Bot = object
    telegram_constants_module = types.ModuleType("telegram.constants")
    telegram_constants_module.ParseMode = types.SimpleNamespace(
        HTML="HTML",
        MARKDOWN="Markdown",
    )

    class BadRequest(Exception):
        pass

    telegram_error_module = types.ModuleType("telegram.error")
    telegram_error_module.BadRequest = BadRequest
    telegram_error_module.TelegramError = Exception
    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.constants"] = telegram_constants_module
    sys.modules["telegram.error"] = telegram_error_module

from telegram.error import BadRequest

from app.telegram_delivery import send_markdown_text


class FakeBot:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.messages: list[tuple[int, str, str | None]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> None:
        if self.fail_once:
            self.fail_once = False
            raise BadRequest("can't parse entities")
        self.messages.append((chat_id, text, parse_mode))


class TelegramDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_markdown_as_telegram_html(self) -> None:
        bot = FakeBot()

        await send_markdown_text(bot, 123, "**bold** and `code`")

        self.assertEqual(
            bot.messages,
            [(123, "<b>bold</b> and <code>code</code>", "HTML")],
        )

    async def test_falls_back_to_plain_text_without_html_tags(self) -> None:
        bot = FakeBot(fail_once=True)

        await send_markdown_text(bot, 123, "**bold**")

        self.assertEqual(bot.messages, [(123, "bold", None)])


if __name__ == "__main__":
    unittest.main()
