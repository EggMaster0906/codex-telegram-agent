from __future__ import annotations

import unittest

try:
    import telegram  # noqa: F401
except ModuleNotFoundError:
    import sys
    import types

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

from app.models import (
    build_model_keyboard,
    model_label,
    model_provider,
    model_message,
    parse_model_list,
    provider_model_id,
    qualify_gemini_models,
    resolve_model_argument,
    resolve_model_callback,
)


class ModelTests(unittest.TestCase):
    def test_parses_unique_model_whitelist(self) -> None:
        self.assertEqual(
            parse_model_list(" gpt-a, gpt-b,gpt-a, "),
            ("gpt-a", "gpt-b"),
        )

    def test_resolves_only_valid_model_callbacks(self) -> None:
        models = ("gpt-a", "gpt-b")
        self.assertEqual(resolve_model_callback("model:1", models), "gpt-b")
        self.assertIsNone(resolve_model_callback("model:-1", models))
        self.assertIsNone(resolve_model_callback("model:2", models))
        self.assertIsNone(resolve_model_callback("artifact:0", models))

    def test_keyboard_marks_current_model_and_uses_short_callbacks(self) -> None:
        keyboard = build_model_keyboard(
            ("gpt-a", "gpt-b", "gpt-c"),
            "gpt-b",
        )

        buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertEqual([button.callback_data for button in buttons], [
            "model:0",
            "model:1",
            "model:2",
        ])
        self.assertEqual(buttons[1].text, "✓ gpt-b")

    def test_gpt_5_6_alias_resolves_to_sol(self) -> None:
        models = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")

        self.assertEqual(provider_model_id("gpt-5.6"), "gpt-5.6-sol")
        self.assertEqual(provider_model_id("codex:gpt-5.6"), "gpt-5.6-sol")
        self.assertEqual(
            resolve_model_argument("gpt-5.6", models),
            "gpt-5.6-sol",
        )
        self.assertEqual(
            resolve_model_argument("codex:gpt-5.6", models),
            "gpt-5.6-sol",
        )
        self.assertIsNone(
            resolve_model_argument(
                "codex:Gemini 3.5 Flash (Low)",
                models + ("gemini:Gemini 3.5 Flash (Low)",),
            )
        )

        keyboard = build_model_keyboard(models, "gpt-5.6")
        self.assertEqual(keyboard.inline_keyboard[0][0].text, "✓ gpt-5.6-sol")

    def test_message_explains_missing_whitelist(self) -> None:
        self.assertIn("CODEX_MODELS", model_message((), None))

    def test_gemini_models_are_qualified_and_displayed(self) -> None:
        models = ("gpt-a",) + qualify_gemini_models(("Gemini 3.5 Flash (Low)",))

        self.assertEqual(
            models,
            ("gpt-a", "gemini:Gemini 3.5 Flash (Low)"),
        )
        self.assertEqual(
            model_label("gemini:Gemini 3.5 Flash (Low)"),
            "Gemini 3.5 Flash (Low)",
        )
        self.assertEqual(
            model_provider("gemini:Gemini 3.5 Flash (Low)"),
            "gemini",
        )
        self.assertEqual(
            provider_model_id("gemini:Gemini 3.5 Flash (Low)"),
            "Gemini 3.5 Flash (Low)",
        )
        self.assertEqual(
            resolve_model_argument("Gemini 3.5 Flash (Low)", models),
            "gemini:Gemini 3.5 Flash (Low)",
        )
        self.assertEqual(resolve_model_argument("codex:gpt-a", models), "gpt-a")


if __name__ == "__main__":
    unittest.main()
