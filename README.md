# Codex Telegram Agent

Codex Telegram Agent 是部署於 Linux 主機的輕量任務代理。授權使用者可以
透過 Telegram 建立 Codex 任務、查詢狀態、接收文字結果，並直接下載任務
產生的圖片、文件或其他檔案。

```text
Telegram 使用者
  -> Telegram Bot
  -> SQLite 任務佇列
  -> 背景 Worker
  -> Codex CLI
  -> Task 獨立目錄
  -> Telegram 文字與檔案回傳
```

## 現有功能

- 只接受 `ALLOWED_CHAT_IDS` 內的 Telegram chat。
- 使用 SQLite 保存 prompt、狀態、workspace 與輸出路徑。
- 背景 worker 依序執行 pending tasks。
- 透過 Codex CLI 非互動模式執行任務。
- 每個 Task 使用獨立目錄保存 prompt、log、final output 與 artifacts。
- 任務完成後將 final output 分段回傳 Telegram。
- 自動使用 Telegram `send_document` 傳送任務產生的 artifacts。
- 支援 `/file <task_id>` 重新下載 final output 與 artifacts。
- 下載時驗證 task 所屬 chat id，避免跨使用者讀取。
- 啟動時自動 migration 既有 SQLite schema。

## 任務流程

1. 使用者透過 `/run <prompt>` 建立任務。
2. Bot 將任務寫入 SQLite，狀態設為 `pending`。
3. Bot 建立 `tasks/task-XXXXXX/` 與 `artifacts/`。
4. Worker 將任務改為 `running` 並呼叫 Codex CLI。
5. Codex 在原 workspace 工作，使用者產物寫入該 Task 的 `artifacts/`。
6. Worker 保存完整 log 與 Codex 最終回覆。
7. 成功時回傳文字結果並自動傳送 artifacts。
8. 使用者之後仍可透過 `/file <task_id>` 重新下載。

## 專案結構

```text
codex-telegram-agent/
  app/
    artifacts.py        # Task 目錄與產物掃描
    bot.py              # Telegram command entrypoint
    worker.py           # Background task worker
    db.py               # SQLite schema and task operations
    config.py           # Environment configuration
    codex_runner.py     # Codex CLI subprocess wrapper
    telegram_utils.py   # Telegram message helpers
  data/
    tasks.sqlite3       # Runtime database
  tasks/
    task-000001/
      prompt.txt        # 原始任務內容
      task.log          # 完整執行 log
      final.md          # Codex 最終回覆
      artifacts/        # 圖片、文件等使用者產物
  systemd/
    codex-telegram-agent.service
  .env                  # Runtime secrets and local configuration
  .env.example          # Environment template
  FEATURE_BACKLOG.md    # 待開發與已完成功能
  requirements.txt
  README.md
```

## 目前支援的 Telegram 指令

```text
/start
/run <task prompt>
/status
/file <task_id>
```

`/start` 會回傳目前 chat id 與授權狀態，方便第一次設定 `ALLOWED_CHAT_IDS`。

`/run <task prompt>` 會建立新 Task 並立即回覆 task id。

`/status` 會顯示目前 chat 最近五筆任務與狀態。

`/file <task_id>` 只允許原任務所屬的 Telegram chat 下載，並傳送該 Task
的 `final.md` 與所有 artifacts。新任務完成時，artifacts 也會自動傳送。

## 環境變數

設定檔位置：

```text
/home/ai-agent/codex-telegram-agent/.env
```

必要欄位：

```env
TELEGRAM_BOT_TOKEN=replace_me
ALLOWED_CHAT_IDS=123456789
DEFAULT_WORKSPACE=/home/ai-agent
CODEX_BIN=/home/ai-agent/.local/bin/codex
CODEX_SANDBOX_MODE=danger-full-access
TASK_TIMEOUT_SECONDS=5400
DATABASE_PATH=/home/ai-agent/codex-telegram-agent/data/tasks.sqlite3
TASKS_DIR=/home/ai-agent/codex-telegram-agent/tasks
WORKER_POLL_SECONDS=2
```

不要將真實 bot token 寫入 Git 或公開文件。

`.env.example` 預設使用較保守的 `workspace-write`。目前遠端主機無法
初始化 bubblewrap sandbox，經管理者明確核准後，實際部署環境使用
`CODEX_SANDBOX_MODE=danger-full-access`。

此模式不提供 Codex 內建檔案 sandbox，Codex 可以讀寫 `ai-agent` Linux
帳號原本就有權限存取的檔案。請勿任意擴大該帳號的 sudo 或檔案權限。

## 遠端部署

```bash
cd /home/ai-agent/codex-telegram-agent
./.venv/bin/python -m app.bot
```

目前部署位置與 Codex CLI：

```text
/home/ai-agent/codex-telegram-agent
/home/ai-agent/.local/bin/codex
```

Bot 目前以手動背景程序執行，log 位於：

```text
/home/ai-agent/codex-telegram-agent/logs/bot-manual.log
```

檢查程序：

```bash
pgrep -af -- "python -m app.bot"
```

## 驗證

執行單元測試與語法檢查：

```bash
cd /home/ai-agent/codex-telegram-agent
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m compileall -q app tests
```

已完成下列端對端驗證：

- Telegram Bot API `getMe` 成功。
- Telegram `/start`、`/run` 與主動訊息發送成功。
- 背景 worker 可執行 Telegram 建立的 Task。
- Codex CLI 可在 Task `artifacts/` 內實際建立檔案。
- `final.md`、artifact 掃描、SQLite migration 與 bot 重啟均正常。

## 待開發功能

- `/result <task_id>` 與 `/log <task_id>`。
- `/continue <task_id>` 與 Codex session resume。
- 任務取消。
- `/files <task_id>` artifact 清單。
- Artifact metadata 資料表。
- 多專案 workspace 白名單。
- systemd 常駐服務與自動重啟。
