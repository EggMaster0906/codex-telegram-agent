from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.attachments import INPUTS_DIR_NAME
from app.db import Artifact, ArtifactInput, Task, TaskStore


TASK_LOG_NAME = "task.log"
FINAL_OUTPUT_NAME = "final.md"
PROMPT_NAME = "prompt.txt"
ARTIFACTS_DIR_NAME = "artifacts"
DELIVERY_MANIFEST_NAME = ".delivery.json"


def task_directory(tasks_dir: Path, task_id: int) -> Path:
    return tasks_dir / f"task-{task_id:06d}"


def turn_directory(tasks_dir: Path, task_id: int, turn_id: int) -> Path:
    return task_directory(tasks_dir, task_id) / f"turn-{turn_id:06d}"


def prepare_task_directory(task_dir: Path, prompt: str) -> Path:
    artifact_dir = task_dir / ARTIFACTS_DIR_NAME
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_prompt(task_dir, prompt)
    return artifact_dir


def write_prompt(task_dir: Path, prompt: str) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / PROMPT_NAME).write_text(prompt + "\n", encoding="utf-8")


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
        relative_path = path.relative_to(task_dir)
        if (
            path.is_file()
            and not path.is_symlink()
            and path.resolve() not in excluded
            and relative_path.parts[0] != INPUTS_DIR_NAME
            and not any(part.startswith(".") for part in relative_path.parts)
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


def artifact_metadata(
    task: Task,
    turn_id: int | None = None,
) -> list[ArtifactInput]:
    if not task.task_dir:
        return []

    task_dir = Path(task.task_dir).resolve()
    metadata = []
    for path in downloadable_files(task):
        if path.is_symlink():
            continue
        resolved = path.resolve()
        try:
            relative_path = resolved.relative_to(task_dir)
        except ValueError:
            continue
        if not resolved.is_file():
            continue

        stat = resolved.stat()
        metadata.append(
            ArtifactInput(
                turn_id=turn_id,
                task_dir=str(task_dir),
                display_name=str(relative_path),
                relative_path=str(relative_path),
                file_size=stat.st_size,
                created_at=datetime.fromtimestamp(
                    stat.st_ctime,
                    tz=timezone.utc,
                ).isoformat(),
            )
        )
    return metadata


def sync_artifact_metadata(store: TaskStore, task: Task) -> list[Artifact]:
    turn_id = (
        store.get_turn_id_for_task_dir(task.id, task.task_dir)
        if task.task_dir
        else None
    )
    return store.sync_artifacts(
        task.id,
        artifact_metadata(task, turn_id),
    )


def resolve_artifact_path(task: Task, artifact: Artifact) -> Path | None:
    if artifact.task_id != task.id or not task.task_dir:
        return None

    task_dir = Path(task.task_dir).resolve()
    if artifact.task_dir != str(task_dir):
        return None

    relative_path = Path(artifact.relative_path)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or any(part.startswith(".") for part in relative_path.parts)
    ):
        return None

    candidate = task_dir / relative_path
    if candidate.is_symlink():
        return None
    resolved = candidate.resolve()
    try:
        resolved.relative_to(task_dir)
    except ValueError:
        return None
    if not resolved.is_file() or resolved.is_symlink():
        return None
    return resolved


def delivery_manifest_path(task_dir: Path) -> Path:
    return task_dir / ARTIFACTS_DIR_NAME / DELIVERY_MANIFEST_NAME


def delivered_artifact_files(task: Task) -> list[Path]:
    if not task.task_dir:
        return []

    task_dir = Path(task.task_dir)
    artifact_dir = (task_dir / ARTIFACTS_DIR_NAME).resolve()
    manifest_path = delivery_manifest_path(task_dir)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []

    if not isinstance(manifest, dict) or manifest.get("delivery") != "files":
        return []

    attachments = manifest.get("attachments")
    if not isinstance(attachments, list):
        return []

    files = []
    seen = set()
    for item in attachments:
        if not isinstance(item, str) or not item.strip():
            continue

        relative_path = Path(item)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or any(part.startswith(".") for part in relative_path.parts)
        ):
            continue

        candidate = artifact_dir / relative_path
        if candidate.is_symlink():
            continue

        path = candidate.resolve()
        try:
            path.relative_to(artifact_dir)
        except ValueError:
            continue

        if path in seen or not path.is_file() or path.is_symlink():
            continue

        seen.add(path)
        files.append(path)

    return files
