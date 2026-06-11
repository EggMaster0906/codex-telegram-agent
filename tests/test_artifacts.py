from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.artifacts import (
    DELIVERY_MANIFEST_NAME,
    FINAL_OUTPUT_NAME,
    TASK_LOG_NAME,
    artifact_files,
    delivered_artifact_files,
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
            input_path = task_dir / "inputs" / "source.pdf"

            log_path.write_text("log", encoding="utf-8")
            output_path.write_text("done", encoding="utf-8")
            report_path.write_bytes(b"pdf")
            direct_output_path.write_bytes(b"png")
            hidden_path.write_text("secret", encoding="utf-8")
            input_path.parent.mkdir()
            input_path.write_bytes(b"user upload")

            task = self.make_task(task_dir, log_path, output_path)

            self.assertEqual(
                artifact_files(task),
                [direct_output_path, report_path],
            )
            self.assertEqual(
                downloadable_files(task),
                [output_path, direct_output_path, report_path],
            )

    def test_only_delivers_manifest_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir) / "task-000001"
            artifact_dir = prepare_task_directory(task_dir, "make a presentation")
            log_path = task_dir / TASK_LOG_NAME
            output_path = task_dir / FINAL_OUTPUT_NAME
            presentation = artifact_dir / "slides.pptx"
            notes = artifact_dir / "notes.md"
            nested_image = artifact_dir / "images" / "cover.png"

            nested_image.parent.mkdir()
            presentation.write_bytes(b"pptx")
            notes.write_text("internal notes", encoding="utf-8")
            nested_image.write_bytes(b"png")
            (artifact_dir / "outside-link").symlink_to(output_path)
            (artifact_dir / DELIVERY_MANIFEST_NAME).write_text(
                json.dumps(
                    {
                        "delivery": "files",
                        "attachments": [
                            "slides.pptx",
                            "images/cover.png",
                            "slides.pptx",
                            "../final.md",
                            ".internal",
                            "missing.pdf",
                            "outside-link",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            task = self.make_task(task_dir, log_path, output_path)

            self.assertEqual(
                delivered_artifact_files(task),
                [presentation.resolve(), nested_image.resolve()],
            )

    def test_defaults_to_text_when_manifest_is_missing_or_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir) / "task-000001"
            artifact_dir = prepare_task_directory(task_dir, "answer a question")
            log_path = task_dir / TASK_LOG_NAME
            output_path = task_dir / FINAL_OUTPUT_NAME
            (artifact_dir / "unexpected.md").write_text("answer", encoding="utf-8")
            task = self.make_task(task_dir, log_path, output_path)

            self.assertEqual(delivered_artifact_files(task), [])

            manifest_path = artifact_dir / DELIVERY_MANIFEST_NAME
            manifest_path.write_text(
                '{"delivery":"text","attachments":["unexpected.md"]}',
                encoding="utf-8",
            )
            self.assertEqual(delivered_artifact_files(task), [])

            manifest_path.write_text("not json", encoding="utf-8")
            self.assertEqual(delivered_artifact_files(task), [])

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
            codex_session_id=None,
            parent_task_id=None,
        )


if __name__ == "__main__":
    unittest.main()
