from __future__ import annotations

import asyncio
import contextlib

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.attachments import (
    DEFAULT_ATTACHMENT_PROMPT,
    MAX_TELEGRAM_DOWNLOAD_BYTES,
    attachment_from_message,
    available_input_path,
    build_attachment_prompt,
    input_directory,
    parse_attachment_caption,
)
from app.artifacts import (
    prepare_task_directory,
    resolve_artifact_path,
    sync_artifact_metadata,
    task_directory,
    turn_directory,
    write_prompt,
)
from app.config import load_settings
from app.db import TaskStore
from app.file_selection import (
    FILE_CALLBACK_PREFIX,
    MAX_TELEGRAM_DOCUMENT_BYTES,
    build_file_keyboard,
    file_list_message,
    human_file_size,
    parse_file_callback,
    truncate_text,
)
from app.models import (
    CODEX_PROVIDER,
    MODEL_CALLBACK_PREFIX,
    build_model_keyboard,
    model_label,
    model_message,
    model_provider,
    resolve_model_argument,
    resolve_model_callback,
)
from app.progress import (
    PROGRESS_CALLBACK_PREFIX,
    build_progress_keyboard,
    progress_message,
    resolve_progress_callback,
    resolve_progress_value,
)
from app.task_followup import read_final_output, read_log_tail
from app.telegram_delivery import send_markdown_text
from app.telegram_utils import (
    COMMAND_HELP,
    build_help_message,
    is_authorized,
    split_telegram_message,
)
from app.usage import (
    build_usage_message,
    query_antigravity_usage,
    query_codex_usage,
)
from app.worker import Worker


settings = load_settings()
store = TaskStore(settings.database_path)
store.init()
store.reconcile_turn_directories(settings.tasks_dir)


def selected_model_for_chat(chat_id: int) -> str | None:
    selected_model = store.get_selected_model(chat_id, settings.default_model)
    if selected_model is not None:
        resolved_model = resolve_model_argument(
            selected_model,
            settings.available_models,
        )
        if resolved_model is not None:
            return resolved_model
    return settings.default_model


async def reject_non_codex_session_model(
    update: Update,
    model: str | None,
) -> bool:
    if model_provider(model) == CODEX_PROVIDER:
        return False
    if update.message is None:
        return True
    await update.message.reply_text(
        f"目前 {model_label(model)} 僅支援 /run 單輪任務。\n"
        "多輪對話請先用 /model 切回 Codex 模型。"
    )
    return True


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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(build_help_message())


async def run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Usage: /run <task prompt>")
        return

    chat = update.effective_chat
    selected_model = selected_model_for_chat(chat.id)
    task_id = store.create_task(
        chat.id,
        prompt,
        settings.default_workspace,
        model=selected_model,
    )
    task_dir = task_directory(settings.tasks_dir, task_id)
    prepare_task_directory(task_dir, prompt)
    store.set_task_dir(task_id, task_dir)
    await update.message.reply_text(
        f"Task #{task_id} queued (model: {model_label(selected_model)})."
    )


async def new_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_auth(update):
        return

    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Usage: /new <task prompt>")
        return

    chat = update.effective_chat
    selected_model = selected_model_for_chat(chat.id)
    if await reject_non_codex_session_model(update, selected_model):
        return
    task_id, turn_id = store.create_session(
        chat.id,
        prompt,
        settings.default_workspace,
        model=selected_model,
    )
    turn_number = store.get_turn_number(turn_id)
    if turn_number is None:
        await update.message.reply_text(f"Task #{task_id} could not create turn.")
        return
    turn_dir = turn_directory(settings.tasks_dir, task_id, turn_number)
    prepare_task_directory(turn_dir, prompt)
    store.set_turn_dir(turn_id, turn_dir)
    await update.message.reply_text(f"Task #{task_id} queued as a new session.")


