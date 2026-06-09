from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from app.artifacts import downloadable_files, prepare_task_directory, task_directory
from app.config import load_settings
from app.db import TaskStore
from app.task_followup import build_followup_prompt, read_final_output, read_log_tail
from app.telegram_utils import is_authorized, split_telegram_message
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


async def result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /result <task_id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /result <task_id>")
        return

    chat = update.effective_chat
    task = store.get_task(task_id, chat.id)
    if task is None:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    try:
        final_output = read_final_output(task)
    except OSError as exc:
        await update.message.reply_text(f"Could not read Task #{task_id} result: {exc}")
        return

    if final_output is None:
        await update.message.reply_text(
            f"Task #{task_id} has no final output yet (status: {task.status})."
        )
        return

    message = f"Task #{task_id} result:\n\n{final_output}"
    for chunk in split_telegram_message(message):
        await update.message.reply_text(chunk)


async def log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /log <task_id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /log <task_id>")
        return

    chat = update.effective_chat
    task = store.get_task(task_id, chat.id)
    if task is None:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    try:
        log_tail = read_log_tail(task)
    except OSError as exc:
        await update.message.reply_text(f"Could not read Task #{task_id} log: {exc}")
        return

    if log_tail is None:
        await update.message.reply_text(
            f"Task #{task_id} has no log yet (status: {task.status})."
        )
        return

    message = f"Task #{task_id} log (latest lines):\n\n{log_tail or '(empty log)'}"
    for chunk in split_telegram_message(message):
        await update.message.reply_text(chunk)


async def continue_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_auth(update):
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /continue <task_id> <follow-up question>")
        return

    try:
        parent_task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /continue <task_id> <follow-up question>")
        return

    question = " ".join(context.args[1:]).strip()
    if not question:
        await update.message.reply_text("Usage: /continue <task_id> <follow-up question>")
        return

    chat = update.effective_chat
    parent_task = store.get_task(parent_task_id, chat.id)
    if parent_task is None:
        await update.message.reply_text(f"Task #{parent_task_id} not found.")
        return

    if parent_task.status != "done":
        await update.message.reply_text(
            f"Task #{parent_task_id} is not complete (status: {parent_task.status})."
        )
        return

    try:
        final_output = read_final_output(parent_task)
    except OSError as exc:
        await update.message.reply_text(
            f"Could not read Task #{parent_task_id} result: {exc}"
        )
        return

    if final_output is None:
        await update.message.reply_text(
            f"Task #{parent_task_id} has no final output to continue from."
        )
        return

    prompt = build_followup_prompt(parent_task, final_output, question)
    task_id = store.create_task(
        chat.id,
        prompt,
        Path(parent_task.workspace_path),
        parent_task_id=parent_task.id,
    )
    task_dir = task_directory(settings.tasks_dir, task_id)
    prepare_task_directory(task_dir, prompt)
    store.set_task_dir(task_id, task_dir)
    await update.message.reply_text(
        f"Task #{task_id} queued as a follow-up to Task #{parent_task.id}."
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
    application.add_handler(CommandHandler("result", result))
    application.add_handler(CommandHandler("log", log))
    application.add_handler(CommandHandler("continue", continue_task))
    application.run_polling()


if __name__ == "__main__":
    main()
