from __future__ import annotations

from pathlib import Path

from app.db import Task


TASK_LOG_NAME = "task.log"
FINAL_OUTPUT_NAME = "final.md"
PROMPT_NAME = "prompt.txt"
ARTIFACTS_DIR_NAME = "artifacts"


def task_directory(tasks_dir: Path, task_id: int) -> Path:
    return tasks_dir / f"task-{task_id:06d}"


def prepare_task_directory(task_dir: Path, prompt: str) -> Path:
    artifact_dir = task_dir / ARTIFACTS_DIR_NAME
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / PROMPT_NAME).write_text(prompt + "\n", encoding="utf-8")
    return artifact_dir


def artifact_files(task: Task) -> list[Path]:
    if not task.task_dir:
        return []

    task_dir = Path(task.task_dir)
    if not task_dir.is_dir():
        return []

    excluded = {
        Path(task.log_path).resolve() if task.log_path else None,
        Path(task.output_path).resolve() if task.output_path else None,
        (task_dir / PROMPT_NAME).resolve(),
    }
    files = []
    for path in task_dir.rglob("*"):
        if (
            path.is_file()
            and not path.is_symlink()
            and path.resolve() not in excluded
            and not any(part.startswith(".") for part in path.relative_to(task_dir).parts)
        ):
            files.append(path)
    return sorted(
        files,
        key=lambda path: (
            len(path.relative_to(task_dir).parts),
            str(path.relative_to(task_dir)),
        ),
    )


def downloadable_files(task: Task) -> list[Path]:
    files = []
    if task.output_path:
        output_path = Path(task.output_path)
        if output_path.is_file() and not output_path.is_symlink():
            files.append(output_path)
    files.extend(artifact_files(task))
    return files
