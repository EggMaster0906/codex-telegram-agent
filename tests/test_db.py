from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import TaskStore


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

            store = TaskStore(database_path)
            store.init()
            task_id = store.create_task(123, "hello", Path("/tmp"))
            task_dir = Path(temp_dir) / "tasks" / "task-000001"
            store.set_task_dir(task_id, task_dir)

            task = store.get_task(task_id, 123)
            self.assertIsNotNone(task)
            self.assertEqual(task.task_dir, str(task_dir))
            self.assertIsNone(task.codex_session_id)
            self.assertIsNone(task.parent_task_id)
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


if __name__ == "__main__":
    unittest.main()
