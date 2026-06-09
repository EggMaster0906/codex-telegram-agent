from __future__ import annotations

import asyncio
import contextlib

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from app.artifacts import downloadable_files, prepare_task_directory, task_directory
from app.config import load_settings
from app.db import TaskStore
from app.telegram_utils import is_authorized
from app.worker import Worker


settings = load_settings()
store = TaskStore(settings.database_path)
store.init()


async def require_auth(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False

    if is_authorized(chat.id, settings.allowed_chat_ids):
        return True

    if update.message:
        await update.message.reply_text("Unauthorized chat.")
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return

    if is_authorized(chat.id, settings.allowed_chat_ids):
        status = "authorized"
    else:
        status = "unauthorized"

    await update.message.reply_text(
        f"Codex Telegram Agent is online.\nchat_id={chat.id}\nstatus={status}"
    )


async def run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Usage: /run <task prompt>")
        return

    chat = update.effective_chat
    task_id = store.create_task(chat.id, prompt, settings.default_workspace)
    task_dir = task_directory(settings.tasks_dir, task_id)
    prepare_task_directory(task_dir, prompt)
    store.set_task_dir(task_id, task_dir)
    await update.message.reply_text(f"Task #{task_id} queued.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    chat = update.effective_chat
    tasks = store.recent_tasks(chat.id)
    if not tasks:
        await update.message.reply_text("No tasks yet.")
        return

    lines = [f"#{task.id} {task.status}: {task.prompt[:80]}" for task in tasks]
    await update.message.reply_text("\n".join(lines))


async def file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /file <task_id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /file <task_id>")
        return

    chat = update.effective_chat
    task = store.get_task(task_id, chat.id)
    if task is None:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    files = downloadable_files(task)
    if not files:
        await update.message.reply_text(
            f"Task #{task_id} has no output files yet (status: {task.status})."
        )
        return

    for path in files:
        try:
            with path.open("rb") as document:
                await context.bot.send_document(
                    chat_id=chat.id,
                    document=document,
                    caption=f"Task #{task.id}: {path.name}",
                )
        except (OSError, TelegramError) as exc:
            await update.message.reply_text(
                f"Could not send {path.name}: {exc}"
            )


async def post_init(application: Application) -> None:
    worker = Worker(settings, store, application.bot)
    application.bot_data["worker"] = worker
    application.bot_data["worker_task"] = asyncio.create_task(worker.run_forever())


async def post_shutdown(application: Application) -> None:
    worker = application.bot_data.get("worker")
    if worker:
        worker.stop()

    worker_task = application.bot_data.get("worker_task")
    if worker_task:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


def main() -> None:
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("run", run))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("file", file))
    application.run_polling()


if __name__ == "__main__":
    main()
