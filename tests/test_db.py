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
            self.assertIn("model", task_columns)
            self.assertIn("model", turn_columns)
            self.assertIn("artifacts", artifact_tables)
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

            second_turn_id = store.create_turn(first_task_id, "follow up")
            self.assertFalse(store.is_first_turn(first_task_id, second_turn_id))

            second_task_id, _ = store.create_session(123, "second", Path("/tmp"))
            self.assertEqual(store.get_active_task(123, 86400).id, second_task_id)
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
