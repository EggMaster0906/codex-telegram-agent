from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


OUTPUT_CHUNK_SIZE = 64 * 1024
LEADING_STATUS_LINE = re.compile(
    r"^(?:"
    r"I will|I'll|I\u2019ll|I am going to|I'm going to|I need to|Let me"
    r")\s+"
    r"(?:"
    r"search|perform|write|look|check|inspect|read|run|create|prepare|"
    r"analy[sz]e|summari[sz]e|collect|use|open|find|verify|gather|"
    r"draft|make|generate|save|send"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AntigravityResult:
    exit_code: int


def strip_leading_status_lines(text: str) -> str:
    lines = text.splitlines()
    index = 0
    stripped_count = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if not LEADING_STATUS_LINE.match(line):
            break
        stripped_count += 1
        index += 1

    if not stripped_count or index >= len(lines):
        return text.strip()

    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:]).strip()


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


def build_antigravity_command(
    *,
    antigravity_bin: str,
    sandbox_mode: str,
    artifact_dir: Path,
    prompt: str,
    input_dir: Path | None = None,
    model: str | None = None,
) -> list[str]:
    command = [antigravity_bin]
    if sandbox_mode == "danger-full-access":
        command.append("--dangerously-skip-permissions")
    else:
        command.append("--sandbox")
    command.extend(["--add-dir", str(artifact_dir)])
    if input_dir is not None and input_dir.is_dir():
        command.extend(["--add-dir", str(input_dir)])
    if model:
        command.extend(["--model", model])
    command.extend(["--print", prompt])
    return command


async def _consume_stream(
    reader: asyncio.StreamReader,
    log_file: BinaryIO,
    output_chunks: list[bytes] | None = None,
) -> None:
    while chunk := await reader.read(OUTPUT_CHUNK_SIZE):
        log_file.write(chunk)
        log_file.flush()
        if output_chunks is not None:
            output_chunks.append(chunk)


async def run_antigravity(
    *,
    antigravity_bin: str,
    sandbox_mode: str,
    prompt: str,
    workspace_path: Path,
    artifact_dir: Path,
    log_path: Path,
    output_path: Path,
    timeout_seconds: int,
    input_dir: Path | None = None,
    model: str | None = None,
) -> AntigravityResult:
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
        "when the requested result is inherently file-based.\n"
        f"- Save every user deliverable under: {artifact_dir}\n"
        f"- Always write a JSON delivery manifest to: {delivery_manifest_path}\n"
        '- For text-only delivery, use: {"delivery":"text","attachments":[]}\n'
        '- For file delivery, use: {"delivery":"files","attachments":["relative/path.ext"]}\n'
        "- The delivery manifest is internal metadata. Do not mention it in the final "
        "response and do not list absolute server paths.\n"
        "- Do not include process notes, search plans, tool-use announcements, or "
        "delivery manifest details in the final response.\n"
    )

    command = build_antigravity_command(
        antigravity_bin=antigravity_bin,
        sandbox_mode=sandbox_mode,
        artifact_dir=artifact_dir,
        input_dir=input_dir,
        prompt=prompt + artifact_instruction,
        model=model,
    )

    stdout_chunks: list[bytes] = []
    with log_path.open("wb") as log_file:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=workspace_path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None

        stdout_consumer = asyncio.create_task(
            _consume_stream(process.stdout, log_file, stdout_chunks)
        )
        stderr_consumer = asyncio.create_task(
            _consume_stream(process.stderr, log_file)
        )
        try:
            exit_code, _, _ = await asyncio.wait_for(
                asyncio.gather(
                    process.wait(),
                    stdout_consumer,
                    stderr_consumer,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            await terminate_process_group(process)
            for consumer in (stdout_consumer, stderr_consumer):
                if not consumer.done():
                    consumer.cancel()
            await asyncio.gather(
                stdout_consumer,
                stderr_consumer,
                return_exceptions=True,
            )
            log_file.write(b"\nTask timed out and was killed.\n")
            log_file.flush()
            output_path.write_text(
                b"".join(stdout_chunks).decode("utf-8", errors="replace"),
                encoding="utf-8",
            )
            return AntigravityResult(124)
        except asyncio.CancelledError:
            await terminate_process_group(process)
            for consumer in (stdout_consumer, stderr_consumer):
                if not consumer.done():
                    consumer.cancel()
            await asyncio.gather(
                stdout_consumer,
                stderr_consumer,
                return_exceptions=True,
            )
            raise
        except Exception:
            await terminate_process_group(process)
            for consumer in (stdout_consumer, stderr_consumer):
                if not consumer.done():
                    consumer.cancel()
            await asyncio.gather(
                stdout_consumer,
                stderr_consumer,
                return_exceptions=True,
            )
            raise

    output_text = strip_leading_status_lines(
        b"".join(stdout_chunks).decode("utf-8", errors="replace")
    )
    output_path.write_text(output_text.strip() + "\n", encoding="utf-8")
    if not delivery_manifest_path.exists():
        delivery_manifest_path.write_text(
            json.dumps({"delivery": "text", "attachments": []}),
            encoding="utf-8",
        )
    return AntigravityResult(exit_code)
