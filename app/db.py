from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Task:
    id: int
    chat_id: int
    prompt: str
    status: str
    workspace_path: str
    task_dir: str | None
    log_path: str | None
    output_path: str | None
    error_message: str | None


class TaskStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                create table if not exists tasks (
                    id integer primary key autoincrement,
                    chat_id integer not null,
                    prompt text not null,
                    status text not null,
                    workspace_path text not null,
                    task_dir text,
                    log_path text,
                    output_path text,
                    error_message text,
                    created_at text not null,
                    started_at text,
                    finished_at text
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("pragma table_info(tasks)").fetchall()
            }
            if "task_dir" not in columns:
                conn.execute("alter table tasks add column task_dir text")

    def create_task(self, chat_id: int, prompt: str, workspace_path: Path) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                insert into tasks (chat_id, prompt, status, workspace_path, created_at)
                values (?, ?, 'pending', ?, ?)
                """,
                (chat_id, prompt, str(workspace_path), utc_now()),
            )
            return int(cursor.lastrowid)

    def next_pending(self) -> Task | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from tasks
                where status = 'pending'
                order by id asc
                limit 1
                """
            ).fetchone()
            return self._task(row) if row else None

    def set_task_dir(self, task_id: int, task_dir: Path) -> None:
        with self.connect() as conn:
            conn.execute(
                "update tasks set task_dir = ? where id = ?",
                (str(task_dir), task_id),
            )

    def get_task(self, task_id: int, chat_id: int) -> Task | None:
        with self.connect() as conn:
            row = conn.execute(
                "select * from tasks where id = ? and chat_id = ?",
                (task_id, chat_id),
            ).fetchone()
            return self._task(row) if row else None

    def mark_running(
        self,
        task_id: int,
        task_dir: Path,
        log_path: Path,
        output_path: Path,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update tasks
                set status = 'running', started_at = ?, task_dir = ?,
                    log_path = ?, output_path = ?
                where id = ?
                """,
                (
                    utc_now(),
                    str(task_dir),
                    str(log_path),
                    str(output_path),
                    task_id,
                ),
            )

    def mark_done(self, task_id: int) -> None:
        self._finish(task_id, "done", None)

    def mark_failed(self, task_id: int, error_message: str) -> None:
        self._finish(task_id, "failed", error_message)

    def recent_tasks(self, chat_id: int, limit: int = 5) -> list[Task]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from tasks
                where chat_id = ?
                order by id desc
                limit ?
                """,
                (chat_id, limit),
            ).fetchall()
            return [self._task(row) for row in rows]

    def _finish(self, task_id: int, status: str, error_message: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update tasks
                set status = ?, finished_at = ?, error_message = ?
                where id = ?
                """,
                (status, utc_now(), error_message, task_id),
            )

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            prompt=str(row["prompt"]),
            status=str(row["status"]),
            workspace_path=str(row["workspace_path"]),
            task_dir=row["task_dir"],
            log_path=row["log_path"],
            output_path=row["output_path"],
            error_message=row["error_message"],
        )
