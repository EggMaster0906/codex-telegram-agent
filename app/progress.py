from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup


PROGRESS_CALLBACK_PREFIX = "progress:"


def resolve_progress_value(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    return None


def resolve_progress_callback(callback_data: str) -> bool | None:
    if not callback_data.startswith(PROGRESS_CALLBACK_PREFIX):
        return None
    return resolve_progress_value(
        callback_data.removeprefix(PROGRESS_CALLBACK_PREFIX)
    )


def build_progress_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if enabled else ''}開啟",
                    callback_data=f"{PROGRESS_CALLBACK_PREFIX}on",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if not enabled else ''}關閉",
                    callback_data=f"{PROGRESS_CALLBACK_PREFIX}off",
                ),
            ]
        ]
    )


def progress_message(enabled: bool) -> str:
    status = "已開啟" if enabled else "已關閉"
    return (
        "即時任務進度\n\n"
        "開啟後，Agent 產生的中途工作摘要會立即傳送到此聊天室。\n"
        f"目前狀態：{status}"
    )
