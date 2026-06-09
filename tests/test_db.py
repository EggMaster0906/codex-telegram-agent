from __future__ import annotations

import sqlite3
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
