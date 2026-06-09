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

    artifact_instruction = (
        "\n\n"
        "Output file requirement:\n"
        f"- Save every file created for the user under: {artifact_dir}\n"
        "- Do not save user deliverables elsewhere.\n"
        "- In the final response, list the created file paths.\n"
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
