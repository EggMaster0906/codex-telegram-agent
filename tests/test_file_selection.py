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
        MARKDOWN="Markdown"
    )
    telegram_error_module = types.ModuleType("telegram.error")
    telegram_error_module.BadRequest = Exception
    telegram_error_module.TelegramError = Exception
    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.constants"] = telegram_constants_module
    sys.modules["telegram.error"] = telegram_error_module

from app.db import Artifact
from app.file_selection import (
    FILE_PAGE_SIZE,
    build_file_keyboard,
    file_list_message,
    parse_file_callback,
)


class FileSelectionTests(unittest.TestCase):
    def test_parses_only_valid_file_callbacks(self) -> None:
        self.assertEqual(
            parse_file_callback("file:download:12").action,
            "download",
        )
        page = parse_file_callback("file:page:5:2")
        self.assertEqual((page.value, page.page), (5, 2))
        self.assertEqual(parse_file_callback("file:all:5").action, "all")
        self.assertIsNone(parse_file_callback("file:page:5:-1"))
        self.assertIsNone(parse_file_callback("file:download:0"))
        self.assertIsNone(parse_file_callback("model:download:1"))

    def test_keyboard_paginates_and_callbacks_stay_short(self) -> None:
        artifacts = [self.make_artifact(index) for index in range(1, 11)]
        keyboard = build_file_keyboard(7, artifacts, 0)
        buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
        ]
        callbacks = [button.callback_data for button in buttons]

        self.assertEqual(
            callbacks[:FILE_PAGE_SIZE],
            [f"file:download:{index}" for index in range(1, 9)],
        )
        self.assertIn("file:page:7:1", callbacks)
        self.assertIn("file:all:7", callbacks)
        self.assertTrue(all(len(callback) <= 64 for callback in callbacks))
        self.assertTrue(all(len(button.text) <= 60 for button in buttons))
        self.assertIn(
            "/file 7 <artifact_id>",
            file_list_message(7, artifacts, 0),
        )

    @staticmethod
    def make_artifact(artifact_id: int) -> Artifact:
        return Artifact(
            id=artifact_id,
            task_id=7,
            turn_id=None,
            task_dir="/tmp/task",
            display_name=f"artifacts/file-{artifact_id}.txt",
            relative_path=f"artifacts/file-{artifact_id}.txt",
            file_size=artifact_id,
            created_at="2026-06-12T00:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