async def end_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_auth(update):
        return

    chat = update.effective_chat
    task = store.end_active_task(chat.id)
    if task is None:
        await update.message.reply_text("目前沒有進行中的 session。")
        return

    await update.message.reply_text(f"Task #{task.id} session ended.")


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_auth(update):
        return
    if update.message is None or update.message.text is None:
        return

    prompt = update.message.text.strip()
    if not prompt:
        return

    chat = update.effective_chat
    task = store.get_active_task(
        chat.id,
        settings.session_timeout_seconds,
    )
    if task is None:
        await update.message.reply_text(
            "目前沒有可接續的 session，或上一個 session 已閒置超過 24 小時。\n"
            "請使用 /new <task prompt> 開始新對話，或使用 "
            "/continue <task_id> 恢復舊對話。"
        )
        return

    selected_model = selected_model_for_chat(chat.id)
    if await reject_non_codex_session_model(update, selected_model):
        return
    turn_id = store.create_turn(task.id, prompt, model=selected_model)
    turn_number = store.get_turn_number(turn_id)
    if turn_number is None:
        await update.message.reply_text(f"Task #{task.id} could not create turn.")
        return
    turn_dir = turn_directory(settings.tasks_dir, task.id, turn_number)
    prepare_task_directory(turn_dir, prompt)
    store.set_turn_dir(turn_id, turn_dir)
    await update.message.reply_text(f"Task #{task.id} follow-up queued.")


async def handle_attachment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_auth(update):
        return
    if update.message is None:
        return

    attachment = attachment_from_message(update.message)
    if attachment is None:
        await update.message.reply_text("無法辨識這個附件類型。")
        return
    if (
        attachment.file_size is not None
        and attachment.file_size > MAX_TELEGRAM_DOWNLOAD_BYTES
    ):
        await update.message.reply_text(
            "附件超過 Telegram Bot API 可下載的 20 MB 上限。"
        )
        return

    chat = update.effective_chat
    selected_model = selected_model_for_chat(chat.id)
    if await reject_non_codex_session_model(update, selected_model):
        return

    force_new_session, prompt = parse_attachment_caption(update.message.caption)
    task = None
    if not force_new_session:
        task = store.get_active_task(
            chat.id,
            settings.session_timeout_seconds,
    )
    if task is None:
        task_id, turn_id = store.create_session(
            chat.id,
            prompt,
            settings.default_workspace,
            initial_status="uploading",
            model=selected_model,
        )
        queue_message = f"Task #{task_id} attachment queued as a new session."
    else:
        task_id = task.id
        turn_id = store.create_turn(
            task_id,
            prompt,
            initial_status="uploading",
            model=selected_model,
        )
        queue_message = f"Task #{task_id} attachment follow-up queued."

    turn_number = store.get_turn_number(turn_id)
    if turn_number is None:
        await update.message.reply_text(f"Task #{task_id} could not create turn.")
        return
    turn_dir = turn_directory(settings.tasks_dir, task_id, turn_number)
    prepare_task_directory(turn_dir, prompt)
    store.set_turn_dir(turn_id, turn_dir)
    destination = available_input_path(
        input_directory(turn_dir),
        attachment.filename,
    )

    try:
        telegram_file = await context.bot.get_file(attachment.file_id)
        await telegram_file.download_to_drive(custom_path=destination)
        full_prompt = build_attachment_prompt(prompt, [destination.resolve()])
        write_prompt(turn_dir, full_prompt)
        store.queue_uploaded_turn(turn_id, task_id, full_prompt)
    except (OSError, RuntimeError, TelegramError) as exc:
        destination.unlink(missing_ok=True)
        message = f"附件下載失敗：{exc}"
        store.mark_turn_failed(turn_id, task_id, message)
        await update.message.reply_text(f"Task #{task_id} {message}")
        return

    await update.message.reply_text(
        f"{queue_message}\n已接收：{destination.name}"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    chat = update.effective_chat
    tasks = store.recent_tasks(chat.id)
    if not tasks:
        await update.message.reply_text("No tasks yet.")
        return

    lines = [
        (
            f"#{task.id} {task.status} [{task.session_status}]: "
            f"{task.prompt[:80]} (model: {model_label(task.model)})"
        )
        for task in tasks
    ]
    await update.message.reply_text("\n".join(lines))


async def usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    codex_report, antigravity_report = await asyncio.gather(
        query_codex_usage(settings.codex_bin),
        query_antigravity_usage(settings.antigravity_bin),
    )
    await update.message.reply_text(
        build_usage_message(codex_report, antigravity_report)
    )


async def file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update):
        return

    if len(context.args) not in {1, 2}:
        await update.message.reply_text(
            "Usage: /file <task_id> [artifact_id]"
        )
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Usage: /file <task_id> [artifact_id]"
        )
        return

    chat = update.effective_chat
    task = store.get_task(task_id, chat.id)
    if task is None:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    artifacts = sync_artifact_metadata(store, task)
    if not artifacts:
        await update.message.reply_text(
            f"Task #{task_id} has no output files yet (status: {task.status})."
        )
        return

    if len(context.args) == 2:
        try:
            artifact_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text(
                "Usage: /file <task_id> [artifact_id]"
            )
            return
        artifact = next(
            (item for item in artifacts if item.id == artifact_id),
            None,
        )
        if artifact is None:
            await update.message.reply_text(
                "找不到這個產物，請重新使用 /file 取得最新清單。"
            )
            return
        error = await send_artifact(context.bot, chat.id, task, artifact)
        if error:
            await update.message.reply_text(error)
        return

    await update.message.reply_text(
        file_list_message(task.id, artifacts, 0),
        reply_markup=build_file_keyboard(task.id, artifacts, 0),
    )


