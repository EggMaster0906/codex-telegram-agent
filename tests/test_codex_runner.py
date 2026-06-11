from __future__ import annotations

import unittest
from pathlib import Path

from app.codex_runner import build_codex_command, parse_session_id


class CodexRunnerTests(unittest.TestCase):
    def test_parses_thread_started_session_id(self) -> None:
        line = (
            b'{"type":"thread.started",'
            b'"thread_id":"019eb720-5456-7283-bad3-033d70184619"}\n'
        )
        self.assertEqual(
            parse_session_id(line),
            "019eb720-5456-7283-bad3-033d70184619",
        )

    def test_ignores_invalid_or_unrelated_events(self) -> None:
        self.assertIsNone(parse_session_id(b"not-json\n"))
        self.assertIsNone(parse_session_id(b'{"type":"turn.completed"}\n'))

    def test_builds_new_and_resume_commands(self) -> None:
        common = {
            "codex_bin": "codex",
            "sandbox_mode": "workspace-write",
            "artifact_dir": Path("/tmp/artifacts"),
            "output_path": Path("/tmp/final.md"),
            "prompt": "do the work",
        }

        new_command = build_codex_command(**common, session_id=None)
        self.assertEqual(
            new_command,
            [
                "codex",
                "exec",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--add-dir",
                "/tmp/artifacts",
                "--json",
                "--output-last-message",
                "/tmp/final.md",
                "do the work",
            ],
        )

        resume_command = build_codex_command(**common, session_id="session-123")
        self.assertEqual(
            resume_command[-3:],
            ["resume", "session-123", "do the work"],
        )


if __name__ == "__main__":
    unittest.main()
