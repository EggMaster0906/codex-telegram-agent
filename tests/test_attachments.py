from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from app.attachments import (
    attachment_from_message,
    available_input_path,
    build_attachment_prompt,
    sanitize_filename,
)


class AttachmentTests(unittest.TestCase):
    def test_extracts_document_and_largest_photo(self) -> None:
        document = types.SimpleNamespace(
            file_id="doc-id",
            file_unique_id="doc-unique",
            file_name="../../report.pdf",
            mime_type="application/pdf",
            file_size=1024,
        )
        message = types.SimpleNamespace(
            photo=None,
            document=document,
            audio=None,
            video=None,
            animation=None,
            voice=None,
            video_note=None,
            sticker=None,
        )
        attachment = attachment_from_message(message)
        self.assertEqual(attachment.file_id, "doc-id")
        self.assertEqual(attachment.filename, "report.pdf")
        self.assertEqual(attachment.file_size, 1024)

        small = types.SimpleNamespace(
            file_id="small",
            file_unique_id="small-id",
            file_size=100,
            width=100,
            height=100,
        )
        large = types.SimpleNamespace(
            file_id="large",
            file_unique_id="large-id",
            file_size=1000,
            width=1000,
            height=1000,
        )
        photo_message = types.SimpleNamespace(photo=(small, large))
        photo = attachment_from_message(photo_message)
        self.assertEqual(photo.file_id, "large")
        self.assertEqual(photo.filename, "photo-large-id.jpg")

    def test_sanitizes_names_and_avoids_collisions(self) -> None:
        self.assertEqual(sanitize_filename("..\\..\\notes.txt"), "notes.txt")
        self.assertEqual(sanitize_filename("\x00"), "attachment")

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first = available_input_path(directory, "report.pdf")
            first.write_bytes(b"first")
            second = available_input_path(directory, "report.pdf")
            self.assertEqual(second.name, "report-2.pdf")

    def test_builds_prompt_with_attachment_paths(self) -> None:
        path = Path("/tmp/task-000001/turn-000001/inputs/report.pdf")
        prompt = build_attachment_prompt("summarize this", [path])
        self.assertIn("summarize this", prompt)
        self.assertIn(str(path), prompt)


if __name__ == "__main__":
    unittest.main()
