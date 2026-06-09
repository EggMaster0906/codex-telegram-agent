from __future__ import annotations


TELEGRAM_LIMIT = 4096
SAFE_CHUNK_SIZE = 3600
COMMAND_HELP = (
    ("start", "/start", "顯示 Bot 狀態、chat ID 與授權狀態"),
    ("help", "/help", "列出目前支援的指令與功能"),
    ("run", "/run <task prompt>", "建立新的 Codex 任務"),
    ("status", "/status", "顯示最近五筆任務與狀態"),
    ("file", "/file <task_id>", "下載任務結果與產出檔案"),
    ("result", "/result <task_id>", "查看任務的文字結果"),
    ("log", "/log <task_id>", "查看任務最近的執行紀錄"),
    (
        "continue",
        "/continue <task_id> <follow-up question>",
        "針對已完成的任務建立後續提問",
    ),
)


def build_help_message() -> str:
    sections = ["目前支援的指令："]
    sections.extend(
        f"{usage}\n{description}"
        for _, usage, description in COMMAND_HELP
    )
    sections.append("任務相關指令僅限已授權的 chat 使用。")
    return "\n\n".join(sections)


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
