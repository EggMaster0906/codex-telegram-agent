from __future__ import annotations

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
        MARKDOWN="Markdown"
    )
    telegram_error_module = types.ModuleType("telegram.error")
    telegram_error_module.BadRequest = Exception
    telegram_error_module.TelegramError = Exception
    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.constants"] = telegram_constants_module
    sys.modules["telegram.error"] = telegram_error_module

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
                [None, "Markdown", "Markdown"],
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


if __name__ == "__main__":
    unittest.main()