async def send_artifact(bot, chat_id: int, task, artifact) -> str | None:
    path = resolve_artifact_path(task, artifact)
    if path is None:
        return "檔案已不存在或選項已失效，請重新使用 /file。"

    try:
        file_size = path.stat().st_size
        if file_size > MAX_TELEGRAM_DOCUMENT_BYTES:
            return (
                f"{artifact.display_name} 大小為 {human_file_size(file_size)}，"
                "超過 Telegram Bot API 的 50 MB 傳送上限。"
            )
        with path.open("rb") as document:
            await bot.send_document(
                chat_id=chat_id,
                document=document,
                caption=truncate_text(
                    f"Task #{task.id}: {artifact.display_name}",
                    900,
                ),
            )
    except (OSError, TelegramError) as exc:
        return f"無法傳送 {artifact.display_name}：{exc}"
    return None


async def file_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if query is None or chat is None:
        return
    if not is_authorized(chat.id, settings.allowed_chat_ids):
        await query.answer("Unauthorized chat.", show_alert=True)
        return

    callback = parse_file_callback(query.data or "")
    if callback is None:
        await query.answer(
            "這個檔案選項已失效，請重新使用 /file。",
            show_alert=True,
        )
        return

    if callback.action == "download":
        artifact = store.get_artifact(callback.value)
        task = (
            store.get_task(artifact.task_id, chat.id)
            if artifact is not None
            else None
        )
        if artifact is None or task is None:
            await query.answer("無權限或檔案選項已失效。", show_alert=True)
            return
        await query.answer()
        error = await send_artifact(context.bot, chat.id, task, artifact)
        if error:
            await context.bot.send_message(chat.id, error)
        return

    task = store.get_task(callback.value, chat.id)
    if task is None:
        await query.answer("無權限或 Task 不存在。", show_alert=True)
        return
    artifacts = sync_artifact_metadata(store, task)
    if not artifacts:
        await query.answer("目前沒有可下載產物。", show_alert=True)
        return

    if callback.action == "page":
        page = callback.page or 0
        await query.answer()
        await query.edit_message_text(
            file_list_message(task.id, artifacts, page),
            reply_markup=build_file_keyboard(task.id, artifacts, page),
        )
        return

    await query.answer("開始傳送全部產物")
    for artifact in artifacts:
        error = await send_artifact(context.bot, chat.id, task, artifact)
        if error:
            await context.bot.send_message(chat.id, error)


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
    await send_markdown_text(context.bot, chat.id, message)


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

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /continue <task_id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /continue <task_id>")
        return

    chat = update.effective_chat
    task = store.get_task(task_id, chat.id)
    if task is None:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    if (
        task.codex_session_id is None
        and not (
            task.status in {"pending", "running"}
            and store.has_turns(task.id)
        )
    ):
        await update.message.reply_text(
            f"Task #{task_id} 沒有可恢復的 Codex session。"
        )
        return

    store.activate_task(task.id, chat.id)
    await update.message.reply_text(
        f"Task #{task.id} session resumed. 後續普通文字會接續此 session。"
    )


