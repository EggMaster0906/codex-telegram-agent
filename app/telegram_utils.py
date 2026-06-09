from __future__ import annotations


TELEGRAM_LIMIT = 4096
SAFE_CHUNK_SIZE = 3600


def split_telegram_message(text: str) -> list[str]:
    if not text:
        return ["(empty output)"]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= SAFE_CHUNK_SIZE:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, SAFE_CHUNK_SIZE)
        if split_at <= 0:
            split_at = SAFE_CHUNK_SIZE
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()

    return chunks


def is_authorized(chat_id: int, allowed_chat_ids: set[int]) -> bool:
    return chat_id in allowed_chat_ids
