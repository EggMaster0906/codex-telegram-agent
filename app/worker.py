from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from app.attachments import input_directory
from app.antigravity_runner import run_antigravity
from app.artifacts import (
    FINAL_OUTPUT_NAME,
    TASK_LOG_NAME,
    delivered_artifact_files,
    prepare_task_directory,
    sync_artifact_metadata,
    task_directory,
    turn_directory,
)
from app.codex_runner import run_codex
from app.config import Settings
from app.db import Task, TaskStore, TaskTurn
from app.models import (
    CODEX_PROVIDER,
    GEMINI_PROVIDER,
    model_provider,
    provider_model_id,
)
from app.telegram_delivery import send_markdown_text


logger = logging.getLogger(__name__)


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
            turn = self.store.next_pending_turn()
            if turn is not None:
                await self.run_turn_safely(turn)
                continue

            task = self.store.next_pending()
            if task is not None:
                await self.run_legacy_task_safely(task)
                continue

            await asyncio.sleep(self.settings.worker_poll_seconds)

    async def run_turn_safely(self, turn: TaskTurn) -> None:
        try:
            await self.run_turn(turn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "Task #%s turn #%s crashed",
                turn.task_id,
                turn.id,
            )
            message = f"Worker error: {type(exc).__name__}: {exc}"
            self.store.mark_turn_failed(turn.id, turn.task_id, message)
            task = self.store.get_task_by_id(turn.task_id)
            if task is not None:
                await self.send_message_safely(
                    task.chat_id,
                    f"Task #{task.id} failed.\n{message}",
                )

    async def run_legacy_task_safely(self, task: Task) -> None:
        try:
            await self.run_legacy_task(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Task #%s crashed", task.id)
            message = f"Worker error: {type(exc).__name__}: {exc}"
            self.store.mark_failed(task.id, message)
            await self.send_message_safely(
                task.chat_id,
                f"Task #{task.id} failed.\n{message}",
            )

    async def send_message_safely(self, chat_id: int, text: str) -> bool:
        try:
            await self.bot.send_message(chat_id, text)
        except TelegramError:
            logger.exception("Could not send Telegram message to chat %s", chat_id)
            return False
        return True

    async def send_markdown_safely(self, chat_id: int, text: str) -> bool:
        try:
            await send_markdown_text(self.bot, chat_id, text)
        except TelegramError:
            logger.exception("Could not send Telegram result to chat %s", chat_id)
            return False
        return True

    async def run_turn(self, turn: TaskTurn) -> None:
        task = self.store.get_task_by_id(turn.task_id)
        if task is None:
            return

        if model_provider(turn.model) != CODEX_PROVIDER:
            message = (
                "Gemini model currently supports only legacy /run "
                "single-turn tasks."
            )
            self.store.mark_turn_failed(turn.id, task.id, message)
            await self.send_message_safely(
                task.chat_id,
                f"Task #{task.id} could not continue.\n{message}",
            )
            return

        first_turn = self.store.is_first_turn(task.id, turn.id)
        if not first_turn and not task.codex_session_id:
            message = "Codex session is unavailable because its first turn did not start."
            self.store.mark_turn_failed(turn.id, task.id, message)
            await self.send_message_safely(
                task.chat_id,
                f"Task #{task.id} could not continue.\n{message}",
            )
            return

        turn_dir = (
            Path(turn.task_dir)
            if turn.task_dir
            else turn_directory(self.settings.tasks_dir, task.id, turn.turn_number)
        )
        artifact_dir = prepare_task_directory(turn_dir, turn.prompt)
        log_path = turn_dir / TASK_LOG_NAME
        output_path = turn_dir / FINAL_OUTPUT_NAME
        self.store.mark_turn_running(
            turn.id,
            task.id,
            turn_dir,
            log_path,
            output_path,
        )

        if first_turn:
            await self.send_message_safely(
                task.chat_id,
                f"Task #{task.id} started.",
            )

        result = await run_codex(
            codex_bin=self.settings.codex_bin,
            sandbox_mode=self.settings.codex_sandbox_mode,
            prompt=turn.prompt,
            workspace_path=Path(task.workspace_path),
            artifact_dir=artifact_dir,
            input_dir=input_directory(turn_dir),
            log_path=log_path,
            output_path=output_path,
            timeout_seconds=self.settings.task_timeout_seconds,
            session_id=task.codex_session_id,
            model=provider_model_id(turn.model),
        )

        if result.session_id:
            self.store.set_codex_session_id(task.id, result.session_id)

        if result.exit_code == 0 and result.session_id:
            self.store.mark_turn_done(turn.id, task.id)
            final_text = (
                output_path.read_text(encoding="utf-8", errors="replace")
                if output_path.exists()
                else "(empty output)"
            )
            await self.send_markdown_safely(task.chat_id, final_text)
            completed_task = Task(
                id=task.id,
                chat_id=task.chat_id,
                prompt=turn.prompt,
                status="done",
                workspace_path=task.workspace_path,
                task_dir=str(turn_dir),
                log_path=str(log_path),
                output_path=str(output_path),
                error_message=None,
                codex_session_id=result.session_id,
                parent_task_id=task.parent_task_id,
                session_status=task.session_status,
                last_activity_at=task.last_activity_at,
                model=turn.model,
            )
            sync_artifact_metadata(self.store, completed_task)
            await self.send_artifacts(completed_task)
            return

        if result.exit_code == 0:
            message = "Codex completed without returning a resumable session ID."
        else:
            message = f"Codex exited with code {result.exit_code}. See log: {log_path}"
        self.store.mark_turn_failed(turn.id, task.id, message)
        await self.send_message_safely(
            task.chat_id,
            f"Task #{task.id} failed.\n{message}",
        )

    async def run_legacy_task(self, task: Task) -> None:
        task_dir = (
            Path(task.task_dir)
            if task.task_dir
            else task_directory(self.settings.tasks_dir, task.id)
        )
        artifact_dir = prepare_task_directory(task_dir, task.prompt)
        log_path = task_dir / TASK_LOG_NAME
        output_path = task_dir / FINAL_OUTPUT_NAME
        self.store.mark_running(task.id, task_dir, log_path, output_path)
        await self.send_message_safely(
            task.chat_id,
            f"Task #{task.id} started.",
        )

        provider = model_provider(task.model)
        if provider == GEMINI_PROVIDER:
            result = await run_antigravity(
                antigravity_bin=self.settings.antigravity_bin,
                sandbox_mode=self.settings.antigravity_sandbox_mode,
                prompt=task.prompt,
                workspace_path=Path(task.workspace_path),
                artifact_dir=artifact_dir,
                input_dir=input_directory(task_dir),
                log_path=log_path,
                output_path=output_path,
                timeout_seconds=self.settings.task_timeout_seconds,
                model=provider_model_id(task.model),
            )
            if result.exit_code == 0:
                self.store.mark_done(task.id)
                final_text = (
                    output_path.read_text(encoding="utf-8", errors="replace")
                    if output_path.exists()
                    else "(empty output)"
                )
                header = f"Task #{task.id} done.\n\n"
                await self.send_markdown_safely(
                    task.chat_id,
                    header + final_text,
                )
                completed_task = Task(
                    **{
                        **task.__dict__,
                        "status": "done",
                        "task_dir": str(task_dir),
                        "log_path": str(log_path),
                        "output_path": str(output_path),
                    }
                )
                sync_artifact_metadata(self.store, completed_task)
                await self.send_artifacts(completed_task)
                return

            message = (
                f"Antigravity exited with code {result.exit_code}. "
                f"See log: {log_path}"
            )
            self.store.mark_failed(task.id, message)
            await self.send_message_safely(
                task.chat_id,
                f"Task #{task.id} failed.\n{message}",
            )
            return

        result = await run_codex(
            codex_bin=self.settings.codex_bin,
            sandbox_mode=self.settings.codex_sandbox_mode,
            prompt=task.prompt,
            workspace_path=Path(task.workspace_path),
            artifact_dir=artifact_dir,
            input_dir=input_directory(task_dir),
            log_path=log_path,
            output_path=output_path,
            timeout_seconds=self.settings.task_timeout_seconds,
            model=provider_model_id(task.model),
        )

        if result.exit_code == 0:
            if result.session_id:
                self.store.set_codex_session_id(task.id, result.session_id)
            self.store.mark_done(task.id)
            final_text = (
                output_path.read_text(encoding="utf-8", errors="replace")
                if output_path.exists()
                else "(empty output)"
            )
            header = f"Task #{task.id} done.\n\n"
            await self.send_markdown_safely(
                task.chat_id,
                header + final_text,
            )
            completed_task = Task(
                **{
                    **task.__dict__,
                    "status": "done",
                    "task_dir": str(task_dir),
                    "log_path": str(log_path),
                    "output_path": str(output_path),
                    "codex_session_id": result.session_id,
                }
            )
            sync_artifact_metadata(self.store, completed_task)
            await self.send_artifacts(completed_task)
        else:
            message = f"Codex exited with code {result.exit_code}. See log: {log_path}"
            self.store.mark_failed(task.id, message)
            await self.send_message_safely(
                task.chat_id,
                f"Task #{task.id} failed.\n{message}",
            )

    async def send_artifacts(self, task: Task) -> None:
        files = delivered_artifact_files(task)
        for path in files:
            try:
                with path.open("rb") as document:
                    await self.bot.send_document(
                        chat_id=task.chat_id,
                        document=document,
                        caption=f"Task #{task.id}: {path.name}",
                    )
            except (OSError, TelegramError) as exc:
                await self.send_message_safely(
                    task.chat_id,
                    f"Task #{task.id} could not send {path.name}: {exc}",
                )
