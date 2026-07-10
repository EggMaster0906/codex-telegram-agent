from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.antigravity_runner import build_antigravity_command, run_antigravity


class AntigravityRunnerTests(unittest.IsolatedAsyncioTestCase):
    def test_builds_print_command_with_model_and_dirs(self) -> None:
        command = build_antigravity_command(
            antigravity_bin="agy",
            sandbox_mode="workspace-write",
            artifact_dir=Path("/tmp/artifacts"),
            input_dir=Path("/tmp"),
            prompt="do the work",
            model="Gemini 3.5 Flash (Low)",
        )

        self.assertEqual(command[0], "agy")
        self.assertIn("--sandbox", command)
        self.assertIn("--model", command)
        self.assertEqual(
            command[command.index("--model") + 1],
            "Gemini 3.5 Flash (Low)",
        )
        self.assertEqual(command[-2:], ["--print", "do the work"])
        self.assertEqual(command.count("--add-dir"), 2)

    def test_danger_full_access_maps_to_permission_skip(self) -> None:
        command = build_antigravity_command(
            antigravity_bin="agy",
            sandbox_mode="danger-full-access",
            artifact_dir=Path("/tmp/artifacts"),
            prompt="do the work",
        )

        self.assertIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--sandbox", command)

    async def test_writes_stdout_to_final_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "fake-agy"
            script.write_text(
                """#!/usr/bin/env python3
import sys

print("final answer")
print("debug line", file=sys.stderr)
""",
                encoding="utf-8",
            )
            script.chmod(0o755)

            result = await run_antigravity(
                antigravity_bin=str(script),
                sandbox_mode="workspace-write",
                prompt="test",
                workspace_path=root,
                artifact_dir=root / "artifacts",
                log_path=root / "task.log",
                output_path=root / "final.md",
                timeout_seconds=5,
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual((root / "final.md").read_text(), "final answer\n")
            log_text = (root / "task.log").read_text()
            self.assertIn("final answer", log_text)
            self.assertIn("debug line", log_text)
            self.assertTrue((root / "artifacts" / ".delivery.json").exists())

    async def test_timeout_kills_children_that_inherit_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "fake-agy"
            script.write_text(
                """#!/usr/bin/env python3
import subprocess
import sys
import time

subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print("partial", flush=True)
time.sleep(60)
""",
                encoding="utf-8",
            )
            script.chmod(0o755)
            started_at = time.monotonic()

            result = await run_antigravity(
                antigravity_bin=str(script),
                sandbox_mode="workspace-write",
                prompt="test",
                workspace_path=root,
                artifact_dir=root / "artifacts",
                log_path=root / "task.log",
                output_path=root / "final.md",
                timeout_seconds=0.5,
            )

            self.assertEqual(result.exit_code, 124)
            self.assertLess(time.monotonic() - started_at, 2)
            self.assertIn("partial", (root / "final.md").read_text())
            self.assertIn(b"Task timed out", (root / "task.log").read_bytes())


if __name__ == "__main__":
    unittest.main()
