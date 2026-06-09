from __future__ import annotations

import asyncio
from pathlib import Path


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
) -> int:
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

    command = [
        codex_bin,
        "exec",
        "--sandbox",
        sandbox_mode,
        "--skip-git-repo-check",
        "--add-dir",
        str(artifact_dir),
        "--output-last-message",
        str(output_path),
        prompt + artifact_instruction,
    ]

    with log_path.open("wb") as log_file:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=workspace_path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            return await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            log_file.write(b"\nTask timed out and was killed.\n")
            return 124
