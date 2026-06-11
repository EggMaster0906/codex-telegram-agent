from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    codex_session_id: str | None
    parent_task_id: int | None
    session_status: str = "ended"
    last_activity_at: str | None = None


@dataclass(frozen=True)
class TaskTurn:
    id: int
    task_id: int
    prompt: str
    status: str
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
        conn.execute("pragma foreign_keys = on")
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
                    codex_session_id text,
                    parent_task_id integer,
                    session_status text not null default 'ended',
                    last_activity_at text,
                    created_at text not null,
                    started_at text,
                    finished_at text,
                    foreign key (parent_task_id) references tasks(id)
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("pragma table_info(tasks)").fetchall()
            }
            migrations = {
                "task_dir": "alter table tasks add column task_dir text",
                "codex_session_id": (
                    "alter table tasks add column codex_session_id text"
                ),
                "parent_task_id": "alter table tasks add column parent_task_id integer",
                "session_status": (
                    "alter table tasks add column session_status text "
                    "not null default 'ended'"
                ),
                "last_activity_at": (
                    "alter table tasks add column last_activity_at text"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    conn.execute(statement)

            conn.execute(
                """
                create table if not exists task_turns (
                    id integer primary key autoincrement,
                    task_id integer not null,
                    prompt text not null,
                    status text not null,
                    task_dir text,
                    log_path text,
                    output_path text,
                    error_message text,
                    created_at text not null,
                    started_at text,
                    finished_at text,
                    foreign key (task_id) references tasks(id)
                )
                """
            )

    def create_task(
        self,
        chat_id: int,
        prompt: str,
        workspace_path: Path,
        *,
        parent_task_id: int | None = None,
        codex_session_id: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                insert into tasks (
                    chat_id, prompt, status, workspace_path, codex_session_id,
                    parent_task_id, session_status, created_at
                )
                values (?, ?, 'pending', ?, ?, ?, 'ended', ?)
                """,
                (
                    chat_id,
                    prompt,
                    str(workspace_path),
                    codex_session_id,
                    parent_task_id,
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def create_session(
        self,
        chat_id: int,
        prompt: str,
        workspace_path: Path,
    ) -> tuple[int, int]:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                update tasks
                set session_status = 'ended'
                where chat_id = ? and session_status = 'active'
                """,
                (chat_id,),
            )
            cursor = conn.execute(
                """
                insert into tasks (
                    chat_id, prompt, status, workspace_path, session_status,
                    last_activity_at, created_at
                )
                values (?, ?, 'pending', ?, 'active', ?, ?)
                """,
                (chat_id, prompt, str(workspace_path), now, now),
            )
            task_id = int(cursor.lastrowid)
            turn_id = self._insert_turn(conn, task_id, prompt, now)
            return task_id, turn_id

    def create_turn(self, task_id: int, prompt: str) -> int:
        now = utc_now()
        with self.connect() as conn:
            turn_id = self._insert_turn(conn, task_id, prompt, now)
            conn.execute(
                """
                update tasks
                set status = 'pending', error_message = null,
                    last_activity_at = ?
                where id = ?
                """,
                (now, task_id),
            )
            return turn_id

    @staticmethod
    def _insert_turn(
        conn: sqlite3.Connection,
        task_id: int,
        prompt: str,
        created_at: str,
    ) -> int:
        cursor = conn.execute(
            """
            insert into task_turns (task_id, prompt, status, created_at)
            values (?, ?, 'pending', ?)
            """,
            (task_id, prompt, created_at),
        )
        return int(cursor.lastrowid)

    def next_pending_turn(self) -> TaskTurn | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from task_turns
                where status = 'pending'
                order by id asc
                limit 1
                """
            ).fetchone()
            return self._turn(row) if row else None

    def next_pending(self) -> Task | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from tasks
                where status = 'pending'
                  and not exists (
                      select 1 from task_turns where task_turns.task_id = tasks.id
                  )
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

    def set_turn_dir(self, turn_id: int, task_dir: Path) -> None:
        with self.connect() as conn:
            conn.execute(
                "update task_turns set task_dir = ? where id = ?",
                (str(task_dir), turn_id),
            )

    def set_codex_session_id(self, task_id: int, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update tasks set codex_session_id = ? where id = ?",
                (session_id, task_id),
            )

    def get_task(self, task_id: int, chat_id: int) -> Task | None:
        with self.connect() as conn:
            row = conn.execute(
                "select * from tasks where id = ? and chat_id = ?",
                (task_id, chat_id),
            ).fetchone()
            return self._task(row) if row else None

    def get_task_by_id(self, task_id: int) -> Task | None:
        with self.connect() as conn:
            row = conn.execute(
                "select * from tasks where id = ?",
                (task_id,),
            ).fetchone()
            return self._task(row) if row else None

    def is_first_turn(self, task_id: int, turn_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "select min(id) as first_id from task_turns where task_id = ?",
                (task_id,),
            ).fetchone()
            return row is not None and int(row["first_id"]) == turn_id

    def has_turns(self, task_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "select 1 from task_turns where task_id = ? limit 1",
                (task_id,),
            ).fetchone()
            return row is not None

    def get_active_task(
        self,
        chat_id: int,
        timeout_seconds: int,
        *,
        now: datetime | None = None,
    ) -> Task | None:
        current = now or datetime.now(timezone.utc)
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from tasks
                where chat_id = ? and session_status = 'active'
                order by id desc
                limit 1
                """,
                (chat_id,),
            ).fetchone()
            if row is None:
                return None

            last_activity = row["last_activity_at"]
            if last_activity:
                last_activity_at = datetime.fromisoformat(str(last_activity))
                if current - last_activity_at >= timedelta(seconds=timeout_seconds):
                    conn.execute(
                        """
                        update tasks
                        set session_status = 'expired'
                        where id = ?
                        """,
                        (int(row["id"]),),
                    )
                    return None

            return self._task(row)

    def activate_task(self, task_id: int, chat_id: int) -> Task | None:
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "select * from tasks where id = ? and chat_id = ?",
                (task_id, chat_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                update tasks
                set session_status = 'ended'
                where chat_id = ? and session_status = 'active' and id != ?
                """,
                (chat_id, task_id),
            )
            conn.execute(
                """
                update tasks
                set session_status = 'active', last_activity_at = ?
                where id = ?
                """,
                (now, task_id),
            )
            updated = dict(row)
            updated["session_status"] = "active"
            updated["last_activity_at"] = now
            return self._task(updated)

    def end_active_task(self, chat_id: int) -> Task | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from tasks
                where chat_id = ? and session_status = 'active'
                order by id desc
                limit 1
                """,
                (chat_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "update tasks set session_status = 'ended' where id = ?",
                (int(row["id"]),),
            )
            return self._task(row)

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

    def mark_turn_running(
        self,
        turn_id: int,
        task_id: int,
        task_dir: Path,
        log_path: Path,
        output_path: Path,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                update task_turns
                set status = 'running', started_at = ?, task_dir = ?,
                    log_path = ?, output_path = ?
                where id = ?
                """,
                (
                    now,
                    str(task_dir),
                    str(log_path),
                    str(output_path),
                    turn_id,
                ),
            )
            conn.execute(
                """
                update tasks
                set status = 'running', started_at = coalesce(started_at, ?),
                    task_dir = ?, log_path = ?, output_path = ?,
                    error_message = null
                where id = ?
                """,
                (
                    now,
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

    def mark_turn_done(self, turn_id: int, task_id: int) -> None:
        self._finish_turn(turn_id, task_id, "done", None)

    def mark_turn_failed(
        self,
        turn_id: int,
        task_id: int,
        error_message: str,
    ) -> None:
        self._finish_turn(turn_id, task_id, "failed", error_message)

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

    def _finish_turn(
        self,
        turn_id: int,
        task_id: int,
        status: str,
        error_message: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update task_turns
                set status = ?, finished_at = ?, error_message = ?
                where id = ?
                """,
                (status, utc_now(), error_message, turn_id),
            )
            pending = conn.execute(
                """
                select 1 from task_turns
                where task_id = ? and status = 'pending'
                limit 1
                """,
                (task_id,),
            ).fetchone()
            task_status = "pending" if pending else status
            conn.execute(
                """
                update tasks
                set status = ?, finished_at = ?, error_message = ?
                where id = ?
                """,
                (
                    task_status,
                    utc_now() if not pending else None,
                    error_message,
                    task_id,
                ),
            )

    @staticmethod
    def _task(row: sqlite3.Row | dict[str, object]) -> Task:
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
            codex_session_id=row["codex_session_id"],
            parent_task_id=row["parent_task_id"],
            session_status=str(row["session_status"]),
            last_activity_at=row["last_activity_at"],
        )

    @staticmethod
    def _turn(row: sqlite3.Row) -> TaskTurn:
        return TaskTurn(
            id=int(row["id"]),
            task_id=int(row["task_id"]),
            prompt=str(row["prompt"]),
            status=str(row["status"]),
            task_dir=row["task_dir"],
            log_path=row["log_path"],
            output_path=row["output_path"],
            error_message=row["error_message"],
        )
