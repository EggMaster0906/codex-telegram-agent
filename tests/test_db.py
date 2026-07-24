from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import ArtifactInput, TaskStore


class TaskStoreTests(unittest.TestCase):
    def test_init_migrates_existing_database_and_scopes_task_by_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "tasks.sqlite3"
            with sqlite3.connect(database_path) as conn:
                conn.execute(
                    """
                    create table tasks (
                        id integer primary key autoincrement,
                        chat_id integer not null,
                        prompt text not null,
                        status text not null,
                        workspace_path text not null,
                        log_path text,
                        output_path text,
                        error_message text,
                        created_at text not null,
                        started_at text,
                        finished_at text
                    )
                    """
                )
                conn.execute(
                    """
                    create table task_turns (
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
                        finished_at text
                    )
                    """
                )
                conn.execute(
                    """
                    create table chat_settings (
                        chat_id integer primary key,
                        selected_model text,
                        updated_at text not null
                    )
                    """
                )
                conn.execute(
                    """
                    insert into chat_settings (
                        chat_id, selected_model, updated_at
                    ) values (123, 'old-model', '2026-01-01T00:00:00+00:00')
                    """
                )
                conn.execute(
                    """
                    insert into tasks (
                        id, chat_id, prompt, status, workspace_path, created_at
                    )
                    values
                        (1, 123, 'old first', 'done', '/tmp', '2026-01-01T00:00:00+00:00'),
                        (2, 123, 'old second', 'done', '/tmp', '2026-01-01T00:00:00+00:00')
                    """
                )
                conn.execute(
                    """
                    insert into task_turns (
                        id, task_id, prompt, status, created_at
                    )
                    values
                        (1, 1, 'old first turn', 'done', '2026-01-01T00:00:00+00:00'),
                        (2, 1, 'old second turn', 'done', '2026-01-01T00:01:00+00:00'),
                        (3, 2, 'old other task', 'done', '2026-01-01T00:02:00+00:00')
                    """
                )

            store = TaskStore(database_path)
            store.init()
            with store.connect() as conn:
                task_columns = {
                    row["name"]
                    for row in conn.execute("pragma table_info(tasks)")
                }
                turn_columns = {
                    row["name"]
                    for row in conn.execute("pragma table_info(task_turns)")
                }
                artifact_tables = {
                    row["name"]
                    for row in conn.execute(
                        "select name from sqlite_master where type = 'table'"
                    )
                }
                chat_setting_columns = {
                    row["name"]
                    for row in conn.execute("pragma table_info(chat_settings)")
                }
            self.assertIn("model", task_columns)
            self.assertIn("model", turn_columns)
            self.assertIn("turn_number", turn_columns)
            self.assertIn("artifacts", artifact_tables)
            self.assertIn("progress_enabled", chat_setting_columns)
            self.assertFalse(store.get_progress_enabled(123))
            self.assertEqual(store.get_selected_model(123, None), "old-model")
            with store.connect() as conn:
                migrated_turn_numbers = conn.execute(
                    """
                    select task_id, id, turn_number
                    from task_turns
                    order by id
                    """
                ).fetchall()
            self.assertEqual(
                [
                    (row["task_id"], row["id"], row["turn_number"])
                    for row in migrated_turn_numbers
                ],
                [(1, 1, 1), (1, 2, 2), (2, 3, 1)],
            )
            task_id = store.create_task(123, "hello", Path("/tmp"))
            task_dir = Path(temp_dir) / "tasks" / "task-000001"
            store.set_task_dir(task_id, task_dir)

            task = store.get_task(task_id, 123)
            self.assertIsNotNone(task)
            self.assertEqual(task.task_dir, str(task_dir))
            self.assertIsNone(task.codex_session_id)
            self.assertIsNone(task.parent_task_id)
            self.assertIsNone(task.model)
            self.assertIsNone(store.get_task(task_id, 456))

            followup_id = store.create_task(
                123,
                "follow up",
                Path("/tmp"),
                parent_task_id=task_id,
                codex_session_id="session-123",
            )
            followup = store.get_task(followup_id, 123)
            self.assertIsNotNone(followup)
            self.assertEqual(followup.parent_task_id, task_id)
            self.assertEqual(followup.codex_session_id, "session-123")

    def test_session_switching_turns_and_sliding_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir) / "tasks.sqlite3")
            store.init()

            first_task_id, first_turn_id = store.create_session(
                123,
                "first",
                Path("/tmp"),
            )
            first_task = store.get_active_task(123, 86400)
            self.assertIsNotNone(first_task)
            self.assertEqual(first_task.id, first_task_id)
            self.assertTrue(store.is_first_turn(first_task_id, first_turn_id))
            self.assertEqual(store.get_turn_number(first_turn_id), 1)

            second_turn_id = store.create_turn(first_task_id, "follow up")
            self.assertFalse(store.is_first_turn(first_task_id, second_turn_id))
            self.assertEqual(store.get_turn_number(second_turn_id), 2)

            second_task_id, second_task_first_turn_id = store.create_session(
                123,
                "second",
                Path("/tmp"),
            )
            self.assertEqual(store.get_active_task(123, 86400).id, second_task_id)
            self.assertEqual(store.get_turn_number(second_task_first_turn_id), 1)
            self.assertEqual(
                store.get_task(first_task_id, 123).session_status,
                "ended",
            )

            resumed = store.activate_task(first_task_id, 123)
            self.assertIsNotNone(resumed)
            self.assertEqual(store.get_active_task(123, 86400).id, first_task_id)
            self.assertEqual(
                store.get_task(second_task_id, 123).session_status,
                "ended",
            )

            last_activity = datetime.fromisoformat(resumed.last_activity_at)
            active = store.get_active_task(
                123,
                86400,
                now=last_activity + timedelta(hours=23, minutes=59),
            )
            self.assertIsNotNone(active)

            expired = store.get_active_task(
                123,
                86400,
                now=last_activity + timedelta(hours=24),
            )
            self.assertIsNone(expired)
            self.assertEqual(
                store.get_task(first_task_id, 123).session_status,
                "expired",
            )

    def test_create_turn_resets_last_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir) / "tasks.sqlite3")
            store.init()
            task_id, _ = store.create_session(123, "first", Path("/tmp"))
            before = datetime.now(timezone.utc) - timedelta(days=1)

            with store.connect() as conn:
                conn.execute(
                    "update tasks set last_activity_at = ? where id = ?",
                    (before.isoformat(), task_id),
                )

            store.create_turn(task_id, "follow up")
            task = store.get_task(task_id, 123)
            self.assertGreater(
                datetime.fromisoformat(task.last_activity_at),
                before,
            )

    def test_reconcile_turn_directories_renumbers_existing_standard_dirs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tasks_dir = root / "tasks"
            store = TaskStore(root / "tasks.sqlite3")
            store.init()
            task_id, first_turn_id = store.create_session(
                123,
                "first",
                root,
            )
            second_turn_id = store.create_turn(task_id, "second")
            old_first_dir = tasks_dir / "task-000001" / "turn-000002"
            old_second_dir = tasks_dir / "task-000001" / "turn-000003"
            old_first_dir.mkdir(parents=True)
            old_second_dir.mkdir(parents=True)
            (old_first_dir / "task.log").write_text("first", encoding="utf-8")
            (old_first_dir / "final.md").write_text("first", encoding="utf-8")
            (old_second_dir / "task.log").write_text("second", encoding="utf-8")
            (old_second_dir / "final.md").write_text("second", encoding="utf-8")

            with store.connect() as conn:
                conn.execute(
                    """
                    update task_turns
                    set task_dir = ?, log_path = ?, output_path = ?
                    where id = ?
                    """,
                    (
                        str(old_first_dir),
                        str(old_first_dir / "task.log"),
                        str(old_first_dir / "final.md"),
                        first_turn_id,
                    ),
                )
                conn.execute(
                    """
                    update task_turns
                    set task_dir = ?, log_path = ?, output_path = ?
                    where id = ?
                    """,
                    (
                        str(old_second_dir),
                        str(old_second_dir / "task.log"),
                        str(old_second_dir / "final.md"),
                        second_turn_id,
                    ),
                )
                conn.execute(
                    """
                    update tasks
                    set task_dir = ?, log_path = ?, output_path = ?
                    where id = ?
                    """,
                    (
                        str(old_second_dir),
                        str(old_second_dir / "task.log"),
                        str(old_second_dir / "final.md"),
                        task_id,
                    ),
                )
            store.sync_artifacts(
                task_id,
                [
                    ArtifactInput(
                        turn_id=second_turn_id,
                        task_dir=str(old_second_dir),
                        display_name="artifacts/report.md",
                        relative_path="artifacts/report.md",
                        file_size=10,
                        created_at="2026-01-01T00:00:00+00:00",
                    )
                ],
            )

            moved = store.reconcile_turn_directories(tasks_dir)

            new_first_dir = tasks_dir / "task-000001" / "turn-000001"
            new_second_dir = tasks_dir / "task-000001" / "turn-000002"
            self.assertEqual(
                [(old, new) for old, new in moved],
                [
                    (old_first_dir, new_first_dir),
                    (old_second_dir, new_second_dir),
                ],
            )
            self.assertTrue((new_first_dir / "final.md").is_file())
            self.assertTrue((new_second_dir / "final.md").is_file())
            self.assertFalse(old_second_dir.exists())

            with store.connect() as conn:
                turn_dirs = conn.execute(
                    """
                    select id, task_dir, log_path, output_path
                    from task_turns
                    where task_id = ?
                    order by id
                    """,
                    (task_id,),
                ).fetchall()
            self.assertEqual(
                [row["task_dir"] for row in turn_dirs],
                [str(new_first_dir), str(new_second_dir)],
            )
            self.assertEqual(
                store.get_task(task_id, 123).output_path,
                str(new_second_dir / "final.md"),
            )
            self.assertEqual(
                store.get_artifacts(task_id)[0].task_dir,
                str(new_second_dir),
            )

    def test_uploaded_turn_is_not_pending_until_queued(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir) / "tasks.sqlite3")
            store.init()
            task_id, turn_id = store.create_session(
                123,
                "inspect attachment",
                Path("/tmp"),
                initial_status="uploading",
            )

            self.assertIsNone(store.next_pending_turn())
            store.queue_uploaded_turn(
                turn_id,
                task_id,
                "inspect attachment\n\n- /tmp/report.pdf",
            )

            turn = store.next_pending_turn()
            self.assertIsNotNone(turn)
            self.assertEqual(turn.id, turn_id)
            self.assertIn("/tmp/report.pdf", turn.prompt)
            self.assertEqual(store.get_task(task_id, 123).status, "pending")

    def test_model_preference_persists_and_turns_capture_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "tasks.sqlite3"
            store = TaskStore(database_path)
            store.init()

            self.assertEqual(
                store.get_selected_model(123, "default-model"),
                "default-model",
            )
            store.set_selected_model(123, "model-a")
            task_id, first_turn_id = store.create_session(
                123,
                "first",
                Path("/tmp"),
                model=store.get_selected_model(123, "default-model"),
            )

            store.set_selected_model(123, "model-b")
            second_turn_id = store.create_turn(
                task_id,
                "second",
                model=store.get_selected_model(123, "default-model"),
            )

            restarted_store = TaskStore(database_path)
            restarted_store.init()
            self.assertEqual(
                restarted_store.get_selected_model(123, "default-model"),
                "model-b",
            )

            first_turn = restarted_store.next_pending_turn()
            self.assertEqual(first_turn.id, first_turn_id)
            self.assertEqual(first_turn.model, "model-a")
            with restarted_store.connect() as conn:
                conn.execute(
                    "update task_turns set status = 'done' where id = ?",
                    (first_turn_id,),
                )
            second_turn = restarted_store.next_pending_turn()
            self.assertEqual(second_turn.id, second_turn_id)
            self.assertEqual(second_turn.model, "model-b")

    def test_progress_preference_defaults_off_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "tasks.sqlite3"
            store = TaskStore(database_path)
            store.init()

            self.assertFalse(store.get_progress_enabled(123))
            store.set_progress_enabled(123, True)
            store.set_selected_model(123, "model-a")

            restarted_store = TaskStore(database_path)
            restarted_store.init()
            self.assertTrue(restarted_store.get_progress_enabled(123))
            self.assertEqual(
                restarted_store.get_selected_model(123, None),
                "model-a",
            )

            restarted_store.set_progress_enabled(123, False)
            self.assertFalse(restarted_store.get_progress_enabled(123))
            self.assertEqual(
                restarted_store.get_selected_model(123, None),
                "model-a",
            )

    def test_artifact_metadata_sync_keeps_ids_and_removes_stale_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir) / "tasks.sqlite3")
            store.init()
            task_id, turn_id = store.create_session(
                123,
                "report",
                Path("/tmp"),
            )
            first = ArtifactInput(
                turn_id=turn_id,
                task_dir="/tmp/task-1",
                display_name="final.md",
                relative_path="final.md",
                file_size=10,
                created_at="2026-06-12T00:00:00+00:00",
            )
            second = ArtifactInput(
                turn_id=turn_id,
                task_dir="/tmp/task-1",
                display_name="artifacts/report.pdf",
                relative_path="artifacts/report.pdf",
                file_size=20,
                created_at="2026-06-12T00:00:00+00:00",
            )

            initial = store.sync_artifacts(task_id, [first, second])
            refreshed = store.sync_artifacts(
                task_id,
                [
                    ArtifactInput(
                        **{
                            **second.__dict__,
                            "file_size": 25,
                        }
                    )
                ],
            )

            self.assertEqual(len(initial), 2)
            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0].id, initial[1].id)
            self.assertEqual(refreshed[0].turn_id, turn_id)
            self.assertEqual(refreshed[0].file_size, 25)
            self.assertIsNone(store.get_artifact(initial[0].id))


if __name__ == "__main__":
    unittest.main()
