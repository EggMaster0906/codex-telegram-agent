from __future__ import annotations

from typing import Protocol

from telegram.constants import ParseMode
from telegram.error import BadRequest

from app.telegram_utils import (
    prepare_telegram_html,
    split_telegram_message,
    strip_telegram_html,
)


class MessageBot(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> object: ...


async def send_markdown_text(
    bot: MessageBot,
    chat_id: int,
    text: str,
) -> None:
    formatted_text = prepare_telegram_html(text)
    for chunk in split_telegram_message(formatted_text):
        try:
            await bot.send_message(
                chat_id,
                chunk,
                parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            # A malformed or split entity must not hide the result.
            await bot.send_message(chat_id, strip_telegram_html(chunk))
