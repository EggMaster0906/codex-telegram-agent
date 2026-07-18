from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time
from dataclasses import dataclass
from datetime import datetime


ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07]*(?:\x07|\x1b\\)"
    r"|[PX^_].*?\x1b\\"
    r"|[@-_]"
    r")"
)
ANTIGRAVITY_GROUP_PATTERN = re.compile(
    r"(?m)^(GEMINI MODELS|CLAUDE AND GPT MODELS)\s*$"
)
ANTIGRAVITY_LIMIT_PATTERN = re.compile(
    r"(Weekly Limit|Five Hour Limit)(.*?)(?="
    r"Weekly Limit|Five Hour Limit|\Z)",
    re.DOTALL,
)
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)%")


class UsageQueryError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageWindow:
    label: str
    remaining_percent: float | None
    resets_at: int | None = None
    status: str | None = None


@dataclass(frozen=True)
class UsageGroup:
    name: str
    windows: tuple[UsageWindow, ...]


@dataclass(frozen=True)
class ProviderUsage:
    provider: str
    groups: tuple[UsageGroup, ...] = ()
    error: str | None = None


async def query_codex_usage(
    codex_bin: str,
    timeout_seconds: float = 15.0,
) -> ProviderUsage:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            codex_bin,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if process.stdin is None or process.stdout is None:
            raise UsageQueryError("無法建立 Codex App Server 通訊管道")

        await _write_json_line(
            process.stdin,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "codex-telegram-agent",
                        "title": "Codex Telegram Agent",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        await asyncio.wait_for(
            _read_json_response(process.stdout, 1),
            timeout=timeout_seconds,
        )
        await _write_json_line(process.stdin, {"method": "initialized"})
        await _write_json_line(
            process.stdin,
            {"id": 2, "method": "account/rateLimits/read"},
        )
        response = await asyncio.wait_for(
            _read_json_response(process.stdout, 2),
            timeout=timeout_seconds,
        )
        return parse_codex_usage_response(response)
    except asyncio.TimeoutError:
        return ProviderUsage("Codex", error="查詢逾時")
    except OSError as exc:
        return ProviderUsage("Codex", error=f"無法執行 Codex CLI：{exc}")
    except (UsageQueryError, ValueError, TypeError) as exc:
        return ProviderUsage("Codex", error=str(exc))
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


async def query_antigravity_usage(
    antigravity_bin: str,
    timeout_seconds: float = 20.0,
) -> ProviderUsage:
    try:
        output = await asyncio.to_thread(
            _capture_antigravity_usage,
            antigravity_bin,
            timeout_seconds,
        )
        return parse_antigravity_usage_output(output)
    except OSError as exc:
        return ProviderUsage(
            "Antigravity",
            error=f"無法執行 Antigravity CLI：{exc}",
        )
    except (UsageQueryError, ValueError) as exc:
        return ProviderUsage("Antigravity", error=str(exc))


def parse_codex_usage_response(response: dict[str, object]) -> ProviderUsage:
    error = response.get("error")
    if error is not None:
        raise UsageQueryError(_rpc_error_message(error))

    result = response.get("result")
    if not isinstance(result, dict):
        raise UsageQueryError("Codex 回傳的用量格式不正確")

    raw_buckets = result.get("rateLimitsByLimitId")
    if not isinstance(raw_buckets, dict) or not raw_buckets:
        snapshot = result.get("rateLimits")
        if not isinstance(snapshot, dict):
            raise UsageQueryError("Codex 未回傳可用的額度資料")
        raw_buckets = {"codex": snapshot}

    groups: list[UsageGroup] = []
    for bucket_id, raw_snapshot in raw_buckets.items():
        if not isinstance(raw_snapshot, dict):
            continue
        windows: list[UsageWindow] = []
        for key in ("primary", "secondary"):
            raw_window = raw_snapshot.get(key)
            if not isinstance(raw_window, dict):
                continue
            used_percent = raw_window.get("usedPercent")
            if not isinstance(used_percent, (int, float)):
                continue
            duration = raw_window.get("windowDurationMins")
            resets_at = raw_window.get("resetsAt")
            windows.append(
                UsageWindow(
                    label=_duration_label(duration),
                    remaining_percent=max(0.0, min(100.0, 100 - used_percent)),
                    resets_at=resets_at if isinstance(resets_at, int) else None,
                )
            )

        individual = raw_snapshot.get("individualLimit")
        if isinstance(individual, dict):
            remaining = individual.get("remainingPercent")
            resets_at = individual.get("resetsAt")
            if isinstance(remaining, (int, float)):
                windows.append(
                    UsageWindow(
                        label="個人用量上限",
                        remaining_percent=max(0.0, min(100.0, remaining)),
                        resets_at=resets_at if isinstance(resets_at, int) else None,
                    )
                )

        if not windows:
            continue
        limit_name = raw_snapshot.get("limitName")
        group_name = (
            str(limit_name)
            if isinstance(limit_name, str) and limit_name.strip()
            else str(bucket_id)
        )
        groups.append(UsageGroup(group_name, tuple(windows)))

    if not groups:
        raise UsageQueryError("Codex 未回傳可顯示的剩餘額度")
    return ProviderUsage("Codex", tuple(groups))


def parse_antigravity_usage_output(output: str) -> ProviderUsage:
    plain = ANSI_ESCAPE_PATTERN.sub("", output).replace("\r", "\n")
    start = plain.rfind("GEMINI MODELS")
    if start < 0:
        raise UsageQueryError("Antigravity /usage 未回傳模型額度")
    plain = plain[start:]

    matches = list(ANTIGRAVITY_GROUP_PATTERN.finditer(plain))
    groups: list[UsageGroup] = []
    for index, match in enumerate(matches):
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(plain)
        section = plain[match.end():section_end]
        windows: list[UsageWindow] = []
        for limit_match in ANTIGRAVITY_LIMIT_PATTERN.finditer(section):
            label = {
                "Weekly Limit": "每週",
                "Five Hour Limit": "5 小時",
            }[limit_match.group(1)]
            details = limit_match.group(2)
            percent_match = PERCENT_PATTERN.search(details)
            status = _antigravity_status(details)
            if percent_match is None and status is None:
                continue
            windows.append(
                UsageWindow(
                    label=label,
                    remaining_percent=(
                        float(percent_match.group(1))
                        if percent_match is not None
                        else None
                    ),
                    status=status,
                )
            )
        if windows:
            group_name = {
                "GEMINI MODELS": "Gemini 模型",
                "CLAUDE AND GPT MODELS": "Claude 與 GPT 模型",
            }[match.group(1)]
            groups.append(UsageGroup(group_name, tuple(windows)))

    if not groups:
        raise UsageQueryError("Antigravity /usage 的額度格式無法辨識")
    return ProviderUsage("Antigravity", tuple(groups))


def build_usage_message(*reports: ProviderUsage) -> str:
    lines = ["目前剩餘用量："]
    for report in reports:
        lines.append("")
        lines.append(report.provider)
        if report.error:
            lines.append(f"  無法取得：{report.error}")
            continue
        for group in report.groups:
            show_group = not (
                report.provider == "Codex"
                and len(report.groups) == 1
                and group.name.casefold() == "codex"
            )
            if show_group:
                lines.append(f"  {group.name}")
                indent = "    "
            else:
                indent = "  "
            for window in group.windows:
                suffix = ""
                if window.resets_at is not None:
                    reset_time = datetime.fromtimestamp(
                        window.resets_at
                    ).astimezone()
                    suffix = f"，重設 {reset_time:%m/%d %H:%M}"
                if window.remaining_percent is None:
                    value = _translate_status(window.status)
                    lines.append(f"{indent}{window.label}：{value}{suffix}")
                else:
                    value = _format_percent(window.remaining_percent)
                    lines.append(
                        f"{indent}{window.label}：剩餘 {value}{suffix}"
                    )
    return "\n".join(lines)


async def _write_json_line(
    stream: asyncio.StreamWriter,
    payload: dict[str, object],
) -> None:
    stream.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
    await stream.drain()


async def _read_json_response(
    stream: asyncio.StreamReader,
    request_id: int,
) -> dict[str, object]:
    while True:
        line = await stream.readline()
        if not line:
            raise UsageQueryError("Codex App Server 提前結束")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message


def _capture_antigravity_usage(
    antigravity_bin: str,
    timeout_seconds: float,
) -> str:
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    try:
        fcntl.ioctl(
            slave_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", 80, 120, 0, 0),
        )
        environment = os.environ.copy()
        environment.update(
            {"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "80"}
        )
        process = subprocess.Popen(
            [antigravity_bin],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=environment,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = -1

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            _read_pty_chunk(master_fd, output, 0.2)
            if b"? for shortcuts" in output:
                break
            if process.poll() is not None:
                raise UsageQueryError("Antigravity CLI 在登入完成前結束")
        else:
            raise UsageQueryError("Antigravity CLI 啟動逾時")

        os.write(master_fd, b"/usage\r")
        quota_seen = False
        quiet_deadline: float | None = None
        while time.monotonic() < deadline:
            received = _read_pty_chunk(master_fd, output, 0.2)
            if b"GEMINI MODELS" in output:
                quota_seen = True
            if quota_seen and received:
                quiet_deadline = time.monotonic() + 1.0
            if quota_seen and quiet_deadline and time.monotonic() >= quiet_deadline:
                break
            if process.poll() is not None:
                break
        if not quota_seen:
            raise UsageQueryError("Antigravity /usage 查詢逾時")
        return output.decode("utf-8", errors="replace")
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)


def _read_pty_chunk(master_fd: int, output: bytearray, wait: float) -> bool:
    readable, _, _ = select.select([master_fd], [], [], wait)
    if not readable:
        return False
    try:
        chunk = os.read(master_fd, 65_536)
    except OSError:
        return False
    if not chunk:
        return False
    output.extend(chunk)
    return True


def _duration_label(duration: object) -> str:
    if duration == 300:
        return "5 小時"
    if duration == 10_080:
        return "每週"
    if isinstance(duration, int) and duration > 0:
        if duration % 1_440 == 0:
            return f"{duration // 1_440} 天"
        if duration % 60 == 0:
            return f"{duration // 60} 小時"
        return f"{duration} 分鐘"
    return "額度"


def _rpc_error_message(error: object) -> str:
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return f"Codex：{error['message']}"
    return f"Codex 查詢失敗：{error}"


def _antigravity_status(details: str) -> str | None:
    for status in ("Quota available", "Quota exhausted", "Disabled"):
        if status in details:
            return status
    return None


def _translate_status(status: str | None) -> str:
    return {
        "Quota available": "可用",
        "Quota exhausted": "0%（已用盡）",
        "Disabled": "停用",
    }.get(status, "未知")


def _format_percent(value: float) -> str:
    value = float(value)
    if value.is_integer():
        return f"{int(value)}%"
    return f"{value:.2f}%"
