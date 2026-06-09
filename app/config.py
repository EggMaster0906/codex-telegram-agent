from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_chat_ids: set[int]
    default_workspace: Path
    codex_bin: str
    codex_sandbox_mode: str
    task_timeout_seconds: int
    database_path: Path
    tasks_dir: Path
    worker_poll_seconds: float


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _chat_ids(raw: str) -> set[int]:
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


def load_settings() -> Settings:
    load_dotenv()
    root = Path("/home/ai-agent/codex-telegram-agent")

    return Settings(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        allowed_chat_ids=_chat_ids(_required("ALLOWED_CHAT_IDS")),
        default_workspace=Path(os.getenv("DEFAULT_WORKSPACE", "/home/ai-agent")),
        codex_bin=os.getenv("CODEX_BIN", "codex"),
        codex_sandbox_mode=os.getenv("CODEX_SANDBOX_MODE", "workspace-write"),
        task_timeout_seconds=int(os.getenv("TASK_TIMEOUT_SECONDS", "5400")),
        database_path=Path(os.getenv("DATABASE_PATH", str(root / "data/tasks.sqlite3"))),
        tasks_dir=Path(os.getenv("TASKS_DIR", str(root / "tasks"))),
        worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "2")),
    )
