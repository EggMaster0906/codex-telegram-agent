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
    model: str | None = None


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
    model: str | None = None


@dataclass(frozen=True)
class Artifact:
    id: int
    task_id: int
    turn_id: int | None
    task_dir: str
    display_name: str
    relative_path: str
    file_size: int
    created_at: str


@dataclass(frozen=True)
class ArtifactInput:
    turn_id: int | None
    task_dir: str
    display_name: str
    relative_path: str
    file_size: int
    created_at: str


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
                    model text,
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
                "model": "alter table tasks add column model text",
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
                    model text,
                    created_at text not null,
                    started_at text,
                    finished_at text,
                    foreign key (task_id) references tasks(id)
                )
                """
            )
            turn_columns = {
                str(row["name"])
                for row in conn.execute("pragma table_info(task_turns)").fetchall()
            }
            if "model" not in turn_columns:
                conn.execute("alter table task_turns add column model text")

            conn.execute(
                """
                create table if not exists chat_settings (
                    chat_id integer primary key,
                    selected_model text,
                    updated_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists artifacts (
                    id integer primary key autoincrement,
                    task_id integer not null,
                    turn_id integer,
                    task_dir text not null,
                    display_name text not null,
                    relative_path text not null,
                    file_size integer not null,
                    created_at text not null,
                    unique (task_id, task_dir, relative_path),
                    foreign key (task_id) references tasks(id),
                    foreign key (turn_id) references task_turns(id)
                )
                """
            )
            artifact_columns = {
                str(row["name"])
                for row in conn.execute("pragma table_info(artifacts)").fetchall()
            }
            if "turn_id" not in artifact_columns:
                conn.execute("alter table artifacts add column turn_id integer")

    def create_task(
        self,
        chat_id: int,
        prompt: str,
        workspace_path: Path,
        *,
        parent_task_id: int | None = None,
        codex_session_id: str | None = None,
        model: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                insert into tasks (
                    chat_id, prompt, status, workspace_path, codex_session_id,
                    parent_task_id, session_status, model, created_at
                )
                values (?, ?, 'pending', ?, ?, ?, 'ended', ?, ?)
                """,
                (
                    chat_id,
                    prompt,
                    str(workspace_path),
                    codex_session_id,
                    parent_task_id,
                    model,
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def create_session(
        self,
        chat_id: int,
        prompt: str,
        workspace_path: Path,
        *,
        initial_status: str = "pending",
        model: str | None = None,
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
                    last_activity_at, model, created_at
                )
                values (?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    chat_id,
                    prompt,
                    initial_status,
                    str(workspace_path),
                    now,
                    model,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid)
            turn_id = self._insert_turn(
                conn,
                task_id,
                prompt,
                now,
                initial_status,
                model,
            )
            return task_id, turn_id

    def create_turn(
        self,
        task_id: int,
        prompt: str,
        *,
        initial_status: str = "pending",
        model: str | None = None,
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            turn_id = self._insert_turn(
                conn,
                task_id,
                prompt,
                now,
                initial_status,
                model,
            )
            conn.execute(
                """
                update tasks
                set status = ?, error_message = null,
                    last_activity_at = ?, model = ?
                where id = ?
                """,
                (initial_status, now, model, task_id),
            )
            return turn_id

    @staticmethod
    def _insert_turn(
        conn: sqlite3.Connection,
        task_id: int,
        prompt: str,
        created_at: str,
        status: str = "pending",
        model: str | None = None,
    ) -> int:
        cursor = conn.execute(
            """
            insert into task_turns (task_id, prompt, status, model, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (task_id, prompt, status, model, created_at),
        )
        return int(cursor.lastrowid)

    def queue_uploaded_turn(
        self,
        turn_id: int,
        task_id: int,
        prompt: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update task_turns
                set prompt = ?, status = 'pending'
                where id = ? and task_id = ? and status = 'uploading'
                """,
                (prompt, turn_id, task_id),
            )
            conn.execute(
                """
                update tasks
                set status = 'pending', error_message = null
                where id = ?
                """,
                (task_id,),
            )

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

    def get_selected_model(
        self,
        chat_id: int,
        default_model: str | None,
    ) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "select selected_model from chat_settings where chat_id = ?",
                (chat_id,),
            ).fetchone()
            return str(row["selected_model"]) if row else default_model

    def set_selected_model(self, chat_id: int, model: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into chat_settings (chat_id, selected_model, updated_at)
                values (?, ?, ?)
                on conflict(chat_id) do update set
                    selected_model = excluded.selected_model,
                    updated_at = excluded.updated_at
                """,
                (chat_id, model, utc_now()),
            )

    def sync_artifacts(
        self,
        task_id: int,
        artifacts: list[ArtifactInput],
    ) -> list[Artifact]:
        keys = {
            (artifact.task_dir, artifact.relative_path)
            for artifact in artifacts
        }
        with self.connect() as conn:
            existing = conn.execute(
                """
                select id, task_dir, relative_path
                from artifacts
                where task_id = ?
                """,
                (task_id,),
            ).fetchall()
            for row in existing:
                key = (str(row["task_dir"]), str(row["relative_path"]))
                if key not in keys:
                    conn.execute(
                        "delete from artifacts where id = ?",
                        (int(row["id"]),),
                    )

            for artifact in artifacts:
                conn.execute(
                    """
                    insert into artifacts (
                        task_id, turn_id, task_dir, display_name, relative_path,
                        file_size, created_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?)
                    on conflict(task_id, task_dir, relative_path) do update set
                        turn_id = excluded.turn_id,
                        display_name = excluded.display_name,
                        file_size = excluded.file_size,
                        created_at = excluded.created_at
                    """,
                    (
                        task_id,
                        artifact.turn_id,
                        artifact.task_dir,
                        artifact.display_name,
                        artifact.relative_path,
                        artifact.file_size,
                        artifact.created_at,
                    ),
                )

            rows = conn.execute(
                """
                select * from artifacts
                where task_id = ?
                order by id asc
                """,
                (task_id,),
            ).fetchall()
            return [self._artifact(row) for row in rows]

    def get_turn_id_for_task_dir(
        self,
        task_id: int,
        task_dir: str,
    ) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select id from task_turns
                where task_id = ? and task_dir = ?
                order by id desc
                limit 1
                """,
                (task_id, task_dir),
            ).fetchone()
            return int(row["id"]) if row else None

    def get_artifacts(self, task_id: int) -> list[Artifact]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from artifacts
                where task_id = ?
                order by id asc
                """,
                (task_id,),
            ).fetchall()
            return [self._artifact(row) for row in rows]

    def get_artifact(self, artifact_id: int) -> Artifact | None:
        with self.connect() as conn:
            row = conn.execute(
                "select * from artifacts where id = ?",
                (artifact_id,),
            ).fetchone()
            return self._artifact(row) if row else None

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
            model=row["model"],
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
            model=row["model"],
        )

    @staticmethod
    def _artifact(row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=int(row["id"]),
            task_id=int(row["task_id"]),
            turn_id=int(row["turn_id"]) if row["turn_id"] is not None else None,
            task_dir=str(row["task_dir"]),
            display_name=str(row["display_name"]),
            relative_path=str(row["relative_path"]),
            file_size=int(row["file_size"]),
            created_at=str(row["created_at"]),
        )
