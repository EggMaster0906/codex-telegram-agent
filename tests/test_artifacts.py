from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.artifacts import (
    FINAL_OUTPUT_NAME,
    TASK_LOG_NAME,
    artifact_files,
    downloadable_files,
    prepare_task_directory,
)
from app.db import Task


class ArtifactTests(unittest.TestCase):
    def test_downloads_final_output_and_user_artifacts_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir) / "task-000001"
            artifact_dir = prepare_task_directory(task_dir, "make a report")
            log_path = task_dir / TASK_LOG_NAME
            output_path = task_dir / FINAL_OUTPUT_NAME
            report_path = artifact_dir / "report.pdf"
            direct_output_path = task_dir / "preview.png"
            hidden_path = artifact_dir / ".internal"

            log_path.write_text("log", encoding="utf-8")
            output_path.write_text("done", encoding="utf-8")
            report_path.write_bytes(b"pdf")
            direct_output_path.write_bytes(b"png")
            hidden_path.write_text("secret", encoding="utf-8")

            task = self.make_task(task_dir, log_path, output_path)

            self.assertEqual(
                artifact_files(task),
                [direct_output_path, report_path],
            )
            self.assertEqual(
                downloadable_files(task),
                [output_path, direct_output_path, report_path],
            )

    @staticmethod
    def make_task(task_dir: Path, log_path: Path, output_path: Path) -> Task:
        return Task(
            id=1,
            chat_id=123,
            prompt="make a report",
            status="done",
            workspace_path="/tmp",
            task_dir=str(task_dir),
            log_path=str(log_path),
            output_path=str(output_path),
            error_message=None,
        )


if __name__ == "__main__":
    unittest.main()
