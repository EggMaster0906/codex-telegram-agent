from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup


MODEL_CALLBACK_PREFIX = "model:"


def parse_model_list(raw: str) -> tuple[str, ...]:
    models: list[str] = []
    for item in raw.split(","):
        model = item.strip()
        if model and model not in models:
            models.append(model)
    return tuple(models)


def resolve_model_callback(
    callback_data: str,
    available_models: tuple[str, ...],
) -> str | None:
    if not callback_data.startswith(MODEL_CALLBACK_PREFIX):
        return None
    try:
        index = int(callback_data.removeprefix(MODEL_CALLBACK_PREFIX))
    except ValueError:
        return None
    if index < 0 or index >= len(available_models):
        return None
    return available_models[index]


def build_model_keyboard(
    available_models: tuple[str, ...],
    current_model: str | None,
) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = [
        InlineKeyboardButton(
            text=f"{'✓ ' if model == current_model else ''}{model}",
            callback_data=f"{MODEL_CALLBACK_PREFIX}{index}",
        )
        for index, model in enumerate(available_models)
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def model_message(
    available_models: tuple[str, ...],
    current_model: str | None,
) -> str:
    if not available_models:
        return (
            "目前未設定可切換的模型。\n"
            "請由管理者設定 CODEX_MODELS 模型白名單。"
        )
    current = current_model or "Codex 預設模型"
    return f"目前模型：{current}\n請選擇後續新 Turn 使用的模型："
