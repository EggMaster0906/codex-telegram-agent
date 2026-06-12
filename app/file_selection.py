from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING

from app.db import Artifact

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup


FILE_CALLBACK_PREFIX = "file:"
FILE_PAGE_SIZE = 8
MAX_TELEGRAM_DOCUMENT_BYTES = 50 * 1024 * 1024
BUTTON_LABEL_LIMIT = 60
LIST_NAME_LIMIT = 300


@dataclass(frozen=True)
class FileCallback:
    action: str
    value: int
    page: int | None = None


def parse_file_callback(callback_data: str) -> FileCallback | None:
    parts = callback_data.split(":")
    if len(parts) not in {3, 4} or parts[0] != "file":
        return None

    action = parts[1]
    if action not in {"download", "page", "all"}:
        return None
    try:
        value = int(parts[2])
        page = int(parts[3]) if len(parts) == 4 else None
    except ValueError:
        return None
    if value <= 0:
        return None
    if action == "page":
        if page is None or page < 0:
            return None
    elif page is not None:
        return None
    return FileCallback(action=action, value=value, page=page)


def human_file_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def build_file_keyboard(
    task_id: int,
    artifacts: list[Artifact],
    page: int,
) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    page_count = max(1, ceil(len(artifacts) / FILE_PAGE_SIZE))
    page = min(max(page, 0), page_count - 1)
    start = page * FILE_PAGE_SIZE
    visible = artifacts[start : start + FILE_PAGE_SIZE]
    rows = [
        [
            InlineKeyboardButton(
                text=truncate_text(
                    f"{artifact.display_name} "
                    f"({human_file_size(artifact.file_size)})",
                    BUTTON_LABEL_LIMIT,
                ),
                callback_data=f"{FILE_CALLBACK_PREFIX}download:{artifact.id}",
            )
        ]
        for artifact in visible
    ]

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="上一頁",
                callback_data=f"{FILE_CALLBACK_PREFIX}page:{task_id}:{page - 1}",
            )
        )
    if page + 1 < page_count:
        navigation.append(
            InlineKeyboardButton(
                text="下一頁",
                callback_data=f"{FILE_CALLBACK_PREFIX}page:{task_id}:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    if len(artifacts) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="下載全部",
                    callback_data=f"{FILE_CALLBACK_PREFIX}all:{task_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def file_list_message(
    task_id: int,
    artifacts: list[Artifact],
    page: int,
) -> str:
    page_count = max(1, ceil(len(artifacts) / FILE_PAGE_SIZE))
    page = min(max(page, 0), page_count - 1)
    start = page * FILE_PAGE_SIZE
    visible = artifacts[start : start + FILE_PAGE_SIZE]
    lines = [
        f"Task #{task_id} 可下載產物（第 {page + 1}/{page_count} 頁）：",
        "",
    ]
    lines.extend(
        f"[{artifact.id}] {truncate_text(artifact.display_name, LIST_NAME_LIMIT)} "
        f"({human_file_size(artifact.file_size)})"
        for artifact in visible
    )
    lines.extend(
        [
            "",
            f"文字備援：/file {task_id} <artifact_id>",
        ]
    )
    return "\n".join(lines)
