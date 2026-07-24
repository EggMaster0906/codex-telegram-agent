from __future__ import annotations

import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from app.codex_runner import (
    build_codex_command,
    consume_process_output,
    parse_session_id,
    run_codex,
)


class CodexRunnerTests(unittest.IsolatedAsyncioTestCase):
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
            "input_dir": None,
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

        model_command = build_codex_command(
            **common,
            session_id="session-123",
            model="gpt-test",
        )
        self.assertIn("--model", model_command)
        self.assertEqual(
            model_command[model_command.index("--model") + 1],
            "gpt-test",
        )
        self.assertLess(
            model_command.index("--model"),
            model_command.index("resume"),
        )

    def test_adds_existing_input_directory(self) -> None:
        input_dir = Path("/tmp")
        command = build_codex_command(
            codex_bin="codex",
            sandbox_mode="workspace-write",
            artifact_dir=Path("/tmp/artifacts"),
            input_dir=input_dir,
            output_path=Path("/tmp/final.md"),
            prompt="inspect attachment",
            session_id=None,
        )
        self.assertIn(str(input_dir), command)

    async def test_emits_progress_but_holds_back_final_agent_message(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "session-progress"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Checking files"},
            },
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "git status"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Running tests"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Final answer"},
            },
            {"type": "turn.completed"},
        ]
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"".join(
                json.dumps(event).encode("utf-8") + b"\n"
                for event in events
            )
        )
        reader.feed_eof()
        progress: list[str] = []

        session_id = await consume_process_output(
            reader,
            io.BytesIO(),
            None,
            on_progress=progress.append,
        )

        self.assertEqual(session_id, "session-progress")
        self.assertEqual(progress, ["Checking files", "Running tests"])

    async def test_streams_json_line_larger_than_asyncio_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "fake-codex"
            script.write_text(
                """#!/usr/bin/env python3
import json
import sys

output_path = sys.argv[sys.argv.index("--output-last-message") + 1]
print('{"type":"thread.started","thread_id":"session-large"}')
print(json.dumps({"type": "item.completed", "output": "x" * 300000}))
with open(output_path, "w", encoding="utf-8") as output:
    output.write("done")
""",
                encoding="utf-8",
            )
            script.chmod(0o755)
            log_path = root / "task.log"

            result = await run_codex(
                codex_bin=str(script),
                sandbox_mode="workspace-write",
                prompt="test",
                workspace_path=root,
                artifact_dir=root / "artifacts",
                log_path=log_path,
                output_path=root / "final.md",
                timeout_seconds=5,
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.session_id, "session-large")
            self.assertGreater(log_path.stat().st_size, 300000)

    async def test_timeout_kills_children_that_inherit_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "fake-codex"
            script.write_text(
                """#!/usr/bin/env python3
import subprocess
import sys
import time

subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print('{"type":"thread.started","thread_id":"session-timeout"}', flush=True)
time.sleep(60)
""",
                encoding="utf-8",
            )
            script.chmod(0o755)
            log_path = root / "task.log"
            started_at = time.monotonic()

            result = await run_codex(
                codex_bin=str(script),
                sandbox_mode="workspace-write",
                prompt="test",
                workspace_path=root,
                artifact_dir=root / "artifacts",
                log_path=log_path,
                output_path=root / "final.md",
                timeout_seconds=0.5,
            )

            self.assertEqual(result.exit_code, 124)
            self.assertEqual(result.session_id, "session-timeout")
            self.assertLess(time.monotonic() - started_at, 2)
            self.assertIn(b"Task timed out", log_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
