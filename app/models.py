from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup


MODEL_CALLBACK_PREFIX = "model:"
CODEX_PROVIDER = "codex"
GEMINI_PROVIDER = "gemini"
MODEL_PROVIDER_SEPARATOR = ":"


def parse_model_list(raw: str) -> tuple[str, ...]:
    models: list[str] = []
    for item in raw.split(","):
        model = item.strip()
        if model and model not in models:
            models.append(model)
    return tuple(models)


def qualify_gemini_models(models: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        f"{GEMINI_PROVIDER}{MODEL_PROVIDER_SEPARATOR}{model}"
        for model in models
    )


def model_provider(model: str | None) -> str:
    if model and model.startswith(f"{GEMINI_PROVIDER}{MODEL_PROVIDER_SEPARATOR}"):
        return GEMINI_PROVIDER
    return CODEX_PROVIDER


def provider_model_id(model: str | None) -> str | None:
    if model is None:
        return None
    if model.startswith(f"{GEMINI_PROVIDER}{MODEL_PROVIDER_SEPARATOR}"):
        return model.split(MODEL_PROVIDER_SEPARATOR, 1)[1]
    if model.startswith(f"{CODEX_PROVIDER}{MODEL_PROVIDER_SEPARATOR}"):
        return model.split(MODEL_PROVIDER_SEPARATOR, 1)[1]
    return model


def model_label(model: str | None) -> str:
    if model is None:
        return "Codex 預設模型"
    return provider_model_id(model) or model


def resolve_model_argument(
    requested_model: str,
    available_models: tuple[str, ...],
) -> str | None:
    requested_model = requested_model.strip()
    if requested_model in available_models:
        return requested_model

    if requested_model.startswith(f"{CODEX_PROVIDER}{MODEL_PROVIDER_SEPARATOR}"):
        codex_model = requested_model.split(MODEL_PROVIDER_SEPARATOR, 1)[1]
        if codex_model in available_models:
            return codex_model

    gemini_model = f"{GEMINI_PROVIDER}{MODEL_PROVIDER_SEPARATOR}{requested_model}"
    if gemini_model in available_models:
        return gemini_model

    return None


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
            text=f"{'✓ ' if model == current_model else ''}{model_label(model)}",
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
            "請由管理者設定 CODEX_MODELS 或 ANTIGRAVITY_MODELS 模型白名單。"
        )
    current = model_label(current_model)
    return f"目前模型：{current}\n請選擇後續新 Turn 使用的模型："
