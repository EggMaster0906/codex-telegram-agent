from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Task
from app.task_followup import read_final_output, read_log_tail


class TaskFollowupTests(unittest.TestCase):
    def test_reads_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "final.md"
            output_path.write_text("previous answer", encoding="utf-8")
            task = self.make_task(output_path=output_path)

            final_output = read_final_output(task)
            self.assertEqual(final_output, "previous answer")

    def test_reads_only_configured_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "task.log"
            log_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            task = self.make_task(log_path=log_path)

            self.assertEqual(read_log_tail(task, line_limit=2), "three\nfour")
            self.assertEqual(
                read_log_tail(task, line_limit=4, character_limit=7),
                "ee\nfour",
            )

    @staticmethod
    def make_task(
        *,
        output_path: Path | None = None,
        log_path: Path | None = None,
    ) -> Task:
        return Task(
            id=12,
            chat_id=123,
            prompt="original request",
            status="done",
            workspace_path="/tmp",
            task_dir=None,
            log_path=str(log_path) if log_path else None,
            output_path=str(output_path) if output_path else None,
            error_message=None,
            codex_session_id=None,
            parent_task_id=None,
        )


if __name__ == "__main__":
    unittest.main()
