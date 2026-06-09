from __future__ import annotations

from pathlib import Path

from app.db import Task


LOG_TAIL_LINES = 80
LOG_TAIL_CHARACTERS = 12_000


def read_final_output(task: Task) -> str | None:
    if not task.output_path:
        return None

    path = Path(task.output_path)
    if not path.is_file():
        return None

    return path.read_text(encoding="utf-8", errors="replace")


def read_log_tail(
    task: Task,
    *,
    line_limit: int = LOG_TAIL_LINES,
    character_limit: int = LOG_TAIL_CHARACTERS,
) -> str | None:
    if not task.log_path:
        return None

    path = Path(task.log_path)
    if not path.is_file():
        return None

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = "\n".join(lines[-line_limit:])
    if len(tail) > character_limit:
        tail = tail[-character_limit:]
    return tail


def build_followup_prompt(task: Task, final_output: str, question: str) -> str:
    return (
        f"This is a follow-up to Task #{task.id}.\n\n"
        "Original task:\n"
        f"{task.prompt}\n\n"
        "Previous final response:\n"
        f"{final_output}\n\n"
        "Follow-up request:\n"
        f"{question}"
    )
