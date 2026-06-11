from __future__ import annotations

import re


TELEGRAM_LIMIT = 4096
SAFE_CHUNK_SIZE = 3600
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
COMMAND_HELP = (
    ("start", "/start", "顯示 Bot 狀態、chat ID 與授權狀態"),
    ("help", "/help", "列出目前支援的指令與功能"),
    ("new", "/new <task prompt>", "建立新的 24 小時 Codex session"),
    ("end", "/end", "結束目前的 Codex session"),
    ("run", "/run <task prompt>", "建立獨立的舊式 Codex 任務"),
    ("status", "/status", "顯示最近五筆任務與狀態"),
    ("file", "/file <task_id>", "下載任務結果與產出檔案"),
    ("result", "/result <task_id>", "查看任務的文字結果"),
    ("log", "/log <task_id>", "查看任務最近的執行紀錄"),
    (
        "continue",
        "/continue <task_id>",
        "恢復指定 Task 的 Codex session",
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


def prepare_telegram_markdown(text: str) -> str:
    """Convert common Codex Markdown into Telegram's legacy Markdown subset."""
    lines: list[str] = []
    in_code_block = False

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            lines.append(line)
            continue

        if not in_code_block:
            heading = HEADING_PATTERN.match(line)
            if heading:
                line = f"*{heading.group(1)}*"
            line = line.replace("**", "*").replace("~~", "")

        lines.append(line)

    return "\n".join(lines)


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
