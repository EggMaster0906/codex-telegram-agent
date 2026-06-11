from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path


INPUTS_DIR_NAME = "inputs"
DEFAULT_ATTACHMENT_PROMPT = "請檢視並處理使用者提供的附件。"
MAX_TELEGRAM_DOWNLOAD_BYTES = 20 * 1024 * 1024
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
NEW_SESSION_CAPTION = re.compile(
    r"^/new(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$",
    re.DOTALL,
)


@dataclass(frozen=True)
class IncomingAttachment:
    file_id: str
    file_unique_id: str
    filename: str
    file_size: int | None


def attachment_from_message(message: object) -> IncomingAttachment | None:
    photo = getattr(message, "photo", None)
    if photo:
        attachment = max(
            photo,
            key=lambda item: (
                getattr(item, "file_size", 0) or 0,
                (getattr(item, "width", 0) or 0)
                * (getattr(item, "height", 0) or 0),
            ),
        )
        return _attachment(attachment, "photo", ".jpg")

    candidates = (
        ("document", "document", ""),
        ("audio", "audio", ""),
        ("video", "video", ".mp4"),
        ("animation", "animation", ".mp4"),
        ("voice", "voice", ".ogg"),
        ("video_note", "video-note", ".mp4"),
        ("sticker", "sticker", ""),
    )
    for attribute, fallback_name, fallback_extension in candidates:
        attachment = getattr(message, attribute, None)
        if attachment is not None:
            return _attachment(attachment, fallback_name, fallback_extension)
    return None


def input_directory(turn_dir: Path) -> Path:
    return turn_dir / INPUTS_DIR_NAME


def available_input_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(filename)
    candidate = directory / safe_name
    suffix = candidate.suffix
    stem = candidate.stem
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def build_attachment_prompt(prompt: str, paths: list[Path]) -> str:
    lines = [
        prompt.strip() or DEFAULT_ATTACHMENT_PROMPT,
        "",
        "使用者提供的附件已下載到以下路徑，請依照上述需求讀取並處理：",
    ]
    lines.extend(f"- {path}" for path in paths)
    return "\n".join(lines)


def parse_attachment_caption(caption: str | None) -> tuple[bool, str]:
    text = (caption or "").strip()
    match = NEW_SESSION_CAPTION.fullmatch(text)
    if match is None:
        return False, text or DEFAULT_ATTACHMENT_PROMPT

    prompt = (match.group(1) or "").strip()
    return True, prompt or DEFAULT_ATTACHMENT_PROMPT


def sanitize_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = CONTROL_CHARACTERS.sub("_", name).strip(" .")
    if not name.strip("_") or name in {".", ".."}:
        name = "attachment"

    path = Path(name)
    suffix = path.suffix[:20]
    stem_limit = max(1, 180 - len(suffix))
    stem = path.stem[:stem_limit].rstrip(" .") or "attachment"
    return f"{stem}{suffix}"


def _attachment(
    attachment: object,
    fallback_name: str,
    fallback_extension: str,
) -> IncomingAttachment:
    file_id = str(getattr(attachment, "file_id"))
    unique_id = str(getattr(attachment, "file_unique_id", file_id))
    filename = getattr(attachment, "file_name", None)
    if not filename:
        mime_type = getattr(attachment, "mime_type", None)
        guessed_extension = mimetypes.guess_extension(mime_type or "") or ""
        extension = guessed_extension or fallback_extension
        if fallback_name == "sticker" and not extension:
            extension = ".webp"
        filename = f"{fallback_name}-{unique_id}{extension}"
    return IncomingAttachment(
        file_id=file_id,
        file_unique_id=unique_id,
        filename=sanitize_filename(str(filename)),
        file_size=getattr(attachment, "file_size", None),
    )
