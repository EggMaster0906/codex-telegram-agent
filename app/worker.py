from __future__ import annotations

import asyncio
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from app.artifacts import (
    FINAL_OUTPUT_NAME,
    TASK_LOG_NAME,
    artifact_files,
    prepare_task_directory,
    task_directory,
)
from app.codex_runner import run_codex
from app.config import Settings
from app.db import Task, TaskStore
from app.telegram_utils import split_telegram_message


class Worker:
    def __init__(self, settings: Settings, store: TaskStore, bot: Bot) -> None:
        self.settings = settings
        self.store = store
        self.bot = bot
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            task = self.store.next_pending()
            if task is None:
                await asyncio.sleep(self.settings.worker_poll_seconds)
                continue

            await self.run_task(task)

    async def run_task(self, task: Task) -> None:
        task_dir = (
            Path(task.task_dir)
            if task.task_dir
            else task_directory(self.settings.tasks_dir, task.id)
        )
        artifact_dir = prepare_task_directory(task_dir, task.prompt)
        log_path = task_dir / TASK_LOG_NAME
        output_path = task_dir / FINAL_OUTPUT_NAME
        self.store.mark_running(task.id, task_dir, log_path, output_path)
        await self.bot.send_message(task.chat_id, f"Task #{task.id} started.")

        exit_code = await run_codex(
            codex_bin=self.settings.codex_bin,
            sandbox_mode=self.settings.codex_sandbox_mode,
            prompt=task.prompt,
            workspace_path=Path(task.workspace_path),
            artifact_dir=artifact_dir,
            log_path=log_path,
            output_path=output_path,
            timeout_seconds=self.settings.task_timeout_seconds,
        )

        if exit_code == 0:
            self.store.mark_done(task.id)
            final_text = (
                output_path.read_text(encoding="utf-8", errors="replace")
                if output_path.exists()
                else "(empty output)"
            )
            header = f"Task #{task.id} done.\n\n"
            for chunk in split_telegram_message(header + final_text):
                await self.bot.send_message(task.chat_id, chunk)
            await self.send_artifacts(task, task_dir, log_path, output_path)
        else:
            message = f"Codex exited with code {exit_code}. See log: {log_path}"
            self.store.mark_failed(task.id, message)
            await self.bot.send_message(task.chat_id, f"Task #{task.id} failed.\n{message}")

    async def send_artifacts(
        self,
        task: Task,
        task_dir: Path,
        log_path: Path,
        output_path: Path,
    ) -> None:
        completed_task = Task(
            id=task.id,
            chat_id=task.chat_id,
            prompt=task.prompt,
            status="done",
            workspace_path=task.workspace_path,
            task_dir=str(task_dir),
            log_path=str(log_path),
            output_path=str(output_path),
            error_message=None,
            codex_session_id=task.codex_session_id,
            parent_task_id=task.parent_task_id,
        )
        files = artifact_files(completed_task)
        for path in files:
            try:
                with path.open("rb") as document:
                    await self.bot.send_document(
                        chat_id=task.chat_id,
                        document=document,
                        caption=f"Task #{task.id}: {path.name}",
                    )
            except (OSError, TelegramError) as exc:
                await self.bot.send_message(
                    task.chat_id,
                    f"Task #{task.id} could not send {path.name}: {exc}",
                )