async def model_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_auth(update):
        return

    chat = update.effective_chat
    current_model = selected_model_for_chat(chat.id)
    if not context.args:
        reply_markup = (
            build_model_keyboard(settings.available_models, current_model)
            if settings.available_models
            else None
        )
        await update.message.reply_text(
            model_message(settings.available_models, current_model),
            reply_markup=reply_markup,
        )
        return

    requested_model = " ".join(context.args)
    selected_model = resolve_model_argument(
        requested_model,
        settings.available_models,
    )
    if selected_model is None:
        available = ", ".join(
            model_label(model) for model in settings.available_models
        )
        await update.message.reply_text(
            f"模型「{requested_model}」不在允許的白名單中。\n"
            f"可用模型：{available or '(未設定)'}"
        )
        return

    store.set_selected_model(chat.id, selected_model)
    await update.message.reply_text(
        f"已切換至 {model_label(selected_model)}，從下一個新建 Turn 開始生效。"
    )


async def model_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if query is None or chat is None:
        return
    if not is_authorized(chat.id, settings.allowed_chat_ids):
        await query.answer("Unauthorized chat.", show_alert=True)
        return

    selected_model = resolve_model_callback(
        query.data or "",
        settings.available_models,
    )
    if selected_model is None:
        await query.answer("這個模型選項已失效，請重新使用 /model。", show_alert=True)
        return

    store.set_selected_model(chat.id, selected_model)
    await query.answer(f"已切換至 {model_label(selected_model)}")
    await query.edit_message_text(
        model_message(settings.available_models, selected_model),
        reply_markup=build_model_keyboard(
            settings.available_models,
            selected_model,
        ),
    )


async def progress_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await require_auth(update):
        return

    chat = update.effective_chat
    enabled = store.get_progress_enabled(chat.id)
    if context.args:
        if len(context.args) != 1:
            await update.message.reply_text("Usage: /progress [on|off]")
            return
        requested = resolve_progress_value(context.args[0])
        if requested is None:
            await update.message.reply_text("Usage: /progress [on|off]")
            return
        enabled = requested
        store.set_progress_enabled(chat.id, enabled)

    await update.message.reply_text(
        progress_message(enabled),
        reply_markup=build_progress_keyboard(enabled),
    )


async def progress_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if query is None or chat is None:
        return
    if not is_authorized(chat.id, settings.allowed_chat_ids):
        await query.answer("Unauthorized chat.", show_alert=True)
        return

    enabled = resolve_progress_callback(query.data or "")
    if enabled is None:
        await query.answer(
            "這個進度選項已失效，請重新使用 /progress。",
            show_alert=True,
        )
        return

    current = store.get_progress_enabled(chat.id)
    if current == enabled:
        status = "已開啟" if enabled else "已關閉"
        await query.answer(f"即時任務進度{status}")
        return

    store.set_progress_enabled(chat.id, enabled)
    status = "已開啟" if enabled else "已關閉"
    await query.answer(f"即時任務進度{status}")
    await query.edit_message_text(
        progress_message(enabled),
        reply_markup=build_progress_keyboard(enabled),
    )


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand(command=command, description=description)
            for command, _, description in COMMAND_HELP
        ]
    )
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
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("new", new_session))
    application.add_handler(CommandHandler("end", end_session))
    application.add_handler(CommandHandler("run", run))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("usage", usage))
    application.add_handler(CommandHandler("file", file))
    application.add_handler(CommandHandler("result", result))
    application.add_handler(CommandHandler("log", log))
    application.add_handler(CommandHandler("continue", continue_task))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("progress", progress_command))
    application.add_handler(
        CallbackQueryHandler(
            progress_callback,
            pattern=f"^{PROGRESS_CALLBACK_PREFIX}",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            model_callback,
            pattern=f"^{MODEL_CALLBACK_PREFIX}",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            file_callback,
            pattern=f"^{FILE_CALLBACK_PREFIX}",
        )
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(
        MessageHandler(filters.ATTACHMENT, handle_attachment)
    )
    application.run_polling()


if __name__ == "__main__":
    main()
