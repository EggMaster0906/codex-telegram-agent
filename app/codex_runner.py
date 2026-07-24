from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable


OUTPUT_CHUNK_SIZE = 64 * 1024
SESSION_SCAN_LIMIT = 1024 * 1024
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexResult:
    exit_code: int
    session_id: str | None


def parse_event(line: bytes) -> dict[str, object] | None:
    try:
        event = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    return event if isinstance(event, dict) else None


def parse_session_id(line: bytes) -> str | None:
    event = parse_event(line)
    if event is None:
        return None
    if event.get("type") == "thread.started":
        thread_id = event.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None
    return None


class ProgressEventTracker:
    def __init__(self) -> None:
        self.pending_message: str | None = None
        self.last_emitted_message: str | None = None

    def consume(self, event: dict[str, object]) -> str | None:
        event_type = event.get("type")
        if event_type == "turn.completed":
            self.pending_message = None
            return None

        item = event.get("item")
        if not isinstance(item, dict):
            return None

        item_type = item.get("type")
        if event_type == "item.completed" and item_type == "agent_message":
            message = item.get("text")
            if not isinstance(message, str) or not message.strip():
                return None
            progress = self._take_pending()
            self.pending_message = message.strip()
            return progress

        if event_type in {"item.started", "item.completed"}:
            return self._take_pending()
        return None

    def _take_pending(self) -> str | None:
        message = self.pending_message
        self.pending_message = None
        if message is None or message == self.last_emitted_message:
            return None
        self.last_emitted_message = message
        return message


async def consume_process_output(
    reader: asyncio.StreamReader,
    log_file: BinaryIO,
    session_id: str | None,
    on_session_id: Callable[[str], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> str | None:
    captured_session_id = session_id
    line_buffer = bytearray()
    progress_tracker = ProgressEventTracker()

    def consume_line(line: bytes) -> None:
        nonlocal captured_session_id
        event = parse_event(line)
        if event is None:
            return

        if not captured_session_id and event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str):
                captured_session_id = thread_id
                if on_session_id:
                    on_session_id(thread_id)

        progress = progress_tracker.consume(event)
        if progress and on_progress:
            try:
                on_progress(progress)
            except Exception:
                logger.exception("Could not publish Codex progress event")

    while chunk := await reader.read(OUTPUT_CHUNK_SIZE):
        log_file.write(chunk)
        log_file.flush()
        line_buffer.extend(chunk)
        while True:
            newline_index = line_buffer.find(b"\n")
            if newline_index < 0:
                break
            line = bytes(line_buffer[: newline_index + 1])
            del line_buffer[: newline_index + 1]
            consume_line(line)

        if len(line_buffer) > SESSION_SCAN_LIMIT:
            del line_buffer[:-OUTPUT_CHUNK_SIZE]

    if line_buffer:
        consume_line(bytes(line_buffer))
    return captured_session_id


async def terminate_process_group(
    process: asyncio.subprocess.Process,
) -> None:
    if process.returncode is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()
    await process.wait()


def build_codex_command(
    *,
    codex_bin: str,
    sandbox_mode: str,
    artifact_dir: Path,
    output_path: Path,
    prompt: str,
    session_id: str | None,
    input_dir: Path | None = None,
    model: str | None = None,
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "--sandbox",
        sandbox_mode,
        "--skip-git-repo-check",
        "--add-dir",
        str(artifact_dir),
    ]
    if model:
        command.extend(["--model", model])
    if input_dir is not None and input_dir.is_dir():
        command.extend(["--add-dir", str(input_dir)])
    command.extend(
        [
            "--json",
            "--output-last-message",
            str(output_path),
        ]
    )
    if session_id:
        command.extend(["resume", session_id])
    command.append(prompt)
    return command


async def run_codex(
    *,
    codex_bin: str,
    sandbox_mode: str,
    prompt: str,
    workspace_path: Path,
    artifact_dir: Path,
    log_path: Path,
    output_path: Path,
    timeout_seconds: int,
    input_dir: Path | None = None,
    session_id: str | None = None,
    model: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> CodexResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    delivery_manifest_path = artifact_dir / ".delivery.json"
    artifact_instruction = (
        "\n\n"
        "Telegram delivery policy:\n"
        "- The final response is delivered as a Telegram text message by default.\n"
        "- Do not create a Markdown or text file merely to duplicate a normal answer.\n"
        "- Create deliverable files only when the user explicitly requests a file or "
        "when the requested result is inherently file-based, such as a presentation, "
        "image, PDF, Word document, spreadsheet, archive, or modified source file.\n"
        f"- Save every user deliverable under: {artifact_dir}\n"
        "- Do not save user deliverables elsewhere.\n"
        f"- Always write a JSON delivery manifest to: {delivery_manifest_path}\n"
        '- For text-only delivery, use: {"delivery":"text","attachments":[]}\n'
        '- For file delivery, use: {"delivery":"files","attachments":["relative/path.ext"]}\n'
        "- Attachment paths must be relative to the artifact directory and include only "
        "files that should be automatically sent to the user.\n"
        "- The delivery manifest is internal metadata. Do not mention it in the final "
        "response and do not list absolute server paths.\n"
        "- Do not include process notes, search plans, tool-use announcements, or "
        "delivery manifest details in the final response.\n"
    )

    command = build_codex_command(
        codex_bin=codex_bin,
        sandbox_mode=sandbox_mode,
        artifact_dir=artifact_dir,
        input_dir=input_dir,
        output_path=output_path,
        prompt=prompt + artifact_instruction,
        session_id=session_id,
        model=model,
    )

    captured_session_id = session_id
    with log_path.open("wb") as log_file:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=workspace_path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

        assert process.stdout is not None

        def capture_session_id(value: str) -> None:
            nonlocal captured_session_id
            captured_session_id = value

        consumer = asyncio.create_task(
            consume_process_output(
                process.stdout,
                log_file,
                captured_session_id,
                capture_session_id,
                on_progress,
            )
        )
        try:
            exit_code, captured_session_id = await asyncio.wait_for(
                asyncio.gather(process.wait(), consumer),
                timeout=timeout_seconds,
            )
            return CodexResult(exit_code, captured_session_id)
        except asyncio.TimeoutError:
            await terminate_process_group(process)
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            log_file.write(b"\nTask timed out and was killed.\n")
            log_file.flush()
            return CodexResult(124, captured_session_id)
        except asyncio.CancelledError:
            await terminate_process_group(process)
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            raise
        except Exception:
            await terminate_process_group(process)
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            raise
