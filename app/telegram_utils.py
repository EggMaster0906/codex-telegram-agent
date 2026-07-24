from __future__ import annotations

import html
import re


TELEGRAM_LIMIT = 4096
SAFE_CHUNK_SIZE = 3600
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
CODE_SPAN_PATTERN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
LINK_PATTERN = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
COMMAND_HELP = (
    ("start", "/start", "顯示 Bot 狀態、chat ID 與授權狀態"),
    ("help", "/help", "列出目前支援的指令與功能"),
    ("new", "/new <task prompt>", "建立新的 24 小時 Codex session"),
    ("end", "/end", "結束目前的 Codex session"),
    ("run", "/run <task prompt>", "建立獨立的舊式 Codex 任務"),
    ("status", "/status", "顯示最近五筆任務與狀態"),
    ("usage", "/usage", "顯示 Codex 與 Antigravity 剩餘用量"),
    ("model", "/model [model_id]", "查看或切換後續 Turn 使用的模型"),
    ("progress", "/progress [on|off]", "查看或切換即時任務進度"),
    ("file", "/file <task_id> [artifact_id]", "選擇並下載任務產物"),
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
    sections.append(
        "可直接傳送文件、圖片或其他附件，並用 caption 說明處理需求。"
    )
    sections.append("任務相關指令僅限已授權的 chat 使用。")
    return "\n\n".join(sections)


def prepare_telegram_html(text: str) -> str:
    """Convert common Markdown into Telegram's HTML parse mode subset."""
    lines: list[str] = []
    in_code_block = False
    code_block_lines: list[str] = []

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if in_code_block:
                code = html.escape("\n".join(code_block_lines))
                lines.append(f"<pre>{code}</pre>")
                code_block_lines = []
                in_code_block = False
            else:
                in_code_block = True
                code_block_lines = []
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        heading = HEADING_PATTERN.match(line)
        if heading:
            lines.append(f"<b>{format_inline_markdown(heading.group(1))}</b>")
            continue

        lines.append(format_inline_markdown(line))

    if in_code_block:
        code = html.escape("\n".join(code_block_lines))
        lines.append(f"<pre>{code}</pre>")

    return "\n".join(lines)


def prepare_telegram_markdown(text: str) -> str:
    return prepare_telegram_html(text)


def format_inline_markdown(text: str) -> str:
    fragments: list[str] = []

    def stash(fragment: str) -> str:
        fragments.append(fragment)
        return f"\x00{len(fragments) - 1}\x00"

    def code_replacement(match: re.Match[str]) -> str:
        return stash(f"<code>{html.escape(match.group(1))}</code>")

    def link_replacement(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        url = html.escape(match.group(2), quote=True)
        return stash(f'<a href="{url}">{label}</a>')

    text = CODE_SPAN_PATTERN.sub(code_replacement, text)
    text = LINK_PATTERN.sub(link_replacement, text)
    text = html.escape(text)

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)

    for index, fragment in enumerate(fragments):
        text = text.replace(f"\x00{index}\x00", fragment)
    return text


def strip_telegram_html(text: str) -> str:
    return html.unescape(HTML_TAG_PATTERN.sub("", text))


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
