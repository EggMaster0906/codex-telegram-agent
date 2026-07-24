from __future__ import annotations

import sys
import types
import unittest

try:
    import telegram  # noqa: F401
except ModuleNotFoundError:
    class InlineKeyboardButton:
        def __init__(self, *, text: str, callback_data: str) -> None:
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard: list[list[object]]) -> None:
            self.inline_keyboard = inline_keyboard

    telegram_module = types.ModuleType("telegram")
    telegram_module.Bot = object
    telegram_module.InlineKeyboardButton = InlineKeyboardButton
    telegram_module.InlineKeyboardMarkup = InlineKeyboardMarkup
    telegram_constants_module = types.ModuleType("telegram.constants")
    telegram_constants_module.ParseMode = types.SimpleNamespace(
        HTML="HTML",
        MARKDOWN="Markdown",
    )
    telegram_error_module = types.ModuleType("telegram.error")
    telegram_error_module.BadRequest = Exception
    telegram_error_module.TelegramError = Exception
    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.constants"] = telegram_constants_module
    sys.modules["telegram.error"] = telegram_error_module

from app.progress import (
    build_progress_keyboard,
    progress_message,
    resolve_progress_callback,
    resolve_progress_value,
)


class ProgressTests(unittest.TestCase):
    def test_resolves_command_and_callback_values(self) -> None:
        self.assertTrue(resolve_progress_value("ON"))
        self.assertFalse(resolve_progress_value("off"))
        self.assertIsNone(resolve_progress_value("status"))
        self.assertTrue(resolve_progress_callback("progress:on"))
        self.assertFalse(resolve_progress_callback("progress:off"))
        self.assertIsNone(resolve_progress_callback("model:0"))

    def test_keyboard_marks_current_state(self) -> None:
        enabled_keyboard = build_progress_keyboard(True)
        enabled_buttons = enabled_keyboard.inline_keyboard[0]
        self.assertEqual(
            [button.callback_data for button in enabled_buttons],
            ["progress:on", "progress:off"],
        )
        self.assertEqual(enabled_buttons[0].text, "✓ 開啟")
        self.assertEqual(enabled_buttons[1].text, "關閉")

        disabled_buttons = build_progress_keyboard(False).inline_keyboard[0]
        self.assertEqual(disabled_buttons[0].text, "開啟")
        self.assertEqual(disabled_buttons[1].text, "✓ 關閉")
        self.assertIn("已關閉", progress_message(False))


if __name__ == "__main__":
    unittest.main()
