from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexResult:
    exit_code: int
    session_id: str | None


def parse_session_id(line: bytes) -> str | None:
    try:
        event = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(event, dict):
        return None
    if event.get("type") == "thread.started":
        thread_id = event.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None
    return None


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
        )

        async def consume_output() -> None:
            nonlocal captured_session_id
            assert process.stdout is not None
            while line := await process.stdout.readline():
                log_file.write(line)
                parsed_session_id = parse_session_id(line)
                if parsed_session_id:
                    captured_session_id = parsed_session_id

        consumer = asyncio.create_task(consume_output())
        try:
            exit_code = await asyncio.wait_for(
                process.wait(),
                timeout=timeout_seconds,
            )
            await consumer
            return CodexResult(exit_code, captured_session_id)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            await consumer
            log_file.write(b"\nTask timed out and was killed.\n")
            return CodexResult(124, captured_session_id)
