from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv_module

try:
    import telegram  # noqa: F401
except ModuleNotFoundError:
    telegram_module = types.ModuleType("telegram")
    telegram_module.Bot = object
    telegram_constants_module = types.ModuleType("telegram.constants")
    telegram_constants_module.ParseMode = types.SimpleNamespace(
        HTML="HTML",
        MARKDOWN="Markdown"
    )
    telegram_error_module = types.ModuleType("telegram.error")
    telegram_error_module.BadRequest = Exception
    telegram_error_module.TelegramError = Exception
    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.constants"] = telegram_constants_module
    sys.modules["telegram.error"] = telegram_error_module

from app.antigravity_runner import AntigravityResult
from app.codex_runner import CodexResult
from app.config import Settings
from app.db import TaskStore
from app.worker import Worker


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.parse_modes: list[str | None] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> None:
        self.messages.append((chat_id, text))
        self.parse_modes.append(parse_mode)

    async def send_document(self, **kwargs: object) -> None:
        raise AssertionError("No document should be sent in this test")


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_continues_after_one_turn_crashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root / "tasks.sqlite3")
            store.init()
            first_task_id, _ = store.create_session(123, "first", root)
            second_task_id, _ = store.create_session(123, "second", root)
            settings = Settings(
                telegram_bot_token="test",
                allowed_chat_ids={123},
                default_workspace=root,
                codex_bin="codex",
                codex_sandbox_mode="workspace-write",
                task_timeout_seconds=60,
                database_path=root / "tasks.sqlite3",
                tasks_dir=root / "tasks",
                worker_poll_seconds=0.01,
                session_timeout_seconds=86400,
            )
            worker = Worker(settings, store, FakeBot())
            calls = 0
            output_paths: list[Path] = []
            second_completed = asyncio.Event()

            async def fake_run_codex(**kwargs: object) -> CodexResult:
                nonlocal calls
                calls += 1
                output_path = kwargs["output_path"]
                assert isinstance(output_path, Path)
                output_paths.append(output_path)
                if calls == 1:
                    raise RuntimeError("broken output stream")

                artifact_dir = kwargs["artifact_dir"]
                assert isinstance(artifact_dir, Path)
                output_path.write_text("done", encoding="utf-8")
                (artifact_dir / ".delivery.json").write_text(
                    json.dumps({"delivery": "text", "attachments": []}),
                    encoding="utf-8",
                )
                second_completed.set()
                return CodexResult(0, "session-2")

            with patch("app.worker.run_codex", side_effect=fake_run_codex):
                worker_task = asyncio.create_task(worker.run_forever())
                await asyncio.wait_for(second_completed.wait(), timeout=1)
                while store.get_task(second_task_id, 123).status != "done":
                    await asyncio.sleep(0)
                worker.stop()
                await worker_task

            self.assertEqual(store.get_task(first_task_id, 123).status, "failed")
            self.assertEqual(store.get_task(second_task_id, 123).status, "done")
            self.assertEqual(
                [
                    path.relative_to(root / "tasks").as_posix()
                    for path in output_paths
                ],
                [
                    "task-000001/turn-000001/final.md",
                    "task-000002/turn-000001/final.md",
                ],
            )

    async def test_followup_turn_resumes_saved_codex_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root / "tasks.sqlite3")
            store.init()
            task_id, first_turn_id = store.create_session(
                123,
                "first prompt",
                root,
            )
            settings = Settings(
                telegram_bot_token="test",
                allowed_chat_ids={123},
                default_workspace=root,
                codex_bin="codex",
                codex_sandbox_mode="workspace-write",
                task_timeout_seconds=60,
                database_path=root / "tasks.sqlite3",
                tasks_dir=root / "tasks",
                worker_poll_seconds=0.01,
                session_timeout_seconds=86400,
            )
            bot = FakeBot()
            worker = Worker(settings, store, bot)
            second_turn_id = store.create_turn(task_id, "second prompt")
            output_paths: list[Path] = []

            async def fake_run_codex(**kwargs: object) -> CodexResult:
                output_path = kwargs["output_path"]
                artifact_dir = kwargs["artifact_dir"]
                assert isinstance(output_path, Path)
                assert isinstance(artifact_dir, Path)
                output_paths.append(output_path)
                output_path.write_text("done", encoding="utf-8")
                (artifact_dir / ".delivery.json").write_text(
                    json.dumps({"delivery": "text", "attachments": []}),
                    encoding="utf-8",
                )
                session_id = kwargs["session_id"] or "session-123"
                return CodexResult(0, str(session_id))

            mocked_runner = AsyncMock(side_effect=fake_run_codex)
            with patch("app.worker.run_codex", mocked_runner):
                await worker.run_turn(store.next_pending_turn())
                self.assertEqual(
                    store.get_task(task_id, 123).codex_session_id,
                    "session-123",
                )
                self.assertEqual(store.get_task(task_id, 123).status, "pending")
                second_turn = store.next_pending_turn()
                self.assertEqual(second_turn.id, second_turn_id)
                await worker.run_turn(second_turn)

            self.assertEqual(first_turn_id, 1)
            self.assertEqual(store.get_task(task_id, 123).status, "done")
            self.assertIsNone(mocked_runner.await_args_list[0].kwargs["session_id"])
            self.assertEqual(
                mocked_runner.await_args_list[1].kwargs["session_id"],
                "session-123",
            )
            self.assertIsNone(mocked_runner.await_args_list[0].kwargs["model"])
            self.assertEqual(
                [text for _, text in bot.messages],
                ["Task #1 started.", "done", "done"],
            )
            self.assertEqual(
                bot.parse_modes,
                [None, "HTML", "HTML"],
            )
            self.assertEqual(
                [
                    path.relative_to(root / "tasks").as_posix()
                    for path in output_paths
                ],
                [
                    "task-000001/turn-000001/final.md",
                    "task-000001/turn-000002/final.md",
                ],
            )
            self.assertEqual(
                [
                    path.relative_to(root / "tasks").as_posix()
                    for path in output_paths
                ],
                [
                    "task-000001/turn-000001/final.md",
                    "task-000001/turn-000002/final.md",
                ],
            )

    async def test_failed_first_turn_keeps_session_for_queued_followup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root / "tasks.sqlite3")
            store.init()
            task_id, _ = store.create_session(123, "first prompt", root)
            store.create_turn(task_id, "second prompt")
            settings = Settings(
                telegram_bot_token="test",
                allowed_chat_ids={123},
                default_workspace=root,
                codex_bin="codex",
                codex_sandbox_mode="workspace-write",
                task_timeout_seconds=60,
                database_path=root / "tasks.sqlite3",
                tasks_dir=root / "tasks",
                worker_poll_seconds=0.01,
                session_timeout_seconds=86400,
            )
            bot = FakeBot()
            worker = Worker(settings, store, bot)

            async def fake_run_codex(**kwargs: object) -> CodexResult:
                if kwargs["session_id"] is None:
                    return CodexResult(124, "session-after-timeout")

                output_path = kwargs["output_path"]
                artifact_dir = kwargs["artifact_dir"]
                assert isinstance(output_path, Path)
                assert isinstance(artifact_dir, Path)
                output_path.write_text("recovered", encoding="utf-8")
                (artifact_dir / ".delivery.json").write_text(
                    json.dumps({"delivery": "text", "attachments": []}),
                    encoding="utf-8",
                )
                return CodexResult(0, str(kwargs["session_id"]))

            mocked_runner = AsyncMock(side_effect=fake_run_codex)
            with patch("app.worker.run_codex", mocked_runner):
                await worker.run_turn(store.next_pending_turn())
                self.assertEqual(
                    store.get_task(task_id, 123).codex_session_id,
                    "session-after-timeout",
                )
                await worker.run_turn(store.next_pending_turn())

            self.assertEqual(
                mocked_runner.await_args_list[1].kwargs["session_id"],
                "session-after-timeout",
            )
            self.assertEqual(store.get_task(task_id, 123).status, "done")

    async def test_turn_uses_its_captured_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root / "tasks.sqlite3")
            store.init()
            store.create_session(
                123,
                "first prompt",
                root,
                model="gpt-test",
            )
            settings = Settings(
                telegram_bot_token="test",
                allowed_chat_ids={123},
                default_workspace=root,
                codex_bin="codex",
                codex_sandbox_mode="workspace-write",
                task_timeout_seconds=60,
                database_path=root / "tasks.sqlite3",
                tasks_dir=root / "tasks",
                worker_poll_seconds=0.01,
                session_timeout_seconds=86400,
                available_models=("gpt-test",),
                default_model="gpt-test",
            )
            worker = Worker(settings, store, FakeBot())

            async def fake_run_codex(**kwargs: object) -> CodexResult:
                output_path = kwargs["output_path"]
                artifact_dir = kwargs["artifact_dir"]
                assert isinstance(output_path, Path)
                assert isinstance(artifact_dir, Path)
                output_path.write_text("done", encoding="utf-8")
                (artifact_dir / ".delivery.json").write_text(
                    json.dumps({"delivery": "text", "attachments": []}),
                    encoding="utf-8",
                )
                return CodexResult(0, "session-123")

            mocked_runner = AsyncMock(side_effect=fake_run_codex)
            with patch("app.worker.run_codex", mocked_runner):
                await worker.run_turn(store.next_pending_turn())

            self.assertEqual(
                mocked_runner.await_args.kwargs["model"],
                "gpt-test",
            )

    async def test_legacy_run_dispatches_agy_model_to_antigravity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root / "tasks.sqlite3")
            store.init()
            task_id = store.create_task(
                123,
                "single prompt",
                root,
                model="agy:Claude Sonnet 4.6 (Thinking)",
            )
            settings = Settings(
                telegram_bot_token="test",
                allowed_chat_ids={123},
                default_workspace=root,
                codex_bin="codex",
                codex_sandbox_mode="workspace-write",
                task_timeout_seconds=60,
                database_path=root / "tasks.sqlite3",
                tasks_dir=root / "tasks",
                worker_poll_seconds=0.01,
                session_timeout_seconds=86400,
                antigravity_bin="agy",
                antigravity_sandbox_mode="workspace-write",
            )
            worker = Worker(settings, store, FakeBot())

            async def fake_run_antigravity(**kwargs: object) -> AntigravityResult:
                output_path = kwargs["output_path"]
                artifact_dir = kwargs["artifact_dir"]
                assert isinstance(output_path, Path)
                assert isinstance(artifact_dir, Path)
                output_path.write_text("claude done", encoding="utf-8")
                (artifact_dir / ".delivery.json").write_text(
                    json.dumps({"delivery": "text", "attachments": []}),
                    encoding="utf-8",
                )
                return AntigravityResult(0)

            mocked_codex = AsyncMock()
            mocked_agy = AsyncMock(side_effect=fake_run_antigravity)
            with patch("app.worker.run_codex", mocked_codex), patch(
                "app.worker.run_antigravity",
                mocked_agy,
            ):
                await worker.run_legacy_task(store.get_task(task_id, 123))

            mocked_codex.assert_not_awaited()
            self.assertEqual(
                mocked_agy.await_args.kwargs["model"],
                "Claude Sonnet 4.6 (Thinking)",
            )
            self.assertEqual(store.get_task(task_id, 123).status, "done")

    async def test_agy_turn_is_rejected_until_multiturn_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root / "tasks.sqlite3")
            store.init()
            store.create_session(
                123,
                "first prompt",
                root,
                model="agy:Claude Sonnet 4.6 (Thinking)",
            )
            settings = Settings(
                telegram_bot_token="test",
                allowed_chat_ids={123},
                default_workspace=root,
                codex_bin="codex",
                codex_sandbox_mode="workspace-write",
                task_timeout_seconds=60,
                database_path=root / "tasks.sqlite3",
                tasks_dir=root / "tasks",
                worker_poll_seconds=0.01,
                session_timeout_seconds=86400,
                antigravity_bin="agy",
                antigravity_sandbox_mode="workspace-write",
            )
            bot = FakeBot()
            worker = Worker(settings, store, bot)

            with patch("app.worker.run_codex", AsyncMock()) as mocked_codex, patch(
                "app.worker.run_antigravity",
                AsyncMock(),
            ) as mocked_agy:
                await worker.run_turn(store.next_pending_turn())

            mocked_codex.assert_not_awaited()
            mocked_agy.assert_not_awaited()
            self.assertEqual(store.get_task(1, 123).status, "failed")
            self.assertIn("only legacy /run", bot.messages[-1][1])


if __name__ == "__main__":
    unittest.main()
